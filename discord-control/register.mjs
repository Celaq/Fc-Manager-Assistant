// Register only FC Manager's commands, preserving any other application commands.
import {commands} from './server.mjs';

const {DISCORD_APPLICATION_ID:id, DISCORD_BOT_TOKEN:token, DISCORD_GUILD_ID:guild} = process.env;
if (!/^\d{17,20}$/.test(id ?? '') || !token || (guild && !/^\d{17,20}$/.test(guild)))
  throw Error('Check DISCORD_APPLICATION_ID, DISCORD_BOT_TOKEN and optional DISCORD_GUILD_ID');
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
