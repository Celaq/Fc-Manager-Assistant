import argparse
from contextlib import closing
import sys
from pathlib import Path

from . import __version__


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--watch-exit"]:
        from .discord_watch import main as watch
        return watch(argv[1:])
    parser = argparse.ArgumentParser(description="FC 온라인 감독모드 도우미 (Windows)")
    parser.add_argument("--version", action="version", version=f"FC Manager Python {__version__}")
    parser.add_argument("--data-dir", type=Path, help="설정·전적 폴더 (기존 도우미 폴더 사용 가능)")
    parser.add_argument("--assets", type=Path, help="templates와 templates_2560이 있는 폴더")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.exit(2, "실시간 감독모드는 Windows에서 실행하세요. 저장 화면은 python -m coach_reader 로 검사할 수 있습니다.\n")
    from .runtime import assets_directory, data_directory, single_instance
    from .migrate import import_neighbor
    from .windows import WindowsGame
    from .app import Desktop
    from .update import launch_updated
    from tkinter import Tk

    data = data_directory(args.data_dir)
    restart = None
    with single_instance(data), closing(WindowsGame()) as game, assets_directory(args.assets) as assets:
        imported = import_neighbor(data)
        # Rebuild services after importing legacy settings so DPAPI and stats reload together.
        while True:
            root = Tk()
            app = Desktop(root, game, assets, data)
            if imported:
                app.ctx.log(f"기존 설정·전적 가져오기 · {imported}개 파일")
            root.mainloop()
            if getattr(app, "restart", False):
                restart = app.install_root
                break
            if not getattr(app, "reload_data", False):
                break
            imported = 0
    if restart:
        launch_updated(restart)
    return 0


if __name__ == "__main__":
    sys.exit(main())
