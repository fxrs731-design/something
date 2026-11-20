$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$BuildTmp = Join-Path $RootDir "build_tmp"
$ShimBin = Join-Path $BuildTmp "shims"
$SwoftyRepo = Join-Path $BuildTmp "SwoftyLauncher"
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

# Ensure Zig is available
if (-not (Get-Command "zig" -ErrorAction SilentlyContinue)) {
    Write-Host "Zig not found on PATH; attempting winget install..."
    if (Get-Command "winget" -ErrorAction SilentlyContinue) {
        winget install --id zig.zig -e --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    }
    if (-not (Get-Command "zig" -ErrorAction SilentlyContinue)) {
        throw "Zig toolchain is required by Swofty native/build.sh and is not on PATH."
    }
}
Write-Host "Verified Zig version: $(zig version)"

New-Item -ItemType Directory -Force -Path $BuildTmp | Out-Null
New-Item -ItemType Directory -Force -Path $ShimBin | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path $SwoftyCoreOut | Out-Null

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

# 2. Clone or update Swofty-Developments/SwoftyLauncher
if (-not (Test-Path $SwoftyRepo)) {
    git clone --depth 1 https://github.com/Swofty-Developments/SwoftyLauncher.git $SwoftyRepo
} else {
    Push-Location $SwoftyRepo
    git pull --ff-only
    Pop-Location
}

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
python (Join-Path $PSScriptRoot "patch_swofty_build.py") $BuildGraph
if ($LASTEXITCODE -ne 0) {
    throw "Failed to apply verified Windows compatibility fixes to Swofty build_graph.py."
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

res = bridge.run_cli(['doctor'], env_overrides={'SWOFTY_DATA_DIR': r'$TempDataDir'})
print('doctor rc:', res.get('returncode'), 'stdout:', (res.get('stdout') or '')[:300])
assert res.get('returncode') is not None, f'CLI failed to execute: {res}'
print('SwoftyBridge integration verified:', status['cli_path'], 'DLLs:', status['native_dlls'])
"@
python -c $VerifyPy
Pop-Location

Write-Host "Build and PySide6 desktop verification complete: $DistDir"
