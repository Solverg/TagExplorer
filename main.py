"""TagExplorer application entrypoint."""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.updater import apply_update_and_relaunch

APP_NAME = "TagExplorer"
APP_ORGANIZATION = "Solverg"
APP_VERSION = "1.0.0"


def _log_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_ORGANIZATION / APP_NAME / "logs"
    return Path.home() / f".{APP_NAME.lower()}" / "logs"


def _setup_logging() -> Path:
    log_dir = _log_directory()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = Path.cwd()

    log_path = log_dir / "startup.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    return log_path


def _show_startup_error(exc: BaseException, log_path: Path) -> None:
    from PyQt6.QtWidgets import QMessageBox

    QMessageBox.critical(
        None,
        "TagExplorer startup error",
        "TagExplorer could not start.\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"Full traceback was written to:\n{log_path}",
    )


def main(argv: list[str] | None = None) -> int:
    """Run TagExplorer."""
    args = list(sys.argv if argv is None else argv)

    if "--apply-update" in args:
        return apply_update_and_relaunch(args)

    if "--healthcheck" in args:
        print("healthcheck:ok")
        return 0

    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

    from PyQt6.QtWidgets import QApplication

    app = QApplication(args)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setApplicationVersion(APP_VERSION)

    log_path = _setup_logging()
    logging.info("Starting %s %s", APP_NAME, APP_VERSION)

    try:
        from ui.main_window import MainWindow

        window = MainWindow()
    except Exception as exc:  # noqa: BLE001
        logging.exception("Startup failed")
        _show_startup_error(exc, log_path)
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
