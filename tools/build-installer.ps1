param(
    [string]$Python = '.\.venv\Scripts\python.exe',
    [string]$InnoCompiler = '',
    [switch]$AllowDevelopmentPython
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$Python = (Resolve-Path -LiteralPath $Python).Path
if (-not $InnoCompiler) {
    $candidates = @(
        (Join-Path $repo '.codex-python\installer-tools\inno\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $InnoCompiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) { throw 'Inno Setup 6 compiler ISCC.exe was not found.' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework C# compiler was not found.' }

$pyVersion = & $Python -c 'import sys; print("%d.%d" % sys.version_info[:2])'
if ($LASTEXITCODE -ne 0) { throw 'Could not run the requested Python interpreter.' }
if ($pyVersion -ne '3.11' -and -not $AllowDevelopmentPython) {
    throw "Release builds require Python 3.11; found $pyVersion. Use -AllowDevelopmentPython only for local testing."
}
$version = & $Python -c 'import os, sys; sys.path.insert(0, os.getcwd()); import main; print(main.APP_VERSION)'
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw 'Could not determine the application version.' }
$output = Join-Path $repo $(if ($AllowDevelopmentPython) { 'dist\installer-dev' } else { 'dist\installer' })
$work = Join-Path $repo 'build\installer'
$appDist = Join-Path $work 'app'
New-Item -ItemType Directory -Force -Path $output, $work, $appDist | Out-Null

$previousPath = $env:PATH
$previousConfigDir = $env:PYINSTALLER_CONFIG_DIR
$previousBuildPath = $env:TAGEXPLORER_BUILD_PATH
try {
    # Do not bundle unrelated DLLs supplied by Codex or other tools on PATH.
    # In particular, Qt requires the Windows system ICU, not third-party ICU DLLs.
    $env:TAGEXPLORER_BUILD_PATH = @((Join-Path $env:WINDIR 'System32'), $env:WINDIR,
                                   (Join-Path $env:WINDIR 'System32\Wbem'), (Split-Path -Parent $Python)) -join ';'
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $work 'pyinstaller-config'
    # Set PATH inside Python too: managed environments may restore PATH when spawning it.
    & $Python -c 'import os, runpy; os.environ["PATH"] = os.environ["TAGEXPLORER_BUILD_PATH"]; runpy.run_module("PyInstaller", run_name="__main__")' `
        --noconfirm --clean --distpath $appDist --workpath (Join-Path $work 'pyinstaller') build.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
}
finally {
    $env:PATH = $previousPath
    $env:PYINSTALLER_CONFIG_DIR = $previousConfigDir
    $env:TAGEXPLORER_BUILD_PATH = $previousBuildPath
}
$appExe = Join-Path $appDist 'TagExplorer.exe'
if (-not (Test-Path -LiteralPath $appExe)) { throw 'PyInstaller did not produce TagExplorer.exe.' }
$builtVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($appExe).ProductVersion
if ($builtVersion -ne $version) { throw "Built executable version '$builtVersion' does not match APP_VERSION '$version'." }
$appCheck = Start-Process -FilePath $appExe -ArgumentList '--healthcheck' -WindowStyle Hidden -PassThru
if (-not $appCheck.WaitForExit(60000)) {
    $appCheck.Kill()
    throw 'Built application healthcheck timed out.'
}
if ($appCheck.ExitCode -ne 0) { throw "Built application healthcheck failed (exit $($appCheck.ExitCode)); see the startup log." }

& $InnoCompiler "/DAppVersion=$version" "/DAppExe=$appExe" "/DOutputDir=$output" (Join-Path $repo 'installer\TagExplorer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
$setup = Join-Path $output 'TagExplorer-Setup.exe'
$cleaner = Join-Path $work 'BridgeCleaner.exe'
$versionFile = Join-Path $work 'version.txt'
[System.IO.File]::WriteAllText($versionFile, $version, [System.Text.Encoding]::ASCII)
& $compiler /nologo /target:winexe /platform:x64 "/out:$cleaner" (Join-Path $repo 'installer\BridgeCleaner.cs')
if ($LASTEXITCODE -ne 0) { throw 'Update cleanup helper compilation failed.' }
$bridge = Join-Path $output 'TagExplorer.exe'
& $compiler /nologo /target:winexe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll `
    "/win32icon:$(Join-Path $repo 'app.ico')" "/out:$bridge" `
    "/resource:$setup,TagExplorer-Setup.exe" "/resource:$cleaner,BridgeCleaner.exe" "/resource:$versionFile,version.txt" `
    (Join-Path $repo 'installer\UpdateBridge.cs')
if ($LASTEXITCODE -ne 0) { throw 'Legacy-compatible update bridge compilation failed.' }
$bridgeCheck = Start-Process -FilePath $bridge -ArgumentList '--healthcheck' -WindowStyle Hidden -Wait -PassThru
if ($bridgeCheck.ExitCode -ne 0) { throw 'Update bridge healthcheck failed.' }
Write-Host "Installer: $setup"
Write-Host "Legacy updater asset: $bridge"
