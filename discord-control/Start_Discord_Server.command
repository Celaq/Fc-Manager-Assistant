#!/bin/bash
set -eu
cd "$(dirname "$0")"
umask 077

# Finder-launched terminals may not have Homebrew's keg-only Node in PATH.
export PATH="/opt/homebrew/opt/node@24/bin:/usr/local/opt/node@24/bin:$PATH"
if ! command -v node >/dev/null || ! node -e "require('node:sqlite'); if (+process.versions.node.split('.')[0] < 24) process.exit(1)" 2>/dev/null; then
    echo 'Node.js 24가 필요합니다. Homebrew 사용 시 brew install node@24 를 실행하세요.' >&2
    exit 1
fi
if [[ ! -f server.env ]]; then
    cp server.env.example server.env
    echo "설정 파일을 만들었습니다. 다음 파일의 값을 입력한 뒤 다시 실행하세요."
    echo "$PWD/server.env"
    exit 1
fi

case "${1:-start}" in
    start)
        if [[ -z "${DATABASE_PATH:-}" ]]; then
            data_dir="$HOME/Library/Application Support/FC Manager Discord"
            mkdir -p "$data_dir"
            export DATABASE_PATH="$data_dir/control.sqlite"
        fi
        echo "서버 시작 · 종료하려면 Control+C를 누르세요."
        exec node --env-file=server.env server.mjs
        ;;
    register)
        if [[ -z "${DISCORD_BOT_TOKEN:-}" ]]; then
            read -r -s -p 'Discord 봇 토큰 (입력 숨김): ' DISCORD_BOT_TOKEN
            echo
            export DISCORD_BOT_TOKEN
        fi
        exec node --env-file=server.env register.mjs
        ;;
    *)
        echo '사용법: ./Start_Discord_Server.command [start|register]' >&2
        exit 2
        ;;
esac
