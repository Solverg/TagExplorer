# Third-Party Notices

TagExplorer 1.2.0 is distributed as free and open-source software under the
GPL-3.0-or-later license. The Windows binary bundles third-party components
whose licenses must be respected by redistributors.

| Component | Use | License / Notice |
| --- | --- | --- |
| Python | Runtime used by PyInstaller builds | Python Software Foundation License |
| PyQt6 | GUI framework | GPL v3 or commercial PyQt license |
| PyQt6-Qt6 | Qt runtime subset installed by PyQt6 | LGPL v3 |
| PyQt6-sip | PyQt6 support module | BSD-2-Clause / SIP license metadata |
| OpenCV / opencv-python | Video preview decoding and frame extraction | Apache-2.0 for OpenCV; opencv-python packaging scripts are MIT |
| FFmpeg bundled by opencv-python wheels | Media codec backend used by OpenCV wheels | LGPL-2.1-or-later |
| NumPy | OpenCV dependency | BSD-3-Clause |
| sqlcipher3-wheels | SQLCipher-backed encrypted cache | zlib/libpng |
| SQLCipher | Encrypted SQLite engine bundled by sqlcipher3-wheels | BSD-style license |
| pywinstyles | Windows 10/11 window styling integration | CC0-1.0 |
| PyInstaller | Application freezer | GPL-2.0-or-later with bootloader exception |
| pyinstaller-hooks-contrib | PyInstaller hooks | Apache-2.0 or GPL-2.0 |
| altgraph | PyInstaller dependency | MIT |
| packaging | PyInstaller dependency | Apache-2.0 or BSD-2-Clause |
| pefile | PyInstaller Windows PE helper | MIT |
| pywin32-ctypes | PyInstaller Windows helper | BSD-3-Clause |

Release packages should include this notice and the project LICENSE next to the
executable. If a release process creates a full dependency lock file, refresh
this table against that resolved environment before publishing.
