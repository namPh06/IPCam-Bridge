param([switch]$Setup)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & python -c "import sys,struct; assert sys.version_info[:2] == (3,13) and struct.calcsize('P') == 8, 'Use CPython 3.13 x64'"
    if ($LASTEXITCODE -ne 0) { throw 'Cần cài Python 3.13 x64 và thêm python vào PATH.' }
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Không tạo được .venv.' }
    $Setup = $true
}
if ($Setup) {
    & $pythonExe -m pip install --only-binary=:all: -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Không cài được thư viện.' }
}
& $pythonExe run.py
exit $LASTEXITCODE

