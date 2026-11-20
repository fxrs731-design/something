import argparse
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return result.stdout.strip()


def prepare_checkout(repo: Path, remote: str, commit: str) -> str:
    repo = Path(repo)
    new_clone = not repo.exists()
    if new_clone:
        repo.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--no-checkout", "--depth", "1", remote, str(repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"Could not clone SwoftyLauncher from {remote}: {detail}")
    elif not repo.is_dir():
        raise RuntimeError(f"SwoftyLauncher cache path is not a directory: {repo}")

    git_dir = _git(repo, "rev-parse", "--git-dir")
    if not git_dir:
        raise RuntimeError(f"SwoftyLauncher cache is not a Git checkout: {repo}")

    cached_remote = _git(repo, "remote", "get-url", "origin")
    if cached_remote != remote:
        raise RuntimeError(
            f"SwoftyLauncher cache has unexpected origin '{cached_remote}'; expected '{remote}'."
        )

    head_result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head_result.returncode == 0:
        cached_head = head_result.stdout.strip()
        changes = _git(repo, "status", "--porcelain", "--untracked-files=all")
        if new_clone and any(line[:2] != "D " for line in changes.splitlines()):
            raise RuntimeError(
                "Fresh SwoftyLauncher clone has unexpected changes before checkout; "
                "preserve or inspect that cache before rebuilding."
            )
        if not new_clone and changes:
            raise RuntimeError(
                f"SwoftyLauncher checkout at {cached_head} has local changes. "
                "Preserve or clear that cache before rebuilding."
            )
    else:
        # A clone made with --no-checkout has no HEAD and reports tracked paths as
        # deleted. Accept only this empty-worktree state; reject staged or untracked data.
        changes = _git(repo, "status", "--porcelain", "--untracked-files=all")
        if any(line[:2] not in (" D",) for line in changes.splitlines()):
            raise RuntimeError(
                "SwoftyLauncher checkout has no HEAD but contains local or staged changes; "
                "preserve or inspect that cache before rebuilding."
            )
        cached_head = ""

    if new_clone or cached_head != commit:
        _git(repo, "fetch", "--depth", "1", "origin", commit)
        _git(repo, "checkout", "--detach", commit)

    checked_out = _git(repo, "rev-parse", "HEAD")
    if checked_out != commit:
        raise RuntimeError(
            f"SwoftyLauncher checkout mismatch: expected {commit}, found '{checked_out}'."
        )
    return checked_out


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the pinned SwoftyLauncher checkout.")
    parser.add_argument("repo", type=Path)
    parser.add_argument("remote")
    parser.add_argument("commit")
    args = parser.parse_args()
    try:
        checked_out = prepare_checkout(args.repo, args.remote, args.commit)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Verified SwoftyLauncher commit: {checked_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
