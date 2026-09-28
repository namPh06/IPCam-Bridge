$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Chạy run.ps1 -Setup trước.' }
& $pythonExe -m pip install --only-binary=:all: -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Không cài được thư viện đóng gói.' }
& $pythonExe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Kiểm thử thất bại; không đóng gói.' }
& $pythonExe -m PyInstaller --noconfirm IPCameraBridge.spec
if ($LASTEXITCODE -ne 0) { throw 'Đóng gói thất bại.' }
Write-Host 'Bản một file: dist\IPCameraBridge.exe (máy nhận cần cài OBS để dùng webcam ảo)'

