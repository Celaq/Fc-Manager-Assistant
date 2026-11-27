// Register only FC Manager's commands, preserving any other application commands.
import {commands} from './server.mjs';

const {DISCORD_APPLICATION_ID:id, DISCORD_BOT_TOKEN:token, DISCORD_GUILD_ID:guild} = process.env;
if (!/^\d{17,20}$/.test(id ?? '') || !token || (guild && !/^\d{17,20}$/.test(guild)))
  throw Error('Check DISCORD_APPLICATION_ID, DISCORD_BOT_TOKEN and optional DISCORD_GUILD_ID');
if (process.argv.includes('--verify-endpoint')) {
  const origin=new URL(process.env.PUBLIC_ORIGIN);
  if (origin.protocol!=='https:' || origin.origin!==process.env.PUBLIC_ORIGIN) throw Error('PUBLIC_ORIGIN을 확인하세요.');
  const endpoint=origin.origin+'/discord';
  const api='https://discord.com/api/v10/applications/@me';
  const headers={Authorization:'Bot '+token,'Content-Type':'application/json'};
  const response=await fetch(api,{headers,signal:AbortSignal.timeout(15000)});
  if (!response.ok) throw Error('Discord 앱 확인 실패 HTTP '+response.status);
  const app=await response.json();
  if (app.id!==id || (app.interactions_endpoint_url && app.interactions_endpoint_url!==endpoint))
    throw Error('Discord 앱 또는 기존 엔드포인트가 server.env와 다릅니다. Developer Portal에서 주소를 확인하세요.');
  const verified=await fetch(api,{method:'PATCH',headers,body:JSON.stringify({interactions_endpoint_url:endpoint}),signal:AbortSignal.timeout(30000)});
  if (!verified.ok || (await verified.json()).interactions_endpoint_url!==endpoint)
    throw Error('Discord가 Mac 서버 주소를 확인할 수 없습니다. 서버와 HTTPS 터널을 실행하세요. HTTP '+verified.status);
  console.log('Discord 엔드포인트 확인 완료.');
}
const path = 'https://discord.com/api/v10/applications/'+id+(guild ? '/guilds/'+guild : '')+'/commands';
for (const [name,description] of Object.entries(commands)) {
  for (;;) {
    const response = await fetch(path,{method:'POST',headers:{Authorization:'Bot '+token,'Content-Type':'application/json'},
      body:JSON.stringify({type:1,name,description})});
    if (response.ok) break;
    if (response.status !== 429) throw Error('Command registration failed: HTTP '+response.status);
    const delay = (await response.json()).retry_after;
    if (!Number.isFinite(delay) || delay < 0) throw Error('Invalid Discord retry delay');
    await new Promise(resolve => setTimeout(resolve,Math.ceil(delay*1000)));
  }
  console.log('/'+name);
}
