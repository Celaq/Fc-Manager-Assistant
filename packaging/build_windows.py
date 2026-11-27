"""Build a self-contained Windows x64 ZIP on any system with Python 3.11+."""
import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from fc_manager import __version__
from fc_manager.update import ACTIVE, CHANNEL, LAUNCHER, REQUIRED, asset_url, release_name

PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20251031/"
    "cpython-3.12.12%2B20251031-x86_64-pc-windows-msvc-install_only.tar.gz"
)
PYTHON_HASH = "cff398b3f520c442a1b085dd347126c10c1b03f01ccc0decd8c897a687e893f1"
TEMPLATES = ROOT / "releases/FC_Manager_v3.3.4_Coach_Recruitment_Beta.zip"


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def cached(cache, name, url, expected):
    path = cache / name
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as f:
            temp = Path(f.name)
        try:
            with urllib.request.urlopen(url, timeout=90) as source, temp.open("wb") as dest:
                shutil.copyfileobj(source, dest)
            if digest(temp) != expected:
                raise ValueError(f"Download digest mismatch: {name}")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    if digest(path) != expected:
        raise ValueError(f"Cached digest mismatch: {name}")
    return path


def runtime_files(archive):
    with tarfile.open(archive) as tar:
        for item in tar:
            if not item.isfile():
                continue
            path = item.name.removeprefix("python/")
            if (path.startswith(("include/", "libs/", "Scripts/", "Lib/site-packages/",
                                 "Lib/test/", "Lib/idlelib/", "Lib/ensurepip/", "Lib/turtledemo/"))
                    or path.endswith((".pdb", ".pyc", ".chm"))
                    or path.split("/")[0] not in {"DLLs", "Lib", "tcl"}
                    and "/" in path):
                continue
            yield "runtime/" + path, tar.extractfile(item).read()


def wheel_files(wheel):
    with zipfile.ZipFile(wheel) as package:
        for item in package.infolist():
            if item.is_dir():
                continue
            path = item.filename
            top = path.split("/", 1)[0]
            if top.endswith(".data"):
                _, scheme, path = path.split("/", 2)
                if scheme == "scripts":
                    continue
                if scheme not in ("purelib", "platlib"):
                    raise ValueError(f"Unsupported wheel scheme: {scheme}")
            data = package.read(item)
            yield "runtime/Lib/site-packages/" + path, data
            if path.startswith("numpy.libs/msvcp140-") and path.endswith(".dll"):
                # WinRT imports the same VC++ redistributable under its canonical name.
                yield "runtime/msvcp140.dll", data


def app_files():
    yield "launch.py", (HERE / "launch.py").read_bytes()
    for package in ("fc_manager", "coach_reader"):
        for src in sorted((ROOT / package).rglob("*")):
            if src.suffix in (".py", ".json") and not src.name.startswith("test_"):
                yield src.relative_to(ROOT).as_posix(), src.read_bytes()
    with zipfile.ZipFile(TEMPLATES) as templates:
        for item in templates.infolist():
            parts = item.filename.split("/")
            if (len(parts) == 3 and parts[1] in ("templates", "templates_2560")
                    and parts[2].endswith(".png")):
                yield f"assets/{parts[1]}/{parts[2]}", templates.read(item)


def write_entry(archive, name, data):
    info = zipfile.ZipInfo(name)  # Fixed ZIP metadata for reproducible archives across build dates.
    info.create_system = 0
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/fc-manager-build")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--notes", default="FC Manager 3.3.4 Python Windows 배포판")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    runtime = cached(args.cache, "runtime.tar.gz", PYTHON_URL, PYTHON_HASH)
    wheels = [cached(args.cache, name, entry["url"], entry["sha256"])
              for name, entry in json.loads((HERE / "windows-wheels.json").read_text()).items()]
    bundle = args.output / release_name(__version__)
    prefix = "payloads/" + __version__ + "/"
    with tempfile.TemporaryDirectory(dir=args.output) as stage:
        temp = Path(stage) / bundle.name
        with zipfile.ZipFile(temp, "w") as archive:
            cmd = (HERE / LAUNCHER).read_text(encoding="ascii").replace("\n", "\r\n")
            write_entry(archive, LAUNCHER, cmd.encode("ascii"))
            write_entry(archive, ACTIVE, (__version__ + "\r\n").encode("ascii"))
            for name, data in runtime_files(runtime):
                write_entry(archive, prefix + name, data)
            for wheel in wheels:
                for name, data in wheel_files(wheel):
                    write_entry(archive, prefix + name, data)
            for name, data in app_files():
                write_entry(archive, prefix + name, data)
            names = set(archive.namelist())
            missing = [name for name in REQUIRED if prefix + name not in names]
            if missing:
                raise ValueError(f"Missing application files: {missing}")
        temp.replace(bundle)
    manifest = {"schemaVersion": 1, "channel": CHANNEL, "version": __version__,
                "url": asset_url(__version__), "sha256": digest(bundle), "notes": args.notes}
    (args.output / "latest-python.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(bundle, manifest["sha256"])


if __name__ == "__main__":
    main()
