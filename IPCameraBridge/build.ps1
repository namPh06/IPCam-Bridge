param(
    [switch]$Service,
    [string]$PythonPath,
    [string]$ISCCPath
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = if ($PythonPath) { $PythonPath } else { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Chạy run.ps1 -Setup trước.' }
& $pythonExe -m pip install --only-binary=:all: -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Không cài được thư viện đóng gói.' }
& $pythonExe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Kiểm thử thất bại; không đóng gói.' }
if (-not $Service) {
    & $pythonExe -m PyInstaller --noconfirm IPCameraBridge.spec
    if ($LASTEXITCODE -ne 0) { throw 'Đóng gói thất bại.' }
    Write-Host 'Bản một file: dist\IPCameraBridge.exe (máy nhận cần cài OBS để dùng webcam ảo)'
    exit 0
}

& $pythonExe -m PyInstaller --noconfirm --distpath dist-service --workpath build-service IPCameraBridge-service.spec
if ($LASTEXITCODE -ne 0) { throw 'Đóng gói service thất bại.' }
$payload = Join-Path $PSScriptRoot 'dist-service\IPCameraBridge'
$binaryNames = @(Get-ChildItem -LiteralPath $payload -File -Recurse | Select-Object -ExpandProperty Name)
foreach ($required in @('python313.dll', 'pythoncom313.dll', 'pywintypes313.dll', 'servicemanager.pyd')) {
    if ($required -notin $binaryNames) { throw "Payload thiếu $required; không tạo bộ cài." }
}

if (-not $ISCCPath) {
    $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($compiler) { $ISCCPath = $compiler.Source }
    foreach ($candidate in @(
        (Join-Path $PSScriptRoot '.build-tools\InnoSetup6\ISCC.exe'),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
    )) {
        if (-not $ISCCPath -and (Test-Path -LiteralPath $candidate)) { $ISCCPath = $candidate }
    }
}
if (-not $ISCCPath -or -not (Test-Path -LiteralPath $ISCCPath)) {
    throw 'Đã tạo payload dist-service\IPCameraBridge. Cài Inno Setup 6.7.3+ từ https://jrsoftware.org/isdl.php rồi chạy lại build.ps1 -Service -ISCCPath <đường dẫn ISCC.exe>. Xem docs/SERVICE_PACKAGING.md.'
}
& $ISCCPath 'installer.iss'
if ($LASTEXITCODE -ne 0) { throw 'Biên dịch bộ cài service thất bại.' }
$setup = Join-Path $PSScriptRoot 'dist-service\IPCameraBridge-Setup.exe'
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
Set-Content -LiteralPath ($setup + '.sha256') -Value "$hash  IPCameraBridge-Setup.exe" -Encoding ASCII
Write-Host 'Bộ cài một file: dist-service\IPCameraBridge-Setup.exe'
Write-Host "SHA256: $hash"

