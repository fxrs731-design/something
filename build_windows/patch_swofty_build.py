#!/usr/bin/env python3
"""Apply narrowly scoped Windows compatibility fixes to Swofty native/build graph."""

import argparse
from pathlib import Path


_SOURCE_PATH = (
    "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE))",
    "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE).replace(os.sep, '/'))",
)
_SDK_FLAG_GROUPS = (
    "loader_flags =",
    "core_flags =",
    "'src/core/hooks/guard.cpp':",
    "gamelog = [g.compile",
)


def _replace_once_or_patched(source: str, original: str, patched: str, label: str) -> str:
    original_count = source.count(original)
    if original_count == 1:
        return source.replace(original, patched)
    if original_count == 0 and source.count(patched) == 1:
        return source
    raise RuntimeError(f"Unsupported Swofty build_graph.py: expected exactly one {label} entry")


def _patch_sdk_flags(source: str) -> str:
    lines = source.splitlines()
    for group in _SDK_FLAG_GROUPS:
        matches = [line for line in lines if group in line]
        if len(matches) != 1:
            raise RuntimeError(
                f"Unsupported Swofty build_graph.py: expected exactly one {group!r} flag group"
            )
        line = matches[0]
        if "-I$sdk" not in line and "'-I', '$sdk'" not in line:
            raise RuntimeError(
                f"Unsupported Swofty build_graph.py: {group!r} no longer has the expected SDK include"
            )

    updated = source.replace("'-I$sdk'", "'-I', '$sdk'").replace(
        '"-I$sdk"', "'-I', '$sdk'"
    )
    if "-I$sdk" in updated:
        raise RuntimeError(
            "Unsupported Swofty build_graph.py: found an SDK include flag in an unrecognized form"
        )

    updated_lines = updated.splitlines()
    for group in _SDK_FLAG_GROUPS:
        line = next(line for line in updated_lines if group in line)
        if "'-I', '$sdk'" not in line:
            raise RuntimeError(
                f"Unsupported Swofty build_graph.py: {group!r} SDK include was not split"
            )
    return updated


def patch_build_graph(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    original_path, patched_path = _SOURCE_PATH
    updated = _replace_once_or_patched(
        source, original_path, patched_path, "source-path normalization"
    )
    updated = _patch_sdk_flags(updated)

    if updated != source:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_graph", type=Path)
    args = parser.parse_args()

    if patch_build_graph(args.build_graph):
        print(f"Applied Windows compatibility fixes to {args.build_graph}")
    else:
        print(f"Windows compatibility fixes already present in {args.build_graph}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
