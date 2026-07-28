$ErrorActionPreference = "Stop"

$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $SourceDir "uv.lock"))) {
    throw "This installer must stay beside the extracted Stele release files."
}

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Stele supports 64-bit Windows 10 or newer."
}
$Architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($Architecture -ne "X64") {
    throw "Stele cannot install on Windows $Architecture because its GDS engines have no compatible wheels."
}

$SteleRoot = if ($env:STELE_HOME) { $env:STELE_HOME } else { Join-Path $env:USERPROFILE ".stele" }
$AppDir = Join-Path $SteleRoot "app"
$StageDir = Join-Path $SteleRoot "app.new"
$OldDir = Join-Path $SteleRoot "app.old"
New-Item -ItemType Directory -Force -Path $SteleRoot | Out-Null

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($UvCommand) {
    $Uv = $UvCommand.Source
} else {
    Write-Host "Installing the uv runtime (no administrator password needed)..."
    $UvInstaller = Join-Path $SteleRoot "uv-installer.ps1"
    Invoke-WebRequest "https://astral.sh/uv/install.ps1" -OutFile $UvInstaller
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UvInstaller
    $Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
}

Remove-Item -Recurse -Force $StageDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $StageDir | Out-Null
foreach ($Item in @("src", "assets", "profiles", "pyproject.toml", "uv.lock", "README.md", "LICENSE")) {
    Copy-Item -Recurse -Path (Join-Path $SourceDir $Item) -Destination $StageDir
}

Remove-Item -Recurse -Force $OldDir -ErrorAction SilentlyContinue
if (Test-Path $AppDir) {
    Move-Item $AppDir $OldDir
}
Move-Item $StageDir $AppDir

try {
    Push-Location $AppDir
    & $Uv sync --frozen --no-dev
    $env:STELE_HOME = $SteleRoot
    & (Join-Path $AppDir ".venv\Scripts\stele.exe") doctor
    Pop-Location
} catch {
    Pop-Location -ErrorAction SilentlyContinue
    Write-Host "Installation check failed; restoring the previous Stele app."
    Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
    if (Test-Path $OldDir) {
        Move-Item $OldDir $AppDir
    }
    throw
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$Launcher = Join-Path $Desktop "Stele.cmd"
$Executable = Join-Path $AppDir ".venv\Scripts\stele.exe"
Set-Content -Path $Launcher -Value "@echo off`r`nstart `"`" `"$Executable`" ui`r`n"

Write-Host ""
Write-Host "Stele is installed."
Write-Host "Launcher: $Launcher"
Write-Host "Your documents and completed runs stay in: $(Join-Path $SteleRoot 'runs')"
