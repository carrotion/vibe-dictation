# Install / remove the Vibe Dictation tray app at Windows login.
param(
    [switch]$Remove,
    [switch]$NoStart
)

$Repo = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $Repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $Pythonw)) {
    $Pythonw = "pythonw.exe"
}
$Icon = Join-Path $Repo "vibe_dictation\assets\icon_idle.ico"
$Startup = [Environment]::GetFolderPath("Startup")
$Shortcut = Join-Path $Startup "Vibe Dictation.lnk"

if ($Remove) {
    if (Test-Path $Shortcut) { Remove-Item $Shortcut -Force }
    Write-Host "Removed: $Shortcut"
    exit 0
}

if (-not (Test-Path $Icon)) { throw "icon not found: $Icon" }

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($Shortcut)
$link.TargetPath = $Pythonw
$link.Arguments = "-m vibe_dictation"
$link.WorkingDirectory = $Repo
$link.IconLocation = $Icon
$link.Description = "Vibe Dictation - global voice dictation"
$link.Save()
Write-Host "Installed startup shortcut: $Shortcut"
Write-Host "Config: $env:APPDATA\vibe-dictation\config.json"
Write-Host "Logs:   $env:LOCALAPPDATA\vibe-dictation\logs\app.log"

if (-not $NoStart) {
    Start-Process -FilePath $Pythonw -ArgumentList "-m vibe_dictation" -WorkingDirectory $Repo
    Write-Host "Started tray app."
}
