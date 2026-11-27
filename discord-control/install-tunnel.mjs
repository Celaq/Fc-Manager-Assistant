import {createHash} from 'node:crypto';
import {mkdtemp, writeFile, chmod, rename, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';

const arch = {arm64:'arm64', x64:'amd64'}[process.arch];
if (process.platform !== 'darwin' || !arch) throw Error('지원되지 않는 Mac입니다.');
const name = 'cloudflared-darwin-'+arch+'.tgz';
const response = await fetch('https://api.github.com/repos/cloudflare/cloudflared/releases/latest');
if (!response.ok) throw Error('Cloudflare 릴리스 조회 실패 HTTP '+response.status);
const asset = (await response.json()).assets.find(item => item.name === name);
if (!/^sha256:[0-9a-f]{64}$/.test(asset?.digest ?? '') ||
    !asset.browser_download_url.startsWith('https://github.com/cloudflare/cloudflared/releases/download/')) {
    throw Error('Cloudflare 공식 파일 또는 SHA-256을 확인할 수 없습니다.');
}
const download = await fetch(asset.browser_download_url);
if (!download.ok) throw Error('cloudflared 다운로드 실패 HTTP '+download.status);
const bytes = Buffer.from(await download.arrayBuffer());
if ('sha256:'+createHash('sha256').update(bytes).digest('hex') !== asset.digest) throw Error('다운로드 해시 불일치');
const dir=process.argv[2], tmp=await mkdtemp(join(dir,'install-'));
try {
    const archive=join(tmp,'cloudflared.tgz');
    await writeFile(archive,bytes);
    const member=execFileSync('/usr/bin/tar',['-tzf',archive],{encoding:'utf8'}).split('\n').find(s=>/^(\.\/)?cloudflared$/.test(s));
    if (!member) throw Error('cloudflared 실행 파일을 찾지 못했습니다.');
    execFileSync('/usr/bin/tar',['-xzf',archive,'-C',tmp,member]);
    await chmod(join(tmp,'cloudflared'),0o700);
    await rename(join(tmp,'cloudflared'),join(dir,'cloudflared'));
} finally { await rm(tmp,{recursive:true,force:true}); }
