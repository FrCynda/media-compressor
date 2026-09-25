# Launcher for the Image Compressor web UI (double-click start.bat)
$ex = (Get-Command exiftool -ErrorAction SilentlyContinue).Source; if(-not $ex){ $ex="$env:LOCALAPPDATA\Programs\ExifTool\ExifTool.exe" }
$ox = (Get-Command oxipng   -ErrorAction SilentlyContinue).Source; if(-not $ox){ $ox="$env:LOCALAPPDATA\Microsoft\WinGet\Links\oxipng.exe" }
foreach($t in $ex,$ox){ if(-not (Test-Path $t)){ Write-Host "Missing tool: $t`nInstall with: winget install OliverBetz.ExifTool Shssoichiro.Oxipng"; pause; exit } }
$v = "$env:LOCALAPPDATA\imgcompress\venv"
if(-not (Test-Path "$v\Scripts\python.exe")){
  Write-Host 'First run: setting up the JPEG encoder (one time, needs internet)...'
  python -m venv $v; & "$v\Scripts\pip.exe" install -q pyjpegli pillow numpy
}
$env:IC_EXIFTOOL=$ex; $env:IC_OXIPNG=$ox
& "$v\Scripts\python.exe" "$PSScriptRoot\server.py"
