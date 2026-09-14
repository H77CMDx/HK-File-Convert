#!/usr/bin/env python3
"""
Fully offline desktop file converter with a modern PySide6 GUI.
Partially vibe coded : Claude Sonnet 5, GPT 5.6 Luna

Supports:
    - Images       (via Pillow)
    - Audio/Video  (via FFmpeg, bundled through imageio-ffmpeg, or system ffmpeg)
    - PDF          (via PyMuPDF / pypdfium2 for rendering; Pillow for image->PDF)
    - Documents    (docx/odt/txt/md  -> pdf/odt/docx/txt/html/md)
    - Spreadsheets (xlsx/ods/csv/tsv -> pdf/ods/xlsx/csv/tsv/html/json)
    - Presentations(pptx/odp         -> pdf/odp/pptx/txt/html)

Pip dependencies:
    pip install PySide6 Pillow imageio-ffmpeg
    pip install pymupdf          # preferred PDF backend (or: pypdfium2)

    # Office / document conversion (pure python):
    pip install python-docx openpyxl python-pptx odfpy fpdf2

    # Optional image formats:
    pip install pillow-heif pillow-avif-plugin

Run:
    python main.py

The app is designed to degrade gracefully: if an optional backend is missing,
the corresponding features are disabled with a clear, actionable message
instead of the app crashing. Backend *detection* is done without actually
importing the heavy libraries, which makes startup much faster.
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
import importlib.util
import importlib.metadata
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
# Fast, lazy backend detection.
#
# We only check whether the *module spec* is resolvable - we never actually
# import the heavy libraries at startup. That's the single biggest startup
# win: importing Pillow, PyMuPDF and imageio-ffmpeg costs several hundred ms.
# The real imports happen on-demand inside the conversion functions (Python
# caches module imports, so repeat lookups are cheap).
# --------------------------------------------------------------------------

def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _pkg_version(pypi_name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(pypi_name)
    except Exception:
        return None


# Image backend
PIL_AVAILABLE = _module_available("PIL")
HEIC_SUPPORTED = PIL_AVAILABLE and _module_available("pillow_heif")
AVIF_SUPPORTED = PIL_AVAILABLE and _module_available("pillow_avif")

# FFmpeg
IMAGEIO_FFMPEG_AVAILABLE = _module_available("imageio_ffmpeg")

# PDF read backend
PYMUPDF_AVAILABLE = _module_available("pymupdf")
PYPDFIUM2_AVAILABLE = (not PYMUPDF_AVAILABLE) and _module_available("pypdfium2")
PDF_AVAILABLE = PYMUPDF_AVAILABLE or PYPDFIUM2_AVAILABLE

# PDF write backend (pure python)
FPDF2_AVAILABLE = _module_available("fpdf")

# Office backends (pure python)
DOCX_AVAILABLE = _module_available("docx")
OPENPYXL_AVAILABLE = _module_available("openpyxl")
PPTX_AVAILABLE = _module_available("pptx")
ODFPY_AVAILABLE = _module_available("odf")

APP_NAME = "HK File Converter"
ORG_NAME = "HK Software"

_ICON_CACHE: dict[tuple[str, int], QIcon] = {}


def svg_icon(svg_source: str, size: int = 16) -> QIcon:
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

# --- Document (Word-like) ---------------------------------------------------
DOCUMENT_INPUT_FORMATS = ["docx", "odt", "txt", "md"]
DOCUMENT_OUTPUT_FORMATS = ["pdf", "odt", "docx", "txt", "html", "md"]

# --- Spreadsheet (Excel-like) ----------------------------------------------
SPREADSHEET_INPUT_FORMATS = ["xlsx", "ods", "csv", "tsv"]
SPREADSHEET_OUTPUT_FORMATS = ["pdf", "ods", "xlsx", "csv", "tsv", "html", "json"]

# --- Presentation (PowerPoint-like) ----------------------------------------
PRESENTATION_INPUT_FORMATS = ["pptx", "odp"]
PRESENTATION_OUTPUT_FORMATS = ["pdf", "odp", "pptx", "txt", "html"]

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

# --------------------------------------------------------------------------
# Video codec selection.
#
# Previously the app never set an explicit "-c:v", so ffmpeg silently fell
# back to whatever the *default* encoder for the target container is (e.g.
# VP9 for .webm), while still passing "-preset"/"-crf" - options that only
# make sense for libx264/libx265 and are ignored (or rejected) by other
# encoders. That alone made many conversions dramatically slower than
# necessary. We now pick an explicit software codec per container, and
# prefer a hardware encoder when one is actually available on the machine.
# --------------------------------------------------------------------------

# target_ext -> (software encoder, [hardware encoder candidates in priority order])
FFMPEG_VIDEO_CODECS: dict[str, tuple[str, list[str]]] = {
    "mp4": ("libx264", ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]),
    "m4v": ("libx264", ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]),
    "mov": ("libx264", ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]),
    "mkv": ("libx264", ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]),
    "ts":  ("libx264", ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]),
    "webm": ("libvpx-vp9", []),
    "avi": ("mpeg4", []),
    "flv": ("flv", []),
    "wmv": ("wmv2", []),
    "mpg": ("mpeg2video", []),
    "mpeg": ("mpeg2video", []),
}

# Hardware encoders keyed by the exact name ffmpeg lists in `-encoders`.
HW_VIDEO_ENCODERS = {
    "h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf",
}


def _probe_hw_video_encoders(ffmpeg_path: str) -> set[str]:
    """Return the subset of HW_VIDEO_ENCODERS actually available in this ffmpeg build.

    Just because ffmpeg was *compiled* with support for an encoder doesn't
    mean the GPU/driver on this machine can use it, so we don't try to probe
    further than that; convert_media() falls back to software automatically
    if the hardware encoder fails at runtime.
    """
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=10,
        )
        listed = proc.stdout or ""
    except Exception:
        return set()
    return {name for name in HW_VIDEO_ENCODERS if name in listed}


def _video_encode_args(target_ext: str, opts: dict, hw_encoders: set[str]) -> tuple[list[str], str]:
    """Build the "-c:v ... quality/speed flags" portion of an ffmpeg command.

    Returns (args, encoder_name). Speed/quality flags are encoder-specific -
    "-preset"/"-crf" only mean something to libx264/libx265, so each codec
    family gets its own mapping instead of blindly reusing those two flags.
    """
    software, hw_candidates = FFMPEG_VIDEO_CODECS.get(target_ext, ("libx264", []))
    use_hw = opts.get("hw_accel", True)
    crf = opts.get("crf", 23)
    preset = opts.get("video_preset", "veryfast")
    bitrate = opts.get("video_bitrate")

    encoder = None
    if use_hw:
        for candidate in hw_candidates:
            if candidate in hw_encoders:
                encoder = candidate
                break
    if encoder is None:
        encoder = software

    if encoder in ("libx264", "libx265"):
        args = ["-c:v", encoder]
        if preset and preset != "default":
            args += ["-preset", preset]
        args += ["-crf", str(crf)] if bitrate is None else ["-b:v", bitrate]
        return args, encoder

    if encoder == "h264_nvenc":
        # nvenc uses its own preset names and constant-quality mode ("-cq").
        return ["-c:v", "h264_nvenc", "-preset", "p4" if preset != "ultrafast" else "p1",
                "-rc", "vbr", "-cq", str(crf)], encoder

    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast" if preset == "ultrafast" else "medium",
                "-global_quality", str(crf)], encoder

    if encoder == "h264_videotoolbox":
        # VideoToolbox has no CRF; approximate it with a quality-driven bitrate.
        return ["-c:v", "h264_videotoolbox", "-q:v", str(max(1, min(100, 100 - crf * 2)))], encoder

    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp",
                "-qp_i", str(crf), "-qp_p", str(crf)], encoder

    if encoder == "libvpx-vp9":
        # VP9 needs "-b:v 0" to actually honor CRF (constant-quality mode),
        # and "-deadline"/"-cpu-used" are its real speed knobs (not -preset).
        cpu_used = "8" if preset in ("ultrafast", "veryfast") else "4"
        return ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf),
                "-deadline", "good", "-cpu-used", cpu_used, "-row-mt", "1"], encoder

    # Plain software codecs (mpeg4, flv1/flv, wmv2, mpeg2video): use -q:v,
    # the generic "quality" knob these encoders actually understand.
    return ["-c:v", encoder, "-q:v", str(max(2, min(31, crf)))], encoder


def category_of_ext(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext in IMAGE_INPUT_FORMATS:
        return "image"
    if ext in AUDIO_FORMATS:
        return "audio"
    if ext in VIDEO_FORMATS:
        return "video"
    if ext in PDF_INPUT_FORMATS:
        return "pdf"
    if ext in DOCUMENT_INPUT_FORMATS:
        return "document"
    if ext in SPREADSHEET_INPUT_FORMATS:
        return "spreadsheet"
    if ext in PRESENTATION_INPUT_FORMATS:
        return "presentation"
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
    if cat == "document":
        return list(DOCUMENT_OUTPUT_FORMATS)
    if cat == "spreadsheet":
        return list(SPREADSHEET_OUTPUT_FORMATS)
    if cat == "presentation":
        return list(PRESENTATION_OUTPUT_FORMATS)
    return []


def common_valid_targets(exts: list[str]) -> list[str]:
    if not exts:
        return []
    sets = [set(valid_targets_for(e)) for e in exts]
    common = sets[0]
    for s in sets[1:]:
        common &= s
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


def _latin1(s: Any) -> str:
    """Force text into Latin-1 so fpdf2's built-in Helvetica can render it."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return s.encode("latin-1", "replace").decode("latin-1")


# --------------------------------------------------------------------------
# Backend registry
# --------------------------------------------------------------------------

class BackendRegistry:
    """Detects which optional backends are available and how to reach ffmpeg.

    All checks are cheap (module-spec lookups), so it's safe to construct at
    startup. Heavy imports only happen later, inside the conversion workers.
    """

    def __init__(self) -> None:
        self.pil_available = PIL_AVAILABLE
        self.pil_version = _pkg_version("Pillow") if PIL_AVAILABLE else None
        self.heic_supported = HEIC_SUPPORTED
        self.avif_supported = AVIF_SUPPORTED
        self.pdf_available = PDF_AVAILABLE
        self.pdf_backend = "pymupdf" if PYMUPDF_AVAILABLE else ("pypdfium2" if PYPDFIUM2_AVAILABLE else None)
        self.pdf_writer_available = FPDF2_AVAILABLE
        self.docx_available = DOCX_AVAILABLE
        self.openpyxl_available = OPENPYXL_AVAILABLE
        self.pptx_available = PPTX_AVAILABLE
        self.odf_available = ODFPY_AVAILABLE
        self.ffmpeg_path: Optional[str] = self._resolve_ffmpeg()
        self.ffmpeg_available = self.ffmpeg_path is not None
        # Cheap one-time probe; avoids re-running "ffmpeg -encoders" per job.
        self.hw_video_encoders: set[str] = (
            _probe_hw_video_encoders(self.ffmpeg_path) if self.ffmpeg_available else set()
        )

    @staticmethod
    def _resolve_ffmpeg() -> Optional[str]:
        if IMAGEIO_FFMPEG_AVAILABLE:
            try:
                import imageio_ffmpeg  # local, fast
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
            msgs.append("PDF reading: pip install pymupdf   (or: pip install pypdfium2)")
        if not self.pdf_writer_available:
            msgs.append("Document/Spreadsheet/Presentation → PDF: pip install fpdf2")
        if not self.docx_available:
            msgs.append("Word (.docx) reading/writing: pip install python-docx")
        if not self.openpyxl_available:
            msgs.append("Excel (.xlsx) reading/writing: pip install openpyxl")
        if not self.pptx_available:
            msgs.append("PowerPoint (.pptx) reading/writing: pip install python-pptx")
        if not self.odf_available:
            msgs.append("OpenDocument (odt/ods/odp): pip install odfpy")
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
        lines.append(
            f"  HW encoders:   {', '.join(sorted(self.hw_video_encoders)) if self.hw_video_encoders else 'none detected (software encoding only)'}"
        )
        lines.append(f"  PDF backend:   {self.pdf_backend or 'MISSING'}")
        lines.append(f"  PDF writer:    {'fpdf2' if self.pdf_writer_available else 'MISSING'}")
        lines.append(
            f"  Office:        docx={'y' if self.docx_available else 'n'} "
            f"xlsx={'y' if self.openpyxl_available else 'n'} "
            f"pptx={'y' if self.pptx_available else 'n'} "
            f"odf={'y' if self.odf_available else 'n'}"
        )
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
    status: str = "Queued"
    progress: int = 0
    error: str = ""
    output_path: Optional[Path] = None


class JobSignals(QObject):
    progress = Signal(int, int)
    status_changed = Signal(int, str)
    log = Signal(str, str)
    finished = Signal(int, bool, str, str)


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


# --------------------------------------------------------------------------
# Image conversion backend
# --------------------------------------------------------------------------

def convert_image(job: ConversionJob, signals: JobSignals, token: CancelToken) -> Path:
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")
    from PIL import Image, ImageOps, ImageSequence  # noqa: F401  (local import)

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


def _save_as_ico(im, out_path: Path) -> None:
    from PIL import Image
    if im.mode != "RGBA":
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
    from PIL import Image
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


# --------------------------------------------------------------------------
# Media conversion backend
# --------------------------------------------------------------------------

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


def _ffmpeg_thread_count(opts: dict) -> str:
    # Each concurrent job used to pass "-threads 0" (= let the encoder grab
    # every logical core it can see). With N parallel worker jobs running
    # video encodes at once, that's N encoders all fighting over every core
    # at the same time - the single biggest cause of "video conversion is
    # slow" when converting more than one file. Split the machine's cores
    # evenly across however many workers are actually configured instead.
    workers = max(1, int(opts.get("max_workers", 1) or 1))
    cores = os.cpu_count() or 4
    return str(max(1, cores // workers))


def convert_media(job: ConversionJob, signals: JobSignals, token: CancelToken,
                   ffmpeg_path: str, proc_registry: dict, hw_encoders: Optional[set] = None) -> Path:
    src = job.source_path
    target_ext = job.target_ext.lower()
    opts = job.options
    hw_encoders = hw_encoders or set()

    out_name = sanitize_filename(src.stem) + f".{target_ext}"
    out_path = unique_path(job.output_dir / out_name)
    job.output_dir.mkdir(parents=True, exist_ok=True)

    duration = _ffmpeg_duration_seconds(ffmpeg_path, src)
    threads = _ffmpeg_thread_count(opts)

    is_gif_target = target_ext == "gif"
    is_video_source = category_of_ext(src.suffix) == "video"
    is_video_job = category_of_ext(src.suffix) == "video" or category_of_ext(f".{target_ext}") == "video"

    def build_cmd(force_software: bool = False) -> tuple[list[str], str]:
        cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-threads", threads, "-i", str(src)]
        encoder_used = ""

        if is_gif_target and is_video_source:
            fps = opts.get("gif_fps", 10)
            width = opts.get("gif_width", 480)
            vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            cmd += ["-vf", vf, "-loop", "0"]
        elif is_video_job:
            vf_parts = []
            if opts.get("resolution") and opts["resolution"] != "Source":
                vf_parts.append(f"scale={opts['resolution']}")
            if vf_parts:
                cmd += ["-vf", ",".join(vf_parts)]
            if opts.get("fps") and opts["fps"] != "Source":
                cmd += ["-r", str(opts["fps"])]

            codec_opts = dict(opts)
            if force_software:
                codec_opts["hw_accel"] = False
            encode_args, encoder_used = _video_encode_args(target_ext, codec_opts, hw_encoders)
            cmd += encode_args
            # Each encoder thread's own "-threads" also needs bounding for
            # the same oversubscription reason as above (only applies to
            # software codecs; HW encoders ignore it harmlessly).
            cmd += ["-threads", threads]

            if opts.get("audio_bitrate"):
                cmd += ["-b:a", opts["audio_bitrate"]]
        else:
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
        return cmd, encoder_used

    def run_once(cmd: list[str]) -> tuple[int, list[str]]:
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

        return proc.returncode, stderr_lines

    if token.cancelled:
        raise InterruptedError("Cancelled by user")

    cmd, encoder_used = build_cmd()
    returncode, stderr_lines = run_once(cmd)

    # A hardware encoder can fail for reasons that have nothing to do with
    # the file (driver quirk, VRAM pressure, unsupported pixel format) -
    # rather than losing the whole job, transparently retry once in
    # software instead of forcing the user to disable hardware accel.
    if returncode != 0 and not token.cancelled and encoder_used in HW_VIDEO_ENCODERS:
        signals.log.emit(
            "WARNING",
            f"Hardware encoder {encoder_used} failed for {src.name}, retrying with software encoding…",
        )
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        cmd, _ = build_cmd(force_software=True)
        returncode, stderr_lines = run_once(cmd)

    if token.cancelled:
        raise InterruptedError("Cancelled by user")

    if returncode != 0:
        err_text = "".join(stderr_lines).strip() or f"ffmpeg exited with code {returncode}"
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


# --------------------------------------------------------------------------
# PDF -> image backend
# --------------------------------------------------------------------------

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
        import pymupdf
        from PIL import Image  # for ico case
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
        import pypdfium2 as pdfium
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


# ==========================================================================
# Office / Document backends (pure Python)
# ==========================================================================

# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------

def _output_path_for(src: Path, out_dir: Path, target_ext: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = sanitize_filename(src.stem) + f".{target_ext}"
    return unique_path(out_dir / out_name)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


# ----------------------------------------------------------------------
# PDF writer (fpdf2)
# ----------------------------------------------------------------------

def _make_pdf():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    return pdf


def _pdf_write_paragraphs(paras: list[str], out_path: Path, title: str = "") -> None:
    _require(FPDF2_AVAILABLE, "PDF writer not installed. Run: pip install fpdf2")
    pdf = _make_pdf()
    pdf.add_page()
    if title:
        pdf.set_font("Helvetica", "B", 15)
        pdf.multi_cell(0, 9, _latin1(title))
        pdf.ln(2)
    pdf.set_font("Helvetica", size=11)
    for p in paras:
        text = _latin1(p) if p else ""
        if not text.strip():
            pdf.ln(3)
            continue
        pdf.multi_cell(0, 6, text)
    pdf.output(str(out_path))


def _pdf_write_sheets(sheets: dict[str, list[list]], out_path: Path) -> None:
    _require(FPDF2_AVAILABLE, "PDF writer not installed. Run: pip install fpdf2")
    pdf = FPDF(orientation="L")
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.set_margins(10, 10, 10)
    for name, rows in sheets.items():
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 8, _latin1(name))
        pdf.ln(2)
        pdf.set_font("Courier", size=8)
        for row in rows[:2000]:
            line = " | ".join(_latin1(c)[:32] for c in row)
            if not line.strip():
                pdf.ln(3)
                continue
            try:
                pdf.multi_cell(0, 4.5, line)
            except Exception:
                pass
    pdf.output(str(out_path))


def _pdf_write_slides(slides: list[list[str]], out_path: Path) -> None:
    _require(FPDF2_AVAILABLE, "PDF writer not installed. Run: pip install fpdf2")
    pdf = _make_pdf()
    for i, slide in enumerate(slides, 1):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 18)
        pdf.multi_cell(0, 11, f"Slide {i}")
        pdf.ln(3)
        pdf.set_font("Helvetica", size=12)
        for line in slide:
            pdf.multi_cell(0, 7, _latin1(line))
    pdf.output(str(out_path))


# ----------------------------------------------------------------------
# Document (Word-like) read / write
# ----------------------------------------------------------------------

def _read_document_paragraphs(path: Path) -> list[str]:
    ext = path.suffix.lstrip(".").lower()

    if ext in ("txt", "md"):
        return path.read_text(encoding="utf-8", errors="replace").split("\n")

    if ext == "docx":
        _require(DOCX_AVAILABLE, "python-docx is not installed. Run: pip install python-docx")
        import docx
        d = docx.Document(str(path))
        out: list[str] = []
        for p in d.paragraphs:
            out.append(p.text)
        for tbl in d.tables:
            for row in tbl.rows:
                out.append(" | ".join(c.text for c in row.cells))
        return out

    if ext == "odt":
        _require(ODFPY_AVAILABLE, "odfpy is not installed. Run: pip install odfpy")
        from odf.opendocument import load
        from odf.text import P
        from odf import teletype
        doc = load(str(path))
        out = []
        for p in doc.getElementsByType(P):
            out.append(teletype.extractText(p))
        return out

    raise RuntimeError(f"Unsupported document input: .{ext}")


def _write_document(paras: list[str], out_path: Path, target_ext: str, title: str = "") -> None:
    target_ext = target_ext.lower()

    if target_ext == "txt":
        out_path.write_text("\n".join(paras), encoding="utf-8")
        return

    if target_ext == "md":
        out_path.write_text("\n\n".join(paras), encoding="utf-8")
        return

    if target_ext == "html":
        import html as _html
        body = "\n".join(f"<p>{_html.escape(p)}</p>" for p in paras)
        t = _html.escape(title or out_path.stem)
        out_path.write_text(
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{t}</title></head>"
            f"<body>{body}</body></html>",
            encoding="utf-8",
        )
        return

    if target_ext == "pdf":
        _pdf_write_paragraphs(paras, out_path, title=title)
        return

    if target_ext == "odt":
        _require(ODFPY_AVAILABLE, "odfpy is not installed. Run: pip install odfpy")
        from odf.opendocument import OpenDocumentText
        from odf.text import P
        doc = OpenDocumentText()
        for p in paras:
            doc.text.addElement(P(text=p or ""))
        doc.save(str(out_path))
        return

    if target_ext == "docx":
        _require(DOCX_AVAILABLE, "python-docx is not installed. Run: pip install python-docx")
        import docx
        d = docx.Document()
        for p in paras:
            d.add_paragraph(p or "")
        d.save(str(out_path))
        return

    raise RuntimeError(f"Unsupported document output: .{target_ext}")


def convert_document(job: ConversionJob, signals: JobSignals, token: CancelToken) -> Path:
    src = job.source_path
    target_ext = job.target_ext.lower()
    out_path = _output_path_for(src, job.output_dir, target_ext)

    signals.progress.emit(job.job_id, 15)
    paras = _read_document_paragraphs(src)
    if token.cancelled:
        raise InterruptedError("Cancelled by user")
    signals.progress.emit(job.job_id, 60)
    _write_document(paras, out_path, target_ext, title=src.stem)
    signals.progress.emit(job.job_id, 100)
    return out_path


# ----------------------------------------------------------------------
# Spreadsheet read / write
# ----------------------------------------------------------------------

def _read_spreadsheet(path: Path) -> dict[str, list[list]]:
    ext = path.suffix.lstrip(".").lower()

    if ext in ("csv", "tsv"):
        import csv
        delim = "\t" if ext == "tsv" else ","
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delim)
            rows = [list(r) for r in reader]
        return {path.stem: rows}

    if ext == "xlsx":
        _require(OPENPYXL_AVAILABLE, "openpyxl is not installed. Run: pip install openpyxl")
        import openpyxl
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        out: dict[str, list[list]] = {}
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else v for v in row])
            out[ws.title] = rows
        wb.close()
        return out

    if ext == "ods":
        _require(ODFPY_AVAILABLE, "odfpy is not installed. Run: pip install odfpy")
        from odf.opendocument import load
        from odf.table import Table, TableRow, TableCell
        from odf import teletype
        doc = load(str(path))
        out = {}
        for tbl in doc.getElementsByType(Table):
            name = tbl.getAttribute("name") or "Sheet"
            rows = []
            for tr in tbl.getElementsByType(TableRow):
                cells: list[str] = []
                for tc in tr.getElementsByType(TableCell):
                    try:
                        rep = int(tc.getAttribute("numbercolumnsrepeated") or 1)
                    except Exception:
                        rep = 1
                    txt = teletype.extractText(tc)
                    for _ in range(rep):
                        cells.append(txt)
                rows.append(cells)
            out[name] = rows
        return out

    raise RuntimeError(f"Unsupported spreadsheet input: .{ext}")


def _write_spreadsheet(sheets: dict[str, list[list]], out_path: Path, target_ext: str) -> None:
    import csv as _csv
    import json as _json
    import html as _html
    target_ext = target_ext.lower()

    if target_ext in ("csv", "tsv"):
        # Only the first sheet is written (one file per conversion).
        name = next(iter(sheets))
        rows = sheets[name]
        delim = "\t" if target_ext == "tsv" else ","
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f, delimiter=delim)
            for r in rows:
                w.writerow(["" if v is None else v for v in r])
        return

    if target_ext == "json":
        out_path.write_text(json.dumps(sheets, indent=2, default=str), encoding="utf-8")
        return

    if target_ext == "html":
        parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
                 "table{border-collapse:collapse}td,th{border:1px solid #888;padding:2px 6px;font-family:sans-serif;font-size:12px}"
                 "</style></head><body>"]
        for name, rows in sheets.items():
            parts.append(f"<h2>{_html.escape(str(name))}</h2><table>")
            for r in rows:
                parts.append("<tr>" + "".join(f"<td>{_html.escape(str(v))}</td>" for v in r) + "</tr>")
            parts.append("</table>")
        parts.append("</body></html>")
        out_path.write_text("\n".join(parts), encoding="utf-8")
        return

    if target_ext == "pdf":
        _pdf_write_sheets(sheets, out_path)
        return

    if target_ext == "ods":
        _require(ODFPY_AVAILABLE, "odfpy is not installed. Run: pip install odfpy")
        from odf.opendocument import OpenDocumentSpreadsheet
        from odf.table import Table, TableRow, TableCell
        from odf.text import P
        doc = OpenDocumentSpreadsheet()
        for name, rows in sheets.items():
            t = Table(name=str(name))
            for row in rows:
                tr = TableRow()
                for val in row:
                    tc = TableCell()
                    tc.addElement(P(text="" if val is None else str(val)))
                    tr.addElement(tc)
                t.addElement(tr)
            doc.spreadsheet.addElement(t)
        doc.save(str(out_path))
        return

    if target_ext == "xlsx":
        _require(OPENPYXL_AVAILABLE, "openpyxl is not installed. Run: pip install openpyxl")
        import openpyxl
        wb = openpyxl.Workbook()
        # Remove the default sheet; we'll add our own.
        default = wb.active
        wb.remove(default)
        for name, rows in sheets.items():
            ws = wb.create_sheet(title=str(name)[:31] or "Sheet")
            for r in rows:
                ws.append(list(r))
        if not wb.worksheets:
            wb.create_sheet(title="Sheet")
        wb.save(str(out_path))
        return

    raise RuntimeError(f"Unsupported spreadsheet output: .{target_ext}")


def convert_spreadsheet(job: ConversionJob, signals: JobSignals, token: CancelToken) -> Path:
    src = job.source_path
    target_ext = job.target_ext.lower()
    out_path = _output_path_for(src, job.output_dir, target_ext)

    signals.progress.emit(job.job_id, 15)
    sheets = _read_spreadsheet(src)
    if token.cancelled:
        raise InterruptedError("Cancelled by user")
    signals.progress.emit(job.job_id, 60)
    _write_spreadsheet(sheets, out_path, target_ext)
    signals.progress.emit(job.job_id, 100)
    return out_path


# ----------------------------------------------------------------------
# Presentation read / write
# ----------------------------------------------------------------------

def _read_presentation_slides(path: Path) -> list[list[str]]:
    ext = path.suffix.lstrip(".").lower()

    if ext == "pptx":
        _require(PPTX_AVAILABLE, "python-pptx is not installed. Run: pip install python-pptx")
        from pptx import Presentation
        prs = Presentation(str(path))
        slides: list[list[str]] = []
        for slide in prs.slides:
            lines: list[str] = []
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    for p in shape.text_frame.paragraphs:
                        txt = "".join(r.text for r in p.runs) or p.text
                        if txt:
                            lines.append(txt)
            slides.append(lines)
        return slides

    if ext == "odp":
        _require(ODFPY_AVAILABLE, "odfpy is not installed. Run: pip install odfpy")
        from odf.opendocument import load
        from odf.draw import Page
        from odf.text import P
        from odf import teletype
        doc = load(str(path))
        slides = []
        for page in doc.getElementsByType(Page):
            lines = []
            for p in page.getElementsByType(P):
                t = teletype.extractText(p)
                if t:
                    lines.append(t)
            slides.append(lines)
        return slides

    raise RuntimeError(f"Unsupported presentation input: .{ext}")


def _write_presentation(slides: list[list[str]], out_path: Path, target_ext: str, title: str = "") -> None:
    import html as _html
    target_ext = target_ext.lower()

    if target_ext == "txt":
        lines: list[str] = []
        for i, s in enumerate(slides, 1):
            lines.append(f"--- Slide {i} ---")
            lines.extend(s)
            lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return

    if target_ext == "html":
        t = _html.escape(title or out_path.stem)
        parts = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{t}</title>"
                 "<style>section{margin:1em 0;padding:1em;border:1px solid #ccc}</style>"
                 "</head><body>"]
        for i, s in enumerate(slides, 1):
            parts.append(f"<section><h2>Slide {i}</h2>")
            for line in s:
                parts.append(f"<p>{_html.escape(line)}</p>")
            parts.append("</section>")
        parts.append("</body></html>")
        out_path.write_text("\n".join(parts), encoding="utf-8")
        return

    if target_ext == "pdf":
        _pdf_write_slides(slides, out_path)
        return

    if target_ext == "pptx":
        _require(PPTX_AVAILABLE, "python-pptx is not installed. Run: pip install python-pptx")
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        blank = prs.slide_layouts[6]
        for i, s in enumerate(slides, 1):
            slide = prs.slides.add_slide(blank)
            tb = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(6))
            tf = tb.text_frame
            tf.text = f"Slide {i}"
            for line in s:
                p = tf.add_paragraph()
                p.text = line
        prs.save(str(out_path))
        return

    if target_ext == "odp":
        _require(ODFPY_AVAILABLE, "odfpy is not installed. Run: pip install odfpy")
        from odf.opendocument import OpenDocumentPresentation
        from odf.draw import Page, Frame, TextBox
        from odf.text import P
        doc = OpenDocumentPresentation()
        for i, s in enumerate(slides, 1):
            page = Page(name=f"Slide{i}", masterpagename="Standard")
            frame = Frame(width="25cm", height="18cm", x="1cm", y="1cm")
            tb = TextBox()
            tb.addElement(P(text=f"Slide {i}"))
            for line in s:
                tb.addElement(P(text=line))
            frame.addElement(tb)
            page.addElement(frame)
            doc.presentation.addElement(page)
        doc.save(str(out_path))
        return

    raise RuntimeError(f"Unsupported presentation output: .{target_ext}")


def convert_presentation(job: ConversionJob, signals: JobSignals, token: CancelToken) -> Path:
    src = job.source_path
    target_ext = job.target_ext.lower()
    out_path = _output_path_for(src, job.output_dir, target_ext)

    signals.progress.emit(job.job_id, 15)
    slides = _read_presentation_slides(src)
    if token.cancelled:
        raise InterruptedError("Cancelled by user")
    signals.progress.emit(job.job_id, 60)
    _write_presentation(slides, out_path, target_ext, title=src.stem)
    signals.progress.emit(job.job_id, 100)
    return out_path


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
                out_path = convert_media(job, self.signals, self.token,
                                         self.backends.ffmpeg_path, self.proc_registry,
                                         self.backends.hw_video_encoders)
            elif cat == "pdf":
                if not self.backends.pdf_available:
                    raise RuntimeError("No PDF backend installed. Run: pip install pymupdf")
                results = pdf_to_images(job, self.signals, self.token)
                out_path = results[0] if results else None
            elif cat == "document":
                out_path = convert_document(job, self.signals, self.token)
            elif cat == "spreadsheet":
                out_path = convert_spreadsheet(job, self.signals, self.token)
            elif cat == "presentation":
                out_path = convert_presentation(job, self.signals, self.token)
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
# Default options (used when the Options dialog has not been opened yet)
# --------------------------------------------------------------------------

def default_options() -> dict:
    return {
        "preserve_metadata": True,
        "overwrite": False,
        "jpeg_quality": 90,
        "fast_png": True,
        "page_size": "auto",
        "orientation": "auto",
        "one_pdf_per_image": False,
        "bitrate": "Auto",
        "sample_rate": "Auto",
        "channels": "Auto",
        "video_preset": "veryfast",
        "hw_accel": True,
        "resolution": "Source",
        "crf": 23,
        "fps": "Source",
        "gif_fps": 10,
        "gif_width": 480,
        "dpi": 150,
        "page_range": "all",
        "password": "",
    }


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
        self.sub_label = QLabel("Images, audio, video, PDF, and Office documents")
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
# UI: Options dialog
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

        # -- General --
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

        # -- Image --
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

        # -- Audio --
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

        # -- Video --
        self.video_page = QWidget()
        vid_layout = QVBoxLayout(self.video_page)
        vid_box = QGroupBox("Video Encoding")
        vid_form = QFormLayout(vid_box)
        self.video_preset_combo = QComboBox()
        self.video_preset_combo.addItems(["veryfast", "faster", "fast", "medium", "ultrafast"])
        self.video_preset_combo.setCurrentText("veryfast")
        vid_form.addRow("Encoding speed preset:", self.video_preset_combo)
        self.hw_accel_cb = QCheckBox("Use hardware acceleration when available (recommended)")
        self.hw_accel_cb.setChecked(True)
        self.hw_accel_cb.setToolTip(
            "Encodes on the GPU (VideoToolbox/NVENC/QSV/AMF) when the machine supports it, "
            "which is typically several times faster than CPU-only encoding. "
            "Falls back to software automatically if the hardware encoder fails."
        )
        vid_form.addRow(self.hw_accel_cb)
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

        # -- PDF --
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
        d = default_options()
        self.preserve_metadata_cb.setChecked(d["preserve_metadata"])
        self.overwrite_cb.setChecked(d["overwrite"])
        self.jpeg_quality_spin.setValue(d["jpeg_quality"])
        self.fast_png_cb.setChecked(d["fast_png"])
        self.page_size_combo.setCurrentText(d["page_size"].capitalize())
        self.orientation_combo.setCurrentText(d["orientation"].capitalize())
        self.one_pdf_per_image_cb.setChecked(d["one_pdf_per_image"])
        self.audio_bitrate_combo.setCurrentText(d["bitrate"])
        self.sample_rate_combo.setCurrentText(d["sample_rate"])
        self.channels_combo.setCurrentText(d["channels"])
        self.video_preset_combo.setCurrentText(d["video_preset"])
        self.hw_accel_cb.setChecked(d["hw_accel"])
        self.resolution_combo.setCurrentText(d["resolution"])
        self.crf_spin.setValue(d["crf"])
        self.fps_combo.setCurrentText(d["fps"])
        self.gif_fps_spin.setValue(d["gif_fps"])
        self.gif_width_spin.setValue(d["gif_width"])
        self.dpi_spin.setValue(d["dpi"])
        self.page_range_edit.setText(d["page_range"])
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
            "hw_accel": self.hw_accel_cb.isChecked(),
            "resolution": self.resolution_combo.currentText(),
            "crf": self.crf_spin.value(),
            "fps": self.fps_combo.currentText(),
            "gif_fps": self.gif_fps_spin.value(),
            "gif_width": self.gif_width_spin.value(),
            "dpi": self.dpi_spin.value(),
            "page_range": self.page_range_edit.text(),
            "password": self.pdf_password_edit.text(),
        }


# --------------------------------------------------------------------------
# UI: File table
# --------------------------------------------------------------------------

COL_NAME, COL_TYPE, COL_SIZE, COL_FORMAT, COL_STATUS, COL_PROGRESS, COL_REMOVE = range(7)


class FileTable(QTableWidget):
    remove_requested = Signal(int)
    remove_all_requested = Signal()
    open_folder_requested = Signal(int)
    target_format_changed = Signal(int, str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(0, 7, parent)
        self.setHorizontalHeaderLabels(
            ["File", "Type", "Size", "Convert to", "Status", "Progress", ""]
        )
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(COL_TYPE, 90)
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
        self._active_workers: dict[int, tuple["ConversionWorker", JobSignals]] = {}
        self._active_jobs = 0
        self._total_jobs_in_batch = 0
        self._completed_jobs_in_batch = 0
        self._converting = False
        self._per_job_progress: dict[int, int] = {}

        # Lazily-created Options dialog (heavy to build).
        self.options_dialog: Optional[OptionsDialog] = None

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(900, 600)
        self.resize(1100, 720)

        self._build_ui()
        self._restore_settings()

        # Defer diagnostics to after the window paints so startup feels snappy.
        QTimer.singleShot(0, self._log_startup_report)

    # ---------------- UI construction ----------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(10)

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

        # -- Convert Selected To --
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

        # -- Options (lazy dialog) --
        layout.addWidget(self._section_label("OPTIONS"))
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
        if self.options_dialog is None:
            self.options_dialog = OptionsDialog(self)
        self.options_dialog.exec()

    def _gather_options(self) -> dict:
        if self.options_dialog is not None:
            return self.options_dialog.gather_options()
        return default_options()

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
        for line in self.backends.missing_backends_report():
            self.log_panel.append("WARN", f"Feature unavailable: {line}")

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
        self._per_job_progress = {j.job_id: 0 for j in pending_jobs}
        self.statusBar().showMessage("Converting…")

        common_options = self._gather_options()
        # Let each ffmpeg job know how many other jobs may run alongside it,
        # so it can divide CPU cores instead of every job grabbing all of
        # them (see _ffmpeg_thread_count).
        common_options["max_workers"] = max(1, self.thread_pool.maxThreadCount())

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