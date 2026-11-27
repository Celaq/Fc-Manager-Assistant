"""Desktop side of personal Discord notifications and the FC Manager relay protocol.

Drive tick() from the UI event loop. Network workers never call Tk or automation.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import threading
import time
import uuid

from . import discord_io as io
from .discord_watch import start_watcher


@dataclass(frozen=True)
class Device:
    origin: str
    token: str = field(repr=False)
    owner: str

    @classmethod
    def parse(cls, data):
        origin, token, owner = (data[k] for k in ("origin", "token", "owner"))
        if not all(isinstance(v, str) for v in (origin, token, owner)):
            raise ValueError("잘못된 연결 응답")
        if not io.SNOWFLAKE.fullmatch(owner) or io.pairing_link(f"{origin}/#{token}") != (origin, token):
            raise ValueError("잘못된 연결 응답")
        return cls(origin, token, owner)


@dataclass
class Delivery:
    payload: dict = field(repr=False)
    tries: int = 0
    due: float = 0
    manual: bool = False
    url: str = field(default="", repr=False)
    title: str = ""


class _Request:
    def __init__(self, exchange, item, now):
        self.exchange = exchange
        self.item = item
        self.deadline = now + exchange.timeout
        self.response = (0, {})
        self.thread = threading.Thread(target=self._run, daemon=True, name="FC Discord HTTPS")
        self.thread.start()

    def _run(self):
        try:
            self.response = self.exchange.run()
        except Exception:
            # Do not log exception objects from HTTP libraries containing URL/credentials.
            self.response = (0, {})


class DiscordService:
    def __init__(self, app_dir, status, command, log, version="3.3.4", *,
                 exchange=io.Exchange, protect=io.protect, unprotect=io.unprotect,
                 clock=time.monotonic, wall=time.time, watcher=start_watcher):
        self.root = Path(app_dir)
        self.folder = self.root / "오류확인용_보내기"
        self.settings_path = self.root / "settings.json"
        self.device_path = self.root / "discord-device.dat"
        self.status, self.command, self.log = status, command, log
        self.version = version
        self.exchange, self.protect, self.unprotect = exchange, protect, unprotect
        self.clock, self.wall, self.watcher = clock, wall, watcher
        self.marker = None
        self.closed = False
        self.enabled = self.operator_enabled = False
        self.webhook = self.cipher = ""
        self._settings = {}
        self.personal_status = "웹훅을 입력하고 저장해 주세요."
        self.remote_status = "Discord에서 /연결 실행 후 본인의 비공개 링크를 붙여 넣으세요."
        self.device = None
        self.session = uuid.uuid4().hex
        self.ack = self.result = ""
        self.manual_status = ""
        self.manual_pending = self.tunnel_error = False
        self.poll_due = 0
        self.jobs = {}
        self.notifications = deque()
        self.reports = deque()
        self._load()

    def _load(self):
        if self.settings_path.exists():
            try:
                settings = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
                if not isinstance(settings, dict):
                    raise ValueError()
                self._settings = settings
                self.operator_enabled = settings.get("OperatorDiagnostics") is True
                cipher = settings.get("EncryptedWebhook") or ""
                if cipher:
                    webhook = self.unprotect(cipher)
                    if not io.WEBHOOK.fullmatch(webhook):
                        raise ValueError()
                    self.webhook, self.cipher = webhook, cipher
                    self.enabled = settings.get("DiscordEnabled") is True
                    self.personal_status = "암호화하여 저장된 웹훅을 불러왔습니다."
            except (ValueError, OSError, TypeError):
                self.personal_status = "설정을 읽지 못했습니다. 웹훅을 다시 저장하세요."
        if self.device_path.exists():
            try:
                raw = self.device_path.read_text(encoding="utf-8-sig")
                self.device = Device.parse(json.loads(self.unprotect(raw.strip())))
                self.remote_status = "저장한 계정으로 연결 중…"
            except (KeyError, ValueError, TypeError, OSError):
                self.remote_status = "연결 정보를 읽지 못했습니다. /연결 링크를 다시 입력하세요."
        self._watch()

    def _watch(self):
        if self.enabled and self.marker is None:
            try:
                self.marker = self.watcher(self.root)
            except (OSError, ValueError):
                self.log("도우미 종료 감시 프로세스를 시작하지 못했습니다.")

    def _settings_write(self, enabled, operator, cipher):
        settings = {**self._settings, "DiscordEnabled": enabled,
                    "OperatorDiagnostics": operator, "EncryptedWebhook": cipher}
        io.atomic_text(self.settings_path, json.dumps(settings, ensure_ascii=False, indent=2))
        self._settings = settings

    def save_webhook(self, value, enabled):
        value = value.strip()
        if not io.WEBHOOK.fullmatch(value):
            raise ValueError("Discord 웹훅 주소 형식이 올바르지 않습니다.")
        cipher = self.protect(value)
        self._settings_write(bool(enabled), self.operator_enabled, cipher)
        if self.webhook != value or not enabled:
            self.notifications.clear()
            self._cancel("personal")
        self.webhook, self.cipher, self.enabled = value, cipher, bool(enabled)
        self.personal_status = "알림 사용 중" if enabled else "저장됨 · 알림 꺼짐"
        self._watch()

    def set_operator(self, value):
        self._settings_write(self.enabled, bool(value), self.cipher)
        self.operator_enabled = bool(value)
        if not value:
            self.reports = deque(item for item in self.reports if item.manual)
            job = self.jobs.get("report")
            if job and not job.item.manual:
                self._cancel("report")

    def notify(self, title, message, force=False):
        if not self.closed and (self.enabled or force) and self.webhook:
            self.notifications.append(Delivery(io.webhook_body(title, message),
                                               url=self.webhook, title=title))

    def _start(self, lane, url, body, timeout, item=None, token=None):
        exchange = self.exchange(url, body, token=token, timeout=timeout)
        self.jobs[lane] = _Request(exchange, item, self.clock())

    def _cancel(self, lane):
        job = self.jobs.pop(lane, None)
        if job:
            job.exchange.cancel()

    def pair(self, link):
        origin, code = io.pairing_link(link)
        if self.closed:
            return
        if self.device and not self.tunnel_error:
            raise ValueError("계정을 변경하려면 이 PC 연결을 먼저 지우세요.")
        if "pair" in self.jobs:
            raise ValueError("계정 연결 중입니다.")
        self._cancel("poll")
        self._start("pair", origin + "/pair", {"code": code}, 6, item=origin)
        self.remote_status = "계정 연결 중…"

    def _reset_remote(self):
        for lane in ("pair", "poll", "report"):
            self._cancel(lane)
        self.device = None
        self.ack = self.result = self.manual_status = ""
        self.tunnel_error = self.manual_pending = False
        self.reports.clear()
        self.poll_due = 0
        self.session = uuid.uuid4().hex

    def forget(self):
        self.device_path.unlink(missing_ok=True)
        self._reset_remote()
        self.remote_status = "이 PC 연결을 지웠습니다. 서버 연결은 /연결해제로 삭제할 수 있습니다."

    def report(self, reason, capture=None):
        if self.closed:
            return
        try:
            item = io.make_report(self.folder, reason, capture, self.version)
            io.atomic_text(self.folder / "last-error.json", json.dumps(item, ensure_ascii=False))
        except (OSError, ValueError):
            self.log("최근 오류 자료를 저장하지 못했습니다.")
            return
        if self.operator_enabled:
            if not self.device:
                self.log("운영자 오류 전송 대기 불가 · Discord 계정을 먼저 연결하세요.")
            elif len(self.reports) >= 4:
                self.log("운영자 오류 전송 대기열이 가득 찼습니다.")
            else:
                self.reports.append(Delivery(item))

    def request_upload(self):
        if not self.device:
            return "먼저 Discord 계정을 연결하세요."
        if self.manual_pending:
            return "이전 오류업로드 요청을 처리 중입니다."
        if len(self.reports) >= 4:
            return "오류 전송 대기열이 가득 찼습니다. 잠시 후 다시 요청하세요."
        payload = io.manual_report(self.folder, self.version)
        self.reports.append(Delivery(payload, manual=True))
        self.manual_status, self.manual_pending = "전송 대기", True
        return "오류 자료 요청을 수신했습니다."

    def _paired(self, origin, code, obj):
        if code == 530:
            self.remote_status = "연결 실패 · HTTP 530 · Mac 터널 복구 후 /연결을 다시 실행하세요."
            return
        try:
            if not 200 <= code < 300:
                raise ValueError()
            device = Device.parse({**obj, "origin": origin})
            if self.device and self.device.owner != device.owner:
                self.remote_status = "연결을 유지했습니다. 기존 Discord 계정에서 /연결을 실행하세요."
                return
            encrypted = self.protect(json.dumps(vars(device), separators=(",", ":")))
            io.atomic_text(self.device_path, encrypted)
        except (OSError, ValueError, KeyError, TypeError):
            self.remote_status = "연결 실패 · /연결에서 새 링크를 받아 다시 입력하세요."
            return
        self._reset_remote()
        self.device = device
        self.remote_status = f"연결됨 · Discord ID {device.owner}"

    def _polled(self, code, obj):
        self.poll_due = self.clock() + 5
        if code == 401:
            self._reset_remote()
            self.remote_status = "연결 해제됨 · /연결 링크로 다시 연결하세요."
            try:
                self.device_path.unlink(missing_ok=True)
            except OSError:
                self.log("저장된 계정 연결 정보를 삭제하지 못했습니다.")
            return
        if code == 530:
            if not self.tunnel_error:
                self.log("Discord 원격 연결 · HTTP 530 · Mac 서버와 터널을 확인하세요.")
            self.tunnel_error = True
            self.remote_status = "중계 서버 HTTP 530 · 5초 후 재시도 · 주소 변경 시 새 /연결 링크를 입력하세요."
            return
        if not 200 <= code < 300:
            self.remote_status = "원격 연결 대기 · 5초 후 재시도합니다."
            return
        if self.tunnel_error:
            self.log("Discord 원격 연결 복구 · Mac 중계 서버 응답을 확인했습니다.")
        self.tunnel_error = False
        self.remote_status = f"연결됨 · Discord ID {self.device.owner}"
        self.poll_due = self.clock() + 2
        cmd = obj.get("command")
        if not isinstance(cmd, dict):
            return
        ident = cmd.get("id")
        if not isinstance(ident, str) or not io.SNOWFLAKE.fullmatch(ident) or ident == self.ack:
            return
        self.ack = ident
        try:
            expiry = cmd.get("expires")
            if type(expiry) is not int or expiry <= self.wall() * 1000:
                self.result = "명령 유효 시간이 지났습니다."
            elif cmd.get("name") == "upload-error":
                self.result = self.request_upload()
            elif cmd.get("name") in ("start", "stop", "finish"):
                self.result = io.limited(str(self.command(cmd["name"])), 250)
            else:
                self.result = "지원하지 않는 명령입니다."
        except ValueError as exc:
            self.result = io.limited(str(exc), 250)
        except Exception:
            self.result = "실행 실패 · PC에서 실행 기록을 확인하세요."
        self.log(f"Discord 원격 조작 · {self.result}")
        self.poll_due = 0

    def _personal_done(self, item, code):
        if 200 <= code < 300:
            self.log(f"Discord 알림을 보냈습니다 · {item.title}")
            self.personal_status = f"최근 알림 전송 성공 · {datetime.now():%H:%M:%S}"
        elif item.tries < 3:
            item.due = self.clock() + 2**item.tries
            self.notifications.append(item)
            self.log(f"Discord 알림 재시도 {item.tries}/2 · HTTP {code}")
        else:
            self.personal_status = "최근 알림 전송 실패 · 웹훅/인터넷 확인"
            self.log(f"Discord 알림 전송 실패 · HTTP {code}")

    def _report_done(self, item, code, obj):
        if code == 200 and obj.get("delivered") is True:
            outcome = "운영자 Discord에 오류 자료를 전송했습니다."
        elif code in (0, 429, 500, 502, 504, 530) and item.tries < 3:
            item.due = self.clock() + 16 * item.tries
            self.reports.append(item)
            if item.manual:
                self.manual_status = f"전송 재시도 대기 · HTTP {code}"
            self.log(f"운영자 오류 업로드 재시도 {item.tries}/2 · HTTP {code}")
            return
        else:
            outcome = f"오류 자료 전송 실패 · HTTP {code}"
            if code == 530:
                outcome += " · Mac 터널과 /연결 주소를 확인하세요."
        self.log(outcome)
        if item.manual:
            self.manual_status = f"{datetime.now():%H:%M:%S} · {outcome}"
            self.manual_pending = False
            self.poll_due = 0

    def tick(self):
        if self.closed:
            return
        for lane, job in list(self.jobs.items()):
            if self.jobs.get(lane) is not job:
                continue
            if job.thread.is_alive():
                if self.clock() < job.deadline:
                    continue
                job.exchange.cancel()
                response = (0, {})
            else:
                response = job.response
            del self.jobs[lane]
            code, obj = response
            if lane == "pair":
                self._paired(job.item, code, obj)
            elif lane == "poll":
                self._polled(code, obj)
            elif lane == "personal":
                self._personal_done(job.item, code)
            elif lane == "report":
                self._report_done(job.item, code, obj)

        now = self.clock()
        if self.device and "pair" not in self.jobs and "poll" not in self.jobs and now >= self.poll_due:
            status = str(self.status())
            if self.manual_status:
                status += f"\n오류업로드 · {self.manual_status}"
            body = {"session": self.session, "status": io.limited(status, 500),
                    "ack": self.ack, "result": self.result}
            self._start("poll", self.device.origin + "/poll", body, 6, token=self.device.token)
        if self.notifications and "personal" not in self.jobs and self.notifications[0].due <= now:
            item = self.notifications.popleft()
            item.tries += 1
            self._start("personal", item.url, item.payload, 10, item)
        if self.device and self.reports and "report" not in self.jobs and self.reports[0].due <= now:
            item = self.reports.popleft()
            if item.manual:
                self.manual_status = "전송 중"
            item.tries += 1
            self._start("report", self.device.origin + "/diagnostics", item.payload, 14,
                        item, self.device.token)

    def close(self):
        self.closed = True
        if self.marker:
            try:
                self.marker.touch()
            except OSError:
                self.log("정상 종료 표시를 저장하지 못했습니다.")
        for lane in list(self.jobs):
            self._cancel(lane)
        self.notifications.clear()
        self.reports.clear()
