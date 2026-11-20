# F1 Classic Launcher

F1 Classic Launcher is a native Windows desktop application built with PySide6. It targets **F1 25** and organizes the 2010 and 2012 seasons as separate mod configurations for that game; it does not launch the original F1 2010 or F1 2012 games.

## Importing and activating mods

Use **Import Package** for launcher season packages. Use **Swofty Profile Mod Manager** for ordinary F1 25 mods: select the 2010 or 2012 profile, then import a ZIP, 7z, RAR, folder, or `.swoftyaddon`. The official Simplified Importer recognizes nested F1 25 game-folder layouts, including supported `.erp` files and `mod.serps` metadata, and performs conversion in Swofty's own library. A standalone `.erp` has no game-folder destination, and `.serps` is metadata rather than a standalone mod. The launcher does not copy mod files directly into the game installation.

The profile manager stores per-profile enable state through Swofty's supported addon and Simplified Importer commands. Simplified-imported mods can also be reordered through its supported profile command; direct addons do not currently have a Swofty CLI reorder command. The manager can request Swofty's profile conflict check. This records configuration in Swofty; it does not prove that the game loaded the mods. Swofty documents two distinct EA App routes: `game launch` starts the EA App copy without a profile or offline play, so EA AntiCheat runs; its modded `run --apply` session installs the selected profile and offline-play component, and explicitly disables EA AntiCheat for that session. F1 Classic Launcher keeps vanilla EA App launching available and blocks Swofty's modded route. There is no documented path that both applies mods and keeps EA AntiCheat enabled. Simplified Importer downloads are checksum-verified and require network access the first time.

## Mod library and profiles

The **Mod Library** can inspect ZIP archives, ZIP-based `.swoftyaddon` packages, and folders before copying them into the launcher's separate library. The preview shows package metadata and file names. F1 25 metadata is reported as declared compatibility; recognizable game-folder structure without metadata is labeled unverified, and packages declaring another game are rejected. Categories are read from package metadata or inferred from names and paths, so an inferred category is not proof of game compatibility. Original source files are not changed.

The **Profiles** page keeps independent `f1classic_2010`, `f1classic_2012`, and custom library profiles. Mods can be added, removed, enabled, and disabled per local profile; custom profiles can be created, renamed, duplicated, and deleted. These local library memberships are stored separately from Swofty's profile data and do not modify game files. Status distinguishes Imported and Enabled from Applied and Verified; Applied and Verified remain No because the pinned Swofty integration has no documented modded F1 25 launch that retains EA AntiCheat. Conflict checks report missing profile dependencies, season mismatches, and duplicate package-relative paths as potential overlaps, not confirmed in-game conflicts.

## External game launch

The Dashboard's **External Game Launch** section can monitor F1 25 started through its normal EA App/Steam launch path. Enable monitoring, select a 2010 or 2012 F1 25 season or Swofty profile, then leave Simple Toolz open or minimized while starting the game separately. The selection is saved across restarts; monitoring is opt-in and stops when Simple Toolz closes. This detects whether `F1_25.exe` is running but does not apply the selected profile to an independently launched game. Mod status is shown as unverified while the game is running because no documented EA AntiCheat-compatible independent mod-loading method is available in the pinned Swofty integration.

## In-game watermark

The watermark addon uses Swofty's experimental render-overlay API; it is not a desktop window or a custom game-injection implementation. The Windows package includes the addon, but the launcher currently blocks Swofty's modded `run --apply` launch path because it runs without EA AntiCheat. The watermark has not been verified in game and will not appear on the vanilla EA AntiCheat launch path.

## Development

- Run the native desktop app without a persistent terminal using `wscript.exe Launch_F1_Classic_Launcher.vbs`; it opens the packaged executable if present or starts `main.py` with `pythonw.exe`.
- Run Python tests with `python -m unittest discover -s tests -v`.
- Build the Windows distribution with `build_windows/build_swofty_and_launcher.ps1` in the required Windows toolchain. The build produces `dist/F1ClassicLauncher_Full/F1ClassicLauncher.exe` and the Swofty runtime files beside it.
