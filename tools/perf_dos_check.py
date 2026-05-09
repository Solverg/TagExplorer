from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import tracemalloc
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _install_qstandardpaths_stub(appdata_dir: Path) -> None:
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _StandardLocation:
        AppDataLocation = "AppDataLocation"

    class _QStandardPaths:
        StandardLocation = _StandardLocation

        @staticmethod
        def writableLocation(_location: object) -> str:
            appdata_dir.mkdir(parents=True, exist_ok=True)
            return str(appdata_dir)

    qtcore.QStandardPaths = _QStandardPaths
    sys.modules.setdefault("PyQt6", pyqt6)
    sys.modules["PyQt6.QtCore"] = qtcore


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def working_set_mb() -> float | None:
    if os.name != "nt":
        return None
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    psapi = ctypes.WinDLL("psapi.dll")
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        return None
    return counters.WorkingSetSize / 1024 / 1024


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            pass
    return total


def file_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                count += 1
        except OSError:
            pass
    return count


@contextmanager
def measured(name: str, results: dict[str, Any]):
    tracemalloc.start()
    start_ws = working_set_mb()
    start = time.perf_counter()
    error = None
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        elapsed = time.perf_counter() - start
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        end_ws = working_set_mb()
        results[name] = {
            "seconds": round(elapsed, 3),
            "python_peak_mb": round(peak / 1024 / 1024, 2),
            "working_set_delta_mb": (
                round(end_ws - start_ws, 2)
                if start_ws is not None and end_ws is not None
                else None
            ),
            "error": error,
        }


def ensure_flat_files(root: Path, count: int, exts: list[str], *, tags: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    existing = file_count(root)
    if existing >= count:
        return
    tag_values = ["projectA", "urgent", "review", "archive", "media", "doc"]
    for i in range(existing, count):
        tag_text = ""
        if tags:
            tag_text = "".join(f"[{random.choice(tag_values)}]" for _ in range(random.randint(0, 2)))
        name = f"{tag_text} test_file_{i}{exts[i % len(exts)]}"
        (root / name).touch(exist_ok=True)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
JPEG_THUMB = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


def ensure_png_files(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    existing = file_count(root)
    if existing >= count:
        return
    for i in range(existing, count):
        (root / f"[thumb] image_{i:05d}.png").write_bytes(PNG_1X1)


def ensure_unique_extensions(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    existing = file_count(root)
    if existing >= count:
        return
    for i in range(existing, count):
        (root / f"unique_{i:05d}.x{i:05d}").touch(exist_ok=True)


def ensure_long_names(root: Path, count: int) -> dict[str, int]:
    root.mkdir(parents=True, exist_ok=True)
    made = 0
    failed = 0
    prefix = "[longname] "
    for i in range(count):
        stem = prefix + f"{i:04d}_" + ("a" * 218)
        try:
            (root / f"{stem}.txt").touch(exist_ok=True)
            made += 1
        except OSError:
            failed += 1
    return {"created": made, "failed": failed}


def ensure_deep_tree(root: Path, max_depth: int) -> dict[str, int]:
    if root.exists():
        return {"created_depth": sum(1 for _ in root.rglob("marker.txt")), "failed_at": 0}
    current = root
    created_depth = 0
    failed_at = 0
    for depth in range(max_depth):
        current = current / f"d{depth:03d}"
        try:
            current.mkdir(parents=True, exist_ok=True)
            if depth % 10 == 0:
                (current / "marker.txt").touch(exist_ok=True)
            created_depth = depth + 1
        except OSError:
            failed_at = depth + 1
            break
    return {"created_depth": created_depth, "failed_at": failed_at}


def ensure_permission_tree(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    allowed = root / "allowed"
    blocked = root / "blocked"
    allowed.mkdir(exist_ok=True)
    blocked.mkdir(exist_ok=True)
    (allowed / "visible.txt").touch(exist_ok=True)
    (blocked / "hidden.txt").touch(exist_ok=True)

    if os.name != "nt":
        blocked.chmod(0)
        return {"mode": "chmod", "blocked": str(blocked)}

    user = os.environ.get("USERNAME")
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if user and domain else user
    if not principal:
        return {"mode": "skipped", "reason": "no Windows principal"}
    result = subprocess.run(
        ["icacls", str(blocked), "/deny", f"{principal}:(OI)(CI)F"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "mode": "icacls",
        "blocked": str(blocked),
        "principal": principal,
        "returncode": result.returncode,
        "stderr": result.stderr.strip(),
        "stdout": result.stdout.strip(),
    }


def restore_permission_tree(root: Path, info: dict[str, Any]) -> None:
    blocked = Path(info.get("blocked", ""))
    if not blocked:
        return
    try:
        if info.get("mode") == "chmod":
            blocked.chmod(0o700)
        elif info.get("mode") == "icacls" and info.get("principal"):
            subprocess.run(
                ["icacls", str(blocked), "/remove:d", str(info["principal"])],
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except OSError:
        pass


class SlowScandir:
    def __init__(self, iterator: Any, delay_seconds: float) -> None:
        self._iterator = iterator
        self._delay_seconds = delay_seconds

    def __enter__(self) -> "SlowScandir":
        self._iterator.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        return self._iterator.__exit__(*args)

    def __iter__(self) -> "SlowScandir":
        return self

    def __next__(self) -> os.DirEntry[str]:
        time.sleep(self._delay_seconds)
        return next(self._iterator)


def run_scan_case(
    label: str,
    folder: Path,
    scanner: Any,
    cache_factory: Callable[[], Any],
    results: dict[str, Any],
    *,
    recursive: bool,
    use_sqlite: bool = True,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with measured(f"{label}.scan", results):
        if recursive:
            records = scanner.deep_scan_directory(folder, force_rescan=True, should_cancel=should_cancel)
        else:
            records = scanner.scan_directory(folder, should_cancel=should_cancel)
    results[f"{label}.records"] = len(records)

    if use_sqlite and records:
        with measured(f"{label}.sqlite_update", results):
            cache = cache_factory()
            try:
                cache.update_files_batch(records)
                if recursive:
                    cache.delete_missing_tree(folder, {r["path"] for r in records})
                else:
                    cache.delete_missing(folder, {r["path"] for r in records})
            finally:
                cache.close()
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="C:/tmp/texplorer_perf")
    parser.add_argument("--include-100k", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    base = Path(args.base)
    base.mkdir(parents=True, exist_ok=True)
    _install_qstandardpaths_stub(base / "appdata")

    from core.cache import FileCache  # noqa: PLC0415
    from core.filter_engine import filter_files  # noqa: PLC0415
    from core.scanner import FileScanner  # noqa: PLC0415
    from core.tags import normalize_tags, sort_tags  # noqa: PLC0415

    scanner = FileScanner(cache_dir=base / "json_scan_cache", cache_ttl_seconds=3600)
    results: dict[str, Any] = {
        "base": str(base),
        "repo": str(REPO_ROOT),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "notes": [],
    }

    stress_50k = Path("C:/tmp/STRESS_TEST_50K")
    if not stress_50k.exists():
        results["notes"].append("C:/tmp/STRESS_TEST_50K was missing; generating equivalent 50k set.")
        ensure_flat_files(stress_50k, 50_000, [".txt", ".jpg", ".png", ".mp4", ".pdf", ".docx"])
    records_50k = run_scan_case("50k_stress_test_py", stress_50k, scanner, FileCache, results, recursive=False)

    if args.include_100k:
        files_100k = base / "files_100k"
        with measured("100k.generate_or_reuse", results):
            ensure_flat_files(files_100k, 100_000, [".txt", ".jpg", ".png", ".mp4", ".pdf", ".docx"])
        records_100k = run_scan_case("100k_flat", files_100k, scanner, FileCache, results, recursive=False)
    else:
        records_100k = []

    png_10k = base / "png_10k"
    with measured("10k_png.generate_or_reuse", results):
        ensure_png_files(png_10k, 10_000)
    records_png = run_scan_case("10k_png", png_10k, scanner, FileCache, results, recursive=False)
    with measured("10k_png.synthetic_thumbnail_cache_update", results):
        cache = FileCache()
        try:
            for record in records_png:
                cache.update_thumbnail(record["path"], JPEG_THUMB)
        finally:
            cache.close()

    videos = base / "videos_20k"
    with measured("20k_video.generate_or_reuse", results):
        ensure_flat_files(videos, 20_000, [".mp4", ".mkv", ".mov", ".avi"], tags=False)
    run_scan_case("20k_video", videos, scanner, FileCache, results, recursive=False)

    unique_ext = base / "unique_extensions_10k"
    with measured("10k_unique_extensions.generate_or_reuse", results):
        ensure_unique_extensions(unique_ext, 10_000)
    run_scan_case("10k_unique_extensions", unique_ext, scanner, FileCache, results, recursive=False)

    long_names = base / "long_names"
    with measured("long_names.generate_or_reuse", results):
        results["long_names.generation"] = ensure_long_names(long_names, 1_000)
    run_scan_case("long_names", long_names, scanner, FileCache, results, recursive=False)

    deep_root = base / "deep_tree"
    with measured("deep.generate_or_reuse", results):
        results["deep.generation"] = ensure_deep_tree(deep_root, 240)
    run_scan_case("deep", deep_root, scanner, FileCache, results, recursive=True)

    permission_root = base / "permission_denied"
    permission_info = ensure_permission_tree(permission_root)
    results["permission_denied.setup"] = permission_info
    try:
        run_scan_case("permission_denied", permission_root, scanner, FileCache, results, recursive=True)
    finally:
        restore_permission_tree(permission_root, permission_info)

    slow_root = base / "slow_folder"
    ensure_flat_files(slow_root, 2_000, [".txt"], tags=False)
    cancel = threading.Event()
    real_scandir = os.scandir

    def slow_scandir(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> SlowScandir:
        return SlowScandir(real_scandir(path), 0.002)

    try:
        import core.scanner as scanner_module  # noqa: PLC0415

        scanner_module.os.scandir = slow_scandir
        timer = threading.Timer(0.25, cancel.set)
        timer.start()
        run_scan_case(
            "slow_folder_cancelled",
            slow_root,
            scanner,
            FileCache,
            results,
            recursive=False,
            use_sqlite=False,
            should_cancel=cancel.is_set,
        )
        timer.cancel()
    finally:
        import core.scanner as scanner_module  # noqa: PLC0415

        scanner_module.os.scandir = real_scandir

    ui_records = records_100k or records_50k
    with measured("main_thread.sort_tags_on_scan_complete", results):
        _tags = sort_tags(tag for record in ui_records for tag in normalize_tags(record.get("tags", [])))
    with measured("main_thread.apply_filter_no_selection", results):
        tagged, untagged = filter_files(ui_records, [], "OR", None)
        results["main_thread.apply_filter_no_selection.counts"] = {
            "tagged": len(tagged),
            "untagged": len(untagged),
        }
    with measured("main_thread.apply_filter_single_tag", results):
        filter_files(ui_records, ["urgent"], "OR", None)

    db_dir = base / "appdata"
    json_cache_dir = base / "json_scan_cache"
    results["sqlite_db_dir_bytes"] = dir_size(db_dir)
    results["sqlite_db_files"] = {str(path.name): path.stat().st_size for path in db_dir.glob("*") if path.is_file()}
    results["json_scan_cache_bytes"] = dir_size(json_cache_dir)
    results["json_scan_cache_files"] = {
        str(path.name): path.stat().st_size for path in json_cache_dir.glob("*") if path.is_file()
    }
    results["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    report = Path(args.report) if args.report else base / "perf_results.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"REPORT={report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
