#!/usr/bin/env python3
"""Apply narrowly scoped Windows compatibility fixes to Swofty native build tools."""

import argparse
from pathlib import Path


_SOURCE_PATH = (
    "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE))",
    "found.append(os.path.relpath(os.path.join(dirpath, name), NATIVE).replace(os.sep, '/'))",
)
_INIT_CHECK_PATHS = (
    (
        "path = os.path.relpath(os.path.normpath(os.path.join(NATIVE, m.group(1))), NATIVE)",
        "path = os.path.relpath(os.path.normpath(os.path.join(NATIVE, m.group(1))), NATIVE).replace(os.sep, '/')",
        "diagnostic source path",
    ),
    (
        "name = os.path.relpath(report, obj_root)",
        "name = os.path.relpath(report, obj_root).replace(os.sep, '/')",
        "object report source path",
    ),
)
_SDK_FLAG_GROUPS = (
    "loader_flags =",
    "core_flags =",
    "'src/core/hooks/guard.cpp':",
    "gamelog = [g.compile",
)


def _replace_once_or_patched(
    source: str, original: str, patched: str, label: str, source_name: str
) -> str:
    patched_count = source.count(patched)
    original_count = source.replace(patched, "").count(original)
    if patched_count == 1 and original_count == 0:
        return source
    if patched_count == 0 and original_count == 1:
        return source.replace(original, patched)
    raise RuntimeError(
        f"Unsupported Swofty {source_name}: expected exactly one {label} entry"
    )


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


def patch_swofty_sources(build_graph_path: Path, init_check_path: Path) -> bool:
    build_graph_source = build_graph_path.read_text(encoding="utf-8")
    init_check_source = init_check_path.read_text(encoding="utf-8")

    original_path, patched_path = _SOURCE_PATH
    updated_build_graph = _replace_once_or_patched(
        build_graph_source,
        original_path,
        patched_path,
        "source-path normalization",
        "build_graph.py",
    )
    updated_build_graph = _patch_sdk_flags(updated_build_graph)

    updated_init_check = init_check_source
    for original, patched, label in _INIT_CHECK_PATHS:
        updated_init_check = _replace_once_or_patched(
            updated_init_check,
            original,
            patched,
            f"{label} normalization",
            "init_check.py",
        )

    changed = updated_build_graph != build_graph_source or updated_init_check != init_check_source
    if changed:
        build_graph_path.write_text(updated_build_graph, encoding="utf-8")
        init_check_path.write_text(updated_init_check, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_graph", type=Path)
    parser.add_argument("init_check", type=Path)
    args = parser.parse_args()

    if patch_swofty_sources(args.build_graph, args.init_check):
        print(
            "Applied verified Windows compatibility fixes to "
            f"{args.build_graph} and {args.init_check}"
        )
    else:
        print("Verified Windows compatibility fixes already present in Swofty native tools.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
