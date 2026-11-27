"""Tk settings for direct Discord notifications, device pairing and diagnostics."""

from tkinter import BooleanVar, StringVar, ttk


def build_discord_tab(parent, service):
    panel = ttk.Frame(parent, padding=12)
    panel.pack(fill="both", expand=True)
    panel.columnconfigure(0, weight=1)
    personal = ttk.LabelFrame(panel, text="알림 연결", padding=12)
    personal.grid(row=0, column=0, sticky="ew")
    personal.columnconfigure(1, weight=1)
    webhook = StringVar(panel, value=service.webhook)
    enabled = BooleanVar(panel, value=service.enabled)
    personal_label = StringVar(panel, value=service.personal_status)
    ttk.Label(personal, text="개인 웹훅").grid(row=0, column=0, padx=(0, 6))
    ttk.Entry(personal, textvariable=webhook, show="*").grid(row=0, column=1, sticky="ew")
    ttk.Checkbutton(personal, text="감독모드 알림 사용", variable=enabled).grid(row=1, column=0, columnspan=2, sticky="w", pady=6)

    def save(test=False):
        try:
            service.save_webhook(webhook.get(), enabled.get())
            if test:
                service.notify("FC 감독모드 테스트 알림", "Discord 직접 알림이 정상적으로 연결되었습니다.", force=True)
                service.personal_status = "테스트 알림 전송 중…"
        except (ValueError, OSError) as exc:
            service.personal_status = str(exc)
        personal_label.set(service.personal_status)

    ttk.Button(personal, text="저장", command=save).grid(row=0, column=2, padx=5)
    ttk.Button(personal, text="테스트", command=lambda: save(True)).grid(row=0, column=3)
    ttk.Label(personal, textvariable=personal_label, wraplength=610).grid(row=2, column=0, columnspan=4, sticky="w")

    remote = ttk.LabelFrame(panel, text="원격 조작 · 내 Discord 계정", padding=12)
    remote.grid(row=1, column=0, sticky="ew", pady=(12, 0))
    remote.columnconfigure(0, weight=1)
    link = StringVar(panel)
    remote_label = StringVar(panel, value=service.remote_status)
    operator = BooleanVar(panel, value=service.operator_enabled)
    ttk.Entry(remote, textvariable=link, show="*").grid(row=0, column=0, sticky="ew")

    def connect():
        try:
            service.pair(link.get())
            link.set("")
        except (ValueError, OSError) as exc:
            service.remote_status = str(exc)
        remote_label.set(service.remote_status)

    def forget():
        try:
            service.forget()
        except OSError:
            service.remote_status = "이 PC에서 저장한 연결 정보를 삭제하지 못했습니다."
        remote_label.set(service.remote_status)

    def consent():
        try:
            service.set_operator(operator.get())
        except OSError:
            operator.set(service.operator_enabled)
            service.remote_status = "운영자 오류 전송 설정을 저장하지 못했습니다."
            remote_label.set(service.remote_status)

    ttk.Button(remote, text="계정 연결", command=connect).grid(row=0, column=1, padx=5)
    ttk.Button(remote, text="이 PC 연결 지우기", command=forget).grid(row=0, column=2)
    ttk.Checkbutton(remote, text="운영자에게 오류 자동 전송 (진단정보·로그·오류 캡처) · /오류업로드는 1회 전송",
                    variable=operator, command=consent).grid(row=1, column=0, columnspan=3, sticky="w", pady=7)
    ttk.Label(remote, textvariable=remote_label, wraplength=610).grid(row=2, column=0, columnspan=3, sticky="w")

    def refresh():
        personal_label.set(service.personal_status)
        remote_label.set(service.remote_status)
        if not service.closed:
            panel.after(500, refresh)

    refresh()
    return panel
