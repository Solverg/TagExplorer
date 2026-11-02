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

- Native Windows 10/11 desktop UI with application-owned dark and light themes,
  independent of the Windows system color theme.
- Folder navigation with quick access locations, drive roots, direct scans, and
  recursive scans.
- Fast tag filtering with AND / OR logic plus NOT exclusions.
- Batch tag editing with conflict-safe renames.
- Virtualized list and tile views for large folders.
- Image and video previews using PyQt6 image readers and OpenCV.
- Built-in full image viewer with keyboard navigation.
- SQLCipher-backed local cache via `sqlcipher3-wheels`, with an explicit
  plaintext SQLite fallback warning when SQLCipher is unavailable.

## Tag Filtering

Tag filtering is built around two active filter groups:

- `Include`: click a tag pill to require that tag in the result set.
- `Exclude`: click the exclude control inside a tag pill to hide files with
  that tag.

Use the `AND` / `OR` toggle to choose how `Include` tags combine. The mode
applies only to `Include` tags. `Exclude` tags are always applied as a final
"not tagged with" filter, so you can search for files with `work` or `photo`
while excluding files tagged `archive`.

Files without tags are hidden when an `Include` filter is active. Filters are
retained when the current folder is rescanned and reset when navigating to a
different folder. Recursive scans include hidden folders and folders with
names such as `Windows` or `node_modules`, while avoiding directory links and
Windows reparse points. Scanning large trees may therefore take longer.

## Appearance and Navigation

TagExplorer uses its own dark theme by default instead of following the Windows
system theme. Switch between dark and light themes via `View > Theme`; the
selection is saved for future launches.

In the full image viewer, use `Right` / `D` for the next image and `Left` / `A`
for the previous image. A short key press moves by one image, while holding the
key continues navigating after a short delay.

## Tech Stack

- Python 3.10+
- PyQt6
- SQLCipher through `sqlcipher3-wheels`
- OpenCV through `opencv-python`
- PyInstaller onefile Windows application and Inno Setup installer

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

Use a clean 64-bit Windows Python 3.11 virtual environment and Inno Setup 6
to build the installer. The build also requires the Windows .NET Framework 4
C# compiler. UPX is disabled for the application build.

```powershell
python -m pip install -r requirements-release-win-amd64-py311.txt
.\tools\build-installer.ps1 -Python .\.venv\Scripts\python.exe
```

The build writes two required release assets to `dist\installer`:

- `TagExplorer-Setup.exe`: the installer for new users.
- `TagExplorer.exe`: a compatibility bridge for the updater in portable 1.2.0.

Attach **both files with these exact names** to each GitHub Release. The old
updater only recognizes `TagExplorer.exe`; that file is the bridge, not the
installed application. It opens the installer and retains the old executable
if installation is cancelled or fails. After a successful installation and
health check, it starts the installed application and removes the portable
executable. Installation requests administrator approval and defaults to
`%ProgramFiles%\TagExplorer`.

The already-released 1.2.0 updater must be able to write beside its portable
EXE to stage `TagExplorer_new.exe`; it cannot be fixed retroactively. If that
directory is read-only, run `TagExplorer-Setup.exe` manually and remove the
old portable file after verifying the installation. A manual installation
cannot automatically locate every portable copy.

The in-app updater checks `Solverg/TagExplorer` and accepts release tags in
`v1.2.3` or `1.2.3` format. The release tag must match `APP_VERSION` in
`main.py` and the version resource in `version_info.txt`.

The committed `requirements.txt` pins direct dependencies, and
`requirements-release-win-amd64-py311.txt` pins the known transitive dependency
set for the Windows x86-64 / Python 3.11 release. For stronger supply
chain integrity, regenerate a hash-locked file on the target Windows release
machine:

```powershell
python -m pip install pip-tools
pip-compile `
  --generate-hashes `
  --output-file requirements-lock-win-amd64-py311.txt `
  requirements-release-win-amd64-py311.txt
python -m pip install --require-hashes -r requirements-lock-win-amd64-py311.txt
.\tools\build-installer.ps1 -Python .\.venv\Scripts\python.exe
```

## Pre-Release Checks

Run these before building or publishing:

```powershell
python main.py --healthcheck
python -m unittest discover -s tests -v
python tools/smoke-installer.py
```

The installer smoke test builds an isolated non-admin test variant. It does
not replace the manual administrator/UAC and real 1.2.0 update checks below.

## Release Smoke Checklist

- Build inside a clean Windows virtual environment.
- Confirm `python main.py` launches from source in the release environment.
- Install `TagExplorer-Setup.exe` in a clean Windows account and verify the
  shortcuts, uninstall entry, and launch from Program Files.
- Upgrade a real 1.2.0 standalone executable through its in-app updater,
  including from a path with spaces and non-ASCII characters. Confirm the old
  executable is removed only after installation succeeds.
- Cancel the UAC prompt and installer separately, and verify the standalone
  executable remains usable. Exercise a subsequent update of the installed app.
- Confirm settings and cache remain available after the upgrade and that
  uninstalling leaves user data intact.
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
- Exercise AND, OR, and NOT tag filtering.
- Exercise file type filters for images, video, audio, and documents.
- Switch between dark and light themes from `View > Theme`, restart the app,
  and confirm the selected theme persists.
- Confirm the UI remains readable when Windows uses non-default or custom color
  theme settings.
- Open image previews and the full image viewer.
- In the full image viewer, confirm a short `Right` / `D` or `Left` / `A` key
  press advances one image and holding the key continues navigation.
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
- Check the installed executable Properties dialog contains the release version.
- After the release exists on GitHub, confirm the installed version reports no
  update; repeat update installation when a newer release is published.
- Scan the release archive with Windows Defender or the intended AV baseline.

## SQLCipher on Windows

The supported Windows dependency is `sqlcipher3-wheels`. Do not add
`pysqlcipher3` to the release environment or PyInstaller hidden imports; it is
source-distributed and can reintroduce local SQLCipher compilation issues.

If SQLCipher cannot be imported, TagExplorer falls back to standard `sqlite3`
and warns that the cache is unencrypted.

On upgrade, legacy recursive JSON caches are imported into the current cache
without overwriting existing records. Successfully imported JSON files are
removed; unreadable files are retained and reported. The cache database schema
is upgraded in place, preserving records and thumbnails. If SQLCipher becomes
available after a plaintext fallback was used, that database is exported to an
encrypted database before use. If SQLCipher is unavailable for an existing
encrypted database, the app retains the database and reports the problem.

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
