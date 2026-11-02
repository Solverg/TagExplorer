# Project test environment

- On this Windows workspace, use `.\.codex-python\python.exe` for development
  checks. Its isolated Python 3.13 environment contains PyQt6 6.11.0,
  `sqlcipher3-wheels` 0.5.7, OpenCV 4.13.0.92, and pywinstyles 1.8. It is
  local tooling and is not part of a release.
- Run the complete suite in PowerShell with:

  ```powershell
  $env:QT_QPA_PLATFORM = 'offscreen'
  .\.codex-python\python.exe -m unittest discover -s tests -v
  ```

  Check the output for skipped tests, especially the Qt and SQLCipher tests;
  a successful run with skips does not verify those paths.
- If the local interpreter is missing, use a project virtual environment with
  the dependencies in `requirements.txt`; see the README. Do not assume another
  project's virtual environment is available or modify it.
- Official Windows release builds use Python 3.11 and the pinned release
  requirements in `requirements-release-win-amd64-py311.txt`. Passing tests on
  the local Python 3.13 interpreter does not replace release-environment checks.
- This workspace also has `.\.codex-python\release-venv\Scripts\python.exe`
  with those Python 3.11 release pins and a local Inno Setup 6 compiler. Build
  both installer and legacy bridge with `tools\build-installer.ps1`; attach
  both release artifacts using the exact names documented in the README.
