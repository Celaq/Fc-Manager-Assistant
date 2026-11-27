# Discord 원격 조작 서버

서버 운영자가 Discord 앱 하나와 HTTPS 주소 하나를 설정하면 여러 사용자가 같은 봇을 쓸 수 있습니다. 각 PC는 바깥으로 HTTPS 요청을 보내므로 사용자 PC에 포트를 열 필요가 없습니다.

## 맥에서 시작하기

1. 맥에 Node.js 24를 설치합니다. Homebrew를 사용하면 `brew install node@24`로 설치할 수 있습니다. 별도의 npm 패키지는 필요하지 않습니다.
2. ZIP을 풀고 **Start_Discord_Server.command**를 한 번 실행합니다. 같은 폴더에 생성된 **server.env**를 텍스트 편집기로 열어 Discord Application ID, Public Key, 공개 HTTPS 주소를 입력합니다. 테스트할 Discord 서버 ID도 입력하면 해당 서버에 명령을 등록할 수 있습니다.
3. **Start_Discord_Server.command**를 다시 실행하고 터미널 창을 열어 둡니다. 계정 연결 정보는 `~/Library/Application Support/FC Manager Discord/control.sqlite`에 저장됩니다. 맥이 잠자기 상태면 원격 조작을 사용할 수 없습니다.
4. 아래 **운영자 설정**의 1, 3, 4단계를 따라 Discord 앱을 서버에 추가하고 공개 HTTPS 주소를 맥의 `127.0.0.1:3000`에 연결합니다. Discord와 사용자 PC 양쪽에서 같은 주소에 접근할 수 있어야 합니다.
5. 별도 터미널에서 압축을 푼 폴더로 이동한 뒤 `./Start_Discord_Server.command register`를 실행합니다. 프롬프트에 봇 토큰을 입력하면 명령이 등록됩니다. 토큰은 파일에 저장되지 않습니다.
6. Discord에서 **/연결**을 실행한 뒤, 아래 **사용자 연결** 절차로 Windows 게임 PC를 연결합니다.

## 운영자 설정

1. Discord Developer Portal에서 애플리케이션을 만들고 **General Information**의 Application ID와 Public Key를 확인합니다. **Installation**에서 Guild Install을 켜고 `applications.commands` 범위를 포함한 설치 링크로 테스트할 서버에 앱을 추가합니다. [Discord Developer Portal](https://discord.com/developers/applications)
2. Node.js 24와 쓰기 가능한 영구 저장소를 준비합니다. 아래 예시의 도메인과 값을 실제 환경에 맞게 지정하고 이 폴더에서 서버를 실행합니다. SQLite 파일과 같은 디렉터리의 WAL 파일은 재시작 후에도 유지해야 합니다.

   ```sh
   export DISCORD_APPLICATION_ID='애플리케이션 ID'
   export DISCORD_PUBLIC_KEY='공개키 64자리 hex'
   export PUBLIC_ORIGIN='https://fc.example.com'
   export DATABASE_PATH='/var/lib/fc-manager/control.sqlite'
   node server.mjs
   ```

3. HTTPS 리버스 프록시가 해당 도메인의 요청 본문과 Discord 서명 헤더를 그대로 로컬 서버에 전달하도록 설정합니다. 기본 수신 주소는 `127.0.0.1:3000`이며 `HOST`와 `PORT`로 변경할 수 있습니다. 예를 들어 Caddy에서는 다음 설정을 사용합니다.

   ```caddyfile
   fc.example.com {
       reverse_proxy 127.0.0.1:3000
   }
   ```

4. Developer Portal의 **Interactions Endpoint URL**을 `https://fc.example.com/discord`로 저장합니다. Discord가 서명한 PING 요청으로 주소를 검사합니다.
5. 두 번째 터미널에서 같은 `DISCORD_APPLICATION_ID`와 봇의 `DISCORD_BOT_TOKEN`을 설정합니다. 테스트 서버에 즉시 등록하려면 `DISCORD_GUILD_ID`도 설정하고 `node register.mjs`를 실행합니다. 전역 등록 시에는 `DISCORD_GUILD_ID`를 생략합니다. 봇 토큰은 명령 등록 시에만 필요하며 사용자 PC에는 배포하지 않습니다.

## 사용자 연결

1. Windows에서 도우미 v3.2.10 이상을 실행하고 Discord에서 **/연결**을 입력합니다.
2. 본인만 볼 수 있는 응답의 링크 전체를 복사해 도우미 **Discord 알림 → 원격 조작**에 붙여 넣고 **계정 연결**을 누릅니다. 5분간 한 번 사용할 수 있습니다.
3. **/상태**로 현재 상태·판수·최근 명령 결과를 봅니다. **/시작**은 현재 도우미 설정으로 감독모드를 시작합니다. 게임이 실행 중이어야 하며 주차 모드의 시작점수와 목표점수 조건도 적용됩니다. **/이번판까지만**은 현재 경기 후 중지를 예약하며 반복해도 예약이 유지됩니다. **/중지**는 도우미의 중지 버튼과 동일하게 동작합니다.
4. 다른 PC에서 새로 연결하면 이전 PC의 권한이 만료됩니다. **/연결해제**는 계정 연결, 사용하지 않은 연결 코드와 대기 명령을 삭제합니다. PC에서 **이 PC 연결 지우기**를 누르면 해당 PC가 명령 수신을 멈춥니다.

원격 명령은 12초 안에 온라인 도우미가 받아야 합니다. 답장에서 접수 여부를 확인하고 **/상태**에서 PC가 보고한 실행 결과를 확인할 수 있습니다. Discord 웹훅 알림 설정은 독립적으로 작동합니다.

## 검사

이 폴더에서 `node --test server.test.mjs`를 실행합니다. 테스트는 임시 서명 키와 메모리 데이터베이스로 같은 서버의 두 계정 분리, 만료와 재전송, 새 PC 연결 및 권한 해제를 확인합니다.
