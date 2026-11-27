"""Replay a game screenshot with the FC Manager 3.3.4 coach reader."""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from PIL import Image

from . import vision

PACKAGE = Path(__file__).resolve().parent
RELEASE = "FC_Manager_v3.3.4_Coach_Recruitment_Beta.zip"
TRAITS = {int(k): v for k, v in json.loads((PACKAGE / "traits.json").read_text(encoding="utf-8")).items()}
CARDS = ("A 위", "B 위", "C 위", "A 아래", "B 아래", "C 아래")


def load_templates(app_dir):
    names = [*vision.REGIONS, "LowerEmpty", "RefreshText", "Refresh50"]
    def read(file):
        with Image.open(file) as im:
            return np.asarray(im.convert("RGB"), dtype=np.int16)
    if app_dir:
        return {n: read(app_dir / "templates" / f"coach_{n}.png") for n in names}
    with ZipFile(PACKAGE.parent / "releases" / RELEASE) as archive:
        templates = {}
        for name in names:
            with archive.open(f"FC_Manager_v3.3.4/templates/coach_{name}.png") as file:
                templates[name] = read(file)
        return templates


def load_catalog(directory):
    if directory is None:
        return {}
    refs = {}
    for trait in TRAITS:
        file = directory / f"training_trait_icon_{trait:03}.png"
        if file.is_file():
            with Image.open(file) as im:
                refs[trait] = vision.reference(im)
    return refs


def load_rows(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data["rows"]
    if not isinstance(rows, list) or len(rows) not in (4, 7):
        raise ValueError("4개 또는 7개 조합이 있는 coach-preferences.json이 필요합니다.")
    ids = {name: trait for trait, name in TRAITS.items()}
    result = []
    for index, row in enumerate(rows, 1):
        traits = row["traits"]
        if not isinstance(traits, list) or len(traits) > 3 or any(t not in ids for t in traits) or len(set(traits)) != len(traits):
            raise ValueError(f"조합 {index}의 특성 목록 오류")
        result.append([ids[t] for t in traits])
    return result


def read_frame(path):
    with Image.open(path) as image:
        return vision.normalized_image(image)


def inspect(frame, templates, catalog, rows, previous=None, card=None):
    def found(name):
        return vision.marker(frame, templates, name)
    if found("Success"):
        return {"screen": "success"}
    gray = vision.grayscale(frame)
    if found("Prompt"):
        result = {"screen": "confirm", "cost_300": found("Approve300"), "enabled": found("Enabled")}
        if previous is not None:
            result["same_card"] = (vision.marker(previous, templates, "Tab")
                                   and vision.same_card(vision.grayscale(previous), gray, card, dialog=True))
        return result
    if not found("Tab"):
        return {"screen": "unknown"}

    complete = set(catalog) == TRAITS.keys()
    cards = []
    for idx, name in enumerate(CARDS):
        state = vision.card_state(frame, gray, templates, idx)
        entry = {"card": name, "state": state}
        if state == "candidate":
            entry["cost_300"] = vision.marker(frame, templates, "Cost300", idx)
            if catalog:
                slots = vision.read_slots(gray, idx, catalog)
                entry["slots"] = []
                for slot in slots:
                    ranking = sorted(slot.scores.items(), key=lambda item: -item[1])
                    entry["slots"].append({
                        "confirmed": slot.confirmed if complete else None,
                        "scores": {k: round(v, 4) for k, v in ranking},
                    })
                if rows and complete:
                    entry["combination"] = vision.combination(rows, slots)
        cards.append(entry)
    result = {"screen": "board", "refresh_50_issue": vision.refresh_issue(frame, templates),
              "catalog_size": len(catalog), "cards": cards}
    if previous is not None and vision.marker(previous, templates, "Tab"):
        result["same_card"] = vision.same_card(vision.grayscale(previous), gray, card)
    if not all(c["state"] != "unknown" for c in cards):
        result["assessment"] = "unreadable_card"
    elif not any(rows):
        result["assessment"] = "no_presets"
    elif not complete:
        result["assessment"] = "incomplete_catalog"
    else:
        confirmed = next((c for c in cards if c.get("combination", 0) > 0), None)
        if confirmed:
            result.update(assessment="match", card=confirmed["card"], combination=confirmed["combination"])
        elif any(c.get("combination", 0) < 0 for c in cards):
            result["assessment"] = "ambiguous"
        else:
            result["assessment"] = "no_match"
    return result


def main():
    p = argparse.ArgumentParser(description="FC Manager 훈련코치 화면을 판독하여 JSON으로 출력합니다.")
    p.add_argument("image", type=Path, help="게임 화면 또는 coach-board.png")
    p.add_argument("--app-dir", type=Path, help="3.3.4 설치 폴더 (templates, .coach-icons, coach-preferences.json)")
    p.add_argument("--icons", type=Path, help="기준 아이콘 폴더")
    p.add_argument("--preferences", type=Path, help="코치 조합 설정 파일")
    p.add_argument("--previous-board", type=Path, help="비교할 이전 모집 화면")
    p.add_argument("--card", choices=CARDS, help="비교할 후보 위치")
    args = p.parse_args()
    if bool(args.previous_board) != bool(args.card):
        p.error("--previous-board와 --card를 함께 지정하세요.")
    try:
        icons = args.icons or (args.app_dir / ".coach-icons" if args.app_dir else None)
        prefs = args.preferences
        if prefs is None and args.app_dir and (args.app_dir / "coach-preferences.json").is_file():
            prefs = args.app_dir / "coach-preferences.json"
        result = inspect(read_frame(args.image), load_templates(args.app_dir), load_catalog(icons),
                         load_rows(prefs) if prefs else [],
                         read_frame(args.previous_board) if args.previous_board else None,
                         CARDS.index(args.card) if args.card else None)
    except (OSError, KeyError, ValueError, TypeError) as error:
        p.exit(2, f"판독 입력 오류 · {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
