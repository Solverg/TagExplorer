"""SQLite-backed cache for scanned file metadata with optional SQLCipher support."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import threading
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from PyQt6.QtCore import QStandardPaths

from core.tags import normalize_tags

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3
BUSY_TIMEOUT_MS = 5000
THUMBNAIL_CACHE_MAX_BYTES = 5 * 1024 * 1024 * 1024
THUMBNAIL_CACHE_MAX_ROWS = 500_000
THUMBNAIL_PRUNE_INTERVAL_SECONDS = 60
THUMBNAIL_ACCESS_TOUCH_INTERVAL_SECONDS = 5 * 60
CacheRecordRow = tuple[
    str,
    str,
    str,
    str,
    float,
    str,
    int | None,
    int | None,
    int | None,
    int | None,
]

try:
    from sqlcipher3 import dbapi2 as sqlite_engine

    HAS_CIPHER = True
except ImportError:
    sqlite_engine = None

if sqlite_engine is None:
    import sqlite3 as sqlite_engine

    HAS_CIPHER = False


def _get_db_path() -> Path:
    """Return the path to the SQLite database file."""
    location = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    if not location:
        app_data = Path.home() / ".tagexplorer"
    else:
        app_data = Path(location)

    app_data.mkdir(parents=True, exist_ok=True)
    _restrict_path_permissions(app_data, is_dir=True)
    return app_data / "tagexplorer.db"


def _restrict_path_permissions(path: Path, *, is_dir: bool) -> None:
    """Best-effort owner-only permissions for cache paths."""
    try:
        if os.name == "nt":
            _restrict_windows_path(path, is_dir=is_dir)
        else:
            path.chmod(stat.S_IRWXU if is_dir else stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.warning("Could not restrict cache permissions for %s: %s", path, exc)


def _restrict_windows_path(path: Path, *, is_dir: bool) -> None:
    """Use icacls when available to remove inherited access for cache files."""
    icacls = shutil.which("icacls")
    if icacls is None:
        logger.warning("icacls is unavailable; cache permissions were not hardened")
        return

    user = os.environ.get("USERNAME")
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if user and domain else user
    if not principal:
        logger.warning("Could not determine Windows user for cache ACL hardening")
        return

    permission = "(OI)(CI)F" if is_dir else "F"
    result = subprocess.run(
        [
            icacls,
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{principal}:{permission}",
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        logger.warning(
            "Could not harden cache ACL for %s: %s",
            path,
            (result.stderr or result.stdout).strip(),
        )


def _quote_sqlcipher_key(key: str) -> str:
    """Return a SQL literal for SQLCipher PRAGMA key."""
    return "'" + key.replace("'", "''") + "'"


class CacheDatabaseError(RuntimeError):
    """Raised when cache storage cannot be opened or recovered."""


class FileCache:
    """Stores and retrieves scan metadata and thumbnails in a local database."""

    # This shared key is intentionally kept for compatibility with existing caches.
    _STATIC_KEY = "TagExplorer_Universal_Secret_2024_Shared"

    _plaintext_warning_emitted = False
    _recovery_lock = threading.RLock()

    def __init__(self) -> None:
        self.db_path = _get_db_path()
        self._local = threading.local()
        self.security_warnings: list[str] = []
        self.encryption_enabled = HAS_CIPHER
        self._last_thumbnail_prune = 0.0
        if not HAS_CIPHER:
            self._warn_plaintext_fallback()
        self._create_schema()

    def _warn_plaintext_fallback(self) -> None:
        message = (
            "SQLCipher is unavailable; TagExplorer cache will be stored unencrypted."
        )
        self.security_warnings.append(message)
        logger.warning(message)
        if not FileCache._plaintext_warning_emitted:
            warnings.warn(message, RuntimeWarning, stacklevel=2)
            FileCache._plaintext_warning_emitted = True

    def _conn(self) -> sqlite_engine.Connection:
        """Return (or create) a per-thread SQLite connection."""
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = self._open_connection(recover=True)
            self._local.connection = conn
        return conn

    def _open_connection(self, *, recover: bool) -> sqlite_engine.Connection:
        if HAS_CIPHER:
            return self._open_cipher_connection(recover=recover)

        try:
            conn = sqlite_engine.connect(str(self.db_path), timeout=BUSY_TIMEOUT_MS / 1000)
            conn.row_factory = sqlite_engine.Row
            self._configure_connection(conn)
            return conn
        except sqlite_engine.DatabaseError as exc:
            self._close_quietly(locals().get("conn"))
            if recover and self._is_recoverable_database_error(exc):
                self._recover_database(exc)
                return self._open_connection(recover=False)
            raise CacheDatabaseError(f"Could not open cache database: {exc}") from exc

    def _open_cipher_connection(self, *, recover: bool) -> sqlite_engine.Connection:
        last_error: sqlite_engine.DatabaseError | None = None
        compatibility_modes: tuple[int | None, ...] = (None, 3)

        for compatibility in compatibility_modes:
            try:
                conn = sqlite_engine.connect(
                    str(self.db_path),
                    timeout=BUSY_TIMEOUT_MS / 1000,
                )
                conn.row_factory = sqlite_engine.Row
                conn.execute("PRAGMA key = " + _quote_sqlcipher_key(self._STATIC_KEY))
                if compatibility is not None:
                    conn.execute(f"PRAGMA cipher_compatibility = {compatibility}")
                self._validate_cipher_key(conn)
                self._configure_connection(conn)
                if compatibility is not None:
                    logger.info(
                        "Opened cache database with SQLCipher compatibility=%s",
                        compatibility,
                    )
                    migrated = self._migrate_legacy_cipher_database(conn)
                    if migrated:
                        self._close_quietly(conn)
                        return self._open_cipher_connection(recover=recover)
                return conn
            except sqlite_engine.DatabaseError as exc:
                last_error = exc
                self._close_quietly(locals().get("conn"))

        if recover and last_error is not None and self._is_recoverable_database_error(last_error):
            self._recover_database(last_error)
            return self._open_cipher_connection(recover=False)

        raise CacheDatabaseError(f"Could not open cache database: {last_error}") from last_error

    def _configure_connection(self, conn: sqlite_engine.Connection) -> None:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        self._try_set_wal(conn)
        if self.db_path.exists():
            _restrict_path_permissions(self.db_path, is_dir=False)

    def _migrate_legacy_cipher_database(self, conn: sqlite_engine.Connection) -> bool:
        """Re-encrypt a SQLCipher 3-compatible cache into the current format."""
        if not self.db_path.exists():
            return False

        suffix = f".migrating.{uuid4().hex}.db"
        new_path = self.db_path.with_suffix(suffix)

        logger.info("Migrating cache database to current SQLCipher format")
        try:
            if new_path.exists():
                new_path.unlink()
            self._detach_quietly(conn, "migrated")
            conn.execute(
                "ATTACH DATABASE ? AS migrated KEY " + _quote_sqlcipher_key(self._STATIC_KEY),
                (str(new_path),),
            )
            conn.execute("SELECT sqlcipher_export('migrated')")
            conn.execute("DETACH DATABASE migrated")
            conn.commit()

            self._close_quietly(conn)
            with FileCache._recovery_lock:
                self._replace_database_with_migrated(new_path)
            logger.info("Cache database migrated to current SQLCipher format")
            return True
        except sqlite_engine.DatabaseError as exc:
            logger.warning("Could not migrate legacy SQLCipher cache: %s", exc)
            self._detach_quietly(conn, "migrated")
            self._delete_if_exists(new_path)
            return False
        except OSError as exc:
            self._delete_if_exists(new_path)
            raise CacheDatabaseError(f"Could not replace legacy SQLCipher cache: {exc}") from exc

    @staticmethod
    def _detach_quietly(conn: sqlite_engine.Connection, schema: str) -> None:
        try:
            conn.execute(f"DETACH DATABASE {schema}")
        except sqlite_engine.DatabaseError:
            pass

    def _replace_database_with_migrated(self, new_path: Path) -> None:
        self._delete_sidecars(self.db_path)
        self.db_path.unlink()
        new_path.replace(self.db_path)
        self._delete_sidecars(new_path)
        _restrict_path_permissions(self.db_path, is_dir=False)

    def _delete_sidecars(self, path: Path) -> None:
        self._delete_if_exists(Path(str(path) + "-wal"))
        self._delete_if_exists(Path(str(path) + "-shm"))

    @staticmethod
    def _delete_if_exists(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning("Could not delete temporary cache file %s: %s", path, exc)

    def _validate_cipher_key(self, conn: sqlite_engine.Connection) -> None:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()

    @staticmethod
    def _try_set_wal(conn: sqlite_engine.Connection) -> None:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite_engine.DatabaseError as exc:
            logger.debug("WAL is unavailable for cache database: %s", exc)

    @staticmethod
    def _close_quietly(conn: Any) -> None:
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _is_locked_database_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        return "database is locked" in text or "database table is locked" in text

    @staticmethod
    def _is_recoverable_database_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        recoverable_markers = (
            "file is not a database",
            "database disk image is malformed",
            "file is encrypted or is not a database",
            "hmac check failed",
            "error decrypting page",
            "cipher",
            "not an error",
        )
        return any(marker in text for marker in recoverable_markers)

    def _recover_database(self, exc: BaseException) -> None:
        with FileCache._recovery_lock:
            if not self.db_path.exists():
                return

            timestamp = time.strftime("%Y%m%d%H%M%S")
            corrupt_path = self.db_path.with_suffix(f".corrupt.{timestamp}.db")
            logger.warning(
                "Recovering cache database after %s; moving %s to %s",
                exc,
                self.db_path,
                corrupt_path,
            )
            try:
                self.db_path.replace(corrupt_path)
                self._move_sidecar_if_exists("-wal", corrupt_path.with_suffix(corrupt_path.suffix + "-wal"))
                self._move_sidecar_if_exists("-shm", corrupt_path.with_suffix(corrupt_path.suffix + "-shm"))
            except OSError as move_exc:
                raise CacheDatabaseError(
                    f"Cache database is unusable and could not be moved aside: {move_exc}"
                ) from move_exc

    def _move_sidecar_if_exists(self, suffix: str, target: Path) -> None:
        sidecar = Path(str(self.db_path) + suffix)
        if sidecar.exists():
            sidecar.replace(target)

    def _recover_and_recreate(self, exc: BaseException) -> None:
        with FileCache._recovery_lock:
            self.close()
            self._recover_database(exc)
            self._create_schema()

    @staticmethod
    def _rollback_quietly(conn: sqlite_engine.Connection) -> None:
        try:
            conn.rollback()
        except sqlite_engine.DatabaseError:
            pass

    @staticmethod
    def _normalize_tags(raw_tags: Any) -> list[str]:
        """Normalize stored tag payload into a list of strings."""
        return normalize_tags(raw_tags)

    def _load_tags(self, raw_tags: Any, *, path: str) -> list[str]:
        if raw_tags is None:
            return []
        try:
            decoded = json.loads(raw_tags)
        except (TypeError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring corrupt cached tags for %s: %s", path, exc)
            return []
        return self._normalize_tags(decoded)

    def _create_schema(self) -> None:
        """Create or migrate the cache schema."""
        conn = self._conn()
        try:
            self._migrate_schema(conn)
        except sqlite_engine.OperationalError as exc:
            if self._is_locked_database_error(exc):
                raise CacheDatabaseError(
                    "Cache database is locked by another TagExplorer process."
                ) from exc
            if self._is_recoverable_database_error(exc):
                self.close()
                self._recover_database(exc)
                conn = self._conn()
                self._migrate_schema(conn)
                return
            raise
        except sqlite_engine.DatabaseError as exc:
            if self._is_recoverable_database_error(exc):
                self.close()
                self._recover_database(exc)
                conn = self._conn()
                self._migrate_schema(conn)
                return
            raise

    def _migrate_schema(self, conn: sqlite_engine.Connection) -> None:
        conn.execute("BEGIN")
        try:
            self._ensure_files_table(conn)
            self._ensure_files_columns(conn)
            self._ensure_thumbnails_table(conn)
            self._ensure_thumbnail_columns(conn)
            self._migrate_inline_thumbnails(conn)
            self._delete_orphan_thumbnails(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_file_type ON files(file_type)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_thumbnails_last_accessed ON thumbnails(last_accessed)"
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _ensure_files_table(self, conn: sqlite_engine.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                folder TEXT NOT NULL COLLATE NOCASE,
                filename TEXT NOT NULL,
                tags TEXT NOT NULL,
                modified_time REAL NOT NULL,
                file_type TEXT NOT NULL,
                size INTEGER,
                modified_time_ns INTEGER,
                device_id INTEGER,
                inode INTEGER
            )
            """
        )

    def _ensure_files_columns(self, conn: sqlite_engine.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(files)")
        columns = {str(row["name"]).lower() for row in cursor.fetchall()}
        migrations = {
            "size": "ALTER TABLE files ADD COLUMN size INTEGER",
            "modified_time_ns": "ALTER TABLE files ADD COLUMN modified_time_ns INTEGER",
            "device_id": "ALTER TABLE files ADD COLUMN device_id INTEGER",
            "inode": "ALTER TABLE files ADD COLUMN inode INTEGER",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)

    def _ensure_thumbnails_table(self, conn: sqlite_engine.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS thumbnails (
                path TEXT PRIMARY KEY,
                thumbnail BLOB NOT NULL,
                bytes INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0,
                last_accessed REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
            )
            """
        )

    def _ensure_thumbnail_columns(self, conn: sqlite_engine.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(thumbnails)")
        columns = {str(row["name"]).lower() for row in cursor.fetchall()}
        migrations = {
            "bytes": "ALTER TABLE thumbnails ADD COLUMN bytes INTEGER NOT NULL DEFAULT 0",
            "created_at": "ALTER TABLE thumbnails ADD COLUMN created_at REAL NOT NULL DEFAULT 0",
            "last_accessed": "ALTER TABLE thumbnails ADD COLUMN last_accessed REAL NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)

        now = time.time()
        conn.execute("UPDATE thumbnails SET bytes = length(thumbnail) WHERE bytes = 0")
        conn.execute("UPDATE thumbnails SET created_at = ? WHERE created_at = 0", (now,))
        conn.execute("UPDATE thumbnails SET last_accessed = ? WHERE last_accessed = 0", (now,))

    def _migrate_inline_thumbnails(self, conn: sqlite_engine.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(files)")
        columns = {str(row["name"]).lower() for row in cursor.fetchall()}
        if "thumbnail" not in columns:
            return

        now = time.time()
        conn.execute(
            """
            INSERT OR REPLACE INTO thumbnails(path, thumbnail, bytes, created_at, last_accessed)
            SELECT path, thumbnail, length(thumbnail), ?, ?
            FROM files
            WHERE thumbnail IS NOT NULL
            """,
            (now, now),
        )

    def _delete_orphan_thumbnails(self, conn: sqlite_engine.Connection) -> None:
        conn.execute(
            """
            DELETE FROM thumbnails
            WHERE NOT EXISTS (
                SELECT 1
                FROM files
                WHERE files.path = thumbnails.path
            )
            """
        )

    def update_thumbnail(self, path: str, blob: bytes) -> None:
        """Update the thumbnail data for an existing file record."""
        self.update_thumbnails_batch([(path, blob)])

    def _update_thumbnail(self, path: str, blob: bytes, *, allow_recovery: bool) -> None:
        self._update_thumbnails_batch([(path, blob)], allow_recovery=allow_recovery)

    def update_thumbnails_batch(self, thumbnails: list[tuple[str, bytes]]) -> None:
        """Update thumbnail blobs in one transaction."""
        if not thumbnails:
            return
        self._update_thumbnails_batch(thumbnails, allow_recovery=True)

    def _update_thumbnails_batch(
        self,
        thumbnails: list[tuple[str, bytes]],
        *,
        allow_recovery: bool,
    ) -> None:
        conn = self._conn()
        now = time.time()
        data = [(blob, len(blob), now, now, path) for path, blob in thumbnails]
        try:
            conn.executemany(
                """
                INSERT INTO thumbnails(path, thumbnail, bytes, created_at, last_accessed)
                SELECT path, ?, ?, ?, ?
                FROM files
                WHERE path = ?
                ON CONFLICT(path) DO UPDATE SET
                    thumbnail = excluded.thumbnail,
                    bytes = excluded.bytes,
                    last_accessed = excluded.last_accessed
                """,
                data,
            )
            self._prune_thumbnail_cache_if_needed(conn, now=now)
            conn.commit()
        except sqlite_engine.OperationalError as exc:
            if self._is_locked_database_error(exc):
                logger.warning("Cache thumbnail update skipped because database is locked")
                conn.rollback()
                return
            if allow_recovery and self._is_recoverable_database_error(exc):
                self._rollback_quietly(conn)
                self._recover_and_recreate(exc)
                self._update_thumbnails_batch(thumbnails, allow_recovery=False)
                return
            raise
        except sqlite_engine.DatabaseError as exc:
            self._rollback_quietly(conn)
            if allow_recovery and self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                self._update_thumbnails_batch(thumbnails, allow_recovery=False)
                return
            raise

    def _prune_thumbnail_cache_if_needed(
        self,
        conn: sqlite_engine.Connection,
        *,
        now: float | None = None,
        force: bool = False,
    ) -> None:
        now = time.time() if now is None else now
        if not force and now - self._last_thumbnail_prune < THUMBNAIL_PRUNE_INTERVAL_SECONDS:
            return
        self._last_thumbnail_prune = now

        row = conn.execute("SELECT COUNT(*) AS count, COALESCE(SUM(bytes), 0) AS bytes FROM thumbnails").fetchone()
        row_count = int(row["count"] or 0)
        total_bytes = int(row["bytes"] or 0)
        if row_count <= THUMBNAIL_CACHE_MAX_ROWS and total_bytes <= THUMBNAIL_CACHE_MAX_BYTES:
            return

        target_rows = int(THUMBNAIL_CACHE_MAX_ROWS * 0.9)
        target_bytes = int(THUMBNAIL_CACHE_MAX_BYTES * 0.9)
        while row_count > target_rows or total_bytes > target_bytes:
            victims = conn.execute(
                """
                SELECT path, bytes
                FROM thumbnails
                ORDER BY last_accessed ASC
                LIMIT 1000
                """
            ).fetchall()
            if not victims:
                return
            conn.executemany("DELETE FROM thumbnails WHERE path = ?", [(row["path"],) for row in victims])
            row_count -= len(victims)
            total_bytes -= sum(int(row["bytes"] or 0) for row in victims)

    def get_thumbnail(self, path: str) -> bytes | None:
        """Retrieve the thumbnail binary data if it exists."""
        conn = self._conn()
        try:
            cursor = conn.execute(
                "SELECT thumbnail, last_accessed FROM thumbnails WHERE path = ?",
                (path,),
            )
            row = cursor.fetchone()
            if row is not None:
                now = time.time()
                last_accessed = float(row["last_accessed"] or 0)
                if now - last_accessed > THUMBNAIL_ACCESS_TOUCH_INTERVAL_SECONDS:
                    conn.execute(
                        "UPDATE thumbnails SET last_accessed = ? WHERE path = ?",
                        (now, path),
                    )
                    conn.commit()
        except sqlite_engine.DatabaseError as exc:
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return None
            logger.warning("Could not read cached thumbnail for %s: %s", path, exc)
            return None
        if row is None:
            return None
        return row["thumbnail"]

    def get_all_files(self, folder: Path) -> list[dict[str, Any]]:
        folder_text = folder.resolve().as_posix()
        conn = self._conn()
        try:
            cursor = conn.execute(
                """
                SELECT path, filename, tags, modified_time, file_type
                FROM files
                WHERE folder = ?
                ORDER BY filename COLLATE NOCASE
                """,
                (folder_text,),
            )
        except sqlite_engine.DatabaseError as exc:
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return []
            raise
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_files_under(self, folder: Path) -> list[dict[str, Any]]:
        """Return files located anywhere under folder (recursive subtree)."""
        root = folder.resolve().as_posix().rstrip("/")
        like_pattern = self._subtree_like_pattern(root)
        conn = self._conn()
        try:
            cursor = conn.execute(
                """
                SELECT path, filename, tags, modified_time, file_type
                FROM files
                WHERE path LIKE ? ESCAPE '\\'
                ORDER BY path COLLATE NOCASE
                """,
                (like_pattern,),
            )
        except sqlite_engine.DatabaseError as exc:
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return []
            raise
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def _row_to_record(self, row: sqlite_engine.Row) -> dict[str, Any]:
        path = row["path"]
        return {
            "path": path,
            "filename": row["filename"],
            "tags": self._load_tags(row["tags"], path=path),
            "modified_time": row["modified_time"],
            "file_type": row["file_type"],
        }

    @staticmethod
    def _subtree_like_pattern(root: str) -> str:
        escaped = root.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{escaped}/%"

    def update_file(self, file_info: dict[str, Any]) -> None:
        self.update_files_batch([file_info])

    def update_files_batch(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        self._update_files_batch(records, allow_recovery=True)

    def _update_files_batch(
        self,
        records: list[dict[str, Any]],
        *,
        allow_recovery: bool,
    ) -> None:
        conn = self._conn()
        data: list[CacheRecordRow] = []
        for info in records:
            path = Path(info["path"]).resolve()
            data.append(
                (
                    path.as_posix(),
                    path.parent.as_posix(),
                    info["filename"],
                    json.dumps(self._normalize_tags(info.get("tags", []))),
                    info["modified_time"],
                    info["file_type"],
                    self._optional_int(info.get("size")),
                    self._optional_int(info.get("modified_time_ns")),
                    self._optional_int(info.get("device_id")),
                    self._optional_int(info.get("inode")),
                )
            )

        try:
            self._delete_replaced_file_thumbnails(conn, data)
            conn.executemany(
                """
                INSERT INTO files(
                    path, folder, filename, tags, modified_time, file_type,
                    size, modified_time_ns, device_id, inode
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    folder=excluded.folder,
                    filename=excluded.filename,
                    tags=excluded.tags,
                    modified_time=excluded.modified_time,
                    file_type=excluded.file_type,
                    size=excluded.size,
                    modified_time_ns=excluded.modified_time_ns,
                    device_id=excluded.device_id,
                    inode=excluded.inode
                """,
                data,
            )
            conn.commit()
        except sqlite_engine.OperationalError as exc:
            conn.rollback()
            if self._is_locked_database_error(exc):
                raise CacheDatabaseError(
                    "Cache database is locked by another TagExplorer process."
                ) from exc
            if allow_recovery and self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                self._update_files_batch(records, allow_recovery=False)
                return
            raise
        except sqlite_engine.DatabaseError as exc:
            self._rollback_quietly(conn)
            if allow_recovery and self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                self._update_files_batch(records, allow_recovery=False)
                return
            raise

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _delete_replaced_file_thumbnails(
        self,
        conn: sqlite_engine.Connection,
        records: list[CacheRecordRow],
    ) -> None:
        """Drop thumbnails only when persisted file identity changed."""
        incoming_identity = {
            path: (device_id, inode)
            for (
                path,
                _folder,
                _filename,
                _tags,
                _modified_time,
                _file_type,
                _size,
                _modified_time_ns,
                device_id,
                inode,
            ) in records
            if device_id is not None and inode is not None
        }
        if not incoming_identity:
            return

        paths_to_clear: list[str] = []
        paths = list(incoming_identity)
        for offset in range(0, len(paths), 500):
            chunk = paths[offset : offset + 500]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = conn.execute(
                f"""
                SELECT f.path, f.device_id, f.inode
                FROM files AS f
                INNER JOIN thumbnails AS t ON t.path = f.path
                WHERE f.path IN ({placeholders})
                  AND f.device_id IS NOT NULL
                  AND f.inode IS NOT NULL
                """,
                chunk,
            )
            for row in cursor.fetchall():
                new_identity = incoming_identity[row["path"]]
                old_identity = (row["device_id"], row["inode"])
                if old_identity != new_identity:
                    paths_to_clear.append(row["path"])

        if paths_to_clear:
            conn.executemany(
                "DELETE FROM thumbnails WHERE path = ?",
                [(path,) for path in paths_to_clear],
            )

    def delete_missing(self, folder: Path, current_paths: set[str]) -> None:
        folder_text = folder.resolve().as_posix()
        conn = self._conn()
        try:
            cursor = conn.execute("SELECT path FROM files WHERE folder = ?", (folder_text,))
        except sqlite_engine.DatabaseError as exc:
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return
            raise
        cached_paths = {row["path"] for row in cursor.fetchall()}
        self._delete_paths(conn, cached_paths - current_paths)

    def delete_missing_tree(self, folder: Path, current_paths: set[str]) -> None:
        """Delete cached paths in subtree that are not present in latest recursive scan."""
        root = folder.resolve().as_posix().rstrip("/")
        like_pattern = self._subtree_like_pattern(root)
        conn = self._conn()
        try:
            cursor = conn.execute(
                "SELECT path FROM files WHERE path LIKE ? ESCAPE '\\'",
                (like_pattern,),
            )
        except sqlite_engine.DatabaseError as exc:
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return
            raise
        cached_paths = {row["path"] for row in cursor.fetchall()}
        self._delete_paths(conn, cached_paths - current_paths)

    def _delete_paths(self, conn: sqlite_engine.Connection, paths: set[str]) -> None:
        if not paths:
            return
        try:
            conn.executemany(
                "DELETE FROM files WHERE path = ?",
                [(p,) for p in paths],
            )
            conn.commit()
        except sqlite_engine.OperationalError as exc:
            conn.rollback()
            if self._is_locked_database_error(exc):
                raise CacheDatabaseError(
                    "Cache database is locked by another TagExplorer process."
                ) from exc
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return
            raise

    def get_record(self, path: Path) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            cursor = conn.execute(
                "SELECT path, filename, tags, modified_time, file_type FROM files WHERE path = ?",
                (path.resolve().as_posix(),),
            )
        except sqlite_engine.DatabaseError as exc:
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return None
            raise
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def clear_folder(self, folder: Path) -> None:
        folder_text = folder.resolve().as_posix()
        conn = self._conn()
        self._delete_by_query(conn, "DELETE FROM files WHERE folder = ?", (folder_text,))

    def clear_tree(self, folder: Path) -> None:
        """Clear all cached files for a folder subtree."""
        root = folder.resolve().as_posix().rstrip("/")
        like_pattern = self._subtree_like_pattern(root)
        conn = self._conn()
        self._delete_by_query(
            conn,
            "DELETE FROM files WHERE path LIKE ? ESCAPE '\\'",
            (like_pattern,),
        )

    def _delete_by_query(
        self,
        conn: sqlite_engine.Connection,
        statement: str,
        parameters: Sequence[Any],
    ) -> None:
        try:
            conn.execute(statement, parameters)
            conn.commit()
        except sqlite_engine.OperationalError as exc:
            conn.rollback()
            if self._is_locked_database_error(exc):
                raise CacheDatabaseError(
                    "Cache database is locked by another TagExplorer process."
                ) from exc
            if self._is_recoverable_database_error(exc):
                self._recover_and_recreate(exc)
                return
            raise

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            self._local.connection = None
