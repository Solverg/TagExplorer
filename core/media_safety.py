"""Safety guards for previewing and opening untrusted local media files."""

from __future__ import annotations

import math
import multiprocessing
import os
from pathlib import Path, PureWindowsPath
from queue import Empty
import stat as stat_module
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QImage


MAX_IMAGE_FILE_BYTES = 1024 * 1024 * 1024
MAX_VIDEO_FILE_BYTES = 100 * 1024 * 1024 * 1024
MAX_IMAGE_PIXELS = 250_000_000
MAX_IMAGE_DIMENSION = 100_000
MAX_VIDEO_FRAME_PIXELS = 150_000_000
MAX_QIMAGE_ALLOCATION_MB = 1024
MEDIA_DECODE_TIMEOUT_SECONDS = 6.0
VIDEO_OPEN_TIMEOUT_MS = 3000
VIDEO_READ_TIMEOUT_MS = 3000
EXPLORER_SELECT_TIMEOUT_SECONDS = 5.0
IMAGE_PREVIEW_TARGET_EDGE = 8192

ACTIVE_OPEN_EXTENSIONS = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".cpl",
        ".exe",
        ".gadget",
        ".hta",
        ".jar",
        ".js",
        ".jse",
        ".msc",
        ".msi",
        ".msp",
        ".pif",
        ".ps1",
        ".reg",
        ".scf",
        ".scr",
        ".url",
        ".vb",
        ".vbe",
        ".vbs",
        ".ws",
        ".wsf",
        ".wsh",
    }
)
SHORTCUT_EXTENSIONS = frozenset({".lnk", ".url"})


class MediaSafetyError(Exception):
    """Raised when a file should not be decoded or opened automatically."""


def format_bytes(size: int) -> str:
    units = ("bytes", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "bytes":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size} bytes"


def is_network_path(path: Path | str) -> bool:
    text = os.fspath(path)
    normalized = text.replace("/", "\\")
    if normalized.startswith("\\\\?\\UNC\\"):
        return True
    if normalized.startswith("\\\\?\\") or normalized.startswith("\\\\.\\"):
        return False
    if normalized.startswith("\\\\"):
        return True
    if text.startswith("//"):
        return True
    return PureWindowsPath(text).drive.startswith("\\\\")


def _is_windows_reparse_point(stat_result: os.stat_result) -> bool:
    if os.name != "nt":
        return False
    attrs = getattr(stat_result, "st_file_attributes", 0)
    return bool(attrs & getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400))


def ensure_regular_file_for_preview(path: Path | str, *, max_bytes: int) -> os.stat_result:
    file_path = Path(path)
    try:
        stat_result = file_path.lstat()
    except OSError as exc:
        raise MediaSafetyError(f"File is no longer available: {exc}") from exc

    if stat_module.S_ISLNK(stat_result.st_mode):
        raise MediaSafetyError("Preview skipped for symbolic links.")
    if _is_windows_reparse_point(stat_result):
        raise MediaSafetyError("Preview skipped for Windows reparse points.")
    if not stat_module.S_ISREG(stat_result.st_mode):
        raise MediaSafetyError("Preview is only available for regular files.")
    if stat_result.st_size > max_bytes:
        raise MediaSafetyError(
            f"Preview skipped because the file is larger than {format_bytes(max_bytes)}."
        )
    return stat_result


def _configure_qimage_reader_limit() -> None:
    from PyQt6.QtGui import QImageReader

    set_limit = getattr(QImageReader, "setAllocationLimit", None)
    if set_limit is None:
        return
    try:
        set_limit(MAX_QIMAGE_ALLOCATION_MB)
    except (AttributeError, TypeError, RuntimeError):
        return


def _validate_dimensions(width: int, height: int, *, kind: str) -> None:
    if width <= 0 or height <= 0:
        return
    pixels = width * height
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise MediaSafetyError(
            f"{kind} dimensions are too large: {width}x{height}."
        )
    if pixels > MAX_IMAGE_PIXELS:
        raise MediaSafetyError(
            f"{kind} is too large to preview safely: {width}x{height}."
        )


def read_limited_image(path: Path | str, target_size: "QSize | None" = None) -> "QImage":
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImageReader

    ensure_regular_file_for_preview(path, max_bytes=MAX_IMAGE_FILE_BYTES)
    _configure_qimage_reader_limit()

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)

    original_size = reader.size()
    if original_size.isValid():
        _validate_dimensions(original_size.width(), original_size.height(), kind="Image")
        if target_size is not None:
            scaled = original_size.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            reader.setScaledSize(scaled)

    image = reader.read()
    if image.isNull():
        error_text = reader.errorString() or "Unable to decode image."
        raise MediaSafetyError(error_text)

    _validate_dimensions(image.width(), image.height(), kind="Decoded image")
    return image


def _finite_positive(value: float, default: float) -> float:
    if math.isfinite(value) and value > 0:
        return value
    return default


def _target_dimensions(target_size: "QSize | None") -> tuple[int, int]:
    if target_size is None:
        return IMAGE_PREVIEW_TARGET_EDGE, IMAGE_PREVIEW_TARGET_EDGE
    return max(1, target_size.width()), max(1, target_size.height())


def _set_cv_timeout(cap: Any, cv2: Any, attr_name: str, value: int) -> None:
    prop = getattr(cv2, attr_name, None)
    if prop is None:
        return
    try:
        cap.set(prop, value)
    except Exception:
        return


def _open_video_capture(path_text: str, cv2: Any) -> Any:
    params: list[int] = []
    for attr_name, value in (
        ("CAP_PROP_OPEN_TIMEOUT_MSEC", VIDEO_OPEN_TIMEOUT_MS),
        ("CAP_PROP_READ_TIMEOUT_MSEC", VIDEO_READ_TIMEOUT_MS),
    ):
        prop = getattr(cv2, attr_name, None)
        if prop is not None:
            params.extend([prop, value])

    if params:
        try:
            cap = cv2.VideoCapture(path_text, getattr(cv2, "CAP_ANY", 0), params)
            if cap is not None and cap.isOpened():
                return cap
            if cap is not None:
                cap.release()
        except Exception:
            pass

    cap = cv2.VideoCapture()
    _set_cv_timeout(cap, cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", VIDEO_OPEN_TIMEOUT_MS)
    _set_cv_timeout(cap, cv2, "CAP_PROP_READ_TIMEOUT_MSEC", VIDEO_READ_TIMEOUT_MS)
    cap.open(path_text)
    return cap


def _cv_float(cap: Any, prop: int) -> float:
    try:
        return float(cap.get(prop) or 0.0)
    except Exception:
        return 0.0


def _resize_frame(frame: Any, cv2: Any, target_width: int, target_height: int) -> Any:
    height, width = frame.shape[:2]
    scale = min(target_width / width, target_height / height, 1.0)
    if scale >= 1.0:
        return frame
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    return cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)


def _video_frame_worker(
    path_text: str,
    target_width: int,
    target_height: int,
    result_queue: Any,
) -> None:
    cap = None
    try:
        import cv2

        cap = _open_video_capture(path_text, cv2)
        if not cap.isOpened():
            result_queue.put({"error": "Unable to open video."})
            return

        width_meta = int(_finite_positive(_cv_float(cap, cv2.CAP_PROP_FRAME_WIDTH), 0.0))
        height_meta = int(_finite_positive(_cv_float(cap, cv2.CAP_PROP_FRAME_HEIGHT), 0.0))
        if width_meta > 0 and height_meta > 0 and width_meta * height_meta > MAX_VIDEO_FRAME_PIXELS:
            result_queue.put(
                {"error": f"Video frame is too large to preview safely: {width_meta}x{height_meta}."}
            )
            return

        fps = _finite_positive(_cv_float(cap, cv2.CAP_PROP_FPS), 25.0)
        total_raw = _cv_float(cap, cv2.CAP_PROP_FRAME_COUNT)
        total = int(total_raw) if math.isfinite(total_raw) and total_raw > 0 else 0
        duration = total / fps if total > 0 and fps > 0 else None

        frame = None
        if total > 0:
            target_seconds = (25.0, (duration or 0.0) / 2.0, 0.0)
            for target_sec in target_seconds:
                target_frame = int(fps * target_sec)
                if 0 <= target_frame < total:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    ok, candidate = cap.read()
                    if ok and candidate is not None:
                        frame = candidate
                        break

        if frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            for _ in range(10):
                ok, candidate = cap.read()
                if ok and candidate is not None:
                    frame = candidate
                    break

        if frame is None:
            result_queue.put({"error": "Unable to read video frame."})
            return

        if not hasattr(frame, "shape") or len(frame.shape) < 2:
            result_queue.put({"error": "Video decoder returned an invalid frame."})
            return

        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            result_queue.put({"error": "Video decoder returned an empty frame."})
            return
        if width * height > MAX_VIDEO_FRAME_PIXELS:
            result_queue.put(
                {"error": f"Video frame is too large to preview safely: {width}x{height}."}
            )
            return

        channels = frame.shape[2] if len(frame.shape) > 2 else 1
        if channels == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        elif channels not in (1, 3):
            result_queue.put({"error": "Video decoder returned an unsupported frame format."})
            return

        frame = _resize_frame(frame, cv2, target_width, target_height)
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
        if not ok:
            result_queue.put({"error": "Unable to encode video preview frame."})
            return

        result_queue.put({"image": encoded.tobytes(), "duration": duration})
    except Exception as exc:  # noqa: BLE001
        result_queue.put({"error": str(exc)})
    finally:
        if cap is not None:
            cap.release()


def read_limited_video_frame(
    path: Path | str,
    target_size: "QSize | None" = None,
    *,
    timeout_seconds: float = MEDIA_DECODE_TIMEOUT_SECONDS,
) -> tuple["QImage", float | None]:
    from PyQt6.QtGui import QImage

    ensure_regular_file_for_preview(path, max_bytes=MAX_VIDEO_FILE_BYTES)
    target_width, target_height = _target_dimensions(target_size)

    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_video_frame_worker,
        args=(str(path), target_width, target_height, result_queue),
    )
    process.daemon = True
    process.start()

    result: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        wait_for = min(0.1, max(0.0, deadline - time.monotonic()))
        try:
            result = result_queue.get(timeout=wait_for)
            break
        except Empty:
            if not process.is_alive():
                try:
                    result = result_queue.get_nowait()
                except Empty:
                    result = None
                break

    if result is None and process.is_alive():
        process.terminate()
        process.join(1.0)
        kill = getattr(process, "kill", None)
        if process.is_alive() and kill is not None:
            kill()
            process.join(1.0)
        result_queue.close()
        raise MediaSafetyError("Video preview timed out.")

    process.join(1.0)

    try:
        if result is None:
            result = result_queue.get_nowait()
    except Empty as exc:
        if process.exitcode:
            raise MediaSafetyError("Video preview worker failed.") from exc
        raise MediaSafetyError("Video preview worker returned no data.") from exc
    finally:
        result_queue.close()

    error = result.get("error")
    if error:
        raise MediaSafetyError(str(error))

    image = QImage.fromData(result.get("image", b""))
    if image.isNull():
        raise MediaSafetyError("Unable to decode video preview frame.")
    return image, result.get("duration")


def startfile_warning_messages(path: Path | str) -> list[str]:
    suffix = Path(path).suffix.lower()
    messages: list[str] = []
    if suffix in SHORTCUT_EXTENSIONS:
        messages.append(
            "This file is a shortcut or URL and may open a different local, network, or web target."
        )
    elif suffix in ACTIVE_OPEN_EXTENSIONS:
        messages.append("This file type can run programs or scripts.")
    if is_network_path(path):
        messages.append("This file is on a network path and opening it may contact that host.")
    return messages


def explorer_select_warning_messages(path: Path | str) -> list[str]:
    if is_network_path(path):
        return ["This location is on a network share and may contact that host."]
    return []
