# Simple Toolz by F4XRS — Next AI Handoff

## Project overview

- Repository: https://github.com/fxrs731-design/something
- Working branch: `fxrs731-design-windows-build-pipeline`
- HEAD: `c60a64e22ee76e519fbdfa43b62b562f356e604a` (`Fix pinned Swofty checkout cache handling`)
- Product: native PySide6 Windows desktop launcher for **F1 25 only**. The 2010 and 2012 seasons are profiles/mods for F1 25, not separate games.
- Scope protection: this handoff describes uncommitted work. Do not reset, revert, clean, stash, overwrite, commit, push, merge, or modify `main` or PR #1 without explicit authorization. Do not change the pinned Swofty commit.

## Git state and protected work

At handoff creation, the worktree has **16 modified tracked files and 4 untracked files** (the three pre-existing untracked files plus this document). There are no staged changes, tracked deletions, or moves. All pre-existing changes remain uncommitted.

### Modified tracked files

| File | Purpose of current changes |
|---|---|
| `Launch_F1_Classic_Launcher.bat` | Starts the VBS launcher without opening a persistent console. |
| `README.md` | Documents the native app, profile/import behavior, external monitoring, and AntiCheat limitation. |
| `core/backup_manager.py` | Adds backup-start logging; retains legacy direct-copy backup/restore support. |
| `core/config_manager.py` | Adds persistent profile/monitor settings, atomic JSON replacement, and explicit invalid-settings errors. |
| `core/game_adapters.py` | Restricts executable selection to the supported F1 25 executable in the configured installation. |
| `core/game_detector.py` | Detects/validates F1 25 and the adjacent EA AntiCheat service-launcher path; blocks unsupported modded launching and custom launch arguments. |
| `core/logger.py` | Keeps persistent timestamped logs, avoids propagation, closes replaced handlers, and adds exception logging. |
| `core/mod_installer.py` | Retains the legacy season-package flow and adds separate `f1classic_2010` / `f1classic_2012` Swofty profile management and import/update logic. |
| `core/package_importer.py` | Adds progress reporting for extraction, copying, validation, and package storage; reports failures explicitly. |
| `core/season_manager.py` | Reuses scanned entries and reports package removal failures rather than silently ignoring them. |
| `core/swofty_bridge.py` | Runs Swofty commands without a console, handles importer/addon/profile operations, and refuses the AntiCheat-incompatible modded launch path. |
| `main.py` | Creates the native desktop application context, adds the F1 25 process monitor, and shows a startup error if settings cannot be loaded. |
| `tests/test_launcher.py` | Covers season-profile separation, imports, content updates, external process monitoring, settings persistence, and worker behavior. |
| `tests/test_swofty_bridge_and_conflicts.py` | Covers hidden subprocess flags, Swofty profile/addon handling, updates, and launch guards. |
| `tests/test_swofty_compat.py` | Covers F1 25 / EA AntiCheat service-launcher path validation. |
| `ui/desktop_app.py` | Uses a single background operation worker, progress/error feedback, cached season scans/cards, external-launch monitoring, and the Swofty profile manager. |

### Pre-existing untracked files

- `Launch_F1_Classic_Launcher.vbs` — opens the packaged launcher when found, otherwise starts the Python entry point using `pythonw.exe`.
- `core/game_process_monitor.py` — enumerates Windows processes and recognizes `F1_25.exe` without spawning a subprocess.
- `core/process_utils.py` — supplies Windows no-console subprocess options.

`NEXT_AI_HANDOFF.md` is the only file added by the audit/handoff task.

## Architecture

- `main.py` starts a PySide6 `QApplication` and `F1ClassicLauncherWindow`; there is no web server, browser, localhost UI, or WebView.
- `core/config_manager.py` stores settings, imported season packages, backups, logs, and Swofty user data under the user's app-data directory.
- `core/season_manager.py` scans launcher season packages; `core/package_importer.py` validates and stores package imports.
- `core/mod_installer.py` handles legacy season-package activation and the newer Swofty profile manager. `core/swofty_bridge.py` invokes the bundled Swofty CLI and Simplified Importer.
- `ui/desktop_app.py` sends long scans, imports, conversions, profile changes, setup actions, activation/deactivation, and launch requests to one `QThread` worker at a time. UI updates return through Qt signals. While work is active, the window disables controls and refuses to close until the operation finishes.
- `core/logger.py` writes timestamped diagnostics to the configured app-data log directory. Swofty subprocess stdout/stderr and exit results are captured for results/errors.
- The external-launch monitor is opt-in and uses a 3-second `QTimer`; it tracks `F1_25.exe` start/stop while the launcher remains open or minimized. It does not launch the game or apply mods.

## Implemented, tested, and unverified

### Confirmed by automated execution in this audit

- `python -m unittest discover -s tests -v`: **50 tests passed**.
- Python syntax compilation: **28 Python files passed** without writing bytecode.
- PowerShell AST syntax parse of `build_windows/build_swofty_and_launcher.ps1`: passed.
- `git diff --check`: passed.
- Existing tests exercise the Qt window offscreen, operation exclusivity/error reporting, settings persistence, F1 25 process start/stop detection, and the no-web-UI architecture.

### Confirmed by GitHub Actions on the committed baseline

Windows workflow run [44804560047](https://github.com/fxrs731-design/something/actions/runs/44804560047) completed successfully on this branch at the same HEAD commit listed above. Its `build-windows` job passed, including Swofty compilation, native DLL checks, PySide6 packaging, and artifact upload. Artifact: `F1ClassicLauncher_Windows_Full`, 290,155,967 bytes.

**That run built commit `c60a64e...`, not the current uncommitted worktree changes. The current modified source has not had a full Windows build.** No real F1 25 installation or in-game mod behavior was tested.

### Implemented but not verified on a real F1 25 installation

- F1 25 installation validation and Swofty's official EA App launch command.
- Importing supported F1 25 mods through Simplified Importer; installing `.swoftyaddon` packages; profile enable state and Simplified-imported mod ordering.
- Conflict checks, backup/recovery paths, and the experimental in-game watermark.
- External detection of a game started independently.

### Partially implemented or known risks

- **Season-profile split:** the new profile manager uses `f1classic_2010` and `f1classic_2012`. The older Season Library activation path still uses Swofty's shared `default` profile. The two paths are not yet unified; test their interaction before claiming that every imported season remains isolated.
- **Mod status:** imported/stored, enabled in a Swofty profile, applied to game files, and verified in-game are different states. A successful import, enable command, conflict check, or `active_seasons` setting is not proof of game loading. The external monitor reports a running game separately and labels mod state unverified when appropriate.
- The manager does not expose a Swofty CLI reorder command for direct addons; only Simplified-imported mods can be reordered.
- Simplified Importer is downloaded from its **latest** GitHub release when absent. Asset checksums are verified, but the importer version is not pinned.
- The process-operation guard is per window, not a cross-process mutex. The launch button has no explicit already-running/pending-game guard once the Swofty command returns.
- Closing is prevented while a worker is running; long Swofty operations may take several minutes, and there is no cancellation workflow.
- Auto-detection checks common Steam/EA locations on drives C: through F:. Custom/other locations require manual configuration.
- `Launch_F1_Classic_Launcher.vbs` checks for the executable at the repository root and `dist\F1ClassicLauncher.exe`, but the complete build output is under `dist\F1ClassicLauncher_Full`. The built portable folder is the supported artifact; verify/repair the source-tree VBS path before relying on it to launch that folder.

## F1 25 launching and AntiCheat blocker

The launcher does not start `F1_25.exe` directly and does not disable, bypass, or tamper with EA AntiCheat. `GameDetector` validates the F1 25 game directory and rejects custom launch arguments. Vanilla launch goes through Swofty's `game launch --game ...` command, which delegates to the installed EA App path.

The pin in `build_windows/build_swofty_and_launcher.ps1` is **`873fe1b4dec0bdbacea64b90218d4ff765f28116`**. The exact upstream [`game.rs`](https://github.com/Swofty-Developments/SwoftyLauncher/blob/873fe1b4dec0bdbacea64b90218d4ff765f28116/apps/cli/src/commands/game.rs) describes `game launch` as starting the EA App copy with “no profile, no offline play, EA AntiCheat runs.” That is the vanilla route used by this integration. The modded `run` route is different: upstream [`launch.rs`](https://github.com/Swofty-Developments/SwoftyLauncher/blob/873fe1b4dec0bdbacea64b90218d4ff765f28116/apps/cli/src/commands/run/launch.rs) gates modded sessions on offline-play consent and explicitly says they run without EA AntiCheat. On an EA App installation it still launches via the EA App, but upstream's [`launching documentation`](https://github.com/Swofty-Developments/SwoftyLauncher/blob/873fe1b4dec0bdbacea64b90218d4ff765f28116/docs/reference/launch.md) says the modded flow stages offline play and the profile; only a vanilla profile leaves EA's AntiCheat launcher in place. The [`offline-play safety documentation`](https://github.com/Swofty-Developments/SwoftyLauncher/blob/873fe1b4dec0bdbacea64b90218d4ff765f28116/docs/reference/safety.md) states that loaders/addons require running without EA AntiCheat and that Swofty disables it for modded sessions.

`swofty apply` is a separate profile-install operation: upstream [`apply.rs`](https://github.com/Swofty-Developments/SwoftyLauncher/blob/873fe1b4dec0bdbacea64b90218d4ff765f28116/apps/cli/src/commands/build/apply.rs) installs the profile and tells the user to run `swofty restore`; it does not document an EA AntiCheat-compatible modded launch. Do not chain `apply` with `game launch` as a workaround: upstream does not claim that combination is supported, and its normal EA App launch deliberately omits the profile and offline-play staging.

The pinned Swofty `run` route does have journaled restoration: its wrapper holds a session lock and rolls back on normal exit, crash, stop, or error; unfinished journals are recovered by the next run or launcher start, and `swofty restore` is the documented manual recovery command. See upstream [`Backups and recovery`](https://github.com/Swofty-Developments/SwoftyLauncher/blob/873fe1b4dec0bdbacea64b90218d4ff765f28116/docs/reference/restore.md). This current launcher intentionally does not start that modded session. Its legacy direct-copy backup manager is a separate path and should be tested independently.

Therefore the blocker is a **missing documented Swofty path that both applies the profile and keeps EA AntiCheat enabled**. Swofty does support modded launches through its own offline-play route, including for EA App-installed copies, but that route expressly disables EA AntiCheat and is outside this launcher's permitted behavior. The launcher blocks `run --apply` rather than claiming compatibility. Do not add launch arguments, direct executable launches, injection, or security workarounds. Actual EA App/AntiCheat behavior still needs controlled real-install verification.

## Swofty and watermark details

- Swofty itself is checked out at the fixed commit above. `build_windows/prepare_swofty_checkout.py` verifies origin and SHA, rejects dirty existing caches, and handles a fresh `--no-checkout` clone.
- The build applies narrow native-build compatibility patches to the pinned source, compiles the CLI and native Windows loader DLLs, builds the watermark DLL, then packages both with the PySide6 executable.
- The watermark is a native addon using Swofty's experimental overlay API. It has not been observed in game. The legacy season activation/build integration targets the `default` profile; the new named profile manager does not independently establish the watermark in its 2010/2012 profiles.
- Do not equate packaging or Swofty addon registration with in-game watermark rendering.

## Windows build readiness

The workflow is `.github/workflows/build-windows.yml`. It runs on pushes to `main` and supports `workflow_dispatch`; for this development branch, dispatch it manually and select `fxrs731-design-windows-build-pipeline`. A push to this branch alone does not trigger it.

The Windows runner needs Python 3.11, Git, Bash, Rust 1.95 or newer, Zig 0.15.2, Ninja, PowerShell, PySide6, and PyInstaller. The script can install Ninja with pip, but the remaining native toolchain and network access to the pinned Swofty source are required. The full workflow validates the CLI, native DLLs, Qt startup, tests, portable output, and artifact upload.

**Readiness:** the baseline commit has a successful full Windows workflow run and uploaded artifact. The current uncommitted changes are not yet validated by that workflow, so do not call the current tree release-ready. The prior successful build also does not establish AntiCheat-compatible mod loading or in-game functionality.

## Recommended development roadmap

1. Establish whether upstream offers a legitimate, documented AntiCheat-compatible F1 25 mod-application path; if not, keep mod application blocked and state the limitation.
2. Verify compatible F1 25 mod imports and activation on a real installation, keeping the 2010 and 2012 profiles isolated.
3. Verify backup, failure recovery, and restoration behavior on a disposable F1 25 installation.
4. Complete and test legitimate F1 25 launching through the EA App / EA AntiCheat flow, including duplicate-request handling.
5. Verify the watermark only through a supported Swofty path and in-game test.
6. Later redesign the UI with original Simple Toolz by F4XRS branding; do not copy Swofty/Simplified branding.
7. Build and verify the full Windows package from the current development branch.
8. Perform final Windows performance, shutdown, permissions, path-with-spaces, antivirus, and stability testing.

## Instructions for the replacement agent

1. Start by confirming the branch, HEAD, `git status --short --untracked-files=all`, staged diff, and full `git diff`; inspect all pre-existing untracked files before editing.
2. Treat every existing modification and untracked file as protected. Never reset, revert, clean, stash, or overwrite it. Make only narrowly scoped changes and integrate with current implementations.
3. Keep F1 25 as the only supported game; 2010 and 2012 are F1 25 profiles. Do not change the Swofty pin, commit, push, merge, modify `main`, or modify PR #1 without explicit authorization.
4. Re-run the tests and appropriate build validations after edits. Report what was tested, what was not, and distinguish registration/configuration from actual in-game application.
