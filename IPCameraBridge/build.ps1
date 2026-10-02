param(
    [switch]$Service,
    [string]$PythonPath,
    [string]$ISCCPath,
    [string]$OBSInstallerPath
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = if ($PythonPath) { $PythonPath } else { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Chạy run.ps1 -Setup trước.' }
& $pythonExe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Môi trường build thiếu hoặc xung đột thư viện. Chạy run.ps1 -Setup trước.' }
& $pythonExe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Kiểm thử thất bại; không đóng gói.' }
if (-not $Service) {
    & $pythonExe -m PyInstaller --noconfirm IPCameraBridge.spec
    if ($LASTEXITCODE -ne 0) { throw 'Đóng gói thất bại.' }
    Write-Host 'Bản một file: dist\IPCameraBridge.exe (máy nhận cần cài OBS để dùng webcam ảo)'
    exit 0
}

$obsName = 'OBS-Studio-32.2.2-Windows-x64-Installer.exe'
$obsHash = 'C3A0B880ADBE64DC4BCB68F93016916AB5B55AE43FD227115287BF80257D92DC'
$obsDirectory = Join-Path $PSScriptRoot 'third_party'
$obsTarget = Join-Path $obsDirectory $obsName
if ($OBSInstallerPath) {
    New-Item -ItemType Directory -Path $obsDirectory -Force | Out-Null
    Copy-Item -LiteralPath $OBSInstallerPath -Destination $obsTarget -Force
}
if (-not (Test-Path -LiteralPath $obsTarget)) {
    throw "Thiếu bộ cài OBS chính thức. Chạy lại với -OBSInstallerPath <đường dẫn $obsName>."
}
if ((Get-FileHash -LiteralPath $obsTarget -Algorithm SHA256).Hash -ne $obsHash) {
    throw 'Bộ cài OBS không đúng phiên bản/hash chính thức; đã dừng đóng gói.'
}
$obsSignature = Get-AuthenticodeSignature -LiteralPath $obsTarget
if ($obsSignature.Status -ne 'Valid' -or $obsSignature.SignerCertificate.Subject -notlike '*O="OBS Project, LLC"*') {
    throw 'Chữ ký số của bộ cài OBS không hợp lệ; đã dừng đóng gói.'
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
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
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
$setupVersion = [regex]::Match((Get-Content -LiteralPath 'installer.iss' -Raw), '#define AppVersion "([^"]+)"').Groups[1].Value
if (-not $setupVersion) { throw 'Không đọc được phiên bản bộ cài.' }
$setupName = "IPCameraBridge-Setup-$setupVersion.exe"
$setup = Join-Path $PSScriptRoot "dist-service\$setupName"
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
Set-Content -LiteralPath ($setup + '.sha256') -Value "$hash  $setupName" -Encoding ASCII
# Preserve existing download paths; use the versioned name for fresh Explorer icons.
$compatSetup = Join-Path $PSScriptRoot 'dist-service\IPCameraBridge-Setup.exe'
Copy-Item -LiteralPath $setup -Destination $compatSetup -Force
Set-Content -LiteralPath ($compatSetup + '.sha256') -Value "$hash  IPCameraBridge-Setup.exe" -Encoding ASCII
Write-Host "Bộ cài một file: dist-service\$setupName"
Write-Host "Kèm tùy chọn cài OBS Studio 32.2.2: $obsName"
Write-Host "SHA256: $hash"

