import {createHash} from 'node:crypto';

// Only the relay has the operator's Discord credential.
export function operatorDiagnostics(db, webhook, sendWebhook = fetch) {
  if (webhook && !/^https:\/\/discord\.com\/api\/webhooks\/\d{17,20}\/[\w.-]+$/.test(webhook))
    throw Error('Invalid OPERATOR_DIAGNOSTIC_WEBHOOK');
  db.exec('CREATE TABLE IF NOT EXISTS diagnostic_delivery(owner TEXT PRIMARY KEY, report TEXT, sent INTEGER)');
  const busy = new Set();
  return async function handle(req, send, time) {
    const match = /^Bearer ([\w-]{43})$/.exec(req.headers.authorization ?? '');
    const owner = match && authenticate(match[1]);
    if (!owner) return send(401,{});
    if (!webhook) return send(503,{error:'not_configured'});
    if (busy.has(owner)) return send(429,{});
    busy.add(owner);
    try {
      const chunks = [];
      let size = 0;
      for await (const piece of req) {
        size += piece.length;
        if (size > 3*1024*1024) return send(413,{});
        chunks.push(piece);
      }
      let body;
      try { body = JSON.parse(Buffer.concat(chunks)); } catch { return send(400,{}); }
      if (!body || !/^[a-f0-9]{32}$/.test(body.id ?? '') || typeof body.text !== 'string' ||
          !body.text.trim() || body.text.length > 18000) return send(400,{});
      const previous = db.prepare('SELECT * FROM diagnostic_delivery WHERE owner=?').get(owner);
      if (previous?.report === body.id) return send(200,{delivered:true});
      if (previous && time-previous.sent < 15000) return send(429,{});
      const form = new FormData();
      form.set('payload_json',JSON.stringify({content:'FC Manager 오류 진단',allowed_mentions:{parse:[]}}));
      form.set('files[0]',new Blob([body.text],{type:'text/plain;charset=utf-8'}),'diagnostic.txt');
      if (body.png) {
        if (typeof body.png !== 'string') return send(400,{});
        const image = Buffer.from(body.png,'base64');
        if (image.length < 24 || image.length > 2*1024*1024 ||
            image.subarray(0,8).toString('hex') !== '89504e470d0a1a0a') return send(400,{});
        form.set('files[1]',new Blob([image],{type:'image/png'}),'capture.png');
      }
      if (authenticate(match[1]) !== owner) return send(401,{});
      const result = await sendWebhook(webhook,{method:'POST',body:form,redirect:'error',signal:AbortSignal.timeout(7500)});
      if (!result.ok) return send(result.status === 429 ? 429 : 502,{});
      db.prepare('INSERT OR REPLACE INTO diagnostic_delivery VALUES (?,?,?)').run(owner,body.id,time);
      return send(200,{delivered:true});
    } finally { busy.delete(owner); }
  };

  function authenticate(token) {
    return db.prepare('SELECT owner FROM devices WHERE token=?').get(createHash('sha256').update(token).digest('hex'))?.owner;
  }
}
