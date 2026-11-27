import test from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, sign } from 'node:crypto';
import { once } from 'node:events';
import { commands, createRelay } from './server.mjs';

test('two Discord accounts in one guild only control their own active PC', async t => {
  const {privateKey,publicKey} = generateKeyPairSync('ed25519');
  const app = '100000000000000000';
  const alice = '100000000000000001';
  const bob = '100000000000000002';
  const s1 = '1'.repeat(32);
  const s2 = '2'.repeat(32);
  let time = Date.now();
  let serial = 200000000000000000n;
  const reports=[];
  const webhook="https://discord.com/api/webhooks/100000000000000099/test-only";
  const relay = createRelay({origin:'https://relay.example',database:':memory:',applicationId:app,
    diagnosticWebhook:webhook,sendWebhook:async(url,options)=>{reports.push({url,options});return {ok:true}},
    publicKey:publicKey.export({format:'der',type:'spki'}).subarray(-32).toString('hex'),now:() => time});
  relay.listen(0,'127.0.0.1');
  await once(relay,'listening');
  t.after(() => new Promise(resolve => relay.close(resolve)));
  const base = 'http://127.0.0.1:'+relay.address().port;
  const post = (p,data,headers={}) => fetch(base+p,{method:'POST',headers,body:JSON.stringify(data)});
  function discord(event,stamp=time) {
    const ts = String(Math.floor(stamp/1000));
    const sig = sign(null,Buffer.from(ts+JSON.stringify(event)),privateKey).toString('hex');
    return post('/discord',event,{'x-signature-timestamp':ts,'x-signature-ed25519':sig});
  }
  function evt(user,name) {
    return {type:2,application_id:app,id:String(++serial),guild_id:'900000000000000000',member:{user:{id:user}},
      data:{type:1,name}};
  }
  const slash = async (user,name) => (await (await discord(evt(user,name))).json()).data.content;
  async function pair(user) {
    const code = /#([\w-]{43})/.exec(await slash(user,'연결'))[1];
    const res = await post('/pair',{code});
    assert.equal(res.status,200);
    assert.equal((await post('/pair',{code})).status,401);
    return (await res.json()).token;
  }
  const poll = (token,session,extra={}) => post('/poll',{session,status:'PC '+session[0],...extra},{authorization:'Bearer '+token});
  assert.equal((await post('/discord',evt(alice,'중지'))).status,401);
  assert.equal((await discord(evt(alice,'중지'),time-400_000)).status,401);
  assert.equal((await discord({...evt(alice,'중지'),application_id:bob})).status,403);
  assert.equal((await (await discord({type:1,application_id:app})).json()).type,1);
  assert.equal((await (await discord({type:1})).json()).type,1);
  const a = await pair(alice);
  const b = await pair(bob);
  const incident={id:'a'.repeat(32),text:'coach test report',png:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXxkAAAAASUVORK5CYII='};
  const deliver=(token,payload)=>post('/diagnostics',payload,{authorization:'Bearer '+token});
  assert.equal((await deliver('x'.repeat(43),incident)).status,401);
  assert.equal((await deliver(a,{...incident,png:'no image'})).status,400);
  assert.equal(reports.length,0);
  assert.equal((await deliver(a,incident)).status,200);
  assert.equal((await deliver(a,incident)).status,200);
  assert.equal(reports.length,1);
  assert.equal(reports[0].url,webhook);
  assert.equal(await reports[0].options.body.get('files[0]').text(),incident.text);
  assert.equal(reports[0].options.body.get('files[1]').name,'capture.png');
  assert.equal((await deliver(a,{...incident,id:'b'.repeat(32)})).status,429);
  assert.match(await slash(alice,'시작'),/오프라인/);
  await poll(a,s1);
  await poll(b,s2);
  assert.ok(commands['오류업로드']);
  const uploadEvent = evt(alice,'오류업로드');
  const receipt = (await (await discord(uploadEvent)).json()).data;
  assert.equal(receipt.flags,64);
  assert.match(receipt.content,/접수/);
  assert.match((await (await discord(uploadEvent)).json()).data.content,/이미/);
  assert.equal((await (await poll(b,s2)).json()).command,null);
  const upload = (await (await poll(a,s1)).json()).command;
  assert.equal(upload.name,'upload-error');
  assert.equal(upload.id,uploadEvent.id);
  await poll(a,s1,{ack:upload.id,result:'오류 자료 요청 수신'});
  time += 16000;
  const manualReport = {...incident,id:'c'.repeat(32)};
  assert.equal((await deliver(a,manualReport)).status,200);
  assert.equal((await deliver(a,manualReport)).status,200);
  assert.equal(reports.length,2);
  await poll(a,s1,{status:'오류업로드 · 운영자 Discord에 전송 완료'});
  await poll(b,s2);
  assert.match(await slash(alice,'상태'),/전송 완료/);
  assert.doesNotMatch(await slash(bob,'상태'),/전송 완료/);
  const start = evt(alice,'시작');
  const accepted = await (await discord(start)).json();
  assert.equal(accepted.data.flags,64);
  assert.match(accepted.data.content,/접수/);
  assert.match((await (await discord(start)).json()).data.content,/이미/);
  assert.equal((await (await poll(b,s2)).json()).command,null);
  assert.equal((await (await poll(a,s1)).json()).command.name,'start');
  assert.match(await slash(alice,'이번판까지만'),/처리 중/);
  const ack = {ack:start.id,result:'자동 진행 시작됨'};
  assert.equal((await (await poll(a,s1,ack)).json()).command,null);
  assert.match(await slash(alice,'상태'),/PC 1.*\n.*자동 진행 시작됨/);
  assert.doesNotMatch(await slash(bob,'상태'),/시작됨/);
  await slash(alice,'이번판까지만');
  assert.equal((await (await poll(a,s1)).json()).command.name,'finish');
  await slash(alice,'중지');
  assert.equal((await (await poll(a,s1,ack)).json()).command.name,'stop');
  time += 15_000;
  assert.match(await slash(alice,'상태'),/오프라인/);
  assert.equal((await (await poll(a,s1)).json()).command,null);
  await slash(alice,'시작');
  assert.equal((await (await poll(a,s2)).json()).command,null);
  const a2 = await pair(alice);
  assert.equal((await poll(a,s1)).status,401);
  assert.equal((await poll(a2,s2)).status,200);
  assert.equal((await poll(b,s2)).status,200);
  const code = /#([\w-]{43})/.exec(await slash(alice,'연결'))[1];
  await slash(alice,'연결해제');
  assert.equal((await poll(a2,s2)).status,401);
  assert.equal((await post('/pair',{code})).status,401);
  assert.equal((await poll(b,s2)).status,200);
  const expired = /#([\w-]{43})/.exec(await slash(bob,'연결'))[1];
  time += 300_001;
  assert.equal((await post('/pair',{code:expired})).status,401);
});
