import { createServer } from 'node:http';
import { createHash, createPublicKey, randomBytes, verify } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { pathToFileURL } from 'node:url';

import {operatorDiagnostics} from './diagnostics.mjs';

export const commands = {
  '연결': '내 PC의 도우미에 입력할 개인 연결 링크를 받습니다.',
  '연결해제': '내 PC와의 연결 및 대기 명령을 삭제합니다.',
  '상태': '내 도우미의 현재 상태와 마지막 명령 결과를 확인합니다.',
  '오류업로드': '연결된 내 PC의 오류 자료를 운영자 전용 채널로 한 번 보냅니다.',
  '시작': '내 PC에서 감독모드 자동 진행을 시작합니다.',
  '이번판까지만': '현재 경기 완료 후 중지를 예약합니다.',
  '중지': '내 PC의 자동 진행을 중지합니다.'
};

const hash = value => createHash('sha256').update(value).digest('hex');
const secret = () => randomBytes(32).toString('base64url');
const reply = content => ({type: 4, data: {content, flags: 64, allowed_mentions: {parse: []}}});
const actionNames = new Map([['시작', 'start'], ['이번판까지만', 'finish'], ['중지', 'stop'], ['오류업로드', 'upload-error']]);
const identifier = value => typeof value === 'string' && /^\d{17,20}$/.test(value);

export function createRelay({database, applicationId, publicKey, origin, diagnosticWebhook, sendWebhook, now = Date.now}) {
  const base = new URL(origin);
  if (base.protocol !== 'https:' || base.origin !== origin || !identifier(applicationId) ||
      !/^[a-f\d]{64}$/i.test(publicKey)) throw Error('Invalid PUBLIC_ORIGIN or Discord application configuration');
  const key = createPublicKey({format:'der',type:'spki',key:Buffer.from('302a300506032b6570032100'+publicKey,'hex')});
  const db = new DatabaseSync(database);
  db.exec(`
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS pairs (owner TEXT PRIMARY KEY, code TEXT UNIQUE, expires INTEGER);
    CREATE TABLE IF NOT EXISTS devices (
      owner TEXT PRIMARY KEY, token TEXT UNIQUE, session TEXT, seen INTEGER, status TEXT,
      command TEXT, request TEXT, expires INTEGER, result TEXT
    );
    CREATE TABLE IF NOT EXISTS interactions (id TEXT PRIMARY KEY, expires INTEGER);
    CREATE INDEX IF NOT EXISTS interactions_expiry ON interactions(expires);
  `);
  function atomic(work) {
    db.exec('BEGIN IMMEDIATE');
    try { const result = work(); db.exec('COMMIT'); return result; }
    catch (err) { db.exec('ROLLBACK'); throw err; }
  }
  function interaction(event, time) {
    if (event.type === 1) return {type:1};
    const owner = event.member?.user?.id ?? event.user?.id;
    if (event.type !== 2 || event.data?.type !== 1 || !identifier(owner) || !identifier(event.id))
      return reply('지원하지 않는 요청입니다.');
    db.prepare('DELETE FROM interactions WHERE expires <= ?').run(time);
    db.prepare('DELETE FROM pairs WHERE expires <= ?').run(time);
    if (!db.prepare('INSERT OR IGNORE INTO interactions VALUES (?,?)').run(event.id,time+610_000).changes)
      return reply('이미 접수한 요청입니다.');
    const name = event.data.name;
    if (name === '연결') {
      const code = secret();
      db.prepare('INSERT OR REPLACE INTO pairs VALUES (?,?,?)').run(owner,hash(code),time+300_000);
      return reply('5분 안에 아래 링크를 복사해 도우미의 Discord 탭에 붙여 넣으세요.\n`'+origin+'/#'+code+'`');
    }
    if (name === '연결해제') {
      db.prepare('DELETE FROM pairs WHERE owner=?').run(owner);
      db.prepare('DELETE FROM devices WHERE owner=?').run(owner);
      return reply('내 계정의 연결과 대기 명령을 해제했습니다.');
    }
    const dev = db.prepare('SELECT * FROM devices WHERE owner=?').get(owner);
    if (!dev) return reply('먼저 /연결 명령으로 내 PC를 연결하세요.');
    if (!dev.seen || time-dev.seen > 12_000) return reply('도우미가 오프라인입니다.');
    if (name === '상태') {
      const last = dev.result ? '\n최근 명령 · '+dev.result : '';
      return reply(dev.status + (dev.request && dev.expires > time ? '\n명령 처리 대기 중' : '') + last);
    }
    const cmd = actionNames.get(name);
    if (!cmd) return reply('지원하지 않는 명령입니다.');
    if (cmd === 'upload-error' && !diagnosticWebhook)
      return reply('운영자 오류 채널이 설정되지 않았습니다. Mac 서버의 OPERATOR_DIAGNOSTIC_WEBHOOK을 설정하고 다시 실행하세요.');
    if (dev.request && dev.expires > time && cmd !== 'stop') return reply('이전 명령 처리 중입니다. /상태에서 확인하세요.');
    db.prepare('UPDATE devices SET command=?, request=?, expires=?, result=? WHERE owner=?')
      .run(cmd,event.id,time+12_000,'/'+name+' 접수',owner);
    return reply('/'+name+' 요청을 접수했습니다. 실행 결과는 /상태에서 확인하세요.');
  }
  function devicePoll(token, data, time) {
    const d = db.prepare('SELECT * FROM devices WHERE token=?').get(hash(token));
    if (!d) return [401,{}];
    if (!/^[a-f\d]{32}$/.test(data.session ?? '') || typeof data.status !== 'string' || data.status.length > 500)
      return [400,{}];
    const ack = d.request && d.request === data.ack && typeof data.result === 'string' && d.session === data.session;
    if (ack || d.session !== data.session || (d.request && d.expires <= time)) {
      const result = ack ? data.result.slice(0,250) : d.request ? '명령 만료 또는 도우미 재시작 · 실행 결과 미확인' : d.result;
      db.prepare('UPDATE devices SET request=NULL, command=NULL, result=? WHERE owner=?').run(result,d.owner);
      d.request = null;
    }
    db.prepare('UPDATE devices SET session=?, seen=?, status=? WHERE owner=?').run(data.session,time,data.status,d.owner);
    return [200,{command:d.request ? {id:d.request, name:d.command, expires:d.expires} : null}];
  }
  const report = operatorDiagnostics(db,diagnosticWebhook,sendWebhook);
  const server = createServer(async (req,res) => {
    let operation='서명 확인';
    if (req.method === 'POST' && req.url === '/discord') {
      const started=performance.now();
      res.once('finish',()=>console.log('Discord '+operation+' · HTTP '+res.statusCode+' · '+Math.round(performance.now()-started)+' ms'));
    }
    function send(code,body) {
      res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store'});
      res.end(JSON.stringify(body));
    }
    try {
      if (req.method === 'POST' && req.url === '/diagnostics') return await report(req,send,now());
      if (req.method !== 'POST' || !['/discord','/pair','/poll'].includes(req.url)) return send(404,{});
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 65536) return send(413,{});
        chunks.push(chunk);
      }
      const body = Buffer.concat(chunks);
      const time = now();
      if (req.url === '/discord') {
        const ts = req.headers['x-signature-timestamp'];
        const sig = req.headers['x-signature-ed25519'];
        if (!/^\d{10}$/.test(ts ?? '') || Math.abs(time-Number(ts)*1000)>300_000 ||
            !/^[a-f\d]{128}$/i.test(sig ?? '') ||
            !verify(null,Buffer.concat([Buffer.from(ts),body]),key,Buffer.from(sig,'hex'))) return send(401,{});
      }
      let data;
      try { data = JSON.parse(body); } catch { return send(400,{}); }
      if (!data || typeof data !== 'object') return send(400,{});
      if (req.url === '/discord') {
        // Discord also uses a minimal signed PING when validating the endpoint.
        operation=data.type===1 ? 'PING' : Object.hasOwn(commands,data.data?.name) ? '/'+data.data.name : '요청';
        if (data.type === 1 && data.application_id == null) return send(200,{type:1});
        if (data.application_id !== applicationId) return send(403,{});
        return send(200,atomic(() => interaction(data,time)));
      }
      if (req.url === '/pair') {
        if (typeof data.code !== 'string' || !/^[\w-]{43}$/.test(data.code)) return send(400,{});
        const result = atomic(() => {
          const pair = db.prepare('DELETE FROM pairs WHERE code=? AND expires>? RETURNING owner').get(hash(data.code),time);
          if (!pair) return [401,{}];
          const token = secret();
          db.prepare('INSERT OR REPLACE INTO devices(owner,token) VALUES (?,?)').run(pair.owner,hash(token));
          return [200,{token,owner:pair.owner}];
        });
        return send(...result);
      }
      const auth = /^Bearer ([\w-]{43})$/.exec(req.headers.authorization ?? '');
      if (!auth) return send(401,{});
      return send(...atomic(() => devicePoll(auth[1],data,time)));
    } catch {
      if (!res.headersSent) send(500,{});
    }
  });
  server.on('close',() => db.close());
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = createRelay({origin:process.env.PUBLIC_ORIGIN,applicationId:process.env.DISCORD_APPLICATION_ID,
    diagnosticWebhook:process.env.OPERATOR_DIAGNOSTIC_WEBHOOK,
    publicKey:process.env.DISCORD_PUBLIC_KEY,database:process.env.DATABASE_PATH || 'discord-control.sqlite'});
  server.listen(Number(process.env.PORT || 3000),process.env.HOST || '127.0.0.1');
}
