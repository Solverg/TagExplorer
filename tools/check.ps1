$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".codex-python\python.exe"

if (-not (Test-Path $python)) {
    throw "Portable Python not found at $python"
}

& $python -m compileall core ui
