import tempfile
import unittest
from pathlib import Path

from build_windows.patch_swofty_build import patch_swofty_sources


class TestSwoftyBuildPatch(unittest.TestCase):
    def test_patches_all_sdk_flag_groups_and_is_idempotent(self):
        build_graph_source = "\n".join(
            (
                "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE))",
                "loader_flags = warn + ['-I$sdk', '-Iinclude', '$strip']",
                "loader = [g.compile('cc', 'src/loader/version.c', loader_flags)]",
                "core_flags = warn + a.define + ['-Isrc', '-I$sdk'] + game",
                "'src/core/hooks/guard.cpp': ['-I$sdk'],",
                "gamelog = [g.compile('cxx', 'src/gamelog/gamelog.cpp', ['-Isrc', '-I$sdk'])]",
                "other_flags = ['-I$sdk']",
            )
        )
        init_check_source = "\n".join(
            (
                "path = os.path.relpath(os.path.normpath(os.path.join(NATIVE, m.group(1))), NATIVE)",
                "name = os.path.relpath(report, obj_root)",
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            build_graph = Path(temp_dir) / "build_graph.py"
            init_check = Path(temp_dir) / "init_check.py"
            build_graph.write_text(build_graph_source, encoding="utf-8")
            init_check.write_text(init_check_source, encoding="utf-8")

            self.assertTrue(patch_swofty_sources(build_graph, init_check))
            patched = build_graph.read_text(encoding="utf-8")
            self.assertIn(
                "os.path.relpath(os.path.join(dirpath, name), NATIVE).replace(os.sep, '/')",
                patched,
            )
            self.assertIn("loader_flags = warn + ['-I', '$sdk', '-Iinclude', '$strip']", patched)
            self.assertIn(
                "loader = [g.compile('cc', 'src/loader/version.c', loader_flags)]",
                patched,
            )
            self.assertIn("core_flags = warn + a.define + ['-Isrc', '-I', '$sdk'] + game", patched)
            self.assertIn("'src/core/hooks/guard.cpp': ['-I', '$sdk']", patched)
            self.assertIn(
                "gamelog = [g.compile('cxx', 'src/gamelog/gamelog.cpp', ['-Isrc', '-I', '$sdk'])]",
                patched,
            )
            self.assertIn("other_flags = ['-I', '$sdk']", patched)
            self.assertNotIn("-I$sdk", patched)
            patched_init_check = init_check.read_text(encoding="utf-8")
            self.assertIn(
                "os.path.relpath(os.path.normpath(os.path.join(NATIVE, m.group(1))), NATIVE)"
                ".replace(os.sep, '/')",
                patched_init_check,
            )
            self.assertIn(
                "os.path.relpath(report, obj_root).replace(os.sep, '/')",
                patched_init_check,
            )
            self.assertFalse(patch_swofty_sources(build_graph, init_check))

    def test_rejects_upstream_without_required_sdk_flag_group(self):
        build_graph_source = "\n".join(
            (
                "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE))",
                "loader_flags = warn + ['-I$sdk']",
                "core_flags = ['-Isrc', '-I$sdk']",
                "'src/core/hooks/guard.cpp': ['-I$sdk'],",
                "gamelog = [g.compile('cxx', ['-Isrc', '-I$sdk'])]",
            )
        )
        init_check_source = "\n".join(
            (
                "path = os.path.relpath(os.path.normpath(os.path.join(NATIVE, m.group(1))), NATIVE)",
                "name = os.path.relpath(report, obj_root)",
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            build_graph = Path(temp_dir) / "build_graph.py"
            init_check = Path(temp_dir) / "init_check.py"
            changed = build_graph_source.replace("loader_flags = warn + ['-I$sdk']", "loader_flags = warn")
            build_graph.write_text(changed, encoding="utf-8")
            init_check.write_text(init_check_source, encoding="utf-8")

            with self.assertRaises(RuntimeError):
                patch_swofty_sources(build_graph, init_check)

    def test_rejects_changed_init_check_paths_without_partial_patch(self):
        build_graph_source = "\n".join(
            (
                "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE))",
                "loader_flags = ['-I$sdk']",
                "core_flags = ['-I$sdk']",
                "'src/core/hooks/guard.cpp': ['-I$sdk'],",
                "gamelog = [g.compile('cxx', ['-I$sdk'])]",
            )
        )
        init_check_source = "\n".join(
            (
                "path = os.path.relpath(os.path.normpath(os.path.join(NATIVE, m.group(1))), NATIVE)",
                "name = os.path.relpath(report, obj_root)",
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            build_graph = Path(temp_dir) / "build_graph.py"
            init_check = Path(temp_dir) / "init_check.py"
            changed_init_check = init_check_source.replace(
                "name = os.path.relpath(report, obj_root)",
                "name = os.path.relpath(report, new_obj_root)",
            )
            build_graph.write_text(build_graph_source, encoding="utf-8")
            init_check.write_text(changed_init_check, encoding="utf-8")

            with self.assertRaisesRegex(
                RuntimeError, "init_check.py: expected exactly one object report source path"
            ):
                patch_swofty_sources(build_graph, init_check)
            self.assertEqual(build_graph.read_text(encoding="utf-8"), build_graph_source)
            self.assertEqual(init_check.read_text(encoding="utf-8"), changed_init_check)


if __name__ == "__main__":
    unittest.main()
