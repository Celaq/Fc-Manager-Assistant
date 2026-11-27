"""Non-destructive import of data written by the 3.3.4 Windows helper."""
import os
from pathlib import Path
import shutil
import tempfile

FILES = ("settings.json", "stats.json", "stats.json.bak", "coach-preferences.json",
         "discord-device.dat", "오류확인용_보내기/result_samples/samples.csv",
         "오류확인용_보내기/last-error.json")
PATTERNS = (".coach-icons/training_trait_icon_*.png", ".reward-checks/*")


def known_install(root):
    return (root / "FCManager.ps1").is_file() and any((root / name).is_file() for name in FILES)


def import_existing(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        return 0
    if not known_install(source):
        raise ValueError("기존 FCManager.ps1과 사용자 설정 또는 전적이 있는 폴더를 선택하세요.")
    candidates = [source / name for name in FILES]
    for pattern in PATTERNS:
        candidates.extend(source.glob(pattern))
    copied = 0
    for original in candidates:
        dest = target / original.relative_to(source)
        if not original.is_file() or original.is_symlink() or dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as temp:
            staged = Path(temp.name)
        try:
            shutil.copyfile(original, staged)
            # The single instance owns the target. Never replace an existing setting.
            if not dest.exists():
                os.replace(staged, dest)
                copied += 1
        finally:
            staged.unlink(missing_ok=True)
    return copied


def import_neighbor(target):
    # Only auto-detect the exact previous release beside an otherwise fresh install.
    if any((target / name).is_file() for name in FILES):
        return 0
    old = target.parent / "FC_Manager_v3.3.4"
    if known_install(old):
        return import_existing(old, target)
    return 0
