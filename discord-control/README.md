# Discord 원격 조작 서버

서버 운영자가 Discord 앱 하나와 HTTPS 주소 하나를 설정하면 여러 사용자가 같은 봇을 쓸 수 있습니다. 각 PC는 바깥으로 HTTPS 요청을 보내므로 사용자 PC에 포트를 열 필요가 없습니다.

## 임시 HTTPS 주소를 쓰는 Mac

기존 서버를 종료하고 새 ZIP의 **Setup_Discord.command**를 실행하세요. Mac에서 봇 토큰을 입력하고, 처음 오류 채널을 설정할 때 운영자 전용 채널의 웹훅 URL을 입력합니다. 웹훅 입력에서 Enter를 누르면 기존 값을 유지합니다. 설치 파일이 터널을 연결하고 Discord 엔드포인트를 확인한 뒤 명령을 등록합니다. 터미널은 열어 두세요. 터널이 재시작돼 임시 주소가 변경되면 Discord에서 새 `/연결` 링크를 받아 Windows 도우미에 입력하세요.

## 고정 HTTPS 주소로 맥에서 시작하기

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

## 운영자 오류 보고 채널

맥 서버 v3.2.13 이상에서 기존 `server.env`에 `OPERATOR_DIAGNOSTIC_WEBHOOK`을 추가하고 값으로 운영자가 관리하는 Discord 채널의 웹훅 URL을 입력합니다. 서버를 다시 실행하면 적용됩니다. 기존 SQLite 데이터베이스를 계속 사용하면 PC 연결이 유지됩니다.

Windows 도우미에서 **/연결**로 계정을 연결한 후 **Discord 알림 → 운영자에게 오류 자동 전송**을 켭니다. 코치 판독 실패, 감독모드 멈춤 등 처리된 오류가 발생하면 진단정보와 최근 실행 로그 최대 40줄을 보냅니다. 해당 오류의 캡처가 있고 2 MiB 이하면 이미지 한 장도 첨부합니다. 전송 결과는 도우미의 실행 기록에서 확인할 수 있습니다.

### Discord에서 오류 업로드

`/오류업로드`는 Mac 서버와 Windows 도우미 v3.3.2 이상에서 사용할 수 있습니다. 계정에 연결된 온라인 PC 한 대의 최근 오류 진단정보, 실행 로그 최대 40줄, 오류 당시 저장된 캡처 한 장(최대 2 MiB)을 운영자 웹훅으로 전송합니다. 오류가 발생하면 도우미가 한 건의 자료를 로컬에 보관합니다. 이전 버전에서 업데이트한 직후처럼 저장된 오류가 없으면 기존 진단정보와 로그, 저장된 코치 판독 캡처가 있을 때 그 파일을 사용합니다. 자동 전송을 꺼 두어도 수동 명령은 한 번 실행됩니다. 비공개 응답은 접수 여부를 알려 주고 `/상태`는 실제 전송 결과를 표시합니다.

Windows에서 HTTP 530이 발생하면 업로드를 최대 두 번 더 시도합니다. Mac 서버와 터널이 실행 중인지 확인하세요. Discord가 “응답하지 않았어요”라고 표시하면 Mac 터미널에서 해당 시각의 `Discord /상태 · HTTP 200` 기록을 확인하세요. Mac에 요청이 도착하지 않으면 끝이 `/discord`인 Developer Portal의 Interactions Endpoint URL과 현재 터널 주소를 대조하세요.

## 사용자 연결

1. Windows에서 도우미 v3.2.10 이상을 실행하고 Discord에서 **/연결**을 입력합니다.
2. 본인만 볼 수 있는 응답의 링크 전체를 복사해 도우미 **Discord 알림 → 원격 조작**에 붙여 넣고 **계정 연결**을 누릅니다. 5분간 한 번 사용할 수 있습니다.
3. **/상태**로 현재 상태·판수·최근 명령 결과를 봅니다. **/시작**은 현재 도우미 설정으로 감독모드를 시작합니다. 게임이 실행 중이어야 하며 주차 모드의 시작점수와 목표점수 조건도 적용됩니다. **/이번판까지만**은 현재 경기 후 중지를 예약하며 반복해도 예약이 유지됩니다. **/중지**는 도우미의 중지 버튼과 동일하게 동작합니다.
4. 다른 PC에서 새로 연결하면 이전 PC의 권한이 만료됩니다. **/연결해제**는 계정 연결, 사용하지 않은 연결 코드와 대기 명령을 삭제합니다. PC에서 **이 PC 연결 지우기**를 누르면 해당 PC가 명령 수신을 멈춥니다.

원격 명령은 12초 안에 온라인 도우미가 받아야 합니다. 답장에서 접수 여부를 확인하고 **/상태**에서 PC가 보고한 실행 결과를 확인할 수 있습니다. Discord 웹훅 알림 설정은 독립적으로 작동합니다.

## 검사

이 폴더에서 `node --test server.test.mjs`를 실행합니다. 테스트는 임시 서명 키와 메모리 데이터베이스로 같은 서버의 두 계정 분리, 만료와 재전송, 새 PC 연결 및 권한 해제를 확인합니다.
