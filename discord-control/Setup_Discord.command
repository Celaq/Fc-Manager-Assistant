#!/bin/bash
set -eu
umask 077
cd "$(dirname "$0")"
export PATH="/opt/homebrew/opt/node@24/bin:/usr/local/opt/node@24/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! node -e "require('node:sqlite');if(+process.versions.node.split('.')[0]<24)process.exit(1)" 2>/dev/null; then
    if command -v brew >/dev/null; then brew install node@24; else echo 'Node.js 24를 먼저 설치하세요.'; exit 1; fi
fi
if ! command -v cloudflared >/dev/null; then
    if command -v brew >/dev/null; then
        brew install cloudflared
    else
        bin_dir="$HOME/Library/Application Support/FC Manager Discord/bin"
        mkdir -p "$bin_dir"
        export PATH="$bin_dir:$PATH"
        if ! command -v cloudflared >/dev/null; then node install-tunnel.mjs "$bin_dir"; fi
    fi
fi
echo 'Discord Developer Portal → 이 프로젝트의 앱 → Bot 메뉴의 토큰을 입력하세요.'
IFS= read -r -s -p '봇 토큰 (입력 숨김): ' token
echo
webhook=''
IFS= read -r -s -p '운영자 오류 채널 웹훅 (입력 숨김, Enter로 기존 설정 유지): ' webhook || true
echo
if printf '%s\n%s\n' "$token" "$webhook" | node setup.mjs; then
    exit 0
else
    status=$?
    unset token webhook
    if [[ -t 0 ]]; then
        read -r -p '오류 내용이 위에 표시돼 있습니다. 종료하려면 Enter를 누르세요.' _ || true
    fi
    exit "$status"
fi
