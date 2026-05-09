"""Safer filesystem operations for UI actions."""

from __future__ import annotations

import errno
import os
import stat as stat_module
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


INVALID_FILENAME_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
MAX_WINDOWS_LEGACY_PATH = 260


class FileOperationError(OSError):
    """Base class for user-facing filesystem operation failures."""


class FilenameValidationError(ValueError):
    """Raised when a filename cannot be used safely."""


class FileChangedError(FileOperationError):
    """Raised when the selected file no longer matches the scan snapshot."""


class TargetExistsError(FileOperationError):
    """Raised when a rename target already exists."""


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """A small lstat snapshot used to detect stale scan records."""

    size: int | None = None
    modified_time_ns: int | None = None
    device_id: int | None = None
    inode: int | None = None
    mode: int | None = None
    is_symlink: bool | None = None
    is_reparse_point: bool | None = None

    @classmethod
    def from_stat(cls, stat_result: os.stat_result) -> "FileIdentity":
        return cls(
            size=getattr(stat_result, "st_size", None),
            modified_time_ns=getattr(
                stat_result,
                "st_mtime_ns",
                int(getattr(stat_result, "st_mtime", 0) * 1_000_000_000),
            ),
            device_id=getattr(stat_result, "st_dev", None),
            inode=getattr(stat_result, "st_ino", None),
            mode=getattr(stat_result, "st_mode", None),
            is_symlink=stat_module.S_ISLNK(stat_result.st_mode),
            is_reparse_point=_is_windows_reparse_point(stat_result),
        )

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "FileIdentity | None":
        if not record:
            return None
        return cls(
            size=_optional_int(record.get("size")),
            modified_time_ns=_optional_int(record.get("modified_time_ns")),
            device_id=_optional_int(record.get("device_id")),
            inode=_optional_int(record.get("inode")),
            mode=_optional_int(record.get("mode")),
            is_symlink=_optional_bool(record.get("is_symlink")),
            is_reparse_point=_optional_bool(record.get("is_reparse_point")),
        )

    def matches(self, current: "FileIdentity") -> bool:
        comparisons = (
            (self.device_id, current.device_id),
            (self.inode, current.inode),
            (self.size, current.size),
            (self.modified_time_ns, current.modified_time_ns),
            (self.mode, current.mode),
            (self.is_symlink, current.is_symlink),
            (self.is_reparse_point, current.is_reparse_point),
        )
        known = [(expected, actual) for expected, actual in comparisons if expected is not None]
        if not known:
            return True
        return all(expected == actual for expected, actual in known)


@dataclass(frozen=True, slots=True)
class RenameOperation:
    source: Path
    target: Path
    expected: FileIdentity | None = None


class BatchRenameError(FileOperationError):
    """Raised when a rename plan fails and rollback may be needed."""

    def __init__(
        self,
        message: str,
        *,
        failed_operation: RenameOperation | None = None,
        rollback_errors: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.failed_operation = failed_operation
        self.rollback_errors = rollback_errors or []


def validate_filename(name: str) -> None:
    """Validate a single path component for cross-platform Windows-safe use."""
    if not name:
        raise FilenameValidationError("Filename cannot be empty.")
    if name in {".", ".."}:
        raise FilenameValidationError("Filename cannot be '.' or '..'.")
    if any(ord(ch) < 32 for ch in name):
        raise FilenameValidationError("Filename cannot contain control characters.")
    invalid_chars = sorted(ch for ch in INVALID_FILENAME_CHARS if ch in name)
    if invalid_chars:
        raise FilenameValidationError(
            "Filename cannot contain: " + " ".join(invalid_chars)
        )
    if name != name.rstrip(" ."):
        raise FilenameValidationError("Filename cannot end with a space or dot.")

    reserved_base = name.split(".", 1)[0].rstrip(" .").upper()
    if reserved_base in WINDOWS_RESERVED_NAMES:
        raise FilenameValidationError(f"'{reserved_base}' is a reserved Windows name.")


def build_target_path(source: Path, new_name: str) -> Path:
    """Build and validate a rename target in the source directory."""
    validate_filename(new_name)
    try:
        target = source.with_name(new_name)
    except ValueError as exc:
        raise FilenameValidationError(str(exc)) from exc
    _validate_path_length(target)
    return target


def unique_path(target_path: Path, reserved_targets: set[str] | None = None) -> Path:
    """Return a validated non-existing sibling path not already reserved by a plan."""
    reserved_targets = reserved_targets or set()
    directory = target_path.parent
    stem = target_path.stem
    suffix = target_path.suffix
    counter = 2

    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        _validate_path_length(candidate)
        key = path_key(candidate)
        if key not in reserved_targets and not candidate.exists():
            return candidate
        counter += 1


def path_key(path: Path) -> str:
    """Return a collision key matching the platform's normal filename semantics."""
    normalized = os.path.normpath(os.fspath(path.absolute()))
    return os.path.normcase(normalized)


def ensure_current_regular_file(path: Path, expected: FileIdentity | None = None) -> FileIdentity:
    """Verify that path still points at the expected regular file."""
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise FileOperationError(f"File is no longer available: {exc}") from exc

    current = FileIdentity.from_stat(stat_result)
    if current.is_symlink:
        raise FileOperationError("Refusing to operate on a symbolic link.")
    if current.is_reparse_point:
        raise FileOperationError("Refusing to operate on a Windows reparse point.")
    if not stat_module.S_ISREG(stat_result.st_mode):
        raise FileOperationError("Selected path is no longer a regular file.")
    if expected is not None and not expected.matches(current):
        raise FileChangedError("File changed since it was scanned. Refresh and try again.")
    return current


def preflight_rename(operation: RenameOperation, reserved_targets: set[str] | None = None) -> None:
    """Validate one rename operation without changing the filesystem."""
    source = operation.source
    target = operation.target
    validate_filename(target.name)
    _validate_path_length(target)
    _ensure_same_directory(source, target)
    ensure_current_regular_file(source, operation.expected)

    source_key = path_key(source)
    target_key = path_key(target)
    if reserved_targets is not None and target_key in reserved_targets:
        raise TargetExistsError(f"Rename target is already used by this batch: {target.name}")

    if source_key == target_key:
        return

    try:
        target.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise FileOperationError(f"Could not check target '{target.name}': {exc}") from exc

    raise TargetExistsError(f"A file named '{target.name}' already exists.")


def safe_rename(operation: RenameOperation) -> Path:
    """Rename one regular file without overwriting another file."""
    source = operation.source
    target = operation.target
    preflight_rename(operation)

    if os.fspath(source) == os.fspath(target):
        return target

    source_key = path_key(source)
    target_key = path_key(target)
    case_only = source_key == target_key and source.name != target.name

    try:
        if os.name == "nt":
            if case_only:
                _rename_case_only_windows(source, target)
            else:
                os.rename(source, target)
        else:
            _rename_no_overwrite_posix(source, target)
    except FileExistsError as exc:
        raise TargetExistsError(f"A file named '{target.name}' already exists.") from exc
    except OSError as exc:
        raise FileOperationError(f"Could not rename file: {exc}") from exc

    ensure_current_regular_file(target, None)
    return target


def execute_rename_plan(operations: list[RenameOperation]) -> None:
    """Execute an all-or-rollback rename plan."""
    reserved_targets: set[str] = set()
    for operation in operations:
        preflight_rename(operation, reserved_targets)
        source_key = path_key(operation.source)
        target_key = path_key(operation.target)
        if source_key != target_key:
            reserved_targets.add(target_key)

    completed: list[RenameOperation] = []
    try:
        for operation in operations:
            safe_rename(operation)
            completed.append(operation)
    except FileOperationError as exc:
        rollback_errors: list[str] = []
        for done in reversed(completed):
            rollback = RenameOperation(done.target, done.source, None)
            try:
                safe_rename(rollback)
            except FileOperationError as rollback_exc:
                rollback_errors.append(f"{done.target.name}: {rollback_exc}")
        raise BatchRenameError(
            str(exc),
            failed_operation=operation,
            rollback_errors=rollback_errors,
        ) from exc


def safe_delete(path: Path, expected: FileIdentity | None = None) -> None:
    """Delete one regular file, refusing stale paths and directory-like objects."""
    ensure_current_regular_file(path, expected)
    try:
        if os.name == "nt":
            _delete_to_recycle_bin_windows(path)
        else:
            path.unlink()
    except OSError as exc:
        raise FileOperationError(f"Could not delete file: {exc}") from exc

    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    raise FileOperationError("Delete operation reported success, but the file still exists.")


def _rename_case_only_windows(source: Path, target: Path) -> None:
    temp = _temporary_sibling_path(source)
    os.rename(source, temp)
    try:
        os.rename(temp, target)
    except OSError:
        try:
            os.rename(temp, source)
        except OSError:
            pass
        raise


def _rename_no_overwrite_posix(source: Path, target: Path) -> None:
    try:
        os.link(source, target, follow_symlinks=False)
    except TypeError:
        os.link(source, target)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FileExistsError(os.fspath(target)) from exc
        raise

    try:
        source.unlink()
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def _temporary_sibling_path(source: Path) -> Path:
    for _ in range(100):
        candidate = source.with_name(f".texplorer-rename-{uuid.uuid4().hex}.tmp")
        if not candidate.exists():
            _validate_path_length(candidate)
            return candidate
    raise FileOperationError("Could not allocate a temporary rename path.")


def _delete_to_recycle_bin_windows(path: Path) -> None:
    from ctypes import wintypes
    import ctypes

    shell32 = ctypes.windll.shell32
    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_NOERRORUI = 0x0400
    FOF_SILENT = 0x0004

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", ctypes.c_uint),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    source = str(path) + "\0\0"
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = source
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    result = shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(f"Windows delete failed (code {result}).")
    if op.fAnyOperationsAborted:
        raise OSError("Delete operation was cancelled.")


def _ensure_same_directory(source: Path, target: Path) -> None:
    if path_key(source.parent) != path_key(target.parent):
        raise FileOperationError("Rename target must stay in the same directory.")


def _validate_path_length(path: Path) -> None:
    if os.name != "nt":
        return
    absolute = os.path.abspath(os.fspath(path))
    if len(absolute) >= MAX_WINDOWS_LEGACY_PATH:
        raise FilenameValidationError(
            f"Path is too long for Windows shell operations ({len(absolute)} characters)."
        )


def _is_windows_reparse_point(stat_result: os.stat_result) -> bool:
    if os.name != "nt":
        return False
    attrs = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(attrs & reparse_flag)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
