# F1 Classic Launcher

A modular, standalone Formula One season and mod manager with dynamic manifest parsing, game compatibility validation, per-season file backups, and one-click season switching.

## How to Run
- **Windows:** Double-click `Launch_F1_Classic_Launcher.bat` (requires Python 3.8+).
- **Linux:** Run `python3 main.py`.

## How to Import a Season Package
1. Open the **Import Package** tab in the launcher.
2. Drag and drop a season `.zip` file (such as `examples/F1_2018_Example_Season.zip`) or select a folder containing `manifest.json`.
3. The launcher validates `manifest.json`, checks that declared artwork and modification files exist, prevents duplicate season IDs (offering an explicit update option if already present), and registers the season in **Season Library**.

## Season Package Format (`manifest.json`)
```json
{
  "schema_version": 1,
  "id": "custom.f1-2018-season",
  "name": "F1 2018 Season",
  "version": "1.0.0",
  "author": "Mod Author",
  "description": "Complete Formula One 2018 season",
  "game": "F1 2018",
  "season": 2018,
  "artwork": "cover.png",
  "teams": ["Ferrari", "Mercedes", "Red Bull Racing"],
  "drivers": ["Sebastian Vettel", "Lewis Hamilton"],
  "operations": [
    { "type": "copy_dir", "source": "mod_files", "target": "." }
  ]
}
```
Supported operation types: `copy_file`, `replace_file`, `copy_dir`, `extract_zip`, `mkdir`, `bydanix_merge`.

## Running Automated Tests
```bash
python3 -m unittest discover -s tests -v
```
