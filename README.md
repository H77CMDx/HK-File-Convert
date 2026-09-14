# HK File Converter

HK File Converter is an offline Windows desktop application for batch-converting media and common office files. It uses a PySide6 interface with drag-and-drop support and performs conversions locally on your computer.

<img width="860" height="410" alt="image" src="https://github.com/user-attachments/assets/780b236b-70cf-4b7b-b7d7-21477c7dfda9" /> 

## Download

Download the latest packaged application from the **[GitHub Releases page](https://github.com/H77CMDx/HK-File-Convert/releases)**.

## Supported file types

- **Images:** PNG, JPG/JPEG, WEBP, BMP, GIF, TIFF, ICO, PPM, PGM, TGA, and JP2
- **Audio:** MP3, WAV, FLAC, AAC, M4A, OGG, OPUS, WMA, AIFF, ALAC, and AMR
- **Video:** MP4, MKV, AVI, MOV, WEBM, FLV, WMV, M4V, MPG/MPEG, TS, and GIF
- **PDF:** PDF to PNG, JPG/JPEG, WEBP, BMP, TIFF, or ICO; images to PDF
- **Documents:** DOCX, ODT, TXT, and Markdown to PDF, ODT, DOCX, TXT, HTML, or Markdown
- **Spreadsheets:** XLSX, ODS, CSV, and TSV to PDF, ODS, XLSX, CSV, TSV, HTML, or JSON
- **Presentations:** PPTX and ODP to PDF, ODP, PPTX, TXT, or HTML

HEIC and AVIF image support is enabled when the corresponding Pillow plugins are installed. Conversion options are filtered to the formats supported by the selected input files.

## Using the application

1. Drag files or folders into the drop area, or use the file picker.
2. Choose an output format and destination folder.
3. Open the conversion options when you need to adjust quality, metadata, video settings, PDF DPI, or page ranges.
4. Start the conversion and review the per-file status and output paths.

Existing output files are preserved; the application creates a numbered filename instead of overwriting them.

## Running from source

Requires Python 3.10 or newer on Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install PySide6 Pillow imageio-ffmpeg pymupdf fpdf2
python main.py
```

For document, spreadsheet, and presentation conversions, install the relevant optional packages:

```powershell
python -m pip install python-docx openpyxl python-pptx odfpy
```

PyMuPDF is the preferred PDF rendering backend. Install `pypdfium2` instead if PyMuPDF is unavailable. For HEIC and AVIF support, install the optional Pillow plugins:

```powershell
python -m pip install pillow-heif pillow-avif-plugin
```

Audio and video conversion uses FFmpeg bundled by `imageio-ffmpeg`, or a system FFmpeg installation when available. Missing optional backends do not prevent the application from starting; the affected conversions report an actionable error.

## Building the Windows package

With the virtual environment activated and PyInstaller installed, run:

```powershell
python -m pip install pyinstaller
\.\export pyinstaller.bat
```

The batch file creates an onedir, windowed build. It expects `icon.ico` to be present in the project directory.

To create a standalone Nuitka build as a ZIP archive, install Nuitka and run:

```powershell
python -m pip install nuitka
\.\export nuitka.bat
```

This creates `dist\HK File Converter.zip` and preserves the application icon.

## Features

- Batch conversion with parallel workers
- Drag-and-drop files and folders
- Custom output folders or per-source `converted` folders
- Multi-resolution ICO export
- PDF page ranges and configurable DPI
- JPEG/WebP quality, video quality, and metadata options
- No internet connection required while converting

## License

See the repository for license information.
