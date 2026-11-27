"""Korean Windows desktop UI. Automation and downloads run off the Tk event loop."""
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from . import __version__, update
from .coach_ui import build_coach_page
from .controller import Controller, Options
from .discord import DiscordService
from .discord_ui import build_discord_tab
from .runtime import Context
from .migrate import import_existing


KST = timezone(timedelta(hours=9))
BG, SURFACE, FG, SUBTLE, TEAL = "#101822", "#1a2634", "#f0f5f7", "#acc0cc", "#167c70"


def setup_style(root):
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    font.nametofont("TkDefaultFont").configure(family="Malgun Gothic", size=10)
    style.configure(".", background=SURFACE, foreground=FG, fieldbackground="#243548", bordercolor="#344556")
    style.configure("TNotebook", background=BG, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", padding=(12, 8))
    style.map("TNotebook.Tab", background=[("selected", TEAL)])
    style.configure("TButton", padding=(10, 6))
    style.map("TButton", background=[("active", "#345065")], foreground=[("disabled", "#82939f")])
    style.configure("Start.TButton", background=TEAL, foreground="white")
    style.map("Start.TButton", background=[("active", "#238d81")])
    style.configure("Title.TLabel", font=("Malgun Gothic", 19, "bold"), background=BG)
    style.configure("Status.TLabel", font=("Malgun Gothic", 14, "bold"))
    style.configure("Detail.TLabel", foreground=SUBTLE)
    style.configure("Treeview", rowheight=26)
    style.map("TCombobox", fieldbackground=[("readonly", "#243548")], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", "#243548")
    root.option_add("*TCombobox*Listbox.foreground", FG)


class Desktop:
    def __init__(self, root, game, assets, data):
        self.root, self.data, self.game = root, data, game
        self.events = Queue()
        self.ctx = Context(game, assets, data, self.events, threading.Event())
        self.control = Controller(self.ctx)
        self.state = self.control.snapshot()
        self.closing = self.installing = self.checking = False
        self.available_release = None
        self.active = ""
        self.history = {}
        self.install_root = Path(os.environ["FC_MANAGER_HOME"]) if os.environ.get("FC_MANAGER_HOME") else None
        self.discord = DiscordService(data, self.remote_status, self.remote_command, self.ctx.log, version=__version__)
        self.status = tk.StringVar(root, "대기 중")
        self.info = tk.StringVar(root)
        self.progress = tk.StringVar(root)
        self.game_only = tk.BooleanVar(root, True)
        self.points = tk.StringVar(root, "시작점수 미확인")
        self.reservation = tk.StringVar(root, "예약 없음")
        self.release_status = tk.StringVar(root, f"Python {__version__}")
        self.parking = tk.BooleanVar(root)
        self.target, self.minimum = tk.StringVar(root, "4000"), tk.StringVar(root, "3500")
        self.floor_enabled, self.limit_enabled = tk.BooleanVar(root), tk.BooleanVar(root)
        self.limit = tk.StringVar(root, "20")
        self.club, self.mission = tk.BooleanVar(root), tk.BooleanVar(root)
        self.date = tk.StringVar(root, (datetime.now(KST)+timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M"))
        self.option_controls = []
        self.window()
        self.display_snapshot(self.state)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.ctx.log(f"FC Manager Python {__version__} 시작")
        self.root.after(60, self.poll)
        if self.install_root:
            self.root.after(1800, self.check_update)

    def window(self):
        r = self.root
        r.title(f"FC Manager {__version__} · Python")
        r.geometry("1000x740")
        r.minsize(870, 640)
        setup_style(r)
        shell = ttk.Frame(r, padding=(16, 10, 16, 12), style="TFrame")
        shell.pack(fill="both", expand=True)
        title = ttk.Frame(shell)
        title.pack(fill="x")
        ttk.Label(title, text="FC 온라인 감독모드 도우미", font=("Malgun Gothic", 19, "bold")).pack(side="left")
        ttk.Label(title, textvariable=self.release_status, style="Detail.TLabel").pack(side="right")
        head = ttk.Frame(shell, padding=(0, 12))
        head.pack(fill="x")
        ttk.Label(head, textvariable=self.status, style="Status.TLabel", wraplength=910).pack(anchor="w")
        ttk.Label(head, textvariable=self.progress).pack(anchor="w", pady=(3, 0))
        ttk.Label(head, textvariable=self.info, style="Detail.TLabel").pack(anchor="w", pady=(4, 0))
        bar = ttk.Frame(shell)
        bar.pack(fill="x", pady=(0, 10))
        self.start_button = self.button(bar, "시작  F9", self.start_manager, "Start.TButton")
        self.stop_button = self.button(bar, "중지  F12", self.stop)
        self.finish_button = self.button(bar, "이번 판까지만  F8", self.finish)
        self.capture_button = self.button(bar, "FC 화면 저장", self.capture)
        self.bp_button = self.button(bar, "BP 강화 보호 100%", lambda: self.launch("bp"))
        tabs = ttk.Notebook(shell)
        tabs.pack(fill="both", expand=True)
        pages = {}
        for name in ("진행 설정", "훈련코치", "자동 시작", "Discord 알림", "전적", "실행 기록"):
            pages[name] = ttk.Frame(tabs, padding=8)
            tabs.add(pages[name], text=name)
        self.general_page(pages["진행 설정"])
        self.coach = build_coach_page(pages["훈련코치"], self.data,
                                      lambda rows, limit: self.launch("coach", (rows, limit)))
        self.maintenance_page(pages["자동 시작"])
        build_discord_tab(pages["Discord 알림"], self.discord)
        self.stats_page(pages["전적"])
        log_page = pages["실행 기록"]
        self.logbox = tk.Text(log_page, height=8, bg=BG, fg=FG, wrap="word", state="disabled",
                              insertbackground=FG, font=("Malgun Gothic", 10))
        scroll = ttk.Scrollbar(log_page, command=self.logbox.yview)
        scroll.pack(side="right", fill="y")
        self.logbox.configure(yscrollcommand=scroll.set)
        self.logbox.pack(fill="both", expand=True)
        footer = ttk.Frame(shell)
        footer.pack(fill="x", pady=(8, 0))
        self.button(footer, "진단 폴더 열기", lambda: os.startfile(self.ctx.diagnostic))
        self.import_button = self.button(footer, "이전 설정 가져오기", self.import_settings)
        self.update_button = self.button(footer, "업데이트 확인", self.check_update)
        self.notes_button = self.button(footer, "변경 내용", self.show_notes)

    def button(self, parent, label, action, style="TButton"):
        button = ttk.Button(parent, text=label, command=lambda: self.invoke(action), style=style)
        button.pack(side="left", padx=(0, 6))
        return button

    def invoke(self, action):
        try:
            return action()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))
            self.ctx.log(str(exc))

    def general_page(self, parent):
        mode = ttk.LabelFrame(parent, text="진행 모드", padding=12)
        mode.pack(fill="x", pady=(0, 8))
        for label, value in [("일반", False), ("주차", True)]:
            button = ttk.Radiobutton(mode, text=label, variable=self.parking, value=value)
            button.pack(side="left", padx=(0, 16))
            self.option_controls.append(button)
        ttk.Label(mode, textvariable=self.points).pack(side="left", padx=10)
        self.rank_button = self.button(mode, "시작점수 화면 인식", lambda: self.launch("rank"))
        guard = ttk.Checkbutton(parent, text="FC ONLINE 창에서만 입력", variable=self.game_only)
        guard.pack(anchor="w", pady=(0, 6))
        self.option_controls.append(guard)
        target = ttk.LabelFrame(parent, text="중지 조건", padding=12)
        target.pack(fill="x", pady=(0, 8))
        for label, value, enabled, low, high in [
                ("주차 목표점수", self.target, None, 1, 99999),
                ("최저점수", self.minimum, self.floor_enabled, 0, 99999),
                ("판수 제한", self.limit, self.limit_enabled, 1, 9999)]:
            if enabled is None:
                widget = ttk.Label(target, text=label)
            else:
                widget = ttk.Checkbutton(target, text=label, variable=enabled)
                self.option_controls.append(widget)
            widget.pack(side="left", padx=(0, 4))
            entry = ttk.Spinbox(target, textvariable=value, width=7, from_=low, to=high)
            entry.pack(side="left", padx=(0, 18))
            self.option_controls.append(entry)
        rewards = ttk.LabelFrame(parent, text="기부 및 보상", padding=12)
        rewards.pack(fill="x")
        for column, (label, var) in enumerate((("클럽 자동 기부", self.club), ("미션 허브 자동 확인", self.mission))):
            widget = ttk.Checkbutton(rewards, text=label, variable=var)
            widget.grid(row=0, column=column, sticky="w")
            self.option_controls.append(widget)
        row = ttk.Frame(rewards)
        row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(row, text="한국 시간 23:30부터 자정까지 항목별 하루 한 번 확인합니다.").pack(side="left", padx=(0, 10))
        self.reward_button = self.button(row, "다음 경기 후 확인 예약 / 취소", lambda: self.control.submit("reward"))

    def maintenance_page(self, parent):
        pane = ttk.LabelFrame(parent, text="점검 후 자동 시작", padding=16)
        pane.pack(fill="x")
        ttk.Label(pane, text="넥슨플러그에 로그인하고 FC 온라인 게임 페이지를 열어 두세요.").pack(anchor="w")
        row = ttk.Frame(pane)
        row.pack(fill="x", pady=12)
        ttk.Label(row, text="점검 종료 예정 (한국 시간)").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.date, width=21).pack(side="left", padx=(0, 8))
        self.arm_button = self.button(row, "예약", self.arm)
        self.disarm_button = self.button(row, "예약 취소", lambda: self.control.submit("cancel_reservation"))
        ttk.Label(pane, textvariable=self.reservation, wraplength=800).pack(anchor="w")

    def stats_page(self, parent):
        cols = ("일자", "판수", "승", "무", "패", "미분류", "승률")
        self.table = ttk.Treeview(parent, columns=cols, show="headings")
        for col in cols:
            self.table.heading(col, text=col)
            self.table.column(col, width=100, anchor="center")
        scroll = ttk.Scrollbar(parent, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)

    @staticmethod
    def integer(var, low, high, label):
        try:
            value = int(var.get())
        except ValueError:
            raise ValueError(f"{label}에 정수를 입력하세요.") from None
        if not low <= value <= high:
            raise ValueError(f"{label} 범위는 {low}~{high}입니다.")
        return value

    def options(self):
        return Options(parking=self.parking.get(),
                       target=self.integer(self.target, 1, 99999, "목표점수") if self.parking.get() else 4000,
                       minimum=self.integer(self.minimum, 0, 99999, "최저점수") if self.parking.get() and self.floor_enabled.get() else None,
                       limit=self.integer(self.limit, 1, 9999, "판수") if self.limit_enabled.get() else 0,
                       club=self.club.get(), mission=self.mission.get(), game_only=self.game_only.get())

    def launch(self, kind, payload=None):
        if self.active or self.installing or self.closing:
            raise ValueError("진행 중인 작업을 먼저 중지하세요.")
        self.control.launch(kind, payload)
        self.active = kind
        self.status.set({"coach": "훈련코치 모집 중", "bp": "BP 보호 확인 중", "manager": "감독모드 준비 중",
                         "capture": "FC 화면 저장 중", "rank": "시작점수 확인 중"}[kind])
        self.buttons()

    def start_manager(self):
        options = self.options()
        self.control.manager.validate_start(options.parking, options.target, options.minimum, options.limit)
        if self.active == "manager" and self.state["armed"] and not self.state["running"]:
            self.control.submit("start", options)
        else:
            self.launch("manager", (options, None))

    def capture(self):
        if self.active == "manager":
            self.control.submit("capture")
        else:
            self.launch("capture")

    def arm(self):
        when = datetime.strptime(self.date.get().strip(), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        if self.active == "manager":
            self.control.submit("arm", when)
        else:
            self.launch("manager", (self.options(), when))

    def stop(self):
        self.control.stop()
        if self.active:
            self.status.set("중지 중")

    def finish(self):
        if self.state["running"]:
            self.control.submit("finish")

    def remote_status(self):
        details = [self.status.get(), self.progress.get(), self.info.get()]
        if self.state["rank"] is not None:
            details.append(self.points.get())
        if self.state["finish"]:
            details.append("이번 판 후 중지 예약")
        if self.state["armed"]:
            details.append("점검 후 시작 예약")
        return " · ".join(details)

    def remote_command(self, name):
        try:
            if name == "start":
                if self.state["running"]:
                    return "이미 자동 진행 중입니다."
                if not self.game.game_present():
                    return "FC 온라인을 먼저 실행하세요."
                self.start_manager()
                return "감독모드 시작 요청을 접수했습니다."
            if name == "stop":
                self.stop()
                return "중지 요청을 접수했습니다."
            if name == "finish" and self.state["running"]:
                if not self.state["finish"]:
                    self.control.submit("finish_remote")
                return "이번 판 후 중지 예약 요청을 접수했습니다."
            return "현재 실행 상태에서 사용할 수 없는 명령입니다."
        except (ValueError, RuntimeError, OSError) as exc:
            return str(exc)

    def buttons(self):
        idle = not self.active and not self.installing
        running = bool(self.state["running"]) and self.active == "manager"
        for widget in (self.bp_button, self.rank_button, self.import_button, *self.option_controls):
            widget.state(["!disabled"] if idle else ["disabled"])
        can_start = idle or (self.active == "manager" and self.state["armed"] and not running)
        self.start_button.state(["!disabled"] if can_start else ["disabled"])
        self.coach.set_running(not idle)
        for widget, enabled in ((self.stop_button, bool(self.active)), (self.finish_button, running),
                                (self.capture_button, idle or running), (self.reward_button, running and not self.state["rewards_busy"]),
                                (self.arm_button, (idle or running) and not self.state["armed"]),
                                (self.disarm_button, self.state["armed"]),
                                (self.update_button, not self.checking and not self.installing)):
            widget.state(["!disabled"] if enabled else ["disabled"])
        reward_label = "기부·보상 확인 중" if self.state["rewards_busy"] else (
            "다음 경기 확인 예약 취소" if self.state["reward_pending"] else "다음 경기 후 기부·보상 확인")
        self.reward_button.configure(text=reward_label)
        self.finish_button.configure(text="예약 취소  F8" if self.state["finish"] else "이번 판까지만  F8")

    def display_snapshot(self, state):
        self.state = state
        s = state["session"]
        self.info.set(f"이번 실행 {s['Count']}판 · {s['Win']}승 {s['Draw']}무 {s['Loss']}패"
                      f"     오늘 {state['today']['Count']}판 · 누적 {state['total']['Count']}판"
                      f"     입력 {state['actions']}회 · 일시정지 {state['pauses']}회 · 점수 샘플 {state['samples']}장")
        self.points.set(f"현재 {state['rank']}점" if state["rank"] is not None else "시작점수 미확인")
        count, limit = state["count"], state["limit"]
        self.progress.set(f"완료 {count} / {limit}판 · 남은 경기 {max(0, limit-count)}판" if limit
                          else f"완료 {count}판 · 판수 제한 없음")
        self.reservation.set(state["maintenance"] or "예약 없음")
        if self.active == "manager":
            self.status.set(state["maintenance"] if state["armed"] and not state["running"] else state["status"])
        if self.history != state["daily"]:
            self.history = state["daily"]
            self.table.delete(*self.table.get_children())
            for day, row in sorted(self.history.items(), reverse=True):
                if row["Count"]:
                    classified = row["Win"] + row["Draw"] + row["Loss"]
                    rate = f"{100*row['Win']/classified:.1f}%" if classified else "—"
                    self.table.insert("", "end", values=(day, *(row[k] for k in ("Count", "Win", "Draw", "Loss", "Unclassified")), rate))
        self.buttons()

    def add_log(self, line):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n")
        if int(self.logbox.index("end-1c").split(".")[0]) > 600:
            self.logbox.delete("1.0", "101.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def import_settings(self):
        if self.active or self.installing:
            raise ValueError("작업이 끝난 뒤 설정을 가져올 수 있습니다.")
        folder = filedialog.askdirectory(parent=self.root, title="기존 FCManager.ps1이 있는 폴더")
        if folder:
            count = import_existing(folder, self.data)
            self.ctx.log(f"기존 설정·전적 가져오기 · {count}개 파일")
            if count:
                self.reload_data = True
                self.close()
            else:
                self.status.set("추가로 가져올 설정이 없습니다.")

    def show_notes(self):
        release = self.available_release
        notes = release.notes if release else (
            "코치 모집에서 요청 특성의 이미지 점수가 낮고 다른 아이콘 하나가 뚜렷하게 앞설 때 발생하던 판독 시간 초과를 수정했습니다.")
        messagebox.showinfo("변경 내용", notes, parent=self.root)

    def check_update(self):
        if self.checking or self.installing or self.closing:
            return
        release = self.available_release
        if release and not self.install_root:
            self.release_status.set(f"Python {release.version} · Windows 배포본에서 설치할 수 있습니다.")
            return
        if release and self.active:
            raise ValueError("실행 중인 작업을 마친 뒤 업데이트하세요.")
        self.checking, self.installing = not bool(release), bool(release)
        self.release_status.set("업데이트 설치 중" if release else "업데이트 확인 중")
        self.buttons()
        def task():
            try:
                if release:
                    update.prepare_update(release, self.install_root)
                    update.activate_update(release, self.install_root)
                    self.events.put(("installed", release.version))
                else:
                    self.events.put(("release", update.check_for_update(__version__)))
            except Exception as exc:
                self.events.put(("update_error", str(exc)))
        threading.Thread(target=task, name="FC update", daemon=True).start()

    def handle(self, name, value):
        if name == "snapshot":
            self.display_snapshot(value)
        elif name == "log":
            self.add_log(value)
            if self.active == "coach":
                self.coach.set_status(value.split("] ", 1)[-1])
        elif name == "notify":
            self.discord.notify(*value)
        elif name == "report":
            self.discord.report(*value)
        elif name == "done":
            kind, message = value
            self.active = ""
            self.status.set(message)
            if kind == "coach":
                self.coach.set_status(message)
            self.buttons()
        elif name == "release":
            self.checking = False
            self.available_release = value
            self.release_status.set(f"새 버전 {value.version}" if value else f"최신 버전 · Python {__version__}")
            self.update_button.configure(text=f"v{value.version} 설치" if value else "업데이트 확인")
            self.buttons()
        elif name == "installed":
            self.installing = False
            self.restart = True
            self.close()
        elif name == "update_error":
            self.checking = self.installing = False
            self.release_status.set("업데이트 확인 또는 설치 실패")
            self.ctx.log(f"업데이트 · {value}")
            self.buttons()

    def poll(self):
        try:
            for _ in range(100):
                try:
                    name, value = self.events.get_nowait()
                except Empty:
                    break
                self.handle(name, value)
            if not self.closing:
                keys = self.game.poll_hotkeys()
                if "F12" in keys:
                    self.stop()
                else:
                    if "F8" in keys:
                        self.invoke(self.finish)
                    if "F9" in keys and (not self.active or (self.state["armed"] and not self.state["running"])):
                        self.invoke(self.start_manager)
                self.discord.tick()
        except Exception as exc:
            self.status.set(f"상태 확인 오류 · {exc}")
        if self.closing and not self.control.busy:
            self.discord.close()
            self.root.destroy()
            return
        self.root.after(60, self.poll)

    def close(self):
        if self.installing:
            self.status.set("업데이트 설치를 마친 뒤 종료할 수 있습니다.")
            return
        self.closing = True
        self.control.stop()
