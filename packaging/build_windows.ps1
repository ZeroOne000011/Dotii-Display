param(
    [string]$Version = "",
    [string]$FirmwareBuildDirectory = ".codex/build",
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
if ($Version -notmatch '^\d+\.\d+(?:\.\d+)?$') {
    throw "Version must use a numeric format such as 1.2 or 1.2.0."
}
$VersionParts = @($Version.Split('.'))
while ($VersionParts.Count -lt 3) { $VersionParts += '0' }
$NumericVersion = ($VersionParts + '0') -join ', '

$CodexRoot = Join-Path $ProjectRoot ".codex"
$VenvRoot = Join-Path $CodexRoot "packaging-venv"
$Python = Join-Path $VenvRoot "Scripts\python.exe"
$WorkRoot = Join-Path $CodexRoot "packaging-work"
$DistRoot = Join-Path $WorkRoot "dist"
$BuildRoot = Join-Path $WorkRoot "build"
$FinalReleaseRoot = Join-Path $ProjectRoot "release\DotiiManagementCenter-$Version"
$FinalArchive = Join-Path $ProjectRoot "release\DotiiManagementCenter-$Version-portable.zip"
$ReleaseRoot = Join-Path $WorkRoot "DotiiManagementCenter-$Version"
$Archive = Join-Path $WorkRoot "DotiiManagementCenter-$Version-portable.zip"
$BridgeVersionFile = Join-Path $WorkRoot "packaging-version-bridge.txt"
$HostVersionFile = Join-Path $WorkRoot "packaging-version-host.txt"
$FirmwareBuildRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $FirmwareBuildDirectory))
$FirmwareRoot = Join-Path $ProjectRoot "firmware"
$BuildFirmwareManifest = Join-Path $FirmwareBuildRoot "flasher_args.json"
$BundledFirmwareManifest = Join-Path $FirmwareRoot "flasher_args.json"

function Assert-ExitCode([string]$Action) {
    if ($LASTEXITCODE -ne 0) { throw "$Action failed with exit code $LASTEXITCODE." }
}

function Remove-WorkDirectory {
    $ResolvedWork = [IO.Path]::GetFullPath($WorkRoot)
    if (-not $ResolvedWork.StartsWith($CodexRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build work directory escapes .codex: $ResolvedWork"
    }
    if (Test-Path -LiteralPath $ResolvedWork) {
        Remove-Item -LiteralPath $ResolvedWork -Recurse -Force
    }
}

# Do not overwrite an earlier delivery, even if the caller asks for a clean build.
if ((Test-Path -LiteralPath $FinalReleaseRoot) -or (Test-Path -LiteralPath $FinalArchive)) {
    throw "Release $Version already exists. Move it explicitly before rebuilding."
}
foreach ($ToolName in @("node", "codex-cli", "ffmpeg")) {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "tools\$ToolName") -PathType Container)) {
        throw "Required bundled tool is missing: $ToolName"
    }
}
New-Item -ItemType Directory -Force -Path $CodexRoot | Out-Null
if ($Clean) { Remove-WorkDirectory }
New-Item -ItemType Directory -Force -Path $DistRoot, $BuildRoot | Out-Null
$env:TEMP = $WorkRoot
$env:TMP = $WorkRoot
$env:PIP_CACHE_DIR = Join-Path $CodexRoot "pip-cache"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $WorkRoot "pyinstaller-cache"
$env:PYTHONDONTWRITEBYTECODE = '1'

if (Test-Path -LiteralPath $BuildFirmwareManifest -PathType Leaf) {
    $FirmwareSourceRoot = $FirmwareBuildRoot
    $FirmwareManifest = $BuildFirmwareManifest
    $RefreshFirmwareBundle = $true
} elseif (Test-Path -LiteralPath $BundledFirmwareManifest -PathType Leaf) {
    # Management-center-only releases may reuse the already validated formal
    # firmware bundle. A fresh ESP-IDF build still takes precedence when present.
    $FirmwareSourceRoot = $FirmwareRoot
    $FirmwareManifest = $BundledFirmwareManifest
    $RefreshFirmwareBundle = $false
} else {
    throw "No firmware manifest is available. Run idf.py build or restore firmware\flasher_args.json."
}
$FirmwarePayload = Get-Content -LiteralPath $FirmwareManifest -Raw | ConvertFrom-Json
$FlashFiles = $FirmwarePayload.flash_files.PSObject.Properties
if (@($FlashFiles).Count -eq 0) {
    throw "$FirmwareManifest does not contain flash_files."
}
foreach ($FlashFile in $FlashFiles) {
    $RelativeName = [string]$FlashFile.Value
    $Source = [IO.Path]::GetFullPath((Join-Path $FirmwareSourceRoot $RelativeName))
    if (-not $Source.StartsWith(([IO.Path]::GetFullPath($FirmwareSourceRoot) + '\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Firmware image escapes its source directory: $RelativeName"
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Firmware image is missing: $RelativeName"
    }
}

# Check the actual image, rather than trusting a possibly stale CMake file.
$AppImage = Join-Path $FirmwareSourceRoot 'state_display.bin'
$AppBytes = [IO.File]::ReadAllBytes($AppImage)
if ($AppBytes.Length -lt 80) { throw 'Firmware application image is truncated.' }
$ImageVersion = [Text.Encoding]::UTF8.GetString($AppBytes, 48, 32).Trim([char]0)
if ($ImageVersion -ne $Version) {
    throw "Firmware image version $ImageVersion does not match package $Version."
}
if ($RefreshFirmwareBundle) {
    foreach ($FlashFile in $FlashFiles) {
        $RelativeName = [string]$FlashFile.Value
        $Destination = Join-Path $FirmwareRoot $RelativeName
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
        Copy-Item -LiteralPath (Join-Path $FirmwareSourceRoot $RelativeName) -Destination $Destination -Force
    }
    Copy-Item -LiteralPath $FirmwareManifest -Destination $FirmwareRoot -Force
}

$BridgeVersionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($NumericVersion), prodvers=($NumericVersion)),
  kids=[StringFileInfo([StringTable('080404B0', [StringStruct('CompanyName', 'Dotii'), StringStruct('FileDescription', 'Dotii \u7ba1\u7406\u4e2d\u5fc3\u540e\u53f0\u670d\u52a1'), StringStruct('FileVersion', '$Version'), StringStruct('InternalName', 'DotiiBridge'), StringStruct('OriginalFilename', 'DotiiBridge.exe'), StringStruct('ProductName', 'Dotii \u7ba1\u7406\u4e2d\u5fc3'), StringStruct('ProductVersion', '$Version')])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"@
$HostVersionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($NumericVersion), prodvers=($NumericVersion)),
  kids=[StringFileInfo([StringTable('080404B0', [StringStruct('CompanyName', 'Dotii'), StringStruct('FileDescription', 'Dotii \u7ba1\u7406\u4e2d\u5fc3'), StringStruct('FileVersion', '$Version'), StringStruct('InternalName', 'DotiiManagementCenter'), StringStruct('OriginalFilename', 'DotiiManagementCenter.exe'), StringStruct('ProductName', 'Dotii \u7ba1\u7406\u4e2d\u5fc3'), StringStruct('ProductVersion', '$Version')])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"@
# Keep the PyInstaller data-structure files ASCII-only. This prevents their
# Python parser from decoding Chinese resource strings with the active OEM code page.
[IO.File]::WriteAllText($BridgeVersionFile, $BridgeVersionText, [Text.Encoding]::ASCII)
[IO.File]::WriteAllText($HostVersionFile, $HostVersionText, [Text.Encoding]::ASCII)
if (-not (Test-Path $Python)) {
    & python -m venv $VenvRoot
    Assert-ExitCode 'Create packaging environment'
}

& $Python -m pip install --disable-pip-version-check --requirement "packaging\requirements-build.txt"
Assert-ExitCode 'Install packaging dependencies'

$env:DOTII_VERSION_FILE = $BridgeVersionFile
& $Python -m PyInstaller --noconfirm --clean `
    --distpath $DistRoot --workpath $BuildRoot `
    "packaging\DotiiBridge.spec"
Assert-ExitCode 'Build background service'
$env:DOTII_VERSION_FILE = $HostVersionFile
& $Python -m PyInstaller --noconfirm --clean `
    --distpath $DistRoot --workpath $BuildRoot `
    "packaging\DotiiManagementCenter.spec"
Assert-ExitCode 'Build tray host'

$BridgeExe = Join-Path $DistRoot "DotiiBridge.exe"
$HostExe = Join-Path $DistRoot "DotiiManagementCenter.exe"
if (-not (Test-Path $BridgeExe) -or -not (Test-Path $HostExe)) {
    throw "PyInstaller did not produce both Dotii executables."
}

foreach ($Executable in @($BridgeExe, $HostExe)) {
    $Info = [Diagnostics.FileVersionInfo]::GetVersionInfo($Executable)
    if ($Info.FileVersion -ne $Version -or $Info.ProductVersion -ne $Version) {
        throw "Executable version mismatch: $Executable"
    }
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
foreach ($Notice in Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'tools') -File |
    Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE|THIRD_PARTY)' }) {
    Copy-Item -LiteralPath $Notice.FullName -Destination $ReleaseTools -Force
}

$Checksums = Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object FullName |
    ForEach-Object {
        $Relative = $_.FullName.Substring($ReleaseRoot.Length + 1).Replace('\', '/')
        "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $Relative
    }
$Checksums | Set-Content -LiteralPath (Join-Path $ReleaseRoot "SHA256SUMS.txt") -Encoding UTF8

Compress-Archive -Path (Join-Path $ReleaseRoot "*") -DestinationPath $Archive
$ExpectedReleaseParent = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'release')) + '\'
foreach ($Target in @($FinalReleaseRoot, $FinalArchive)) {
    if (-not ([IO.Path]::GetFullPath($Target)).StartsWith($ExpectedReleaseParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release destination escapes project release directory: $Target"
    }
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $FinalReleaseRoot) | Out-Null
Move-Item -LiteralPath $ReleaseRoot -Destination $FinalReleaseRoot
Move-Item -LiteralPath $Archive -Destination $FinalArchive
Get-FileHash -Algorithm SHA256 $FinalArchive | Format-List
Write-Host "Portable package created: $FinalArchive"
