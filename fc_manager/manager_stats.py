"""Match totals, Korean-calendar daily history and local result samples."""

import csv
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
FIELDS = ("Count", "Win", "Draw", "Loss", "Unclassified")
OUTCOMES = {"win": "Win", "loss": "Loss", "draw": "Draw", "unclassified": "Unclassified"}


def day_key():
    return datetime.now(KST).date().isoformat()


def valid_day(day):
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") == day
    except (ValueError, TypeError):
        return False


def record(source=None, prefix=""):
    source = source or {}
    row = {k: max(0, int(source.get(prefix + k, 0) or 0)) for k in FIELDS}
    row["Count"] = max(row["Count"], sum(row[k] for k in FIELDS[1:]))
    row["Unclassified"] = row["Count"] - sum(row[k] for k in ("Win", "Draw", "Loss"))
    return row


def add_result(row, outcome):
    return dict(row, Count=row["Count"] + 1, **{OUTCOMES[outcome]: row[OUTCOMES[outcome]] + 1})


def append_csv(path, header, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or not path.stat().st_size
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if new and header:
            writer.writerow(header)
        writer.writerow(row)


class MatchStats:
    def __init__(self, root, log):
        self.root = Path(root)
        self.log = log
        self.path = self.root / "stats.json"
        self.diagnostics = self.root / "오류확인용_보내기"
        self.session = record()
        data = {}
        self._primary_valid = False
        for candidate in (self.path, self.path.with_suffix(".json.bak")):
            if candidate.is_file():
                try:
                    loaded = json.loads(candidate.read_text("utf-8-sig"))
                    if not isinstance(loaded, dict) or not isinstance(loaded.get("DailyHistory", {}), dict):
                        raise TypeError("지원하지 않는 통계 형식")
                    record(loaded, "Total")
                    record(loaded, "Today")
                    self._primary_valid = candidate == self.path
                    data = loaded
                    break
                except (ValueError, TypeError, OSError) as exc:
                    log(f"기존 판수 기록 읽기 실패 ({candidate.name}): {exc}")
        self.total = record(data, "Total")
        self.daily = {}
        for day, values in (data.get("DailyHistory") or {}).items():
            if valid_day(day):
                try:
                    self.daily[day] = record(values)
                except (ValueError, TypeError, AttributeError):
                    continue
        if valid_day(data.get("Date")) and data["Date"] not in self.daily:
            self.daily[data["Date"]] = record(data, "Today")
        self._import_samples()
        self.point_count = sum(1 for _ in (self.diagnostics / "point_samples" / "full").glob("*.png"))

    @property
    def today(self):
        return self.daily.get(day_key(), record())

    def _import_samples(self):
        path = self.diagnostics / "result_samples" / "samples.csv"
        if not path.is_file():
            return
        imported, seen = {}, set()
        try:
            with path.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    day = (row.get("time") or "")[:10]
                    name, result = row.get("file"), row.get("result")
                    if (
                        not valid_day(day)
                        or day in self.daily
                        or not name
                        or name in seen
                        or result not in OUTCOMES
                    ):
                        continue
                    seen.add(name)
                    imported[day] = add_result(imported.get(day, record()), result)
        except (OSError, UnicodeError, csv.Error) as exc:
            self.log(f"경기 샘플 목록 읽기 실패: {exc}")
        self.daily.update(imported)

    def register(self, score, frame):
        result = (
            "unclassified"
            if score is None
            else "win"
            if score[0] > score[1]
            else "loss"
            if score[0] < score[1]
            else "draw"
        )
        day = day_key()
        today = add_result(self.daily.get(day, record()), result)
        total = add_result(self.total, result)
        history = {**self.daily, day: today}
        data = {
            "Date": day,
            "DailyHistory": history,
            **{"Today" + k: v for k, v in today.items()},
            **{"Total" + k: v for k, v in total.items()},
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False, mode="w", encoding="utf-8-sig") as f:
            temp = Path(f.name)
            try:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                f.close()
                temp.unlink(missing_ok=True)
                raise
        try:
            if self._primary_valid and self.path.exists():
                shutil.copyfile(self.path, self.path.with_suffix(".json.bak"))
            os.replace(temp, self.path)
            self._primary_valid = True
        finally:
            temp.unlink(missing_ok=True)
        self.session = add_result(self.session, result)
        self.daily, self.total = history, total
        try:
            self._samples(frame, result, score, today)
        except OSError as exc:
            self.log(f"경기 결과 샘플 저장 실패: {exc}")
        return result

    def _samples(self, frame, result, score, today):
        now = datetime.now(KST)
        date = now.strftime("%Y-%m-%d %H:%M:%S")
        counts = [self.session["Count"], today["Count"], self.total["Count"]]
        suffix = f"match{counts[2]:04d}_{result}"
        result_dir = self.diagnostics / "result_samples"
        result_dir.mkdir(parents=True, exist_ok=True)
        name = f"Result_{now:%Y%m%d_%H%M%S}_{suffix}.png"
        frame.save(result_dir / name)
        append_csv(
            result_dir / "samples.csv",
            ["time", "session", "today", "total", "result", "file"],
            [date, *counts, result, name],
        )
        point_dir = self.diagnostics / "point_samples"
        base = f"Point_{now:%Y%m%d_%H%M%S}_{now.microsecond // 1000:03}_{suffix}"
        full_name, roi_name = base + "_full.png", base + "_score_area.png"
        (point_dir / "full").mkdir(parents=True, exist_ok=True)
        (point_dir / "score_area").mkdir(parents=True, exist_ok=True)
        # The full point sample and result sample represent the same capture.
        shutil.copyfile(result_dir / name, point_dir / "full" / full_name)
        w, h = frame.size
        frame.crop((round(w * 0.27), round(h * 0.27), round(w * 0.99), round(h * 0.82))).save(
            point_dir / "score_area" / roi_name
        )
        score_text = f"{score[0]}:{score[1]}" if score else "점수 인식 실패"
        append_csv(
            point_dir / "point_samples.csv",
            [
                "time",
                "session",
                "today",
                "total",
                "result",
                "match_score",
                "full_file",
                "score_area_file",
                "width",
                "height",
            ],
            [date, *counts, result, score_text, full_name, roi_name, w, h],
        )
        self.point_count += 1
