"""Settings and controls for the live coach recruitment worker."""

from tkinter import StringVar, ttk

from .coach import TRAITS, TRAIT_IDS, combination_issue, load_preferences, save_preferences, validate_preferences


EMPTY = "선택 안 함"


class CoachPage:
    def __init__(self, parent, data_dir, on_start, on_stop=None):
        self.data_dir, self.on_start = data_dir, on_start
        self.status = StringVar(parent, "새로 모집 50 CP · 고용 300 CP · 게임의 훈련 코치 모집 탭에서 시작하세요.")
        try:
            rows, limit = load_preferences(data_dir)
        except (OSError, ValueError) as exc:
            rows, limit = [[] for _ in range(7)], 300
            self.status.set(f"저장된 설정을 읽지 못했습니다. 조합을 다시 선택하세요. {exc}")
        else:
            try:
                validate_preferences(rows, limit)
            except ValueError as exc:
                self.status.set(str(exc))

        self.budget = StringVar(parent, str(limit))
        self.controls = []
        content = ttk.Frame(parent, padding=12)
        content.pack(fill="both", expand=True)
        for column in (1, 2, 3):
            content.columnconfigure(column, weight=1)
        ttk.Label(content, text="한 줄의 특성 1~3개를 모두 갖춘 코치 1명을 고용합니다.").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self.boxes = []
        for index, row in enumerate(rows):
            ttk.Label(content, text=f"조합 {index+1}").grid(row=index+1, column=0, sticky="w", padx=(0, 6))
            boxes = []
            for slot in range(3):
                box = ttk.Combobox(content, state="readonly", width=15)
                box.set(TRAITS[row[slot]] if slot < len(row) else EMPTY)
                box.grid(row=index+1, column=slot+1, sticky="ew", padx=(0, 5), pady=2)
                box.bind("<<ComboboxSelected>>", lambda event, i=index: self.update_choices(i))
                boxes.append(box)
                self.controls.append((box, "readonly"))
            self.boxes.append(boxes)
            self.update_choices(index)

        actions = ttk.Frame(content)
        actions.grid(row=8, column=0, columnspan=4, sticky="w", pady=(9, 4))
        ttk.Label(actions, text="CP 사용 한도").pack(side="left")
        spin = ttk.Spinbox(actions, from_=300, to=1_000_000, increment=50, width=10, textvariable=self.budget)
        spin.pack(side="left", padx=(6, 12))
        self.controls.append((spin, "normal"))
        for label, start in (("조합 저장", False), ("모집 시작", True)):
            button = ttk.Button(actions, text=label, command=lambda s=start: self.submit(s))
            button.pack(side="left", padx=(0, 5))
            self.controls.append((button, "normal"))
        self.stop_button = None
        if on_stop is not None:
            self.stop_button = ttk.Button(actions, text="중지", command=on_stop, state="disabled")
            self.stop_button.pack(side="left")
        ttk.Label(content, textvariable=self.status, wraplength=740).grid(
            row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def update_choices(self, index):
        boxes = self.boxes[index]
        chosen = [TRAIT_IDS.get(box.get()) for box in boxes]
        for slot, box in enumerate(boxes):
            others = [trait for i, trait in enumerate(chosen) if i != slot and trait is not None]
            box["values"] = [EMPTY] + [
                name for trait, name in TRAITS.items()
                if trait == chosen[slot] or not combination_issue([*others, trait])
            ]

    def submit(self, start):
        try:
            rows = [[TRAIT_IDS[box.get()] for box in boxes if box.get() != EMPTY] for boxes in self.boxes]
            try:
                limit = int(self.budget.get())
            except ValueError as exc:
                raise ValueError("CP 한도에 정수를 입력하세요.") from exc
            validate_preferences(rows, limit)
            if start and not any(rows):
                raise ValueError("원하는 조합을 하나 이상 입력하세요.")
            save_preferences(self.data_dir, rows, limit)
            if start:
                self.on_start(rows, limit)
            else:
                self.set_status("조합과 CP 한도를 저장했습니다.")
        except (OSError, ValueError, RuntimeError) as exc:
            self.set_status(str(exc))

    def set_running(self, running):
        for widget, idle_state in self.controls:
            widget.configure(state="disabled" if running else idle_state)
        if self.stop_button is not None:
            self.stop_button.configure(state="normal" if running else "disabled")

    def set_status(self, text):
        self.status.set(text)


def build_coach_page(parent, data_dir, on_start, on_stop=None):
    """Populate an empty Tk parent and return its controls. Call methods on the Tk thread."""
    return CoachPage(parent, data_dir, on_start, on_stop)
