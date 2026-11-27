import {existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, renameSync, linkSync, unlinkSync, lstatSync} from 'node:fs';
import {join, dirname, resolve} from 'node:path';
import {homedir} from 'node:os';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {parseEnv} from 'node:util';
import {openSync} from 'node:fs';
import {ReadStream, WriteStream} from 'node:tty';
import {createInterface} from 'node:readline/promises';

export const dataDir=join(homedir(),'Library/Application Support/FC Manager Discord');
export const configFile=join(dataDir,'server.env');
const template=readFileSync(new URL('./server.env.example',import.meta.url),'utf8');
const keys=['DISCORD_APPLICATION_ID','DISCORD_PUBLIC_KEY','PUBLIC_ORIGIN','DISCORD_GUILD_ID','OPERATOR_DIAGNOSTIC_WEBHOOK','DATABASE_PATH','HOST','PORT'];
const placeholder='https://fc.example.com';
const complete=c=>/^\d{17,20}$/.test(c.DISCORD_APPLICATION_ID??'') && /^[\da-f]{64}$/i.test(c.DISCORD_PUBLIC_KEY??'') && c.PUBLIC_ORIGIN?.startsWith('https://') && c.PUBLIC_ORIGIN!==placeholder;
const useful=c=>keys.some(k=>c[k] && c[k]!==placeholder);

export async function askLocal(question) {
    const input=new ReadStream(openSync('/dev/tty','r')), output=new WriteStream(openSync('/dev/tty','w'));
    const rl=createInterface({input,output,terminal:true});
    try { return (await rl.question(question)).trim(); }
    finally {rl.close();input.destroy();output.destroy();}
}

function candidates() {
    const paths=new Set([configFile+'.bak', join(dirname(fileURLToPath(import.meta.url)),'server.env')]);
    const family=/^(?:discord-control|FC[-_]Discord|FC_Manager_Discord_Server)(?:$|[\s(_-])/i;
    for(const base of ['Desktop','Downloads'].map(d=>join(homedir(),d))) {
        let entries;
        try {entries=readdirSync(base,{withFileTypes:true});}
        catch(error){if(error.code!=='ENOENT')console.error('설정 검색 불가 · '+base);continue;}
        for(const entry of entries.filter(e=>e.isDirectory() && family.test(e.name))) {
            const dir=join(base,entry.name);
            paths.add(join(dir,'server.env'));paths.add(join(dir,'discord-control/server.env'));
        }
    }
    return paths;
}

export function saveConfig(text,previous) {
    mkdirSync(dataDir,{recursive:true,mode:0o700});
    const temp=configFile+'.'+randomUUID();
    writeFileSync(temp,text,{mode:0o600,flag:'wx'});
    try {
        if(previous===null){linkSync(temp,configFile);return;}
        if(readFileSync(configFile,'utf8')!==previous) throw Error('설정이 다른 창에서 변경되었습니다. 다시 실행하세요.');
        if(useful(parseEnv(previous))){
            const backup=temp+'.bak';
            try{writeFileSync(backup,previous,{mode:0o600,flag:'wx'});renameSync(backup,configFile+'.bak');}
            finally{if(existsSync(backup))unlinkSync(backup);}
        }
        renameSync(temp,configFile);
    } finally {if(existsSync(temp))unlinkSync(temp);}
}

function importText(path,values) {
    const rows=[];
    for(const k of keys){
        if(!values[k])continue;
        const value=k==='DATABASE_PATH' ? resolve(dirname(path),values[k]) : values[k];
        if(/[\r\n\0]/.test(value))throw Error('기존 설정의 여러 줄 값을 확인하세요 · '+k);
        const q=['"',"'","`"].find(c=>!value.includes(c));
        if(!q)throw Error('기존 설정의 따옴표를 확인하세요 · '+k);
        rows.push(k+'='+q+value+q);
    }
    return rows.join('\n')+'\n';
}

export async function loadConfig(choose=askLocal) {
    const current=existsSync(configFile)?readFileSync(configFile,'utf8'):null;
    const saved=parseEnv(current??'');
    if(complete(saved))return current;
    const options=new Map();
    for(const path of [configFile,...candidates()]){
        if(!existsSync(path)||!lstatSync(path).isFile())continue;
        const values=parseEnv(readFileSync(path,'utf8'));
        if(!useful(values))continue;
        const text=importText(path,values);
        if(!options.has(text)) options.set(text,path);
    }
    const found=[...options];
    let selected=0;
    if(found.length>1){
        found.forEach(([,path],i)=>console.error((i+1)+'. '+path));
        selected=Number(await choose('사용할 기존 설정 번호 (파일 경로만 표시) '))-1;
        if(!Number.isInteger(selected)||!found[selected])throw Error('설정을 변경하지 않았습니다.');
    }
    if(found.length && found[selected][1]!==configFile){
        const [text,path]=found[selected];
        saveConfig(text,current);console.error('기존 설정 복구 · '+path);return text;
    }
    if(current!==null)return current;
    saveConfig(template,null);return template;
}

if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
    const config=parseEnv(await loadConfig());
    if(!complete(config)){console.error('서버 설정을 입력한 뒤 다시 실행하세요 · '+configFile);process.exitCode=1;}
    else console.log(configFile);
}
