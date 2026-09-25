# Run: right-click -> Run with PowerShell   (or:  powershell -ExecutionPolicy Bypass -File compress.ps1)
Add-Type -AssemblyName System.Windows.Forms
function Pick($msg){ $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description=$msg; if($d.ShowDialog() -ne 'OK'){ exit }; $d.SelectedPath }

$in  = Pick 'Select the folder with the PNG images'
$out = Pick 'Select the OUTPUT folder (originals are never modified)'
if((Resolve-Path $in).Path -eq (Resolve-Path $out).Path){ Write-Host 'Input and output must be different folders.'; pause; exit }

$m = Read-Host 'Output format?  [J] JPEG (about 85-90% smaller)   [P] PNG, lossless (about 10% smaller)'
$mode = if($m -match '^[Pp]'){'png'}else{'jpg'}
$q = 90; if($mode -eq 'jpg'){ $x = Read-Host 'JPEG quality (85 smaller / 90 default / 95 best)'; if($x -match '^\d+$'){ $q=[int]$x } }
$w = [math]::Max(1,[int]((Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum/2))

$ex = (Get-Command exiftool -ErrorAction SilentlyContinue).Source; if(-not $ex){ $ex="$env:LOCALAPPDATA\Programs\ExifTool\ExifTool.exe" }
$ox = (Get-Command oxipng   -ErrorAction SilentlyContinue).Source; if(-not $ox){ $ox="$env:LOCALAPPDATA\Microsoft\WinGet\Links\oxipng.exe" }
foreach($t in $ex,$ox){ if(-not (Test-Path $t)){ Write-Host "Missing tool: $t  (winget install OliverBetz.ExifTool Shssoichiro.Oxipng)"; pause; exit } }

# private virtualenv for the JPEG encoder (created once)
$v = "$env:LOCALAPPDATA\imgcompress\venv"
if(-not (Test-Path "$v\Scripts\python.exe")){
  Write-Host 'First run: setting up the JPEG encoder (pyjpegli)...'
  python -m venv $v; & "$v\Scripts\pip.exe" install -q pyjpegli pillow numpy
}
$env:IC_EXIFTOOL=$ex; $env:IC_OXIPNG=$ox
& "$v\Scripts\python.exe" "$PSScriptRoot\worker.py" $in $out $mode $q $w
pause
