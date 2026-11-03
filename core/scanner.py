"""Directory scanning utilities for TagExplorer."""

from __future__ import annotations

import logging
import os
import time
import stat as stat_module
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from core.tags import parse_tags

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mkv", ".mov", ".wmv"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"})
DOCUMENT_EXTENSIONS = frozenset(
    {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx"}
)
CACHE_TTL_SECONDS = 10 * 60
SCAN_BATCH_SIZE = 1000

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
        # Retained for callers that used the former recursive JSON cache.
        self.cache_dir = cache_dir or Path.home() / ".texplorer" / "cache"
        self.cache_ttl_seconds = cache_ttl_seconds

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

    def _record_from_entry(self, entry: os.DirEntry[str],
                           on_error: Callable[[str], None] | None = None) -> FileRecord | None:
        try:
            # On Windows DirEntry.stat() can report zero for st_dev and st_ino.
            # Fetch the same identity that file operations later obtain via lstat.
            stat = Path(entry.path).lstat() if os.name == "nt" else entry.stat(follow_symlinks=False)
        except OSError as exc:
            if on_error:
                on_error(f"Cannot inspect '{entry.path}': {exc}")
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

    def iter_scan_folders(self, folder: Path, recursive: bool = False,
                          should_cancel: Callable[[], bool] | None = None,
                          batch_size: int = SCAN_BATCH_SIZE,
                          on_error: Callable[[str], None] | None = None) -> Iterator[list[dict[str, Any]]]:
        """Return directories only, never reading tags from files or following links."""
        if not self._is_valid_scan_root(folder):
            if on_error:
                on_error(f"Cannot scan '{folder}': folder is unavailable or is a link.")
            return
        queue = deque([str(folder.resolve())])
        batch: list[dict[str, Any]] = []
        while queue:
            if should_cancel and should_cancel():
                return
            current = queue.popleft()
            if not self._is_valid_scan_root(Path(current)):
                if on_error:
                    on_error(f"Cannot scan '{current}': folder is unavailable or is a link.")
                continue
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if should_cancel and should_cancel():
                            return
                        try:
                            if not entry.is_dir(follow_symlinks=False) or self._entry_is_reparse_point(entry, on_error):
                                continue
                            record = self._record_from_entry(entry, on_error)
                            if record is None:
                                continue
                            item = record.to_dict()
                            item['file_type'] = 'folder'
                            item['is_directory'] = True
                            batch.append(item)
                            if recursive:
                                queue.append(entry.path)
                            if len(batch) >= batch_size:
                                yield batch
                                batch = []
                        except OSError as exc:
                            if on_error:
                                on_error(f"Cannot inspect '{entry.path}': {exc}")
                            continue
            except OSError as exc:
                logger.warning("Could not scan directory %s: %s", current, exc)
                if on_error:
                    on_error(f"Cannot scan '{current}': {exc}")
        if batch:
            yield batch

    @staticmethod
    def _is_windows_reparse_point(stat_result: os.stat_result) -> bool:
        if os.name != "nt":
            return False
        attrs = getattr(stat_result, "st_file_attributes", 0)
        reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        return bool(attrs & reparse_flag)

    @classmethod
    def _entry_is_reparse_point(cls, entry: os.DirEntry[str],
                                on_error: Callable[[str], None] | None = None) -> bool:
        try:
            return cls._is_windows_reparse_point(entry.stat(follow_symlinks=False))
        except OSError as exc:
            if on_error:
                on_error(f"Cannot inspect '{entry.path}': {exc}")
            return True

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
        """Safely scan a directory tree recursively."""
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
        """Scan the tree in batches; force_rescan is retained for compatibility."""
        if not self._is_valid_scan_root(folder):
            logger.error("Directory does not exist or is not a dir: %s", folder)
            return

        # A root directory timestamp cannot reveal changes deeper in the tree
        # or changes to file contents. Always scan to return current identities.
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
                                if self._entry_is_reparse_point(entry):
                                    continue
                                queue.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                record = self._record_from_entry(entry)
                                if record is None:
                                    continue
                                record_dict = record.to_dict()
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
        logger.info("Deep scan finished in %.2fs, %d files", elapsed, scanned_files_count)

        if should_cancel and should_cancel():
            return

        if batch:
            yield batch
