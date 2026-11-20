import tempfile
import unittest
from pathlib import Path

from build_windows.patch_swofty_build import patch_build_graph


class TestSwoftyBuildPatch(unittest.TestCase):
    def test_patches_all_sdk_flag_groups_and_is_idempotent(self):
        source = "\n".join(
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

        with tempfile.TemporaryDirectory() as temp_dir:
            build_graph = Path(temp_dir) / "build_graph.py"
            build_graph.write_text(source, encoding="utf-8")

            self.assertTrue(patch_build_graph(build_graph))
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
            self.assertFalse(patch_build_graph(build_graph))

    def test_rejects_upstream_without_required_sdk_flag_group(self):
        source = "\n".join(
            (
                "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE))",
                "loader_flags = warn + ['-I$sdk']",
                "core_flags = ['-Isrc', '-I$sdk']",
                "'src/core/hooks/guard.cpp': ['-I$sdk'],",
                "gamelog = [g.compile('cxx', ['-Isrc', '-I$sdk'])]",
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            build_graph = Path(temp_dir) / "build_graph.py"
            changed = source.replace("loader_flags = warn + ['-I$sdk']", "loader_flags = warn")
            build_graph.write_text(changed, encoding="utf-8")

            with self.assertRaises(RuntimeError):
                patch_build_graph(build_graph)


if __name__ == "__main__":
    unittest.main()
