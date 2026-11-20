$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$BuildTmp = Join-Path $RootDir "build_tmp"
$ShimBin = Join-Path $BuildTmp "shims"
$SwoftyRepo = Join-Path $BuildTmp "SwoftyLauncher"
$SwoftyCommit = "873fe1b4dec0bdbacea64b90218d4ff765f28116"
$SwoftyRemote = "https://github.com/Swofty-Developments/SwoftyLauncher.git"
$DistDir = Join-Path $RootDir "dist\F1ClassicLauncher_Full"
$SwoftyCoreOut = Join-Path $DistDir "swofty_core"

Write-Host "=== F1 Classic Launcher (PySide6 Desktop) + Swofty Core Windows Build ==="

# 1. Verify required tools on Windows
foreach ($cmd in @("git", "cargo", "rustc", "python", "bash")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "Required tool '$cmd' not found on PATH."
    }
}

if (-not (Get-Command "ninja" -ErrorAction SilentlyContinue)) {
    Write-Host "Ninja not found on PATH; installing the Python-distributed Ninja executable..."
    python -m pip install --upgrade ninja
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Ninja."
    }
    $PythonScripts = (python -c "import sysconfig; print(sysconfig.get_path('scripts'))").Trim()
    $env:PATH = "$PythonScripts;$env:PATH"
}
if (-not (Get-Command "ninja" -ErrorAction SilentlyContinue)) {
    throw "Ninja is required by Swofty native/build-win.sh and is not on PATH."
}
Write-Host "Verified Ninja version: $(ninja --version)"

# Enforce Rust >= 1.95.0 (per SwoftyLauncher Cargo.toml rust-version = "1.95")
$RustVerRaw = (rustc --version)
if ($RustVerRaw -match 'rustc\s+(\d+\.\d+\.\d+)') {
    $RustVer = [version]$Matches[1]
    if ($RustVer -lt [version]"1.95.0") {
        throw "Rust >= 1.95.0 is required by SwoftyLauncher, found $RustVer."
    }
    Write-Host "Verified Rust version: $RustVer"
} else {
    throw "Could not parse rustc version from: $RustVerRaw"
}

$RequiredZigVersion = "0.15.2"
if (-not (Get-Command "zig" -ErrorAction SilentlyContinue)) {
    throw "Zig $RequiredZigVersion is required by Swofty native/build-win.sh and is not on PATH."
}
$ZigVersion = (& zig version).Trim()
if ($LASTEXITCODE -ne 0 -or $ZigVersion -ne $RequiredZigVersion) {
    throw "Expected Zig $RequiredZigVersion to match the GitHub Actions workflow, found '$ZigVersion'."
}
Write-Host "Verified Zig version: $ZigVersion"

New-Item -ItemType Directory -Force -Path $BuildTmp | Out-Null
New-Item -ItemType Directory -Force -Path $ShimBin | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path $SwoftyCoreOut | Out-Null

$ZigProbeSource = Join-Path $PSScriptRoot "zig_from_chars_probe.cpp"
$ZigProbeOutput = Join-Path $env:TEMP "f1classic-zig-from-chars-$PID.o"
try {
    & zig c++ -target x86_64-windows-gnu "-std=c++23" -c $ZigProbeSource -o $ZigProbeOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Zig $RequiredZigVersion failed the C++23 std::from_chars(double) probe."
    }
} finally {
    Remove-Item -LiteralPath $ZigProbeOutput -Force -ErrorAction SilentlyContinue
}
Write-Host "Verified Zig C++23 std::from_chars(double) support for x86_64-windows-gnu."

$WatermarkSource = Join-Path $RootDir "native\f1classic_watermark.cpp"
$WatermarkManifest = Join-Path $RootDir "swofty_addons\f1classic.launcher\swofty.json"
$WatermarkPackage = Join-Path $SwoftyCoreOut "addons\f1classic.launcher"
$WatermarkNativeDir = Join-Path $WatermarkPackage "native"
$WatermarkDll = Join-Path $WatermarkNativeDir "f1classic_watermark.dll"
if (-not (Test-Path $WatermarkSource) -or -not (Test-Path $WatermarkManifest)) {
    throw "The F1 Classic Swofty watermark source or addon manifest is missing."
}
New-Item -ItemType Directory -Force -Path $WatermarkNativeDir | Out-Null
Copy-Item $WatermarkManifest (Join-Path $WatermarkPackage "swofty.json") -Force

# Retain a nice shim for non-CI/local builds, but do not shim taskset: upstream probes `taskset -pc` in CI too.
@'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--adjustment)
      shift 2
      ;;
    -[0-9]*)
      shift
      ;;
    *)
      break
      ;;
  esac
done
exec "$@"
'@ | Set-Content -Path (Join-Path $ShimBin "nice") -Encoding Ascii -NoNewline

$env:PATH = "$ShimBin;$env:PATH"

# 2. Check out the verified Swofty-Developments/SwoftyLauncher revision
if (-not (Test-Path $SwoftyRepo)) {
    git clone --no-checkout --depth 1 $SwoftyRemote $SwoftyRepo
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clone SwoftyLauncher."
    }
}

if (-not (Test-Path (Join-Path $SwoftyRepo ".git"))) {
    throw "The cached SwoftyLauncher path is not a Git checkout: $SwoftyRepo"
}
$CachedRemote = (git -C $SwoftyRepo remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or $CachedRemote -ne $SwoftyRemote) {
    throw "The cached SwoftyLauncher checkout has an unexpected origin: '$CachedRemote'."
}
$CachedHead = (git -C $SwoftyRepo rev-parse HEAD 2>$null)
if ($LASTEXITCODE -eq 0) {
    $CachedHead = $CachedHead.Trim()
    if ($CachedHead -ne $SwoftyCommit) {
        $CachedChanges = git -C $SwoftyRepo status --porcelain
        if ($LASTEXITCODE -ne 0) {
            throw "Could not inspect the cached SwoftyLauncher worktree."
        }
        if ($CachedChanges) {
            throw "The cached SwoftyLauncher checkout has local changes and is not pinned to $SwoftyCommit. Preserve or clear that cache before rebuilding."
        }
    }
}
if ($CachedHead -ne $SwoftyCommit) {
    git -C $SwoftyRepo fetch --depth 1 origin $SwoftyCommit
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to fetch pinned SwoftyLauncher commit $SwoftyCommit."
    }
    git -C $SwoftyRepo checkout --detach $SwoftyCommit
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to check out pinned SwoftyLauncher commit $SwoftyCommit."
    }
}
$CheckedOutSwoftyCommit = (git -C $SwoftyRepo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $CheckedOutSwoftyCommit -ne $SwoftyCommit) {
    throw "SwoftyLauncher checkout mismatch: expected $SwoftyCommit, found '$CheckedOutSwoftyCommit'."
}
Write-Host "Verified SwoftyLauncher commit: $CheckedOutSwoftyCommit"

# 3. Compile Swofty CLI + swofty-core (Rust) and native C++ loader DLLs
Push-Location $SwoftyRepo
$CliManifest = Join-Path $SwoftyRepo "apps\cli\Cargo.toml"
if (-not (Test-Path $CliManifest)) {
    throw "Expected CLI manifest not found at: $CliManifest"
}

$BuildGraph = Join-Path $SwoftyRepo "native\tools\build_graph.py"
if (-not (Test-Path $BuildGraph)) {
    throw "Expected native/tools/build_graph.py not found at: $BuildGraph"
}
$InitCheck = Join-Path $SwoftyRepo "native\tools\init_check.py"
if (-not (Test-Path $InitCheck)) {
    throw "Expected native/tools/init_check.py not found at: $InitCheck"
}
python (Join-Path $PSScriptRoot "patch_swofty_build.py") $BuildGraph $InitCheck
if ($LASTEXITCODE -ne 0) {
    throw "Failed to apply verified Windows compatibility fixes to Swofty native build tools."
}

Write-Host "Building Swofty CLI via $CliManifest..."
cargo build --release --manifest-path $CliManifest

$ReleaseDir = Join-Path $SwoftyRepo "target\release"
$SwoftyExe = Join-Path $ReleaseDir "swofty.exe"
if (-not (Test-Path $SwoftyExe)) {
    $FoundExe = Get-ChildItem $ReleaseDir -Filter "swofty*.exe" | Select-Object -First 1
    if ($FoundExe) {
        $SwoftyExe = $FoundExe.FullName
    } else {
        throw "No compiled swofty*.exe binary found in $ReleaseDir"
    }
}
Copy-Item $SwoftyExe (Join-Path $SwoftyCoreOut "swofty.exe") -Force

if (Test-Path "LICENSE") {
    Copy-Item "LICENSE" (Join-Path $SwoftyCoreOut "SWOFTY_LICENSE.txt") -Force
}
if (Test-Path "THIRD_PARTY.md") {
    Copy-Item "THIRD_PARTY.md" (Join-Path $SwoftyCoreOut "SWOFTY_THIRD_PARTY.md") -Force
}

if (-not (Test-Path "native\build.sh")) {
    throw "Expected native/build.sh not found in SwoftyLauncher repository."
}
if (-not (Test-Path "native\build-win.sh")) {
    throw "Expected native/build-win.sh not found in SwoftyLauncher repository."
}
Write-Host "Building native C++ loader via native/build-win.sh..."
bash native/build-win.sh

$SdkInclude = Join-Path $SwoftyRepo "packages\sdk\include"
if (-not (Test-Path $SdkInclude)) {
    throw "Swofty SDK headers not found at: $SdkInclude"
}
Write-Host "Building the F1 Classic in-game watermark addon..."
& zig c++ -target x86_64-windows-gnu -std=c++23 -O2 -Wall -Wextra -I $SdkInclude -shared -s -o $WatermarkDll $WatermarkSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $WatermarkDll)) {
    throw "Failed to compile the F1 Classic Swofty overlay addon."
}
$WatermarkBytes = [System.IO.File]::ReadAllBytes($WatermarkDll)
if ($WatermarkBytes.Length -lt 64 -or $WatermarkBytes[0] -ne 0x4D -or $WatermarkBytes[1] -ne 0x5A) {
    throw "Invalid PE header in $WatermarkDll"
}
$WatermarkPeOffset = [BitConverter]::ToInt32($WatermarkBytes, 0x3C)
$WatermarkMachine = [BitConverter]::ToUInt16($WatermarkBytes, $WatermarkPeOffset + 4)
if ($WatermarkMachine -ne 0x8664) {
    throw "Watermark addon is not x86_64 (machine=0x$($WatermarkMachine.ToString('X4')))"
}

$NativeDistDir = Join-Path $SwoftyRepo "native\dist"
if (-not (Test-Path $NativeDistDir)) {
    throw "Expected native/dist/ output directory was not created by native/build.sh."
}
$NativeDlls = Get-ChildItem $NativeDistDir -Filter "*.dll" -Recurse
if (-not $NativeDlls -or $NativeDlls.Count -eq 0) {
    throw "No .dll files were produced in native/dist/."
}
foreach ($dll in $NativeDlls) {
    $bytes = [System.IO.File]::ReadAllBytes($dll.FullName)
    if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {
        throw "Invalid PE header in $($dll.FullName)"
    }
    $peOffset = [BitConverter]::ToInt32($bytes, 0x3C)
    $machine = [BitConverter]::ToUInt16($bytes, $peOffset + 4)
    if ($machine -ne 0x8664) {
        throw "DLL $($dll.Name) is not x86_64 (machine=0x$($machine.ToString('X4')))"
    }
    Write-Host "Verified x86_64 Windows DLL: $($dll.FullName)"
}

$NativeOut = Join-Path $SwoftyCoreOut "native\dist"
if (Test-Path $NativeOut) {
    Remove-Item $NativeOut -Recurse -Force
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $NativeOut) | Out-Null
Copy-Item $NativeDistDir $NativeOut -Recurse -Force
Pop-Location

# 4. Install PySide6 + PyInstaller and verify Qt startup/imports + unit tests
Push-Location $RootDir
python -m pip install --upgrade pip
if (Test-Path "requirements.txt") {
    python -m pip install --upgrade -r requirements.txt
} else {
    python -m pip install --upgrade PySide6 pyinstaller
}

python -m unittest discover -s . -p "test_*.py" -v

Write-Host "Testing PySide6 desktop window initialization (offscreen)..."
$env:QT_QPA_PLATFORM = "offscreen"
python -c "import tempfile; from pathlib import Path; from PySide6.QtWidgets import QApplication; from main import create_app_context; from ui.desktop_app import F1ClassicLauncherWindow; app = QApplication([]); ctx = create_app_context(Path(tempfile.mkdtemp())); win = F1ClassicLauncherWindow(ctx); print('Qt window initialized:', win.windowTitle())"
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

# 5. Build standalone windowed F1ClassicLauncher.exe with PyInstaller (PySide6)
$pyArgs = @(
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "F1ClassicLauncher",
    "--paths", ".",
    "--collect-submodules", "core",
    "--collect-submodules", "ui",
    "--collect-all", "PySide6"
)
if (Test-Path "templates") {
    $pyArgs += @("--add-data", "templates;templates")
}
$pyArgs += "main.py"

& pyinstaller @pyArgs

$LauncherExe = Join-Path $RootDir "dist\F1ClassicLauncher.exe"
if (-not (Test-Path $LauncherExe)) {
    throw "PyInstaller failed to produce $LauncherExe."
}
Copy-Item $LauncherExe (Join-Path $DistDir "F1ClassicLauncher.exe") -Force

foreach ($optFile in @("LICENSES_AND_ATTRIBUTION.md", "README.md")) {
    if (Test-Path $optFile) {
        Copy-Item $optFile (Join-Path $DistDir $optFile) -Force
    }
}
if (Test-Path "examples") {
    Copy-Item "examples" (Join-Path $DistDir "examples") -Recurse -Force
}

# 6. Genuine integration test: verify SwoftyBridge discovers swofty.exe + native/dist/ and runs swofty doctor
Write-Host "Running SwoftyBridge integration test against compiled swofty.exe..."
$TempDataDir = Join-Path $BuildTmp "swofty_test_data"
New-Item -ItemType Directory -Force -Path $TempDataDir | Out-Null
$VerifyPy = @"
from pathlib import Path
from core.logger import LauncherLogger
from core.swofty_bridge import SwoftyBridge

dist_dir = Path(r'$DistDir')
bridge = SwoftyBridge(dist_dir, LauncherLogger(dist_dir / 'logs'))
status = bridge.get_status()
assert status['available'], f'Bridge did not detect swofty.exe: {status}'
assert status['native_dlls'], f'No native DLLs detected in native/dist: {status}'

env = {'SWOFTY_DATA_DIR': r'$TempDataDir'}
res = bridge.run_cli(['doctor'], env_overrides=env)
print('doctor rc:', res.get('returncode'), 'stdout:', (res.get('stdout') or '')[:300])
assert res.get('returncode') is not None, f'CLI failed to execute: {res}'
addon = dist_dir / 'swofty_core' / 'addons' / 'f1classic.launcher'
installed = bridge.run_cli(['--profile', 'default', 'addons', 'install', str(addon)], env_overrides=env)
assert installed.get('ok'), f'Watermark addon install failed: {installed}'
enabled = bridge.run_cli(['--profile', 'default', 'addons', 'enable', 'f1classic.launcher'], env_overrides=env)
assert enabled.get('ok'), f'Watermark addon enable failed: {enabled}'
print('SwoftyBridge integration verified:', status['cli_path'], 'DLLs:', status['native_dlls'])
"@
python -c $VerifyPy
Pop-Location

Write-Host "Build and PySide6 desktop verification complete: $DistDir"
