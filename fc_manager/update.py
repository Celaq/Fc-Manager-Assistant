"""Updates for the portable Python build. Personal data lives outside versioned payloads."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

FEED = "https://raw.githubusercontent.com/Celaq/Fc-Manager-Assistant/main/releases/latest-python.json"
RELEASE_DIR = FEED.rsplit("/", 1)[0]
CHANNEL = "python-windows-x64"
ACTIVE = "active-version.txt"
LAUNCHER = "Start_FC_Manager.cmd"
MAX_DOWNLOAD = 180 * 1024 * 1024
MAX_UNPACKED = 750 * 1024 * 1024
REQUIRED = {
    "launch.py", "runtime/python.exe", "runtime/pythonw.exe", "runtime/DLLs/_tkinter.pyd",
    "fc_manager/__main__.py", "fc_manager/app.py", "fc_manager/windows.py", "fc_manager/controller.py",
    "coach_reader/traits.json", "assets/templates/client.png",
    "assets/templates_2560/client.png",
}


def version_tuple(version):
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){2}", version):
        raise ValueError("잘못된 버전 번호입니다.")
    return tuple(map(int, version.split(".")))


def release_name(version):
    version_tuple(version)
    return f"FC_Manager_Python_v{version}_win64.zip"


def asset_url(version):
    return f"{RELEASE_DIR}/{release_name(version)}"


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    notes: str

    def __post_init__(self):
        if self.url != asset_url(self.version):
            raise ValueError("신뢰할 수 없는 Python 배포 URL입니다.")
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("잘못된 배포 체크섬입니다.")


class _GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        dest = urlsplit(newurl)
        if dest.scheme != "https" or dest.netloc not in {
            "raw.githubusercontent.com", "github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        }:
            raise ValueError("신뢰할 수 없는 다운로드 리디렉션입니다.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url):
    return urllib.request.build_opener(_GitHubRedirect).open(url, timeout=30)


def check_for_update(current_version: str) -> Release | None:
    with _open_url(FEED) as response:
        data = response.read(65537)
    if len(data) > 65536:
        raise ValueError("배포 정보가 너무 큽니다.")
    manifest = json.loads(data)
    if manifest.get("schemaVersion") != 1 or manifest.get("channel") != CHANNEL:
        raise ValueError("지원하지 않는 Python 배포 정보입니다.")
    release = Release(manifest["version"], manifest["url"], manifest["sha256"],
                      str(manifest.get("notes", ""))[:2000])
    return release if version_tuple(release.version) > version_tuple(current_version) else None


def _installed_version(root):
    if not (root / LAUNCHER).is_file():
        raise ValueError("압축을 푼 Python 배포 폴더에서 실행해 주세요.")
    current = (root / ACTIVE).read_text(encoding="ascii").strip()
    version_tuple(current)
    if not (root / "payloads" / current / "runtime/pythonw.exe").is_file():
        raise ValueError("현재 Python 실행 파일을 찾을 수 없습니다.")
    return current


def _ensure_newer(release, root):
    if version_tuple(release.version) <= version_tuple(_installed_version(root)):
        raise ValueError("현재 버전보다 새 버전만 설치할 수 있습니다.")


def prepare_update(release: Release, install_root) -> Path:
    root = Path(install_root)
    _ensure_newer(release, root)
    cache = root / ".updates"
    cache.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as out:
        temp = Path(out.name)
    try:
        digest = hashlib.sha256()
        size = 0
        with _open_url(release.url) as source, temp.open("wb") as out:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise ValueError("다운로드 크기 제한을 초과했습니다.")
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != release.sha256:
            raise ValueError("다운로드 체크섬이 일치하지 않습니다.")
        target = cache / f"{release.sha256}.zip"
        os.replace(temp, target)
        return target
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def _install_lock(cache):
    with (cache / "install.lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield  # Closing the handle also releases the OS lock on failure.


def _safe_member(info):
    parts = info.filename.split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((" ", "."))
                or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
                or PureWindowsPath(part).is_reserved()):
            raise ValueError("잘못된 배포 파일 경로입니다.")
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind not in (0, stat.S_IFREG):
        raise ValueError("배포에 일반 파일만 포함할 수 있습니다.")
    return parts


def _unpack(bundle, stage, version):
    required = REQUIRED.copy()
    total = 0
    names = set()
    if len(bundle.infolist()) > 22000:
        raise ValueError("배포 파일 개수 제한을 초과했습니다.")
    for info in bundle.infolist():
        parts = _safe_member(info)
        key = info.filename.casefold()
        if key in names:
            raise ValueError("배포 파일 이름이 중복됩니다.")
        names.add(key)
        total += info.file_size
        if total > MAX_UNPACKED:
            raise ValueError("배포 크기 제한을 초과했습니다.")
        if info.filename == ACTIVE and info.file_size <= 32:
            if bundle.read(info).decode("ascii").strip() != version:
                raise ValueError("패키지 버전이 일치하지 않습니다.")
        elif info.filename == LAUNCHER and info.file_size <= 8192:
            continue  # The stable bootstrap is installed once by extracting the initial ZIP.
        elif parts[:2] == ["payloads", version] and len(parts) >= 3:
            name = "/".join(parts[2:])
            if name.casefold() == ".release-sha256":
                raise ValueError("예약된 배포 파일 경로입니다.")
            dest = stage.joinpath(*parts[2:])
            dest.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, dest.open("xb") as out:
                shutil.copyfileobj(source, out)
            required.discard(name)
        else:
            raise ValueError("배포 파일이 프로그램 폴더 밖에 있습니다.")
    if required or ACTIVE not in names or LAUNCHER.casefold() not in names:
        raise ValueError("필수 배포 파일이 없습니다.")


def activate_update(release: Release, install_root) -> None:
    root = Path(install_root)
    cache = root / ".updates"
    with _install_lock(cache):
        _ensure_newer(release, root)
        payload = root / "payloads" / release.version
        with tempfile.TemporaryDirectory(dir=cache) as work:
            stage = Path(work) / "payload"
            stage.mkdir()
            with (cache / f"{release.sha256}.zip").open("rb") as archive:
                if hashlib.file_digest(archive, "sha256").hexdigest() != release.sha256:
                    raise ValueError("설치 체크섬이 일치하지 않습니다.")
                archive.seek(0)
                with zipfile.ZipFile(archive) as bundle:
                    _unpack(bundle, stage, release.version)
            marker = ".release-sha256"
            (stage / marker).write_text(release.sha256, encoding="ascii")
            if payload.exists():
                # A previous run may have stopped just before switching the pointer.
                if not (payload / marker).is_file() or (payload / marker).read_text() != release.sha256:
                    raise ValueError("대상 버전 폴더가 이미 사용 중입니다.")
            else:
                stage.rename(payload)
            new_pointer = Path(work) / ACTIVE
            new_pointer.write_text(release.version + "\n", encoding="ascii")
            os.replace(new_pointer, root / ACTIVE)


def launch_updated(install_root):
    root = Path(install_root).resolve()
    payload = root / "payloads" / _installed_version(root)
    env = dict(os.environ, FC_MANAGER_HOME=str(root), FC_MANAGER_ASSETS=str(payload / "assets"))
    return subprocess.Popen([str(payload / "runtime/pythonw.exe"), "-I", "-B", str(payload / "launch.py"),
                             "--wait-pid", str(os.getpid())],
                            cwd=root, env=env)
