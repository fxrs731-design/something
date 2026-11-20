$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
Write-Host "=== Building F1 Classic Launcher (Windows) ==="
python --version
python -m pip install --upgrade pip
python -m pip install pyinstaller
python -m unittest discover -s . -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { throw "Python tests failed." }
$ReleaseDir = Join-Path (Get-Location) "dist\F1ClassicLauncher_Windows"
$WorkDir = Join-Path (Get-Location) "build\pyinstaller"
if (Test-Path $ReleaseDir) { Remove-Item $ReleaseDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
pyinstaller --noconfirm --clean --onefile --name F1ClassicLauncher --distpath (Join-Path (Get-Location) "dist") --workpath $WorkDir --specpath (Join-Path (Get-Location) "build") --paths . --hidden-import core.backup_manager --hidden-import core.config_manager --hidden-import core.game_adapters --hidden-import core.game_detector --hidden-import core.logger --hidden-import core.manifest_parser --hidden-import core.mod_installer --hidden-import core.package_importer --hidden-import core.path_utils --hidden-import core.season_manager --hidden-import ui.app_server --add-data "frontend.html;." main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
$Exe = Join-Path (Get-Location) "dist\F1ClassicLauncher.exe"
if (!(Test-Path $Exe)) { throw "PyInstaller did not produce F1ClassicLauncher.exe." }
Copy-Item $Exe (Join-Path $ReleaseDir "F1ClassicLauncher.exe") -Force
Copy-Item "README.md" $ReleaseDir -Force
@("F1 Classic Launcher Windows portable build", "", "Run F1ClassicLauncher.exe. User data is stored under %APPDATA%\F1ClassicLauncher.", "", "This build is standalone. It does not bundle Swofty Core or native Swofty DLLs. Native Swofty addon execution has not been implemented or validated for F1 2010/2012.") | Set-Content (Join-Path $ReleaseDir "BUILD_NOTES.txt") -Encoding UTF8
Write-Host "Build complete: $ReleaseDir"
