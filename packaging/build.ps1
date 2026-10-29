# Builds D:\PDFDesk-Setup.exe (Windows installer) from this repository.
# Needs (one-time): Python venv in backend\.venv with requirements + pyinstaller, Node.js, Inno Setup 6
#   winget install JRSoftware.InnoSetup
# and the Tesseract installer in packaging\vendor\tesseract-setup.exe
#   https://github.com/UB-Mannheim/tesseract/releases  (tesseract-ocr-w64-setup-*.exe)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "1/3 building the web UI"
Push-Location frontend; npm install --no-audit --no-fund | Out-Null; npm run build; Pop-Location

Write-Host "2/3 bundling the app (PyInstaller)"
Remove-Item dist-win, build-win -Recurse -Force -ErrorAction SilentlyContinue
& backend\.venv\Scripts\pyinstaller packaging\pdfdesk.spec --noconfirm --distpath "$root\dist-win" --workpath "$root\build-win"

Write-Host "3/3 building the installer (Inno Setup)"
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
if (-not (Test-Path packaging\vendor\tesseract-setup.exe)) { throw "packaging\vendor\tesseract-setup.exe is missing" }
& $iscc packaging\pdfdesk.iss
Write-Host "Done: D:\PDFDesk-Setup.exe"
