param(
    [string]$Version = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$CMakeText = Get-Content -LiteralPath (Join-Path $ProjectRoot "CMakeLists.txt") -Raw
$VersionMatch = [regex]::Match($CMakeText, 'set\s*\(\s*PROJECT_VER\s+"?([^"\s\)]+)')
if (-not $VersionMatch.Success) {
    throw "CMakeLists.txt does not define PROJECT_VER."
}
$ProjectVersion = $VersionMatch.Groups[1].Value
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = $ProjectVersion
}
if ($Version -ne $ProjectVersion) {
    throw "Package version $Version does not match firmware version $ProjectVersion."
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must use semantic version format such as 1.0.0."
}

$CodxRoot = Join-Path $ProjectRoot ".codx"
$VenvRoot = Join-Path $CodxRoot "packaging-venv"
$Python = Join-Path $VenvRoot "Scripts\python.exe"
$WorkRoot = Join-Path $CodxRoot "packaging-work"
$DistRoot = Join-Path $WorkRoot "dist"
$BuildRoot = Join-Path $WorkRoot "build"
$ReleaseRoot = Join-Path $ProjectRoot "release\DotiiManagementCenter-$Version"
$Archive = Join-Path $ProjectRoot "release\DotiiManagementCenter-$Version-portable.zip"
$BridgeVersionFile = Join-Path $CodxRoot "packaging-version-bridge.txt"
$HostVersionFile = Join-Path $CodxRoot "packaging-version-host.txt"
$FirmwareBuildRoot = Join-Path $ProjectRoot "build"
$FirmwareRoot = Join-Path $ProjectRoot "firmware"
$FirmwareManifest = Join-Path $FirmwareBuildRoot "flasher_args.json"

if (-not (Test-Path -LiteralPath $FirmwareManifest -PathType Leaf)) {
    throw "Run idf.py build before packaging: build\flasher_args.json is missing."
}
$FirmwarePayload = Get-Content -LiteralPath $FirmwareManifest -Raw | ConvertFrom-Json
$FlashFiles = $FirmwarePayload.flash_files.PSObject.Properties
if (@($FlashFiles).Count -eq 0) {
    throw "build\flasher_args.json does not contain flash_files."
}
if (Test-Path -LiteralPath $FirmwareRoot) {
    Remove-Item -LiteralPath $FirmwareRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $FirmwareRoot | Out-Null
Copy-Item -LiteralPath $FirmwareManifest -Destination $FirmwareRoot -Force
foreach ($FlashFile in $FlashFiles) {
    $RelativeName = [string]$FlashFile.Value
    $Source = [IO.Path]::GetFullPath((Join-Path $FirmwareBuildRoot $RelativeName))
    if (-not $Source.StartsWith(([IO.Path]::GetFullPath($FirmwareBuildRoot) + '\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Firmware image escapes build directory: $RelativeName"
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Firmware image is missing: $RelativeName"
    }
    $Destination = Join-Path $FirmwareRoot $RelativeName
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

New-Item -ItemType Directory -Force -Path $CodxRoot | Out-Null
$BridgeVersionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($Version.Split('.')[0]), $($Version.Split('.')[1]), $($Version.Split('.')[2]), 0), prodvers=($($Version.Split('.')[0]), $($Version.Split('.')[1]), $($Version.Split('.')[2]), 0)),
  kids=[StringFileInfo([StringTable('080404B0', [StringStruct('CompanyName', 'Dotii'), StringStruct('FileDescription', 'Dotii \u7ba1\u7406\u4e2d\u5fc3\u540e\u53f0\u670d\u52a1'), StringStruct('FileVersion', '$Version'), StringStruct('InternalName', 'DotiiBridge'), StringStruct('OriginalFilename', 'DotiiBridge.exe'), StringStruct('ProductName', 'Dotii \u7ba1\u7406\u4e2d\u5fc3'), StringStruct('ProductVersion', '$Version')])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"@
$HostVersionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($Version.Split('.')[0]), $($Version.Split('.')[1]), $($Version.Split('.')[2]), 0), prodvers=($($Version.Split('.')[0]), $($Version.Split('.')[1]), $($Version.Split('.')[2]), 0)),
  kids=[StringFileInfo([StringTable('080404B0', [StringStruct('CompanyName', 'Dotii'), StringStruct('FileDescription', 'Dotii \u7ba1\u7406\u4e2d\u5fc3'), StringStruct('FileVersion', '$Version'), StringStruct('InternalName', 'DotiiManagementCenter'), StringStruct('OriginalFilename', 'DotiiManagementCenter.exe'), StringStruct('ProductName', 'Dotii \u7ba1\u7406\u4e2d\u5fc3'), StringStruct('ProductVersion', '$Version')])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"@
# Keep the PyInstaller data-structure files ASCII-only. This prevents their
# Python parser from decoding Chinese resource strings with the active OEM code page.
[IO.File]::WriteAllText($BridgeVersionFile, $BridgeVersionText, [Text.Encoding]::ASCII)
[IO.File]::WriteAllText($HostVersionFile, $HostVersionText, [Text.Encoding]::ASCII)
if (-not (Test-Path $Python)) {
    & python -m venv $VenvRoot
}

& $Python -m pip install --disable-pip-version-check --requirement "packaging\requirements-build.txt"

if ($Clean -and (Test-Path $WorkRoot)) {
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $DistRoot, $BuildRoot | Out-Null

$env:DOTII_VERSION_FILE = $BridgeVersionFile
& $Python -m PyInstaller --noconfirm --clean `
    --distpath $DistRoot --workpath $BuildRoot `
    "packaging\DotiiBridge.spec"
$env:DOTII_VERSION_FILE = $HostVersionFile
& $Python -m PyInstaller --noconfirm --clean `
    --distpath $DistRoot --workpath $BuildRoot `
    "packaging\DotiiManagementCenter.spec"

$BridgeExe = Join-Path $DistRoot "DotiiBridge.exe"
$HostExe = Join-Path $DistRoot "DotiiManagementCenter.exe"
if (-not (Test-Path $BridgeExe) -or -not (Test-Path $HostExe)) {
    throw "PyInstaller did not produce both Dotii executables."
}

if (Test-Path -LiteralPath $ReleaseRoot) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
Copy-Item -LiteralPath $BridgeExe -Destination $ReleaseRoot -Force
Copy-Item -LiteralPath $HostExe -Destination $ReleaseRoot -Force

$ReleaseTools = Join-Path $ReleaseRoot "tools"
New-Item -ItemType Directory -Force -Path $ReleaseTools | Out-Null
foreach ($ToolName in @("node", "codex-cli", "ffmpeg")) {
    $ToolPath = Join-Path $ProjectRoot "tools\$ToolName"
    if (-not (Test-Path -LiteralPath $ToolPath -PathType Container)) {
        throw "Required bundled tool is missing: $ToolPath"
    }
    Copy-Item -LiteralPath $ToolPath -Destination $ReleaseTools -Recurse -Force
}

$Checksums = Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object FullName |
    ForEach-Object {
        $Relative = $_.FullName.Substring($ReleaseRoot.Length + 1).Replace('\', '/')
        "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $Relative
    }
$Checksums | Set-Content -LiteralPath (Join-Path $ReleaseRoot "SHA256SUMS.txt") -Encoding UTF8

Compress-Archive -Path (Join-Path $ReleaseRoot "*") -DestinationPath $Archive -Force
Get-FileHash -Algorithm SHA256 $Archive | Format-List
Write-Host "Portable package created: $Archive"
