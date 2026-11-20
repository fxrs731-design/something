import json
import os
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.logger import LauncherLogger
from core.mod_library import ModLibraryManager


class TestModLibraryManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="f1classic_mod_library_"))
        self.logger = LauncherLogger(self.tmp / "logs")
        self.library = ModLibraryManager(self.tmp / "mod-library", self.logger)

    def _write_archive(
        self,
        path: Path,
        *,
        name="2010 Livery Pack",
        game="F1 25",
        season=2010,
        dependencies=None,
        extra_files=None,
        game_version=None,
    ):
        metadata = {
            "name": name,
            "version": "1.2.0",
            "author": "Test Author",
            "description": "A test package.",
            "game": game,
            "season": season,
            "category": "Liveries",
            "dependencies": dependencies or [],
        }
        if game_version is not None:
            metadata["game_version"] = game_version
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(metadata))
            archive.writestr("asset_groups/cars/car.erp", b"livery-data")
            for filename, contents in (extra_files or {}).items():
                archive.writestr(filename, contents)
        return path

    def _write_folder(self, path: Path, *, name="Helmet Pack", game="F1 25", season=2012):
        path.mkdir()
        (path / "manifest.json").write_text(
            json.dumps({
                "name": name,
                "version": "2.0",
                "author": "Folder Author",
                "description": "Folder package.",
                "game": game,
                "season": season,
                "category": "Helmets",
            }),
            encoding="utf-8",
        )
        asset = path / "asset_groups" / "helmets" / "helmet.erp"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"helmet-data")
        return path

    def test_zip_import_inspects_metadata_and_preserves_original_archive(self):
        archive = self._write_archive(self.tmp / "2010-liveries.zip")
        original = archive.read_bytes()
        preview = self.library.inspect_package(archive)

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["name"], "2010 Livery Pack")
        self.assertEqual(preview["category"], "Liveries")
        self.assertEqual(preview["version"], "1.2.0")
        self.assertEqual(preview["author"], "Test Author")
        self.assertEqual(preview["compatibility"], "Declared F1 25")
        self.assertIn("asset_groups/cars/car.erp", preview["files"])

        result = self.library.import_package(archive)
        self.assertTrue(result["ok"], result.get("error"))
        saved = self.library.root / result["mod"]["source"]
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(archive.read_bytes(), original)

    def test_swoftyaddon_zip_package_is_accepted_by_the_local_library(self):
        package = self._write_archive(self.tmp / "f1classic-addon.swoftyaddon")
        result = self.library.import_package(package)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["mod"]["source_type"], "archive")
        self.assertEqual(Path(result["mod"]["source"]).suffix, ".swoftyaddon")

    def test_folder_import_copies_source_without_changing_original(self):
        folder = self._write_folder(self.tmp / "helmet-mod")
        original = (folder / "asset_groups" / "helmets" / "helmet.erp").read_bytes()
        result = self.library.import_package(folder)

        self.assertTrue(result["ok"], result.get("error"))
        saved_root = self.library.root / result["mod"]["source"]
        self.assertTrue((saved_root / "manifest.json").is_file())
        self.assertEqual((saved_root / "asset_groups" / "helmets" / "helmet.erp").read_bytes(), original)
        self.assertEqual((folder / "asset_groups" / "helmets" / "helmet.erp").read_bytes(), original)

    def test_unverified_package_is_not_reported_as_compatible(self):
        archive = self.tmp / "unknown.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("custom/assets/data.bin", b"data")
        preview = self.library.inspect_package(archive)
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["compatibility"], "Unverified")

    def test_duplicate_import_is_detected_by_content_fingerprint(self):
        archive = self._write_archive(self.tmp / "duplicate.zip")
        first = self.library.import_package(archive)
        second = self.library.import_package(archive)

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["mod_id"], first["mod"]["id"])
        self.assertEqual(len(self.library.list_mods()), 1)

    def test_non_f1_25_and_malformed_packages_are_rejected(self):
        wrong_game = self._write_archive(self.tmp / "f1-24.zip", game="F1 24")
        result = self.library.inspect_package(wrong_game)
        self.assertFalse(result["ok"])
        self.assertIn("not F1 25", result["error"])

        malformed = self.tmp / "malformed.zip"
        with zipfile.ZipFile(malformed, "w") as package:
            package.writestr("manifest.json", "{not-json")
            package.writestr("cars/car.erp", b"data")
        result = self.library.inspect_package(malformed)
        self.assertFalse(result["ok"])
        self.assertIn("Malformed manifest.json", result["error"])

    def test_incompatible_game_version_and_windows_device_paths_are_rejected(self):
        wrong_version = self._write_archive(
            self.tmp / "f1-24-version.zip",
            game_version="F1 24",
        )
        result = self.library.inspect_package(wrong_version)
        self.assertFalse(result["ok"])
        self.assertIn("targets F1 2024", result["error"])

        unsafe = self.tmp / "reserved-name.zip"
        with zipfile.ZipFile(unsafe, "w") as package:
            package.writestr("asset_groups/cars/CON.erp", b"unsafe")
        result = self.library.inspect_package(unsafe)
        self.assertFalse(result["ok"])
        self.assertIn("Unsafe package path", result["error"])

    def test_zip_path_traversal_is_rejected_during_inspection(self):
        archive = self.tmp / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("../outside.txt", b"not extracted")

        result = self.library.inspect_package(archive)

        self.assertFalse(result["ok"])
        self.assertIn("Unsafe package path", result["error"])
        self.assertFalse((self.tmp / "outside.txt").exists())

    def test_builtin_profiles_are_separate_and_existing_memberships_are_preserved(self):
        archive_2010 = self._write_archive(self.tmp / "2010.zip")
        archive_2012 = self._write_archive(self.tmp / "2012.zip", name="2012 Livery Pack", season=2012)
        mod_2010 = self.library.import_package(archive_2010)["mod"]
        mod_2012 = self.library.import_package(archive_2012)["mod"]
        self.library.add_mod_to_profile("f1classic_2010", mod_2010["id"])
        self.library.add_mod_to_profile("f1classic_2012", mod_2012["id"])

        reloaded = ModLibraryManager(self.tmp / "mod-library", self.logger)

        self.assertEqual(reloaded.get_profile_mods("f1classic_2010")[0]["id"], mod_2010["id"])
        self.assertEqual(reloaded.get_profile_mods("f1classic_2012")[0]["id"], mod_2012["id"])
        self.assertEqual(reloaded.get_profile("f1classic_2010")["season"], 2010)
        self.assertEqual(reloaded.get_profile("f1classic_2012")["season"], 2012)

    def test_custom_profile_lifecycle_duplicate_and_switch_persist(self):
        mod = self.library.import_package(self._write_archive(self.tmp / "custom.zip"))["mod"]
        custom = self.library.create_profile("My Custom Season")
        self.library.add_mod_to_profile(custom["id"], mod["id"])
        self.library.rename_profile(custom["id"], "Renamed Profile")
        duplicate = self.library.duplicate_profile(custom["id"], "Profile Copy")
        self.library.select_profile(duplicate["id"])

        reloaded = ModLibraryManager(self.tmp / "mod-library", self.logger)
        self.assertEqual(reloaded.selected_profile_id(), duplicate["id"])
        self.assertEqual(reloaded.get_profile(duplicate["id"])["name"], "Profile Copy")
        self.assertTrue(reloaded.get_profile_mods(duplicate["id"])[0]["enabled"])
        self.assertEqual(reloaded.get_profile(custom["id"])["name"], "Renamed Profile")

        reloaded.delete_profile(duplicate["id"])
        self.assertEqual(reloaded.selected_profile_id(), "f1classic_2010")
        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            reloaded.delete_profile("f1classic_2010")

    def test_mod_enabled_and_removal_are_profile_scoped_and_safe(self):
        mod = self.library.import_package(self._write_archive(self.tmp / "enabled.zip"))["mod"]
        profile = self.library.create_profile("Test profile")
        self.library.add_mod_to_profile(profile["id"], mod["id"], enabled=False)
        status = self.library.get_mod_status(mod["id"], profile["id"])
        self.assertEqual(status, {"imported": True, "enabled": False, "applied": False, "verified": False})

        self.library.set_mod_enabled(profile["id"], mod["id"], True)
        self.assertTrue(self.library.get_mod_status(mod["id"], profile["id"])["enabled"])
        self.assertFalse(self.library.get_mod_status(mod["id"], "f1classic_2010")["enabled"])

        blocked = self.library.remove_mod(mod["id"])
        self.assertFalse(blocked["ok"])
        self.assertIn("Remove this mod from these profiles", blocked["error"])
        self.library.remove_mod_from_profile(profile["id"], mod["id"])
        removed = self.library.remove_mod(mod["id"])
        self.assertTrue(removed["ok"])
        self.assertEqual(self.library.list_mods(), [])

    def test_conflicts_report_missing_dependencies_season_mismatch_and_overlapping_paths(self):
        first = self._write_archive(
            self.tmp / "first.zip",
            name="2010 Performance Pack",
            season=2010,
            dependencies=["missing.required.mod"],
            extra_files={"shared/resource.erp": b"first"},
        )
        second = self._write_archive(
            self.tmp / "second.zip",
            name="2012 Driver Suit Pack",
            season=2012,
            extra_files={"shared/resource.erp": b"second"},
        )
        first_mod = self.library.import_package(first)["mod"]
        second_mod = self.library.import_package(second)["mod"]
        self.library.add_mod_to_profile("f1classic_2010", first_mod["id"])
        self.library.add_mod_to_profile("f1classic_2010", second_mod["id"])

        conflicts = self.library.detect_conflicts("f1classic_2010")

        kinds = {conflict["kind"] for conflict in conflicts}
        self.assertIn("missing-dependency", kinds)
        self.assertIn("season-mismatch", kinds)
        self.assertIn("possible-file-overlap", kinds)
        overlaps = [conflict for conflict in conflicts if conflict["kind"] == "possible-file-overlap"]
        self.assertIn("shared/resource.erp", {conflict["path"] for conflict in overlaps})
        overlap = overlaps[0]
        self.assertIn("not proof", overlap["message"])

    def test_interrupted_removal_restores_indexed_package_and_stale_staging_is_cleaned(self):
        archive = self._write_archive(self.tmp / "recovery.zip")
        mod = self.library.import_package(archive)["mod"]
        package_dir = self.library.packages_dir / mod["id"]
        removed_dir = self.library.packages_dir / f".removed-{mod['id']}-999999999-{uuid.uuid4().hex}"
        staging_dir = self.library.packages_dir / f".staging-999999999-{uuid.uuid4().hex}"
        staging_dir.mkdir()
        os.replace(package_dir, removed_dir)

        recovered = ModLibraryManager(self.tmp / "mod-library", self.logger)

        self.assertTrue(package_dir.is_dir())
        self.assertFalse(removed_dir.exists())
        self.assertFalse(staging_dir.exists())
        self.assertEqual(recovered.list_mods()[0]["id"], mod["id"])

    def test_interrupted_import_with_package_directory_is_reindexed(self):
        archive = self._write_archive(self.tmp / "interrupted.zip")
        mod = self.library.import_package(archive)["mod"]
        del self.library._document["mods"][mod["id"]]
        self.library._save()

        recovered = ModLibraryManager(self.tmp / "mod-library", self.logger)

        self.assertEqual(recovered.list_mods()[0]["id"], mod["id"])
        self.assertEqual(recovered.list_mods()[0]["fingerprint"], mod["fingerprint"])

    def test_import_discards_copy_if_folder_changes_during_copy(self):
        folder = self._write_folder(self.tmp / "changing-folder")
        original_copy = ModLibraryManager._copy_folder_payload

        def mutate_copy(source, destination, file_paths, file_count, progress_cb=None, total_size=0):
            original_copy(source, destination, file_paths, file_count, progress_cb, total_size)
            (destination / "asset_groups" / "helmets" / "helmet.erp").write_bytes(b"changed")

        with patch.object(ModLibraryManager, "_copy_folder_payload", side_effect=mutate_copy):
            result = self.library.import_package(folder)

        self.assertFalse(result["ok"])
        self.assertIn("contents changed", result["error"])
        self.assertEqual(self.library.list_mods(), [])
        self.assertEqual(list(self.library.packages_dir.iterdir()), [])
        self.assertEqual((folder / "asset_groups" / "helmets" / "helmet.erp").read_bytes(), b"helmet-data")

    def test_failed_import_rolls_back_library_record_and_staging_directory(self):
        archive = self._write_archive(self.tmp / "failure.zip")
        original_replace = os.replace

        def fail_library_write(source, destination):
            if Path(destination) == self.library.data_file:
                raise OSError("simulated metadata write failure")
            return original_replace(source, destination)

        with patch("core.mod_library.os.replace", side_effect=fail_library_write):
            result = self.library.import_package(archive)

        self.assertFalse(result["ok"])
        self.assertIn("simulated metadata write failure", result["error"])
        self.assertEqual(self.library.list_mods(), [])
        self.assertEqual(list(self.library.packages_dir.iterdir()), [])
        self.assertTrue(archive.is_file())

    def test_profile_save_failure_restores_in_memory_selection(self):
        original = self.library.selected_profile_id()
        with patch("core.mod_library.os.replace", side_effect=OSError("simulated save failure")):
            with self.assertRaisesRegex(OSError, "simulated save failure"):
                self.library.select_profile("f1classic_2012")
        self.assertEqual(self.library.selected_profile_id(), original)

    def test_existing_profile_contents_are_loaded_without_overwriting_them(self):
        library_dir = self.tmp / "preexisting"
        library_dir.mkdir()
        data_file = library_dir / "library.json"
        original = {
            "schema_version": 1,
            "mods": {},
            "profiles": {
                "f1classic_2010": {
                    "id": "f1classic_2010",
                    "name": "2010 F1 25",
                    "kind": "season",
                    "season": 2010,
                    "mods": {"existing.mod": True},
                },
                "f1classic_2012": {
                    "id": "f1classic_2012",
                    "name": "2012 F1 25",
                    "kind": "season",
                    "season": 2012,
                    "mods": {"another.mod": False},
                },
            },
            "selected_profile_id": "f1classic_2012",
        }
        data_file.write_text(json.dumps(original), encoding="utf-8")

        manager = ModLibraryManager(library_dir, self.logger)

        self.assertEqual(manager.get_profile("f1classic_2010")["mods"], {"existing.mod": True})
        self.assertEqual(manager.get_profile("f1classic_2012")["mods"], {"another.mod": False})
        self.assertEqual(manager.selected_profile_id(), "f1classic_2012")
        self.assertEqual(json.loads(data_file.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
