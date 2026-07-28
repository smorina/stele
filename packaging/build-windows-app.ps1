# Build and smoke-test the portable x86-64 Windows Stele folder.

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DistDir = if ($env:STELE_DIST_DIR) {
    [IO.Path]::GetFullPath($env:STELE_DIST_DIR)
} else {
    Join-Path $Root "dist"
}
$WorkDir = if ($env:STELE_BUILD_DIR) {
    [IO.Path]::GetFullPath($env:STELE_BUILD_DIR)
} else {
    Join-Path $Root "build\pyinstaller"
}

if (-not $IsWindows) {
    throw "The Windows app must be built on Windows."
}
if (-not [Environment]::Is64BitProcess) {
    throw "The Windows app must be built with 64-bit Python."
}
$Architecture = [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture
if ($Architecture.ToString() -ne "X64") {
    throw "Unsupported Windows build architecture: $Architecture"
}

$env:PYINSTALLER_CONFIG_DIR = Join-Path $WorkDir "config"
Remove-Item -Recurse -Force (Join-Path $DistDir "Stele") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $DistDir | Out-Null
New-Item -ItemType Directory -Force $WorkDir | Out-Null
New-Item -ItemType Directory -Force $env:PYINSTALLER_CONFIG_DIR | Out-Null

& uv run --frozen pyinstaller `
    --noconfirm `
    --clean `
    --distpath $DistDir `
    --workpath $WorkDir `
    (Join-Path $Root "packaging\stele-windows.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with status $LASTEXITCODE."
}

$App = Join-Path $DistDir "Stele"
$Executable = Join-Path $App "Stele.exe"
if (-not (Test-Path -PathType Leaf $Executable)) {
    throw "PyInstaller did not create $Executable."
}
if (-not (Test-Path -PathType Container (Join-Path $App "_internal"))) {
    throw "PyInstaller did not create Stele's _internal dependency folder."
}

Copy-Item (Join-Path $Root "LICENSE") (Join-Path $App "LICENSE.txt")
Copy-Item (Join-Path $Root "packaging\WINDOWS-README.txt") (Join-Path $App "README.txt")

$SmokeHome = Join-Path ([IO.Path]::GetTempPath()) ("stele-app-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $SmokeHome | Out-Null
try {
    & uv run --frozen python `
        (Join-Path $Root "packaging\smoke-packaged-app.py") `
        $Executable `
        $SmokeHome
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged smoke test failed with status $LASTEXITCODE."
    }
} finally {
    Remove-Item -Recurse -Force $SmokeHome -ErrorAction SilentlyContinue
}

Write-Host "Built portable Windows x86-64 app: $App"
