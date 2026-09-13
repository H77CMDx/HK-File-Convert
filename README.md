# HK File Convert

HK File Convert is an offline desktop file converter for Windows. It provides a simple PySide6 interface for converting image, audio, video, and PDF files.

## Installation

Download the latest packaged application from the **[GitHub Releases page](https://github.com/H77CMDx/HK-File-Convert/releases)**. For people who aren't too tech savvy, I recommend using the .msi installer.

## Supported Formats

- **Images:** PNG, JPG/JPEG, WEBP, BMP, GIF, TIFF, ICO, PPM, PGM, TGA, and JP2
- **Audio:** MP3, WAV, FLAC, AAC, M4A, OGG, OPUS, WMA, AIFF, ALAC, and AMR
- **Video:** MP4, MKV, AVI, MOV, WEBM, FLV, WMV, M4V, MPG/MPEG, TS, and GIF
- **PDF:** PDF to image formats, plus image to PDF

HEIC and AVIF support is enabled when the required Pillow plugin is available.

## Running From Source

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install PySide6 Pillow imageio-ffmpeg pymupdf
python main.py
```

PyMuPDF is the preferred PDF backend. `pypdfium2` can be installed as an alternative if needed.

## Features

- Batch conversion with parallel workers
- Custom output folders or per-source `converted` folders
- Multi-resolution ICO export
- PDF page ranges and configurable DPI
- JPEG quality and metadata options
- No internet connection required while converting

## License

See the repository for license information.