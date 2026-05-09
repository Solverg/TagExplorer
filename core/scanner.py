"""Directory scanning utilities for TagExplorer."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import stat as stat_module
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from core.tags import TAG_PATTERN, parse_tags

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mkv", ".mov", ".wmv"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"})
DOCUMENT_EXTENSIONS = frozenset(
    {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx"}
)
CACHE_TTL_SECONDS = 10 * 60
SCAN_BATCH_SIZE = 1000

# System and technical directories that are almost always noise for media-oriented scans.
IGNORED_DIRS = frozenset(
    {
        "$RECYCLE.BIN",
        "System Volume Information",
        "Windows",
        "Program Files",
        "Program Files (x86)",
        "ProgramData",
        "AppData",
        "Recovery",
        ".Trash",
        ".Trashes",
        "lost+found",
        ".cache",
        "sys",
        "proc",
        "dev",
        ".git",
        ".svn",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
    }
)


@dataclass(slots=True)
class FileRecord:
    """Memory-efficient file record used by deep recursive scans."""

    path: str
    filename: str
    tags: list[str]
    modified_time: float
    file_type: str
    size: int | None = None
    modified_time_ns: int | None = None
    device_id: int | None = None
    inode: int | None = None
    mode: int | None = None
    is_symlink: bool = False
    is_reparse_point: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FileScanner:
    """Scans folders and extracts filename tags and file metadata."""

    def __init__(self, cache_dir: Path | None = None, cache_ttl_seconds: int = CACHE_TTL_SECONDS):
        self.cache_dir = cache_dir or Path.home() / ".texplorer" / "cache"
        self.cache_ttl_seconds = cache_ttl_seconds
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create cache directory %s: %s", self.cache_dir, exc)

    @staticmethod
    def parse_tags(filename: str) -> list[str]:
        """Parse tags from a filename using square-bracket syntax."""
        return parse_tags(filename)

    @staticmethod
    def detect_file_type(path_or_name: Path | str) -> str:
        """Detect coarse file type category from extension."""
        ext = Path(path_or_name).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            return "image"
        if ext in VIDEO_EXTENSIONS:
            return "video"
        if ext in AUDIO_EXTENSIONS:
            return "audio"
        if ext in DOCUMENT_EXTENSIONS:
            return "document"
        return "other"

    def _record_from_entry(self, entry: os.DirEntry[str]) -> FileRecord | None:
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            return None
        if self._is_windows_reparse_point(stat):
            return None

        return FileRecord(
            path=Path(entry.path).resolve().as_posix(),
            filename=entry.name,
            tags=self.parse_tags(entry.name),
            modified_time=stat.st_mtime,
            file_type=self.detect_file_type(entry.name),
            size=stat.st_size,
            modified_time_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            device_id=getattr(stat, "st_dev", None),
            inode=getattr(stat, "st_ino", None),
            mode=getattr(stat, "st_mode", None),
            is_symlink=entry.is_symlink(),
            is_reparse_point=self._is_windows_reparse_point(stat),
        )

    def _get_cache_filepath(self, folder: Path) -> Path:
        """Return stable cache path for recursive scans."""
        resolved = str(folder.resolve())
        safe_name = resolved.replace(":", "").replace("\\", "_").replace("/", "_")
        if len(safe_name) > 200:
            import hashlib

            safe_name = hashlib.md5(resolved.encode("utf-8")).hexdigest()
        return self.cache_dir / f"scan_{safe_name}.json"

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        """Write JSON atomically to avoid corrupted cache files."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    @staticmethod
    def _is_windows_reparse_point(stat_result: os.stat_result) -> bool:
        if os.name != "nt":
            return False
        attrs = getattr(stat_result, "st_file_attributes", 0)
        reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        return bool(attrs & reparse_flag)

    @classmethod
    def _entry_is_reparse_point(cls, entry: os.DirEntry[str]) -> bool:
        try:
            return cls._is_windows_reparse_point(entry.stat(follow_symlinks=False))
        except OSError:
            return True

    @classmethod
    def _directory_snapshot(cls, folder: Path) -> dict[str, Any]:
        stat_result = folder.stat(follow_symlinks=False)
        return {
            "path": str(folder.resolve()),
            "modified_time_ns": getattr(
                stat_result,
                "st_mtime_ns",
                int(stat_result.st_mtime * 1_000_000_000),
            ),
            "device_id": getattr(stat_result, "st_dev", None),
            "inode": getattr(stat_result, "st_ino", None),
            "mode": getattr(stat_result, "st_mode", None),
            "is_reparse_point": cls._is_windows_reparse_point(stat_result),
        }

    @classmethod
    def _is_valid_scan_root(cls, folder: Path) -> bool:
        try:
            stat_result = folder.stat(follow_symlinks=False)
        except OSError:
            return False
        if stat_module.S_ISLNK(stat_result.st_mode):
            return False
        if cls._is_windows_reparse_point(stat_result):
            return False
        return stat_module.S_ISDIR(stat_result.st_mode)

    def _cache_is_valid(self, folder: Path, cache_data: dict[str, Any]) -> bool:
        scanned_at = cache_data.get("scanned_at")
        if not isinstance(scanned_at, (int, float)):
            return False
        if time.time() - float(scanned_at) > self.cache_ttl_seconds:
            return False

        cached_root = cache_data.get("root_snapshot")
        if not isinstance(cached_root, dict):
            return False

        try:
            current_root = self._directory_snapshot(folder)
        except OSError:
            return False

        keys = ("path", "modified_time_ns", "device_id", "inode", "mode", "is_reparse_point")
        return all(cached_root.get(key) == current_root.get(key) for key in keys)

    def scan_directory(
        self,
        folder: Path,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Scan only direct children of `folder` and return file records."""
        records: list[dict[str, Any]] = []
        for batch in self.iter_scan_directory(folder, should_cancel=should_cancel):
            records.extend(batch)
        return records

    def iter_scan_directory(
        self,
        folder: Path,
        batch_size: int = SCAN_BATCH_SIZE,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Scan direct file children and yield records in bounded batches."""
        records: list[dict[str, Any]] = []
        if not self._is_valid_scan_root(folder):
            return

        try:
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    if should_cancel and should_cancel():
                        break
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    record = self._record_from_entry(entry)
                    if record is None:
                        continue
                    records.append(record.to_dict())
                    if len(records) >= batch_size:
                        yield records
                        records = []
        except OSError as exc:
            logger.warning("Could not scan directory %s: %s", folder, exc)

        if records:
            yield records

    def deep_scan_directory(
        self,
        folder: Path,
        force_rescan: bool = False,
        progress_callback: Callable[[int, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Safely scan a directory tree recursively with cache support."""
        records: list[dict[str, Any]] = []
        for batch in self.iter_deep_scan_directory(
            folder,
            force_rescan=force_rescan,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        ):
            records.extend(batch)
        return records

    def iter_deep_scan_directory(
        self,
        folder: Path,
        force_rescan: bool = False,
        progress_callback: Callable[[int, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        batch_size: int = SCAN_BATCH_SIZE,
    ) -> Iterator[list[dict[str, Any]]]:
        """Safely scan a directory tree recursively and yield records in batches."""
        if not self._is_valid_scan_root(folder):
            logger.error("Directory does not exist or is not a dir: %s", folder)
            return

        cache_file = self._get_cache_filepath(folder)

        if not force_rescan and cache_file.exists():
            try:
                if should_cancel and should_cancel():
                    return
                with cache_file.open("r", encoding="utf-8") as handle:
                    cache_data = json.load(handle)
                if not self._cache_is_valid(folder, cache_data):
                    raise ValueError("cache is stale")
                cached_records = cache_data.get("records", [])
                if should_cancel and should_cancel():
                    return
                batch: list[dict[str, Any]] = []
                for record in cached_records:
                    if should_cancel and should_cancel():
                        return
                    batch.append(FileRecord(**record).to_dict())
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                if batch:
                    yield batch
                return
            except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
                logger.warning("Cache load failed for %s, rescanning: %s", folder, exc)

        all_records: list[dict[str, Any]] = []
        batch: list[dict[str, Any]] = []
        queue = deque([str(folder.resolve())])
        visited_dir_ids: set[tuple[int, int]] = set()
        scanned_files_count = 0

        start = time.perf_counter()

        while queue:
            if should_cancel and should_cancel():
                return

            current_dir = queue.popleft()
            if progress_callback:
                progress_callback(scanned_files_count, current_dir)

            try:
                stat = os.stat(current_dir, follow_symlinks=False)
            except OSError:
                continue

            dir_id = (stat.st_dev, stat.st_ino)
            if dir_id in visited_dir_ids:
                continue
            visited_dir_ids.add(dir_id)

            try:
                with os.scandir(current_dir) as iterator:
                    for entry in iterator:
                        if should_cancel and should_cancel():
                            return []
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                                    continue
                                if self._entry_is_reparse_point(entry):
                                    continue
                                queue.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                record = self._record_from_entry(entry)
                                if record is None:
                                    continue
                                record_dict = record.to_dict()
                                all_records.append(record_dict)
                                batch.append(record_dict)
                                scanned_files_count += 1
                                if len(batch) >= batch_size:
                                    yield batch
                                    batch = []
                        except OSError:
                            continue
            except OSError:
                continue

        elapsed = time.perf_counter() - start
        logger.info("Deep scan finished in %.2fs, %d files", elapsed, len(all_records))

        if should_cancel and should_cancel():
            return

        if batch:
            yield batch

        try:
            payload = {
                "scanned_at": time.time(),
                "root_folder": str(folder.resolve()),
                "root_snapshot": self._directory_snapshot(folder),
                "records": all_records,
            }
            self._atomic_write_json(cache_file, payload)
        except OSError as exc:
            logger.error("Failed to save cache for %s: %s", folder, exc)
