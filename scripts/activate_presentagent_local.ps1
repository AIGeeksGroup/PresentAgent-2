$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv-presentagent\Scripts\python.exe"
$VenvScripts = Join-Path $RepoRoot ".venv-presentagent\Scripts"
$MegaTtsRoot = Join-Path $RepoRoot "presentagent\MegaTTS3"
$FfmpegBin = Join-Path $RepoRoot "tools\ffmpeg\bin"
$LibreOfficeWrapper = Join-Path $RepoRoot "libreoffice.cmd"

if (-not (Test-Path $VenvPython)) {
    throw "Missing virtualenv python: $VenvPython"
}
if (-not (Test-Path $MegaTtsRoot)) {
    throw "Missing MegaTTS3 directory: $MegaTtsRoot"
}
if (-not (Test-Path $FfmpegBin)) {
    throw "Missing ffmpeg bin directory: $FfmpegBin"
}
if (-not (Test-Path $LibreOfficeWrapper)) {
    throw "Missing libreoffice wrapper: $LibreOfficeWrapper"
}

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"

$env:PYTHONPATH = "$RepoRoot;$MegaTtsRoot"
$env:PATH = "$VenvScripts;$FfmpegBin;$RepoRoot;$env:PATH"

Write-Host "PresentAgent local environment is ready." -ForegroundColor Green
Write-Host "RepoRoot: $RepoRoot"
Write-Host "Python:   $VenvPython"
Write-Host "PYTHONPATH=$($env:PYTHONPATH)"
Write-Host "PATH includes ffmpeg: $FfmpegBin"
Write-Host "LibreOffice wrapper: $LibreOfficeWrapper"
