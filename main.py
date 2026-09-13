#!/usr/bin/env python3
"""
Fully offline desktop file converter with a modern PySide6 GUI.
Partially vibe coded : Claude Sonnet 5, GPT 5.6 Luna

Supports:
    - Images  (via Pillow)
    - Audio   (via FFmpeg, bundled through imageio-ffmpeg, or system ffmpeg)
    - Video   (via FFmpeg, same as above)
    - PDF     (via PyMuPDF / pypdfium2 for rendering; Pillow for image->PDF)

Pip dependencies:
    pip install PySide6 Pillow imageio-ffmpeg
    pip install pymupdf          # preferred PDF backend
    # or, if pymupdf is unavailable on your platform:
    pip install pypdfium2

Run:
    python converter.py

The app is designed to degrade gracefully: if an optional backend (ffmpeg,
pymupdf/pypdfium2, or extra Pillow plugins for heic/avif) is missing, the
corresponding features are disabled with a clear, actionable message instead
of the app crashing.
"""

from __future__ import annotations

import os
import re
import sys
import time
import shutil
import logging
import platform
import subprocess
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

from PySide6.QtCore import (
    Qt, QSettings, QRunnable, QThreadPool, QObject, Signal, Slot, QSize,
    QTimer, QMimeData, QPoint, QByteArray,
)
from PySide6.QtGui import (
    QDragEnterEvent, QDropEvent, QAction, QIcon, QColor, QPalette, QFont,
    QCloseEvent, QPixmap, QPainter,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QLineEdit, QCheckBox, QSpinBox, QDoubleSpinBox, QGroupBox,
    QFormLayout, QToolButton, QProgressBar, QPlainTextEdit, QMenu,
    QMessageBox, QStyle, QSizePolicy, QFrame, QToolBar, QStatusBar,
    QInputDialog, QAbstractItemView, QSplitter, QStackedWidget, QScrollArea,
    QDialog, QTabWidget,
)

# --------------------------------------------------------------------------
# Optional backend imports (never fail hard on missing packages)
# --------------------------------------------------------------------------

try:
    from PIL import Image, ImageOps, ImageSequence
    PIL_AVAILABLE = True
    try:
        PIL_VERSION = __import__("PIL").__version__
    except Exception:
        PIL_VERSION = "unknown"
except Exception:
    PIL_AVAILABLE = False
    PIL_VERSION = None

HEIC_SUPPORTED = False
AVIF_SUPPORTED = False
if PIL_AVAILABLE:
    try:
        import pillow_heif  # noqa: F401
        pillow_heif.register_heif_opener()
        HEIC_SUPPORTED = True
    except Exception:
        HEIC_SUPPORTED = False
    try:
        import pillow_avif  # noqa: F401
        AVIF_SUPPORTED = True
    except Exception:
        # Some Pillow builds have native avif support baked in.
        try:
            Image.new("RGB", (1, 1)).save(
                Path.home() / ".fc_avif_probe.avif"
            )
            AVIF_SUPPORTED = True
            try:
                (Path.home() / ".fc_avif_probe.avif").unlink(missing_ok=True)
            except Exception:
                pass
        except Exception:
            AVIF_SUPPORTED = False

try:
    import imageio_ffmpeg
    IMAGEIO_FFMPEG_AVAILABLE = True
except Exception:
    IMAGEIO_FFMPEG_AVAILABLE = False

PYMUPDF_AVAILABLE = False
PYPDFIUM2_AVAILABLE = False
try:
    import pymupdf
    PYMUPDF_AVAILABLE = True
    try:
        PYMUPDF_VERSION = pymupdf.__doc__ or pymupdf.VersionBind
    except Exception:
        PYMUPDF_VERSION = "unknown"
except Exception:
    try:
        import pypdfium2 as pdfium
        PYPDFIUM2_AVAILABLE = True
    except Exception:
        PYPDFIUM2_AVAILABLE = False

PDF_AVAILABLE = PYMUPDF_AVAILABLE or PYPDFIUM2_AVAILABLE

APP_NAME = "File Converter"
ORG_NAME = "OfflineTools"


# --------------------------------------------------------------------------
# SVG icons (no emoji anywhere in the UI)
# --------------------------------------------------------------------------
# Every icon is a small inline SVG string rendered to a QIcon at the requested
# size/color. Colors are hardcoded to match the app's single dark theme.

_ICON_CACHE: dict[tuple[str, int], QIcon] = {}


def svg_icon(svg_source: str, size: int = 16) -> QIcon:
    """Render an inline SVG string to a QIcon at the given pixel size (cached)."""
    key = (svg_source, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    renderer = QSvgRenderer(QByteArray(svg_source.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    icon = QIcon(pixmap)
    _ICON_CACHE[key] = icon
    return icon


def _svg(body: str, stroke: str = "#e6e6e6", fill: str = "none") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


ICON_UPLOAD = _svg(
    '<path d="M12 16V4"/><path d="M6 10l6-6 6 6"/>'
    '<path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    stroke="#8fb4ff",
)
ICON_CHEVRON_RIGHT = _svg('<path d="M9 18l6-6-6-6"/>')
ICON_CHEVRON_DOWN = _svg('<path d="M6 9l6 6 6-6"/>')
ICON_CLOSE = _svg('<path d="M6 6l12 12"/><path d="M18 6L6 18"/>', stroke="#c9cbcf")
ICON_FOLDER = _svg(
    '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>'
)
ICON_MOON = _svg('<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/>', stroke="#8fb4ff")
ICON_STOP = _svg('<rect x="6" y="6" width="12" height="12" rx="2"/>', stroke="#e6e6e6")
ICON_PLAY = _svg('<path d="M6 4l14 8-14 8V4z"/>', fill="#ffffff", stroke="none")
ICON_TRASH = _svg(
    '<path d="M4 7h16"/><path d="M9 7V4h6v3"/>'
    '<path d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13"/>'
)
ICON_SETTINGS = _svg(
    '<circle cx="12" cy="12" r="3"/>'
    '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>'
)


# --------------------------------------------------------------------------
# Format constants
# --------------------------------------------------------------------------

IMAGE_INPUT_FORMATS = [
    "png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "ico",
    "ppm", "pgm", "tga", "jp2",
]
if HEIC_SUPPORTED:
    IMAGE_INPUT_FORMATS.append("heic")
if AVIF_SUPPORTED:
    IMAGE_INPUT_FORMATS.append("avif")

IMAGE_OUTPUT_FORMATS = [
    "png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "ico",
    "ppm", "tga", "pdf",
]

AUDIO_FORMATS = [
    "mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aiff",
    "alac", "amr",
]

VIDEO_FORMATS = [
    "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "mpg",
    "mpeg", "ts", "gif",
]

PDF_INPUT_FORMATS = ["pdf"]
PDF_OUTPUT_IMAGE_FORMATS = [
    "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "ico",
]

ICO_SIZES = [16, 32, 48, 64, 128, 256]

FFMPEG_CONTAINER_CODEC_HINTS = {
    "mp3": ["-c:a", "libmp3lame"],
    "aac": ["-c:a", "aac"],
    "m4a": ["-c:a", "aac"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
    "wma": ["-c:a", "wmav2"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
    "aiff": ["-c:a", "pcm_s16be"],
    "alac": ["-c:a", "alac"],
    "amr": ["-c:a", "libopencore_amrnb", "-ar", "8000", "-ac", "1"],
}


def category_of_ext(ext: str) -> str:
    """Return the category name ('image', 'audio', 'video', 'pdf', 'unknown') for a lowercase extension (no dot)."""
    ext = ext.lower().lstrip(".")
    if ext in IMAGE_INPUT_FORMATS:
        return "image"
    if ext in AUDIO_FORMATS:
        return "audio"
    if ext in VIDEO_FORMATS:
        return "video"
    if ext in PDF_INPUT_FORMATS:
        return "pdf"
    return "unknown"


def valid_targets_for(ext: str) -> list[str]:
    cat = category_of_ext(ext)
    if cat == "image":
        return list(IMAGE_OUTPUT_FORMATS)
    if cat == "audio":
        return list(AUDIO_FORMATS)
    if cat == "video":
        return list(VIDEO_FORMATS)
    if cat == "pdf":
        return list(PDF_OUTPUT_IMAGE_FORMATS)
    return []


def common_valid_targets(exts: list[str]) -> list[str]:
    """Intersection of valid targets across a list of source extensions."""
    if not exts:
        return []
    sets = [set(valid_targets_for(e)) for e in exts]
    common = sets[0]
    for s in sets[1:]:
        common &= s
    # Preserve a stable, sensible order using the first list as reference.
    ordered = [f for f in valid_targets_for(exts[0]) if f in common]
    return ordered


# --------------------------------------------------------------------------
# Utility helpers
# --------------------------------------------------------------------------

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    cleaned = _ILLEGAL_CHARS_RE.sub("_", name).strip().rstrip(".")
    return cleaned or "converted_file"


def unique_path(path: Path) -> Path:
    """Return a path that does not already exist, appending ' (1)', ' (2)', ... as needed."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def human_size(num_bytes: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def parse_page_range(spec: str, page_count: int) -> list[int]:
    """Parse a page-range spec like 'all', '1-5', '2,4,7' into a 0-indexed page list."""
    spec = (spec or "all").strip().lower()
    if spec in ("", "all", "*"):
        return list(range(page_count))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a_i, b_i = int(a), int(b)
                for p in range(min(a_i, b_i), max(a_i, b_i) + 1):
                    if 1 <= p <= page_count:
                        pages.add(p - 1)
            except ValueError:
                continue
        else:
            try:
                p = int(part)
                if 1 <= p <= page_count:
                    pages.add(p - 1)
            except ValueError:
                continue
    return sorted(pages) if pages else list(range(page_count))


# --------------------------------------------------------------------------
# Backend registry
# --------------------------------------------------------------------------

class BackendRegistry:
    """Detects which optional backends are available and how to reach ffmpeg."""

    def __init__(self) -> None:
        self.pil_available = PIL_AVAILABLE
        self.pil_version = PIL_VERSION
        self.heic_supported = HEIC_SUPPORTED
        self.avif_supported = AVIF_SUPPORTED
        self.pdf_available = PDF_AVAILABLE
        self.pdf_backend = "pymupdf" if PYMUPDF_AVAILABLE else ("pypdfium2" if PYPDFIUM2_AVAILABLE else None)
        self.ffmpeg_path: Optional[str] = self._resolve_ffmpeg()
        self.ffmpeg_available = self.ffmpeg_path is not None

    @staticmethod
    def _resolve_ffmpeg() -> Optional[str]:
        if IMAGEIO_FFMPEG_AVAILABLE:
            try:
                path = imageio_ffmpeg.get_ffmpeg_exe()
                if path and Path(path).exists():
                    return path
            except Exception:
                pass
        which = shutil.which("ffmpeg")
        if which:
            return which
        return None

    def missing_backends_report(self) -> list[str]:
        msgs = []
        if not self.pil_available:
            msgs.append("Images: pip install Pillow")
        if not self.ffmpeg_available:
            msgs.append("Audio/Video: pip install imageio-ffmpeg  (or install system ffmpeg)")
        if not self.pdf_available:
            msgs.append("PDF: pip install pymupdf   (or: pip install pypdfium2)")
        if self.pil_available and not self.heic_supported:
            msgs.append("HEIC images: pip install pillow-heif")
        if self.pil_available and not self.avif_supported:
            msgs.append("AVIF images: pip install pillow-avif-plugin")
        return msgs

    def startup_report_lines(self) -> list[str]:
        lines = ["STARTUP CHECK"]
        lines.append(f"  Python:        {platform.python_version()} ({platform.platform()})")
        lines.append(f"  Pillow:        {'OK v' + str(self.pil_version) if self.pil_available else 'MISSING'}")
        lines.append(f"    HEIC support:  {'yes' if self.heic_supported else 'no'}")
        lines.append(f"    AVIF support:  {'yes' if self.avif_supported else 'no'}")
        lines.append(f"  FFmpeg:        {'OK (' + self.ffmpeg_path + ')' if self.ffmpeg_available else 'MISSING'}")
        lines.append(f"  PDF backend:   {self.pdf_backend or 'MISSING'}")
        return lines


# --------------------------------------------------------------------------
# Conversion job model
# --------------------------------------------------------------------------

@dataclass
class ConversionJob:
    job_id: int
    source_path: Path
    target_ext: str
    output_dir: Path
    options: dict = field(default_factory=dict)
    status: str = "Queued"          # Queued, Converting, Done, Failed, Cancelled
    progress: int = 0               # 0-100
    error: str = ""
    output_path: Optional[Path] = None


class JobSignals(QObject):
    progress = Signal(int, int)          # job_id, percent
    status_changed = Signal(int, str)    # job_id, status text
    log = Signal(str, str)               # level, message
    finished = Signal(int, bool, str, str)  # job_id, success, message, output_path


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


# --------------------------------------------------------------------------
# Conversion backends (pure functions, run inside worker threads)
# --------------------------------------------------------------------------

def convert_image(job: ConversionJob, signals: JobSignals, token: CancelToken) -> Path:
    """Convert an image (or images->pdf single-file case is handled elsewhere)."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")

    src = job.source_path
    target_ext = job.target_ext.lower()
    opts = job.options

    im = Image.open(src)

    is_animated = getattr(im, "is_animated", False)
    if is_animated and target_ext != "gif":
        signals.log.emit("WARN", f"{src.name}: animated image, exporting first frame only")
        im.seek(0)
        im = im.convert("RGBA") if im.mode in ("P", "LA") else im.copy()

    job.output_dir.mkdir(parents=True, exist_ok=True)
    out_name = sanitize_filename(src.stem) + f".{target_ext}"
    out_path = unique_path(job.output_dir / out_name)

    if target_ext == "ico":
        _save_as_ico(im, out_path)
    elif target_ext == "pdf":
        rgb = im.convert("RGB") if im.mode in ("RGBA", "P", "LA") else im
        save_kwargs = {}
        if opts.get("preserve_metadata") and "exif" in im.info:
            save_kwargs["exif"] = im.info["exif"]
        rgb.save(out_path, "PDF", **save_kwargs)
    elif target_ext in ("jpg", "jpeg"):
        rgb = im.convert("RGB") if im.mode in ("RGBA", "P", "LA") else im
        save_kwargs: dict[str, Any] = {"quality": opts.get("jpeg_quality", 90)}
        if opts.get("preserve_metadata") and "exif" in im.info:
            save_kwargs["exif"] = im.info["exif"]
        rgb.save(out_path, "JPEG", **save_kwargs)
    elif target_ext == "gif":
        if is_animated:
            frames = [f.convert("RGBA") for f in ImageSequence.Iterator(im)]
            frames[0].save(
                out_path, save_all=True, append_images=frames[1:], loop=0,
                duration=im.info.get("duration", 100),
            )
        else:
            im.convert("P", palette=Image.ADAPTIVE).save(out_path)
    elif target_ext in ("tif", "tiff"):
        save_kwargs = {}
        if opts.get("preserve_metadata") and "exif" in im.info:
            save_kwargs["exif"] = im.info["exif"]
        im.save(out_path, "TIFF", **save_kwargs)
    elif target_ext == "webp":
        save_kwargs = {"quality": opts.get("jpeg_quality", 90), "method": 2}
        im.save(out_path, "WEBP", **save_kwargs)
    else:
        # png, bmp, ppm, tga, etc.
        fmt_map = {"ppm": "PPM", "tga": "TGA", "bmp": "BMP", "png": "PNG"}
        fmt = fmt_map.get(target_ext, target_ext.upper())
        save_kwargs = {}
        if fmt == "PNG":
            if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                im = im.convert("RGBA")
            if opts.get("fast_png", True):
                save_kwargs["compress_level"] = 1
        if fmt == "BMP" and im.mode not in ("RGB", "L", "P"):
            im = im.convert("RGB")
        im.save(out_path, fmt, **save_kwargs)

    signals.progress.emit(job.job_id, 100)
    return out_path


def _save_as_ico(im: "Image.Image", out_path: Path) -> None:
    """Save a proper multi-resolution .ico: composite onto square canvas, keep aspect, upscale small sources.

    Pillow's ICO writer only *downscales* the base image it is given for each
    requested size - it will not upscale. So for small sources we must
    upscale the square canvas to the largest requested size ourselves before
    handing it to Pillow, otherwise Pillow silently emits an empty/invalid
    ICO file for any requested size larger than the source.
    """
    if im.mode not in ("RGBA",):
        im = im.convert("RGBA")

    w, h = im.size
    side = max(w, h)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(im, ((side - w) // 2, (side - h) // 2), im)

    max_size = max(ICO_SIZES)
    if side != max_size:
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        square = square.resize((max_size, max_size), resample)

    sizes = [(s, s) for s in ICO_SIZES]
    square.save(out_path, format="ICO", sizes=sizes)


def images_to_pdf(paths: list[Path], out_path: Path, options: dict) -> Path:
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")
    page_size = options.get("page_size", "auto")
    orientation = options.get("orientation", "auto")

    imgs = []
    for p in paths:
        im = Image.open(p)
        im = im.convert("RGB") if im.mode in ("RGBA", "P", "LA") else im
        if orientation == "landscape" and im.height > im.width:
            im = im.rotate(90, expand=True)
        elif orientation == "portrait" and im.width > im.height:
            im = im.rotate(90, expand=True)
        imgs.append(im)

    if not imgs:
        raise RuntimeError("No images to convert")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    first, rest = imgs[0], imgs[1:]
    first.save(out_path, "PDF", save_all=True, append_images=rest)
    return out_path


def _ffmpeg_duration_seconds(ffmpeg_path: str, src: Path) -> Optional[float]:
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-i", str(src)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=15,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stdout or "")
        if m:
            h, mnt, s = m.groups()
            return int(h) * 3600 + int(mnt) * 60 + float(s)
    except Exception:
        pass
    return None


def convert_media(job: ConversionJob, signals: JobSignals, token: CancelToken,
                   ffmpeg_path: str, proc_registry: dict) -> Path:
    """Convert audio or video via FFmpeg with progress parsing and cancellation support."""
    src = job.source_path
    target_ext = job.target_ext.lower()
    opts = job.options

    out_name = sanitize_filename(src.stem) + f".{target_ext}"
    out_path = unique_path(job.output_dir / out_name)
    job.output_dir.mkdir(parents=True, exist_ok=True)

    duration = _ffmpeg_duration_seconds(ffmpeg_path, src)

    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "0", "-i", str(src)]

    is_gif_target = target_ext == "gif"
    is_video_source = category_of_ext(src.suffix) == "video"

    if is_gif_target and is_video_source:
        fps = opts.get("gif_fps", 10)
        width = opts.get("gif_width", 480)
        vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
        cmd += ["-vf", vf, "-loop", "0"]
    elif category_of_ext(src.suffix) == "video" or category_of_ext(f".{target_ext}") == "video":
        preset = opts.get("video_preset", "veryfast")
        if preset and preset != "default":
            cmd += ["-preset", preset]
        if opts.get("resolution") and opts["resolution"] != "Source":
            cmd += ["-vf", f"scale={opts['resolution']}"]
        if opts.get("fps") and opts["fps"] != "Source":
            cmd += ["-r", str(opts["fps"])]
        if opts.get("crf") is not None:
            cmd += ["-crf", str(opts["crf"])]
        elif opts.get("video_bitrate"):
            cmd += ["-b:v", opts["video_bitrate"]]
        if opts.get("audio_bitrate"):
            cmd += ["-b:a", opts["audio_bitrate"]]
    else:
        # audio
        codec_hint = FFMPEG_CONTAINER_CODEC_HINTS.get(target_ext)
        if codec_hint:
            cmd += codec_hint
        if opts.get("bitrate") and opts["bitrate"] != "Auto":
            cmd += ["-b:a", opts["bitrate"]]
        if opts.get("sample_rate") and opts["sample_rate"] != "Auto":
            cmd += ["-ar", str(opts["sample_rate"])]
        if opts.get("channels") and opts["channels"] != "Auto":
            cmd += ["-ac", str(opts["channels"])]
        if not opts.get("preserve_metadata", True):
            cmd += ["-map_metadata", "-1"]

    cmd += ["-progress", "pipe:1", str(out_path)]

    signals.log.emit("INFO", f"$ {' '.join(cmd)}")

    creationflags = 0
    if platform.system() == "Windows":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, creationflags=creationflags,
    )
    proc_registry[job.job_id] = proc

    stderr_lines: list[str] = []
    last_pct = -1

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if token.cancelled:
                _terminate_process(proc)
                break
            line = line.strip()
            if line.startswith("out_time_ms=") and duration:
                try:
                    out_time_ms = int(line.split("=", 1)[1])
                    pct = min(99, int((out_time_ms / 1_000_000) / duration * 100))
                    if pct > last_pct:
                        last_pct = pct
                        signals.progress.emit(job.job_id, max(0, pct))
                except Exception:
                    pass
            elif line.startswith("progress=") and line.endswith("end"):
                if last_pct != 100:
                    last_pct = 100
                    signals.progress.emit(job.job_id, 100)

        if proc.stderr is not None:
            stderr_lines = proc.stderr.readlines()

        proc.wait(timeout=10)
    finally:
        proc_registry.pop(job.job_id, None)

    if token.cancelled:
        raise InterruptedError("Cancelled by user")

    if proc.returncode != 0:
        err_text = "".join(stderr_lines).strip() or f"ffmpeg exited with code {proc.returncode}"
        signals.log.emit("ERROR", f"FFmpeg failed for {src.name}: {err_text}\nCommand: {' '.join(cmd)}")
        raise RuntimeError(err_text.splitlines()[-1] if err_text else "FFmpeg conversion failed")

    return out_path


def _terminate_process(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def pdf_to_images(job: ConversionJob, signals: JobSignals, token: CancelToken) -> list[Path]:
    src = job.source_path
    target_ext = job.target_ext.lower()
    opts = job.options
    dpi = int(opts.get("dpi", 150))
    page_spec = opts.get("page_range", "all")
    password = opts.get("password", "")

    job.output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    if PYMUPDF_AVAILABLE:
        doc = pymupdf.open(str(src))
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                raise RuntimeError("PDF is password-protected; correct password required")
        page_count = doc.page_count
        pages = parse_page_range(page_spec, page_count)
        zoom = dpi / 72.0
        mat = pymupdf.Matrix(zoom, zoom)
        multi = len(pages) > 1
        for i, page_index in enumerate(pages):
            if token.cancelled:
                raise InterruptedError("Cancelled by user")
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=mat, alpha=(target_ext in ("png", "webp", "ico")))
            if multi:
                out_name = f"{sanitize_filename(src.stem)}_page_{page_index + 1:03d}.{target_ext}"
            else:
                out_name = f"{sanitize_filename(src.stem)}.{target_ext}"
            out_path = unique_path(job.output_dir / out_name)
            if target_ext == "ico" and PIL_AVAILABLE:
                im = Image.frombytes("RGBA" if pix.alpha else "RGB", (pix.width, pix.height), pix.samples)
                _save_as_ico(im, out_path)
            else:
                fmt_map = {"jpg": "jpeg"}
                pix.save(str(out_path), output=fmt_map.get(target_ext, target_ext))
            outputs.append(out_path)
            signals.progress.emit(job.job_id, int((i + 1) / len(pages) * 100))
        doc.close()

    elif PYPDFIUM2_AVAILABLE:
        try:
            pdf = pdfium.PdfDocument(str(src), password=password or None)
        except Exception as e:
            raise RuntimeError(f"Could not open PDF (wrong password or corrupt file): {e}")
        page_count = len(pdf)
        pages = parse_page_range(page_spec, page_count)
        scale = dpi / 72.0
        multi = len(pages) > 1
        for i, page_index in enumerate(pages):
            if token.cancelled:
                raise InterruptedError("Cancelled by user")
            page = pdf[page_index]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            if multi:
                out_name = f"{sanitize_filename(src.stem)}_page_{page_index + 1:03d}.{target_ext}"
            else:
                out_name = f"{sanitize_filename(src.stem)}.{target_ext}"
            out_path = unique_path(job.output_dir / out_name)
            if target_ext == "ico":
                _save_as_ico(pil_image, out_path)
            else:
                fmt_map = {"jpg": "JPEG", "jpeg": "JPEG"}
                fmt = fmt_map.get(target_ext, target_ext.upper())
                if fmt == "JPEG" and pil_image.mode != "RGB":
                    pil_image = pil_image.convert("RGB")
                pil_image.save(out_path, fmt)
            outputs.append(out_path)
            signals.progress.emit(job.job_id, int((i + 1) / len(pages) * 100))
    else:
        raise RuntimeError("No PDF backend installed. Run: pip install pymupdf")

    return outputs


# --------------------------------------------------------------------------
# Worker (runs in QThreadPool)
# --------------------------------------------------------------------------

class ConversionWorker(QRunnable):
    def __init__(self, job: ConversionJob, signals: JobSignals, backends: BackendRegistry,
                 token: CancelToken, proc_registry: dict):
        super().__init__()
        self.job = job
        self.signals = signals
        self.backends = backends
        self.token = token
        self.proc_registry = proc_registry
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        job = self.job
        if self.token.cancelled:
            self.signals.status_changed.emit(job.job_id, "Cancelled")
            self.signals.finished.emit(job.job_id, False, "Cancelled before start", "")
            return

        self.signals.status_changed.emit(job.job_id, "Converting")
        try:
            cat = category_of_ext(job.source_path.suffix)
            if cat == "image":
                out_path = convert_image(job, self.signals, self.token)
            elif cat in ("audio", "video"):
                if not self.backends.ffmpeg_available:
                    raise RuntimeError("FFmpeg not available. Run: pip install imageio-ffmpeg")
                out_path = convert_media(job, self.signals, self.token, self.backends.ffmpeg_path, self.proc_registry)
            elif cat == "pdf":
                if not self.backends.pdf_available:
                    raise RuntimeError("No PDF backend installed. Run: pip install pymupdf")
                results = pdf_to_images(job, self.signals, self.token)
                out_path = results[0] if results else None
            else:
                raise RuntimeError(f"Unsupported source type: {job.source_path.suffix}")

            if out_path is None or not Path(out_path).exists() or Path(out_path).stat().st_size == 0:
                raise RuntimeError("Output file was not created or is empty")

            self.signals.status_changed.emit(job.job_id, "Done")
            self.signals.finished.emit(job.job_id, True, "OK", str(out_path))

        except InterruptedError:
            self.signals.status_changed.emit(job.job_id, "Cancelled")
            self.signals.finished.emit(job.job_id, False, "Cancelled", "")
        except Exception as e:
            tb = traceback.format_exc()
            self.signals.log.emit("ERROR", f"{job.source_path.name}: {e}\n{tb}")
            self.signals.status_changed.emit(job.job_id, "Failed")
            self.signals.finished.emit(job.job_id, False, str(e), "")


# --------------------------------------------------------------------------
# UI: Drop zone
# --------------------------------------------------------------------------

class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("DropZone")
        self.setMinimumHeight(120)
        self._default_style()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label = QLabel()
        self.icon_label.setPixmap(svg_icon(ICON_UPLOAD, 36).pixmap(36, 36))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setObjectName("DropIcon")
        self.text_label = QLabel("Drag & drop files or folders here")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setObjectName("DropText")
        self.sub_label = QLabel("Images, audio, video, and PDF files are supported")
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setObjectName("DropSubText")
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.sub_label)

    def _default_style(self) -> None:
        self.setStyleSheet("""
            #DropZone {
                border: 2px dashed palette(mid);
                border-radius: 12px;
                background: palette(base);
            }
            #DropText { font-size: 14px; font-weight: 600; color: #ffffff; }
            #DropSubText { font-size: 11px; color: #ffffff; }
        """)

    def _hover_style(self) -> None:
        self.setStyleSheet("""
            #DropZone {
                border: 2px dashed #4C8DFF;
                border-radius: 12px;
                background: rgba(76, 141, 255, 0.08);
            }
            #DropText { font-size: 14px; font-weight: 600; color: #ffffff; }
            #DropSubText { font-size: 11px; color: #ffffff; }
        """)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            self._hover_style()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._default_style()

    def dropEvent(self, event: QDropEvent) -> None:
        self._default_style()
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()


# --------------------------------------------------------------------------
# UI: Options panel (context sensitive)
# --------------------------------------------------------------------------

class OptionsDialog(QDialog):
    options_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Conversion Options")
        self.setMinimumWidth(500)
        self.setMinimumHeight(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        self.tabs = QTabWidget(self)

        # Common options tab
        general_tab = QWidget()
        gen_layout = QVBoxLayout(general_tab)
        common_box = QGroupBox("General")
        common_form = QFormLayout(common_box)
        self.preserve_metadata_cb = QCheckBox("Preserve metadata (EXIF/ID3)")
        self.preserve_metadata_cb.setChecked(True)
        self.overwrite_cb = QCheckBox("Overwrite existing files")
        self.overwrite_cb.setChecked(False)
        common_form.addRow(self.preserve_metadata_cb)
        common_form.addRow(self.overwrite_cb)
        gen_layout.addWidget(common_box)
        gen_layout.addStretch(1)
        self.tabs.addTab(general_tab, "General")

        # Image page
        self.image_page = QWidget()
        img_layout = QVBoxLayout(self.image_page)
        img_box = QGroupBox("Image Output")
        img_form = QFormLayout(img_box)
        self.jpeg_quality_spin = QSpinBox()
        self.jpeg_quality_spin.setRange(1, 100)
        self.jpeg_quality_spin.setValue(90)
        img_form.addRow("JPEG/WebP quality:", self.jpeg_quality_spin)
        self.fast_png_cb = QCheckBox("Fast PNG compression (much faster saving)")
        self.fast_png_cb.setChecked(True)
        img_form.addRow(self.fast_png_cb)
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["Auto", "A4", "Letter", "Legal"])
        img_form.addRow("PDF page size:", self.page_size_combo)
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["Auto", "Portrait", "Landscape"])
        img_form.addRow("PDF orientation:", self.orientation_combo)
        self.one_pdf_per_image_cb = QCheckBox("One PDF per image (instead of combined)")
        img_form.addRow(self.one_pdf_per_image_cb)
        img_layout.addWidget(img_box)
        img_layout.addStretch(1)
        self.tabs.addTab(self.image_page, "Image")

        # Audio page
        self.audio_page = QWidget()
        aud_layout = QVBoxLayout(self.audio_page)
        aud_box = QGroupBox("Audio Encoding")
        aud_form = QFormLayout(aud_box)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(["Auto", "96k", "128k", "160k", "192k", "256k", "320k"])
        aud_form.addRow("Bitrate:", self.audio_bitrate_combo)
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(["Auto", "22050", "44100", "48000", "96000"])
        aud_form.addRow("Sample rate (Hz):", self.sample_rate_combo)
        self.channels_combo = QComboBox()
        self.channels_combo.addItems(["Auto", "1", "2"])
        aud_form.addRow("Channels:", self.channels_combo)
        aud_layout.addWidget(aud_box)
        aud_layout.addStretch(1)
        self.tabs.addTab(self.audio_page, "Audio")

        # Video page
        self.video_page = QWidget()
        vid_layout = QVBoxLayout(self.video_page)
        vid_box = QGroupBox("Video Encoding")
        vid_form = QFormLayout(vid_box)
        self.video_preset_combo = QComboBox()
        self.video_preset_combo.addItems(["veryfast", "faster", "fast", "medium", "ultrafast"])
        self.video_preset_combo.setCurrentText("veryfast")
        vid_form.addRow("Encoding speed preset:", self.video_preset_combo)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["Source", "1920:-2", "1280:-2", "854:-2", "640:-2"])
        vid_form.addRow("Resolution:", self.resolution_combo)
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(23)
        vid_form.addRow("Quality (CRF, lower=better):", self.crf_spin)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["Source", "15", "24", "25", "30", "60"])
        vid_form.addRow("FPS:", self.fps_combo)
        self.gif_fps_spin = QSpinBox()
        self.gif_fps_spin.setRange(1, 30)
        self.gif_fps_spin.setValue(10)
        vid_form.addRow("GIF fps:", self.gif_fps_spin)
        self.gif_width_spin = QSpinBox()
        self.gif_width_spin.setRange(64, 1920)
        self.gif_width_spin.setValue(480)
        vid_form.addRow("GIF width (px):", self.gif_width_spin)
        vid_layout.addWidget(vid_box)
        vid_layout.addStretch(1)
        self.tabs.addTab(self.video_page, "Video")

        # PDF page
        self.pdf_page = QWidget()
        pdf_layout = QVBoxLayout(self.pdf_page)
        pdf_box = QGroupBox("PDF Conversion")
        pdf_form = QFormLayout(pdf_box)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(36, 1200)
        self.dpi_spin.setValue(150)
        pdf_form.addRow("DPI:", self.dpi_spin)
        self.page_range_edit = QLineEdit("all")
        self.page_range_edit.setPlaceholderText("all, 1-5, 2,4,7")
        pdf_form.addRow("Page range:", self.page_range_edit)
        self.pdf_password_edit = QLineEdit()
        self.pdf_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pdf_password_edit.setPlaceholderText("(if protected)")
        pdf_form.addRow("Password:", self.pdf_password_edit)
        pdf_layout.addWidget(pdf_box)
        pdf_layout.addStretch(1)
        self.tabs.addTab(self.pdf_page, "PDF")

        outer.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        close_btn = QPushButton("Done")
        close_btn.setObjectName("PrimaryButton")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    def _reset_defaults(self) -> None:
        self.preserve_metadata_cb.setChecked(True)
        self.overwrite_cb.setChecked(False)
        self.jpeg_quality_spin.setValue(90)
        self.fast_png_cb.setChecked(True)
        self.page_size_combo.setCurrentText("Auto")
        self.orientation_combo.setCurrentText("Auto")
        self.one_pdf_per_image_cb.setChecked(False)
        self.audio_bitrate_combo.setCurrentText("Auto")
        self.sample_rate_combo.setCurrentText("Auto")
        self.channels_combo.setCurrentText("Auto")
        self.video_preset_combo.setCurrentText("veryfast")
        self.resolution_combo.setCurrentText("Source")
        self.crf_spin.setValue(23)
        self.fps_combo.setCurrentText("Source")
        self.gif_fps_spin.setValue(10)
        self.gif_width_spin.setValue(480)
        self.dpi_spin.setValue(150)
        self.page_range_edit.setText("all")
        self.pdf_password_edit.clear()

    def show_for_category(self, category: str) -> None:
        idx_map = {"image": 1, "audio": 2, "video": 3, "pdf": 4}
        idx = idx_map.get(category)
        if idx is not None:
            self.tabs.setCurrentIndex(idx)

    def gather_options(self) -> dict:
        return {
            "preserve_metadata": self.preserve_metadata_cb.isChecked(),
            "overwrite": self.overwrite_cb.isChecked(),
            "jpeg_quality": self.jpeg_quality_spin.value(),
            "fast_png": self.fast_png_cb.isChecked(),
            "page_size": self.page_size_combo.currentText().lower(),
            "orientation": self.orientation_combo.currentText().lower(),
            "one_pdf_per_image": self.one_pdf_per_image_cb.isChecked(),
            "bitrate": self.audio_bitrate_combo.currentText(),
            "sample_rate": self.sample_rate_combo.currentText(),
            "channels": self.channels_combo.currentText(),
            "video_preset": self.video_preset_combo.currentText(),
            "resolution": self.resolution_combo.currentText(),
            "crf": self.crf_spin.value(),
            "fps": self.fps_combo.currentText(),
            "gif_fps": self.gif_fps_spin.value(),
            "gif_width": self.gif_width_spin.value(),
            "dpi": self.dpi_spin.value(),
            "page_range": self.page_range_edit.text(),
            "password": self.pdf_password_edit.text(),
        }


OptionsPanel = OptionsDialog


# --------------------------------------------------------------------------
# UI: File table
# --------------------------------------------------------------------------

COL_NAME, COL_TYPE, COL_SIZE, COL_FORMAT, COL_STATUS, COL_PROGRESS, COL_REMOVE = range(7)


class FileTable(QTableWidget):
    remove_requested = Signal(int)          # row
    remove_all_requested = Signal()
    open_folder_requested = Signal(int)     # row
    target_format_changed = Signal(int, str)  # row, new ext

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(0, 7, parent)
        self.setHorizontalHeaderLabels(
            ["File", "Type", "Size", "Convert to", "Status", "Progress", ""]
        )
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TYPE, 75)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_SIZE, 80)
        header.setSectionResizeMode(COL_FORMAT, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_FORMAT, 105)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_STATUS, 95)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_PROGRESS, 130)
        header.setSectionResizeMode(COL_REMOVE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_REMOVE, 36)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.verticalHeader().setVisible(False)

    def add_row(self, job: ConversionJob) -> None:
        row = self.rowCount()
        self.insertRow(row)

        name_item = QTableWidgetItem(job.source_path.name)
        name_item.setToolTip(str(job.source_path))
        self.setItem(row, COL_NAME, name_item)

        cat = category_of_ext(job.source_path.suffix)
        self.setItem(row, COL_TYPE, QTableWidgetItem(cat.capitalize()))

        try:
            size = job.source_path.stat().st_size
        except OSError:
            size = 0
        self.setItem(row, COL_SIZE, QTableWidgetItem(human_size(size)))

        combo = QComboBox()
        src_ext = job.source_path.suffix.lstrip(".").lower()
        targets = valid_targets_for(src_ext)
        combo.addItems(targets)
        if job.target_ext in targets:
            combo.setCurrentText(job.target_ext)
        combo.currentTextChanged.connect(lambda text, c=combo: self._on_combo_format_changed(c, text))
        self.setCellWidget(row, COL_FORMAT, combo)

        self.setItem(row, COL_STATUS, QTableWidgetItem(job.status))

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(True)
        bar.setFixedWidth(120)
        self.setCellWidget(row, COL_PROGRESS, bar)

        remove_btn = QToolButton()
        remove_btn.setIcon(svg_icon(ICON_CLOSE, 12))
        remove_btn.setToolTip("Remove")
        remove_btn.clicked.connect(lambda _, b=remove_btn: self._on_remove_btn_clicked(b))
        self.setCellWidget(row, COL_REMOVE, remove_btn)

    def _on_remove_btn_clicked(self, btn: QWidget) -> None:
        for r in range(self.rowCount()):
            if self.cellWidget(r, COL_REMOVE) is btn:
                self.remove_requested.emit(r)
                return

    def _on_combo_format_changed(self, combo: QComboBox, text: str) -> None:
        for r in range(self.rowCount()):
            if self.cellWidget(r, COL_FORMAT) is combo:
                self.target_format_changed.emit(r, text)
                return

    def set_status(self, row: int, status: str) -> None:
        item = self.item(row, COL_STATUS)
        if item:
            item.setText(status)
            color = {
                "Done": QColor("#2ecc71"),
                "Failed": QColor("#e74c3c"),
                "Converting": QColor("#4C8DFF"),
                "Cancelled": QColor("#95a5a6"),
            }.get(status)
            if color:
                item.setForeground(color)

    def set_progress(self, row: int, percent: int) -> None:
        bar = self.cellWidget(row, COL_PROGRESS)
        if isinstance(bar, QProgressBar):
            bar.setValue(percent)

    def target_combo(self, row: int) -> Optional[QComboBox]:
        w = self.cellWidget(row, COL_FORMAT)
        return w if isinstance(w, QComboBox) else None

    def _show_context_menu(self, pos: QPoint) -> None:
        row = self.rowAt(pos.y())
        menu = QMenu(self)
        remove_action = menu.addAction("Remove")
        remove_all_action = menu.addAction("Remove all")
        open_folder_action = menu.addAction("Open containing folder")
        if row < 0:
            remove_action.setEnabled(False)
            open_folder_action.setEnabled(False)
        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == remove_action and row >= 0:
            self.remove_requested.emit(row)
        elif action == remove_all_action:
            self.remove_all_requested.emit()
        elif action == open_folder_action and row >= 0:
            self.open_folder_requested.emit(row)


# --------------------------------------------------------------------------
# UI: Log panel
# --------------------------------------------------------------------------

class LogPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title_label = QLabel("ACTIVITY LOG")
        title_label.setStyleSheet("font-weight: 700; font-size: 13px; color: #9fb4d8; letter-spacing: 0.5px;")
        header.addWidget(title_label)
        header.addStretch(1)

        self.clear_btn = QPushButton(" Clear log")
        self.clear_btn.setIcon(svg_icon(ICON_TRASH, 13))
        self.clear_btn.clicked.connect(self._clear_log)
        self.copy_btn = QPushButton(" Copy log")
        self.copy_btn.clicked.connect(self._copy_log)
        self.open_folder_btn = QPushButton(" Open log folder")
        self.open_folder_btn.setIcon(svg_icon(ICON_FOLDER, 13))
        header.addWidget(self.clear_btn)
        header.addWidget(self.copy_btn)
        header.addWidget(self.open_folder_btn)
        layout.addLayout(header)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setVisible(True)
        self.text.setMinimumHeight(120)
        self.text.setMaximumHeight(200)
        self.text.setFont(QFont("Menlo, Consolas, monospace", 10))
        layout.addWidget(self.text)

    def _clear_log(self) -> None:
        self.text.clear()

    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def append(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.text.appendPlainText(f"[{ts}] {level:<5} {message}")
        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

DARK_QSS = """
QWidget { background-color: #1e1f22; color: #e6e6e6; font-size: 13px; }
QMainWindow, QDialog { background-color: #1e1f22; }
QToolBar { background-color: #26282c; border: none; spacing: 6px; padding: 4px; }
QStatusBar { background-color: #26282c; }
QGroupBox {
    border: 1px solid #3a3d42; border-radius: 8px; margin-top: 10px; padding-top: 10px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    background-color: #33363b; border: 1px solid #45484e; border-radius: 8px;
    padding: 6px 14px;
}
QPushButton:hover { background-color: #3d4046; border-color: #4C8DFF; }
QPushButton:pressed { background-color: #2a2c30; }
QPushButton:disabled { color: #77797d; background-color: #2a2c30; }
QPushButton#PrimaryButton { background-color: #4C8DFF; border: none; color: white; font-weight: 600; }
QPushButton#PrimaryButton:hover { background-color: #6aa0ff; }
QPushButton#PrimaryButton:disabled { background-color: #35415c; color: #8892a5; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: #2a2c30; border: 1px solid #45484e; border-radius: 6px; padding: 4px 6px;
}
QComboBox:hover, QLineEdit:hover { border-color: #4C8DFF; }
QTableWidget {
    background-color: #232427; alternate-background-color: #26282c;
    gridline-color: #3a3d42; border: 1px solid #3a3d42; border-radius: 8px;
}
QHeaderView::section {
    background-color: #2a2c30; padding: 6px; border: none; border-bottom: 1px solid #3a3d42;
}
QProgressBar { border: 1px solid #3a3d42; border-radius: 6px; text-align: center; background: #2a2c30; }
QProgressBar::chunk { background-color: #4C8DFF; border-radius: 5px; }
QPlainTextEdit { background-color: #16171a; border: 1px solid #3a3d42; border-radius: 6px; }
QTabWidget::pane { border: 1px solid #3a3d42; border-radius: 6px; top: -1px; background-color: #232427; padding: 10px; }
QTabBar::tab { background-color: #2a2c30; border: 1px solid #3a3d42; padding: 6px 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
QTabBar::tab:selected { background-color: #232427; border-bottom-color: #232427; font-weight: 600; color: #4C8DFF; }
QTabBar::tab:hover:!selected { background-color: #35383e; }
QToolButton { color: #e6e6e6; border: none; }
QToolButton:hover { color: #4C8DFF; }
QCheckBox, QLabel { background: transparent; }
QSplitter::handle { background-color: #3a3d42; }
QSplitter::handle:hover { background-color: #4C8DFF; }
#LeftPane, #RightPane { background-color: #1e1f22; }
"""

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.backends = BackendRegistry()

        self.jobs: dict[int, ConversionJob] = {}
        self.job_rows: dict[int, int] = {}
        self._next_job_id = 1
        self.cancel_token = CancelToken()
        self.proc_registry: dict[int, subprocess.Popen] = {}
        self.thread_pool = QThreadPool.globalInstance()
        # QThreadPool only takes ownership of the C++ side of a QRunnable; without
        # an explicit Python reference here, the worker (and the QObject signals
        # it owns) can be garbage-collected while still running on a worker
        # thread, corrupting or crashing an in-flight conversion.
        self._active_workers: dict[int, tuple["ConversionWorker", JobSignals]] = {}
        self._active_jobs = 0
        self._total_jobs_in_batch = 0
        self._completed_jobs_in_batch = 0
        self._converting = False

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(900, 600)
        self.resize(1100, 720)

        self._build_ui()
        self._restore_settings()
        self._log_startup_report()
        self._apply_backend_banners()

    # ---------------- UI construction ----------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(10)

        # Banner area for missing backends (full width, above the panes)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.banner_label.setStyleSheet(
            "background-color: #5a4300; color: #ffe9a8; border-radius: 8px; padding: 8px;"
        )
        self.banner_label.setVisible(False)
        root.addWidget(self.banner_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

        left_pane = self._build_left_pane()
        right_pane = self._build_right_pane()
        splitter.addWidget(left_pane)
        splitter.addWidget(right_pane)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 840])

        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 700; font-size: 13px; color: #9fb4d8; letter-spacing: 0.5px;")
        return label

    def _build_left_pane(self) -> QWidget:
        pane = QScrollArea()
        pane.setWidgetResizable(True)
        pane.setFrameShape(QFrame.Shape.NoFrame)
        pane.setMinimumWidth(300)
        pane.setMaximumWidth(460)

        inner = QWidget()
        inner.setObjectName("LeftPane")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(10)

        # -- Add files --
        layout.addWidget(self._section_label("ADD FILES"))
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._add_paths)
        layout.addWidget(self.drop_zone)

        browse_row = QHBoxLayout()
        self.browse_files_btn = QPushButton("Browse Files…")
        self.browse_files_btn.clicked.connect(self._browse_files)
        self.browse_folder_btn = QPushButton("Browse Folder…")
        self.browse_folder_btn.clicked.connect(self._browse_folder)
        browse_row.addWidget(self.browse_files_btn)
        browse_row.addWidget(self.browse_folder_btn)
        layout.addLayout(browse_row)

        # -- Convert Selected To (Moved to Left Pane) --
        layout.addWidget(self._section_label("CONVERT SELECTED TO"))
        self.bulk_format_combo = QComboBox()
        self.bulk_format_combo.setMinimumHeight(32)
        self.bulk_format_combo.setToolTip("Select files in the table to batch-change their output format.")
        self.bulk_format_combo.currentTextChanged.connect(self._apply_bulk_format)
        layout.addWidget(self.bulk_format_combo)

        # -- Output --
        layout.addWidget(self._section_label("OUTPUT"))
        output_group = QGroupBox()
        output_form = QVBoxLayout(output_group)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("converted/ subfolder next to each source file")
        output_form.addWidget(self.output_edit)
        out_btn_row = QHBoxLayout()
        self.output_browse_btn = QPushButton("Browse…")
        self.output_browse_btn.setIcon(svg_icon(ICON_FOLDER, 14))
        self.output_browse_btn.clicked.connect(self._browse_output)
        self.use_source_folder_cb = QCheckBox("Use source folder")
        self.use_source_folder_cb.toggled.connect(self._on_use_source_folder_toggled)
        out_btn_row.addWidget(self.output_browse_btn)
        out_btn_row.addWidget(self.use_source_folder_cb)
        output_form.addLayout(out_btn_row)
        layout.addWidget(output_group)

        # -- Options --
        layout.addWidget(self._section_label("OPTIONS"))
        self.options_dialog = OptionsDialog(self)
        self.options_panel = self.options_dialog
        self.options_btn = QPushButton(" Conversion Options…")
        self.options_btn.setIcon(svg_icon(ICON_SETTINGS, 15))
        self.options_btn.setMinimumHeight(32)
        self.options_btn.clicked.connect(self._open_options_dialog)
        layout.addWidget(self.options_btn)

        # -- Performance --
        layout.addWidget(self._section_label("PERFORMANCE"))
        workers_row = QHBoxLayout()
        workers_row.addWidget(QLabel("Parallel workers:"))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, max(1, (os.cpu_count() or 4) * 2))
        self.workers_spin.setValue(min(os.cpu_count() or 4, 16))
        self.workers_spin.valueChanged.connect(self._on_workers_changed)
        workers_row.addWidget(self.workers_spin)
        workers_row.addStretch(1)
        layout.addLayout(workers_row)

        layout.addStretch(1)

        # -- Actions --
        layout.addWidget(self._section_label("ACTIONS"))
        self.convert_btn = QPushButton(" Convert")
        self.convert_btn.setIcon(svg_icon(ICON_PLAY, 14))
        self.convert_btn.setObjectName("PrimaryButton")
        self.convert_btn.clicked.connect(self._start_conversion)
        self.convert_btn.setEnabled(False)
        self.convert_btn.setMinimumHeight(36)
        layout.addWidget(self.convert_btn)

        secondary_row = QHBoxLayout()
        self.cancel_btn = QPushButton(" Cancel")
        self.cancel_btn.setIcon(svg_icon(ICON_STOP, 14))
        self.cancel_btn.clicked.connect(self._cancel_conversion)
        self.cancel_btn.setEnabled(False)
        self.clear_completed_btn = QPushButton(" Clear completed")
        self.clear_completed_btn.setIcon(svg_icon(ICON_TRASH, 14))
        self.clear_completed_btn.clicked.connect(self._clear_completed)
        secondary_row.addWidget(self.cancel_btn)
        secondary_row.addWidget(self.clear_completed_btn)
        layout.addLayout(secondary_row)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setTextVisible(True)
        layout.addWidget(self.overall_progress)

        self.counter_label = QLabel("")
        self.counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.counter_label)

        pane.setWidget(inner)
        return pane

    def _open_options_dialog(self) -> None:
        self.options_dialog.exec()

    def _build_right_pane(self) -> QWidget:
        inner = QWidget()
        inner.setObjectName("RightPane")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.addWidget(self._section_label("FILES"))
        header_row.addStretch(1)
        layout.addLayout(header_row)

        self.table = FileTable()
        self.table.remove_requested.connect(self._remove_row)
        self.table.remove_all_requested.connect(self._remove_all)
        self.table.open_folder_requested.connect(self._open_containing_folder)
        self.table.target_format_changed.connect(self._on_row_format_changed)
        self.table.itemSelectionChanged.connect(self._refresh_bulk_format_options)
        layout.addWidget(self.table, stretch=1)

        self.log_panel = LogPanel()
        self.log_panel.open_folder_btn.clicked.connect(self._open_log_folder)
        layout.addWidget(self.log_panel)

        return inner

    # ---------------- Settings ----------------

    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        QApplication.instance().setStyleSheet(DARK_QSS)
        output_dir = self.settings.value("last_output_dir", "")
        if output_dir:
            self.output_edit.setText(output_dir)
        use_source = self.settings.value("use_source_folder", False, type=bool)
        self.use_source_folder_cb.setChecked(use_source)
        workers = self.settings.value("workers", self.workers_spin.value(), type=int)
        self.workers_spin.setValue(workers)
        self.thread_pool.setMaxThreadCount(self.workers_spin.value())

    def _save_settings(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("last_output_dir", self.output_edit.text())
        self.settings.setValue("use_source_folder", self.use_source_folder_cb.isChecked())
        self.settings.setValue("workers", self.workers_spin.value())

    # ---------------- Startup diagnostics ----------------

    def _log_startup_report(self) -> None:
        for line in self.backends.startup_report_lines():
            self.log_panel.append("INFO", line)

    def _apply_backend_banners(self) -> None:
        missing = self.backends.missing_backends_report()
        if missing:
            text = "Some features are unavailable until you install extra packages:\n" + "\n".join(
                f"  • {m}" for m in missing
            )
            self.banner_label.setText(text)
            self.banner_label.setVisible(True)

    # ---------------- Adding files ----------------

    def _browse_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Select files to convert")
        if files:
            self._add_paths([Path(f) for f in files])

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            self._add_paths([Path(folder)])

    def _add_paths(self, paths: list[Path]) -> None:
        all_files: list[Path] = []
        for p in paths:
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file():
                        all_files.append(f)
            elif p.is_file():
                all_files.append(p)

        self.table.setUpdatesEnabled(False)
        added = 0
        try:
            for f in all_files:
                cat = category_of_ext(f.suffix)
                if cat == "unknown":
                    self.log_panel.append("WARN", f"Skipping unsupported file: {f.name}")
                    continue
                targets = valid_targets_for(f.suffix)
                if not targets:
                    continue
                job_id = self._next_job_id
                self._next_job_id += 1
                default_target = targets[0]
                out_dir = self._default_output_dir_for(f)
                job = ConversionJob(job_id=job_id, source_path=f, target_ext=default_target, output_dir=out_dir)
                self.jobs[job_id] = job
                self.table.add_row(job)
                self.job_rows[job_id] = self.table.rowCount() - 1
                added += 1
        finally:
            self.table.setUpdatesEnabled(True)

        if added:
            self.log_panel.append("INFO", f"Added {added} file(s)")
        self._refresh_bulk_format_options()
        self._update_convert_button_state()

    def _default_output_dir_for(self, source: Path) -> Path:
        if self.use_source_folder_cb.isChecked():
            return source.parent
        custom = self.output_edit.text().strip()
        if custom:
            return Path(custom)
        return source.parent / "converted"

    # ---------------- Row management ----------------

    def _row_for_job_id(self, job_id: int) -> Optional[int]:
        return self.job_rows.get(job_id)

    def _job_id_for_row(self, row: int) -> Optional[int]:
        for jid, r in self.job_rows.items():
            if r == row:
                return jid
        return None

    def _remove_row(self, row: int) -> None:
        job_id = self._job_id_for_row(row)
        self.table.removeRow(row)
        if job_id is not None:
            self.jobs.pop(job_id, None)
            self.job_rows.pop(job_id, None)
        # Reindex row mapping after removal.
        self.job_rows = {jid: (r - 1 if r > row else r) for jid, r in self.job_rows.items()}
        self._update_convert_button_state()

    def _remove_all(self) -> None:
        self.table.setRowCount(0)
        self.jobs.clear()
        self.job_rows.clear()
        self._update_convert_button_state()

    def _clear_completed(self) -> None:
        rows_to_remove = []
        for job_id, row in list(self.job_rows.items()):
            job = self.jobs.get(job_id)
            if job and job.status in ("Done", "Cancelled"):
                rows_to_remove.append(row)
        for row in sorted(rows_to_remove, reverse=True):
            self._remove_row(row)

    def _open_containing_folder(self, row: int) -> None:
        job_id = self._job_id_for_row(row)
        if job_id is None:
            return
        job = self.jobs[job_id]
        folder = job.output_path.parent if job.output_path else job.source_path.parent
        self._open_folder(folder)

    def _open_folder(self, folder: Path) -> None:
        try:
            if platform.system() == "Windows":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            self.log_panel.append("ERROR", f"Could not open folder: {e}")

    def _open_log_folder(self) -> None:
        log_dir = Path.home() / ".file_converter_logs"
        log_dir.mkdir(exist_ok=True)
        self._open_folder(log_dir)

    def _on_row_format_changed(self, row: int, new_ext: str) -> None:
        job_id = self._job_id_for_row(row)
        if job_id is not None and new_ext:
            self.jobs[job_id].target_ext = new_ext

    # ---------------- Bulk format selection ----------------

    def _refresh_bulk_format_options(self) -> None:
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        rows = selected_rows if selected_rows else list(range(self.table.rowCount()))
        exts = []
        for row in rows:
            job_id = self._job_id_for_row(row)
            if job_id:
                exts.append(self.jobs[job_id].source_path.suffix.lstrip(".").lower())
        common = common_valid_targets(exts) if exts else []

        self.bulk_format_combo.blockSignals(True)
        self.bulk_format_combo.clear()
        if common:
            self.bulk_format_combo.addItems(common)
            self.bulk_format_combo.setEnabled(True)
            self.bulk_format_combo.setToolTip("")
        else:
            self.bulk_format_combo.setEnabled(False)
            self.bulk_format_combo.setToolTip(
                "No common target format for the current selection (mixed file types)."
            )
        self.bulk_format_combo.blockSignals(False)

    def _apply_bulk_format(self, text: str) -> None:
        if not text:
            return
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        rows = selected_rows if selected_rows else list(range(self.table.rowCount()))
        for row in rows:
            combo = self.table.target_combo(row)
            if combo and text in [combo.itemText(i) for i in range(combo.count())]:
                combo.setCurrentText(text)

    # ---------------- Output folder handling ----------------

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.output_edit.setText(folder)
            self.use_source_folder_cb.setChecked(False)
            self._refresh_output_dirs()

    def _on_use_source_folder_toggled(self, checked: bool) -> None:
        self.output_edit.setEnabled(not checked)
        self.output_browse_btn.setEnabled(not checked)
        self._refresh_output_dirs()

    def _refresh_output_dirs(self) -> None:
        for job in self.jobs.values():
            job.output_dir = self._default_output_dir_for(job.source_path)

    # ---------------- Workers ----------------

    def _on_workers_changed(self, value: int) -> None:
        self.thread_pool.setMaxThreadCount(value)

    # ---------------- Conversion lifecycle ----------------

    def _update_convert_button_state(self) -> None:
        has_valid_jobs = any(
            job.status not in ("Done",) and valid_targets_for(job.source_path.suffix)
            for job in self.jobs.values()
        )
        self.convert_btn.setEnabled(has_valid_jobs and not self._converting)

    def _start_conversion(self) -> None:
        pending_jobs = [j for j in self.jobs.values() if j.status in ("Queued", "Failed")]
        if not pending_jobs:
            QMessageBox.information(self, APP_NAME, "Nothing to convert.")
            return

        self.cancel_token = CancelToken()
        self._converting = True
        self._total_jobs_in_batch = len(pending_jobs)
        self._completed_jobs_in_batch = 0
        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.overall_progress.setValue(0)
        self._per_job_progress: dict[int, int] = {j.job_id: 0 for j in pending_jobs}
        self.statusBar().showMessage("Converting…")

        common_options = self.options_panel.gather_options()

        for job in pending_jobs:
            job.options.update(common_options)
            row = self._row_for_job_id(job.job_id)
            if row is not None:
                self.table.set_status(row, "Queued")
                self.table.set_progress(row, 0)
            job.output_dir = self._default_output_dir_for(job.source_path)

            signals = JobSignals()
            signals.progress.connect(self._on_job_progress)
            signals.status_changed.connect(self._on_job_status)
            signals.log.connect(self._on_job_log)
            signals.finished.connect(self._on_job_finished)

            worker = ConversionWorker(job, signals, self.backends, self.cancel_token, self.proc_registry)
            self._active_workers[job.job_id] = (worker, signals)
            self.thread_pool.start(worker)

        self._update_counter_label()

    def _cancel_conversion(self) -> None:
        self.cancel_token.cancel()
        for proc in list(self.proc_registry.values()):
            _terminate_process(proc)
        self.log_panel.append("INFO", "Cancellation requested by user")
        self.cancel_btn.setEnabled(False)
        self.statusBar().showMessage("Cancelling…")

    @Slot(int, int)
    def _on_job_progress(self, job_id: int, percent: int) -> None:
        row = self._row_for_job_id(job_id)
        if row is not None:
            self.table.set_progress(row, percent)
        self._per_job_progress[job_id] = percent
        self._update_overall_progress()

    @Slot(int, str)
    def _on_job_status(self, job_id: int, status: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.status = status
        row = self._row_for_job_id(job_id)
        if row is not None:
            self.table.set_status(row, status)

    @Slot(str, str)
    def _on_job_log(self, level: str, message: str) -> None:
        self.log_panel.append(level, message)

    @Slot(int, bool, str, str)
    def _on_job_finished(self, job_id: int, success: bool, message: str, output_path: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.status = "Done" if success else ("Cancelled" if "Cancelled" in message else "Failed")
            job.error = "" if success else message
            job.output_path = Path(output_path) if output_path else None

        row = self._row_for_job_id(job_id)
        if row is not None:
            self.table.set_status(row, job.status if job else ("Done" if success else "Failed"))
            self.table.set_progress(row, 100 if success else self.table.cellWidget(row, COL_PROGRESS).value())
            if not success and job:
                item = self.table.item(row, COL_NAME)
                if item:
                    item.setToolTip(f"{job.source_path}\nError: {job.error}")

        self._per_job_progress[job_id] = 100
        self._completed_jobs_in_batch += 1
        self._update_overall_progress()
        self._update_counter_label()
        self._active_workers.pop(job_id, None)

        if self._completed_jobs_in_batch >= self._total_jobs_in_batch:
            self._converting = False
            self.cancel_btn.setEnabled(False)
            self._update_convert_button_state()
            self.statusBar().showMessage("Conversion finished")

    def _update_overall_progress(self) -> None:
        if not self._per_job_progress:
            self.overall_progress.setValue(0)
            return
        avg = sum(self._per_job_progress.values()) / len(self._per_job_progress)
        self.overall_progress.setValue(int(avg))

    def _update_counter_label(self) -> None:
        self.counter_label.setText(f"{self._completed_jobs_in_batch} of {self._total_jobs_in_batch}")

    # ---------------- Close handling ----------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._converting:
            reply = QMessageBox.question(
                self, APP_NAME,
                "A conversion is in progress. Cancel and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancel_conversion()
        self._active_workers.clear()
        self._save_settings()
        event.accept()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())