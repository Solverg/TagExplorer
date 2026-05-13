# TagExplorer

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Framework](https://img.shields.io/badge/PyQt6-GUI-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-lightgrey.svg)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)

TagExplorer is a high-performance desktop file manager for Windows that uses
tag-based navigation. Tags are embedded directly into filenames, for example
`report [work][2024].pdf`, so they survive copies, cloud sync, and moves
without a hidden metadata database.

## Key Features

- Native Windows 10/11 desktop UI with Acrylic/Mica styling through
  `pywinstyles`.
- Fast tag filtering with AND / OR logic.
- Batch tag editing with conflict-safe renames.
- Virtualized list and tile views for large folders.
- Image and video previews using PyQt6 image readers and OpenCV.
- SQLCipher-backed local cache via `sqlcipher3-wheels`, with an explicit
  plaintext SQLite fallback warning when SQLCipher is unavailable.

## Tech Stack

- Python 3.10+
- PyQt6
- SQLCipher through `sqlcipher3-wheels`
- OpenCV through `opencv-python`
- PyInstaller onefile Windows release builds

## Installation

```powershell
git clone https://github.com/Solverg/TagExplorer.git
cd TagExplorer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

Run from source:

```powershell
python main.py
```

## Release Build

TagExplorer 1.0.1 is configured for a console-free onefile PyInstaller build.
UPX is disabled for official builds to reduce antivirus false positives and
keep crash analysis simpler.

Use a clean 64-bit Windows Python 3.11 virtual environment for the official
1.0.1 build because `requirements-release-win-amd64-py311.txt` is pinned for
that interpreter and platform.

```powershell
python -m pip install -r requirements-release-win-amd64-py311.txt
python -m PyInstaller --noconfirm --clean build.spec
```

The executable is generated as `dist\TagExplorer.exe`. Release archives should
also include:

- `LICENSE`
- `THIRD_PARTY_NOTICES.md`

The built executable should be attached to GitHub Releases as
`TagExplorer.exe`. The in-app updater checks `Solverg/TagExplorer` and accepts
release tags in `v1.2.3` or `1.2.3` format.

For the 1.0.1 release, publish tag `v1.0.1` or `1.0.1`. A running
TagExplorer 1.0.1 build should report no update while the latest GitHub Release
is also 1.0.1; update installation can only be fully exercised once a newer
release asset exists.

The committed `requirements.txt` pins direct dependencies, and
`requirements-release-win-amd64-py311.txt` pins the known transitive dependency
set for the 1.0.1 Windows x86-64 / Python 3.11 release. For stronger supply
chain integrity, regenerate a hash-locked file on the target Windows release
machine:

```powershell
python -m pip install pip-tools
pip-compile --generate-hashes --output-file requirements-lock-win-amd64-py311.txt requirements-release-win-amd64-py311.txt
python -m pip install --require-hashes -r requirements-lock-win-amd64-py311.txt
python -m PyInstaller --noconfirm --clean build.spec
```

## Pre-Release Checks

Run these before building or publishing:

```powershell
python main.py --healthcheck
python -m unittest discover -s tests -v
```

## Release Smoke Checklist

- Build inside a clean Windows virtual environment.
- Confirm `python main.py` launches from source in the release environment.
- Start `dist\TagExplorer.exe` outside the repository directory.
- Start the exe from a path containing spaces and non-ASCII characters.
- Confirm the first onefile startup completes after extraction to the temporary
  directory.
- Confirm SQLCipher is active and cache creation does not fall back to
  plaintext SQLite.
- Choose a folder through the UI.
- Run a direct scan.
- Enable recursive mode or use "Scan Folder Recursively" and run a recursive
  scan.
- Close the app during a long scan, then reopen it and confirm the app exits
  cleanly and the cache is still usable.
- Reopen the app and confirm cached results for the scanned folder appear
  correctly.
- Exercise AND and OR tag filtering.
- Exercise file type filters for images, video, audio, and documents.
- Open image previews and the full image viewer.
- Open GIF previews and confirm GIFs loop in the full image viewer.
- Open video previews to exercise OpenCV and the multiprocessing worker.
- Batch add tags and batch remove tags, confirming the file view keeps its
  scroll position after refresh.
- Rename one file.
- Try a conflicting rename and confirm the app offers a safe non-overwriting
  name.
- Delete a file and confirm it is removed through Recycle Bin behavior where
  available.
- Open a file.
- Open a file location in Explorer.
- Check `QImageReader.supportedImageFormats()` includes expected image formats.
- Confirm the application starts with `console=False`.
- Confirm startup failures are logged to
  `%LOCALAPPDATA%\Solverg\TagExplorer\logs\startup.log`.
- Check the executable Properties dialog contains version `1.0.1` metadata.
- After the release exists on GitHub, run "Check for Updates" and confirm
  1.0.1 reports no update; repeat update installation when a newer release is
  published.
- Scan the release archive with Windows Defender or the intended AV baseline.

## SQLCipher on Windows

The supported Windows dependency is `sqlcipher3-wheels`. Do not add
`pysqlcipher3` to the release environment or PyInstaller hidden imports; it is
source-distributed and can reintroduce local SQLCipher compilation issues.

If SQLCipher cannot be imported, TagExplorer falls back to standard `sqlite3`
and warns that the cache is unencrypted.

## PyInstaller Notes

`build.spec` explicitly includes hidden imports for PyQt6, OpenCV,
`pywinstyles`, and `sqlcipher3`. The entry point calls
`multiprocessing.freeze_support()` for onefile video preview workers.

The build embeds Windows version metadata from `version_info.txt` and includes
the project license/third-party notices as bundled data.

## Tag Syntax

TagExplorer parses square-bracket tags in filenames.

Supported forms:

```text
<base name> [tag1][tag2].<extension>
[tag1][tag2] <base name>.<extension>
```

Examples:

- `meeting_notes [work][urgent].md`
- `[finance][Q1] invoice_2024.pdf`
- `IMG_1024 [travel][italy][2025].jpg`

Do not use `[` or `]` inside tag text. Tags are case-sensitive.

You can configure the default tag position via
`Options > Tags at the beginning`.

## Stress Testing

```powershell
python stress_test.py
```

This creates a `STRESS_TEST_50K` directory with 50,000 dummy files for checking
scroll performance, filtering, thumbnail behavior, and memory cleanup.

## License

TagExplorer is free software distributed under GPL-3.0-or-later. See `LICENSE`
and `THIRD_PARTY_NOTICES.md` for project and dependency notices.
