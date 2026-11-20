import subprocess
import tempfile
import unittest
from pathlib import Path

from build_windows.prepare_swofty_checkout import prepare_checkout


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestSwoftyCheckout(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="swofty_checkout_test_")
        self.root = Path(self.temp.name)
        self.seed = self.root / "seed"
        self.seed.mkdir()
        git(self.seed, "init", "-b", "main")
        git(self.seed, "config", "user.name", "Test")
        git(self.seed, "config", "user.email", "test@example.invalid")
        (self.seed / "source.txt").write_text("first", encoding="utf-8")
        git(self.seed, "add", "source.txt")
        git(self.seed, "commit", "-m", "first")
        self.old_commit = git(self.seed, "rev-parse", "HEAD")
        (self.seed / "source.txt").write_text("pinned", encoding="utf-8")
        git(self.seed, "commit", "-am", "pinned")
        self.pinned_commit = git(self.seed, "rev-parse", "HEAD")

        self.remote = self.root / "remote.git"
        git(self.root, "clone", "--bare", str(self.seed), str(self.remote))
        subprocess.run(
            ["git", f"--git-dir={self.remote}", "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )
        self.remote_url = str(self.remote)

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_no_checkout_clone_checks_out_exact_pinned_commit(self):
        cache = self.root / "build_tmp" / "SwoftyLauncher"

        checked_out = prepare_checkout(cache, self.remote_url, self.pinned_commit)

        self.assertEqual(checked_out, self.pinned_commit)
        self.assertEqual(git(cache, "rev-parse", "HEAD"), self.pinned_commit)
        self.assertEqual(git(cache, "remote", "get-url", "origin"), self.remote_url)
        self.assertEqual((cache / "source.txt").read_text(encoding="utf-8"), "pinned")

    def test_clean_pinned_cache_is_idempotent(self):
        cache = self.root / "cache"
        self.assertEqual(
            prepare_checkout(cache, self.remote_url, self.pinned_commit),
            self.pinned_commit,
        )

        self.assertEqual(
            prepare_checkout(cache, self.remote_url, self.pinned_commit),
            self.pinned_commit,
        )
        self.assertEqual(git(cache, "status", "--porcelain"), "")

    def test_dirty_pinned_cache_is_preserved_and_rejected(self):
        cache = self.root / "cache"
        prepare_checkout(cache, self.remote_url, self.pinned_commit)
        (cache / "source.txt").write_text("user change", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "has local changes"):
            prepare_checkout(cache, self.remote_url, self.pinned_commit)

        self.assertEqual((cache / "source.txt").read_text(encoding="utf-8"), "user change")

    def test_clean_cache_at_wrong_revision_moves_to_pin(self):
        cache = self.root / "cache"
        git(self.root, "clone", str(self.remote), str(cache))
        git(cache, "checkout", "--detach", self.old_commit)

        checked_out = prepare_checkout(cache, self.remote_url, self.pinned_commit)

        self.assertEqual(checked_out, self.pinned_commit)
        self.assertEqual((cache / "source.txt").read_text(encoding="utf-8"), "pinned")
        self.assertEqual(git(cache, "status", "--porcelain"), "")

    def test_dirty_cache_at_wrong_revision_is_not_overwritten(self):
        cache = self.root / "cache"
        git(self.root, "clone", str(self.remote), str(cache))
        git(cache, "checkout", "--detach", self.old_commit)
        (cache / "source.txt").write_text("user change", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "has local changes"):
            prepare_checkout(cache, self.remote_url, self.pinned_commit)

        self.assertEqual(git(cache, "rev-parse", "HEAD"), self.old_commit)
        self.assertEqual((cache / "source.txt").read_text(encoding="utf-8"), "user change")

    def test_existing_cache_with_wrong_remote_is_rejected(self):
        cache = self.root / "cache"
        git(self.root, "clone", str(self.remote), str(cache))
        wrong_remote = self.root / "other.git"
        git(self.root, "clone", "--bare", str(self.seed), str(wrong_remote))

        with self.assertRaisesRegex(RuntimeError, "unexpected origin"):
            prepare_checkout(cache, str(wrong_remote), self.pinned_commit)

        self.assertEqual(git(cache, "rev-parse", "HEAD"), self.pinned_commit)


if __name__ == "__main__":
    unittest.main()
