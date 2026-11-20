# F1 Classic Launcher

F1 Classic Launcher is a native Windows desktop application built with PySide6. It targets **F1 25** and organizes the 2010 and 2012 seasons as separate mod configurations for that game; it does not launch the original F1 2010 or F1 2012 games.

## Importing and activating mods

Use **Import Package** to select a ZIP archive or folder. Existing `manifest.json` and Swofty `swofty.json` packages are recognized. For ordinary F1 25 game-folder mods, choose the intended season (2010 or 2012); nested game folders are passed to the official Simplified Importer, which converts supported game-file changes into Swofty addons. The launcher stores its package copies and configuration under the current user's application-data directory, while Swofty stores its addon data separately. It does not copy mod files directly into the game installation.

After import, activate a season from its details page. The launcher registers the F1 25 directory with Swofty, imports or enables that season's addons, sets their order, and runs Swofty's conflict/profile check. Deactivation disables the season addons. Before the first modded launch, use **Settings & Games** to run Swofty's one-time setup reset, verify or repair the game if Swofty requests it, and finish setup. Swofty modded sessions run offline without EA AntiCheat; vanilla launches use the normal game launch path. Simplified Importer downloads are checksum-verified and require network access the first time.

## In-game watermark

The watermark is a native Swofty addon using Swofty's experimental render-overlay API, not a desktop window or a custom game-injection implementation. The Windows build compiles and packages the addon alongside Swofty Core. It is enabled for the modded profile and is not part of the vanilla launch path. The watermark and mod application still require verification on a real F1 25 installation.

## Development

- Run the native desktop app with `python main.py` after installing `requirements.txt`.
- Run Python tests with `python -m unittest discover -s tests -v`.
- Build the Windows distribution with `build_windows/build_swofty_and_launcher.ps1` in the required Windows toolchain. The build produces `dist/F1ClassicLauncher_Full/F1ClassicLauncher.exe` and the Swofty runtime files beside it.
