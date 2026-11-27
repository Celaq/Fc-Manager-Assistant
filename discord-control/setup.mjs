import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, writeFileSync, renameSync} from 'node:fs';
import {createServer} from 'node:http';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {setTimeout as delay} from 'node:timers/promises';
import {parseEnv} from 'node:util';
import {createRelay} from './server.mjs';

const dataDir=join(homedir(),'Library/Application Support/FC Manager Discord');
const desktopConfig=join(homedir(),'Desktop/discord-control/server.env');
const configFile=existsSync(desktopConfig) ? desktopConfig : join(dataDir,'server.env');
const configText=readFileSync(existsSync(configFile) ? configFile : new URL('./server.env.example',import.meta.url),'utf8');
const config=parseEnv(configText);
const [token, webhook='']=(await Array.fromAsync(process.stdin)).join('').trimEnd().split(/\r?\n/).map(line=>line.trim());
if (webhook && !/^https:\/\/discord\.com\/api\/webhooks\/\d{17,20}\/[\w.-]+$/.test(webhook)) throw Error('운영자 채널의 Discord 웹훅 주소를 확인하세요.');
if (!token || /\s/.test(token)) throw Error('봇 토큰을 다시 확인하세요.');
const auth={Authorization:'Bot '+token};
const api='https://discord.com/api/v10/applications/@me';
let tunnel, relay, front;
let edgeStatus='터널 연결 확인 전';
function stop(code) {
    tunnel?.kill();
    front?.close();
    process.exit(code);
}
process.on('SIGINT',()=>stop(0));
process.on('SIGTERM',()=>stop(0));
process.on('SIGHUP',()=>stop(0));

// cloudflared prints the new hostname before connecting to the Cloudflare edge.
async function startTunnel(localOrigin) {
    tunnel=spawn('cloudflared',['tunnel','--no-autoupdate','--protocol','auto','--url',localOrigin],{stdio:['ignore','pipe','pipe']});
    return new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>reject(Error('Cloudflare 주소 발급 시간 초과 · '+edgeStatus)),90000);
        tunnel.once('error',err=>{clearTimeout(timer);reject(err);});
        tunnel.once('close',(code,signal)=>{
            clearTimeout(timer);
            console.error('Cloudflare 터널 종료 · '+(signal ?? code)+' · '+edgeStatus);
            stop(1);
        });
        for (const stream of [tunnel.stderr,tunnel.stdout]) {
            const lines=createInterface({input:stream,crlfDelay:Infinity});
            lines.on('line',line=>{
                const host=line.match(/(?:^|[\s|])(https:\/\/[a-z0-9-]+\.trycloudflare\.com)(?=[\s|]|$)/i);
                if (host && host[1]!=='https://api.trycloudflare.com') {
                    clearTimeout(timer);
                    resolve(host[1]);
                }
                if (/Registered tunnel connection/i.test(line)) {
                    const protocol=line.match(/protocol=(quic|http2)/i);
                    edgeStatus='Cloudflare 연결됨'+(protocol ? ' ('+protocol[1]+')' : '');
                } else if (/failed to resolve|lookup .*(:53|no such host|timed? ?out|server misbehaving)/i.test(line)) {
                    edgeStatus='Cloudflare DNS 조회 실패';
                } else if (/failed to dial.*quic|timeout.*no recent network activity/i.test(line)) {
                    edgeStatus='Cloudflare QUIC 연결 실패';
                } else if (/dial tcp|failed to (dial|serve).*http2/i.test(line)) {
                    edgeStatus='Cloudflare TCP 연결 실패';
                } else if (/unable to reach the origin service/i.test(line)) {
                    edgeStatus='터널에서 Mac 서버 연결 실패';
                }
            });
        }
    });
}

function networkReason(error) {
    const code=error.cause?.code ?? error.name;
    return /^[a-z0-9_]+$/i.test(String(code)) ? code : '연결 오류';
}

async function checkRelay(origin,probe) {
    try {
        const response=await fetch(origin+probe,{redirect:'manual',signal:AbortSignal.timeout(4000)});
        if (response.ok && await response.text()===probe) return {ok:true,detail:'응답 확인'};
        return {ok:false,detail:'예상과 다른 응답 HTTP '+response.status};
    } catch (error) {
        return {ok:false,detail:networkReason(error)};
    }
}

async function verifyEndpoint(origin,probe) {
    const endpoint=origin+'/discord';
    const backoffs=[5000,15000,35000];
    let reason='';
    for (let attempt=0;attempt<=backoffs.length;attempt++) {
        let pause=backoffs[attempt];
        try {
            const response=await fetch(api,{method:'PATCH',headers:{...auth,'Content-Type':'application/json'},
                body:JSON.stringify({interactions_endpoint_url:endpoint}),signal:AbortSignal.timeout(20000)});
            const result=await response.json().catch(()=>({}));
            if (response.ok && result.interactions_endpoint_url===endpoint) return;
            const field=result.errors?.interactions_endpoint_url;
            const code=field?._errors?.[0]?.code ?? result.code;
            reason='HTTP '+response.status+(/^[a-z0-9_]{1,90}$/i.test(String(code ?? '')) ? ' ('+code+')' : '');
            if (response.ok) reason+=' · 응답의 엔드포인트 불일치';
            if (response.status===429) {
                const after=Number(result.retry_after ?? response.headers.get('retry-after'));
                if (!Number.isFinite(after) || after>120) break;
                pause=Math.max(pause ?? 0,Math.ceil(after*1000));
            } else if (!(response.status>=500 || (response.status===400 && field))) {
                break;
            }
        } catch (error) {
            reason='Discord API 연결 오류 '+networkReason(error);
            // A lost PATCH response can still mean Discord accepted and verified the URL.
            try {
                const current=await fetch(api,{headers:auth,signal:AbortSignal.timeout(7000)});
                if (current.ok && (await current.json()).interactions_endpoint_url===endpoint) return;
            } catch {}
        }
        if (attempt<backoffs.length) {
            console.log('Discord 주소 검증 대기 · '+reason+' · '+edgeStatus+' · '+Math.ceil(pause/1000)+'초 뒤 재시도');
            await delay(pause);
        }
    }
    // A check from the Mac is diagnostic only. Its DNS and network path can differ from Discord's.
    const external=await checkRelay(origin,probe);
    throw Error('연결 진단 · Mac 로컬 응답 확인 / '+edgeStatus+' / Mac의 공개 주소 조회 '+external.detail+' / Discord '+reason);
}

async function run() {
    const identity=await fetch(api,{headers:auth,signal:AbortSignal.timeout(15000)});
    if (!identity.ok) throw Error('Discord 앱 확인 실패 HTTP '+identity.status);
    const app=await identity.json();
    if (!/^\d{17,20}$/.test(app.id) || !/^[a-f0-9]{64}$/i.test(app.verify_key)) throw Error('앱 ID 또는 공개 키 확인 실패');
    if (config.DISCORD_APPLICATION_ID && config.DISCORD_APPLICATION_ID!==app.id) throw Error('기존 설정의 앱과 입력한 토큰의 앱이 다릅니다.');
    const previous=app.interactions_endpoint_url;
    if (previous && (!/^https:\/\/[\w-]+\.trycloudflare\.com$/.test(config.PUBLIC_ORIGIN) || previous!==config.PUBLIC_ORIGIN+'/discord')) {
        throw Error('이 앱에는 이미 다른 Interactions Endpoint가 설정돼 있습니다. 전용 Discord 앱의 토큰을 사용하세요.');
    }
    mkdirSync(dataDir,{recursive:true,mode:0o700});
    // Reserve the origin port before exposing it. Only this relay can receive requests.
    const probe='/setup-'+randomUUID();
    front=createServer((req,res)=>{
        if (req.url===probe && req.method==='GET') { res.end(probe); return; }
        if (relay) relay.emit('request',req,res);
        else {res.writeHead(503);res.end();}
    });
    await new Promise((resolve,reject)=>{front.once('error',reject);front.listen(0,'127.0.0.1',resolve);});
    console.log('Discord 앱 확인 완료. 임시 HTTPS 주소를 연결합니다.');
    const local='http://127.0.0.1:'+front.address().port;
    const origin=await startTunnel(local);
    relay=createRelay({database:config.DATABASE_PATH || join(dataDir,'control.sqlite'),origin,applicationId:app.id,
        publicKey:app.verify_key, diagnosticWebhook:webhook || config.OPERATOR_DIAGNOSTIC_WEBHOOK});
    const localCheck=await checkRelay(local,probe);
    if (!localCheck.ok) throw Error('연결 진단 · Mac 로컬 서버 응답 실패 · '+localCheck.detail);
    console.log('Mac 로컬 서버 응답 확인. Discord에서 공개 주소를 검증합니다.');
    await verifyEndpoint(origin,probe);
    const endpoint=origin+'/discord';
    const updates={DISCORD_APPLICATION_ID:app.id, DISCORD_PUBLIC_KEY:app.verify_key, PUBLIC_ORIGIN:origin};
    if (webhook) updates.OPERATOR_DIAGNOSTIC_WEBHOOK=webhook;
    let text=configText;
    for(const [key,value] of Object.entries(updates)){
        const line=new RegExp('^'+key+'=.*$','gm');
        text=line.test(text) ? text.replace(line,key+'='+value) : text+'\n'+key+'='+value+'\n';
    }
    const tmp=configFile+'.'+randomUUID();
    writeFileSync(tmp,text,{mode:0o600,flag:'wx'});renameSync(tmp,configFile);
    await new Promise((resolve,reject)=>{
        const child=spawn(process.execPath,['register.mjs'],{stdio:'inherit',env:{...process.env,
            DISCORD_APPLICATION_ID:app.id, DISCORD_GUILD_ID:config.DISCORD_GUILD_ID || '', DISCORD_BOT_TOKEN:token}});
        child.on('error',reject);
        child.on('exit',code=>code===0 ? resolve() : reject(Error('Discord 명령 등록 실패')));
    });
    const install='https://discord.com/oauth2/authorize?client_id='+app.id+'&scope=applications.commands&integration_type=0';
    console.log('설정 완료 · '+endpoint+'\n봇을 처음 추가할 때 열 주소: '+install+'\nDiscord에서 /연결을 실행하세요. 이 창을 닫으면 서버가 종료됩니다.');
    if (!previous && process.platform==='darwin') spawn('/usr/bin/open',[install],{stdio:'ignore'});
}
run().catch(err=>{console.error(err.message);stop(1);});
