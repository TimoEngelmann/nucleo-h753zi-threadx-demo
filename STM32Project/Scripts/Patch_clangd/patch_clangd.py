#!/usr/bin/env python3
"""
patch_clangd.py  -  clangd configuration patcher for STM32 ARM Clang projects

Background
----------
When using clangd with the ST ARM Clang toolchain on Windows, clangd may pick
the wrong default target (e.g. x86_64-pc-windows-msvc) instead of the correct
ARM target.  This causes C++ headers and standard library paths to be resolved
incorrectly, leading to false errors in the editor.

This script fixes all .clangd files found in the current project tree so that
clangd uses the right target, the correct newlib/libc++ include paths, and does
not pass conflicting sysroot flags to the compiler.

See also:
  https://community.st.com/t5/stm32cubeide-for-visual-studio/clangd-assumes-compiler-target-is-x86-64-pc-windows-msvc-for-cpp/m-p/855030#M1471

Features
--------
- Detects the latest installed st-arm-clang toolchain version automatically.
- Replaces ${CUBE_BUNDLE_PATH} placeholders with the resolved bundle path.
- Ensures all required CompileFlags entries are present and up to date.
  Stale include paths (e.g. from an older toolchain version) are replaced.
- Creates numbered backup files before any modification
  (.clangd_backup001, .clangd_backup002, ...).
- Dry-run mode: shows what would change without writing any files.
- Verbose mode: prints detailed step-by-step diagnostics.
- Minimal default output: one start line + one line per patched file.

Usage
-----
    python patch_clangd.py [--dry-run] [-v | --verbose]

Required environment variable
------------------------------
    CUBE_BUNDLE_PATH
        Path to the STM32CubeIDE bundle directory.
        Example (PowerShell):
            $env:CUBE_BUNDLE_PATH = 'D:/dev/Tools/ST/STM32CubeRepo/bundles'

Requirements
------------
    Python 3.6 or newer.
"""

import os
import re
import sys
import argparse
from pathlib import Path
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Toolchain detection
# ---------------------------------------------------------------------------

def detect_toolchain_base(cube_bundle_path: Path) -> Path:
    """Return the path to the latest st-arm-clang toolchain version folder.

    Searches *cube_bundle_path*/st-arm-clang for version subdirectories and
    returns the one with the highest version number (numeric comparison).

    Args:
        cube_bundle_path: Resolved path to the STM32CubeIDE bundle directory.

    Returns:
        Path to the selected toolchain version folder.

    Raises:
        RuntimeError: If the st-arm-clang directory is missing or empty.
    """
    st_dir = cube_bundle_path / "st-arm-clang"

    if not st_dir.exists():
        raise RuntimeError(f"st-arm-clang directory not found: {st_dir}")

    # Sort by numeric version tuple so that e.g. 21.1.1+st.7 → (21,1,1,7)
    # ranks correctly above 9.0.0+st.1 — plain string sort would fail here.
    versions = sorted(
        [p for p in st_dir.iterdir() if p.is_dir()],
        key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name)),
        reverse=True,
    )

    if not versions:
        raise RuntimeError(f"No toolchain versions found in: {st_dir}")

    return versions[0]


# ---------------------------------------------------------------------------
# CMake toolchain config detection
# ---------------------------------------------------------------------------

def read_toolchain_config(project_root: Path) -> Optional[str]:
    """Return the STARM_TOOLCHAIN_CONFIG value from cmake/starm-clang.cmake.

    Looks for a CMake line of the form::

        set(STARM_TOOLCHAIN_CONFIG "STARM_NEWLIB")

    The search is case-sensitive and ignores commented-out lines.

    Args:
        project_root: Root directory of the project.

    Returns:
        The configured value (e.g. ``"STARM_NEWLIB"``), or ``None`` if the
        file does not exist or the setting is not found.
    """
    cmake_file = project_root / "cmake" / "starm-clang.cmake"
    if not cmake_file.exists():
        return None

    # Note: only double-quoted CMake values are matched, e.g.
    #   set(STARM_TOOLCHAIN_CONFIG "STARM_NEWLIB")
    # An unquoted form like set(STARM_TOOLCHAIN_CONFIG STARM_NEWLIB) returns None
    # which lets the patcher run without a config guard (safe fallback).
    pattern = re.compile(r'set\s*\(\s*STARM_TOOLCHAIN_CONFIG\s+"([^"]+)"\s*\)')
    for line in cmake_file.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        m = pattern.search(line)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# YAML structure helpers
#
# These helpers work on a list of raw text lines rather than a parsed YAML
# tree.  They rely on the consistent 2-space indentation used in clangd
# configuration files.  Tab indentation is not supported.
# ---------------------------------------------------------------------------

def _find_child_key(
    lines: List[str], start_idx: int, end_idx: int, key: str
) -> Optional[int]:
    """Return the index of a 2-space-indented *key* within the given range.

    Matches both ``  key:`` (key only) and ``  key: value`` (key with inline
    value on the same line).  Returns ``None`` if the key is not found.
    """
    target = f"  {key}:"
    for i in range(start_idx + 1, end_idx):
        stripped = lines[i].rstrip()
        if stripped == target or stripped.startswith(target + " "):
            return i
    return None


def _find_sequence_end(
    lines: List[str], seq_start_idx: int, parent_end_idx: int
) -> int:
    """Return the index of the first line after the YAML list at *seq_start_idx*.

    The list ends at the first non-empty line that either:
    - has an indent of 2 or less (a sibling key of the list), or
    - has an indent of exactly 4 and does not start with ``-``
      (a scalar key inside the parent block, not a list item).
    Blank lines are treated as part of the list.
    """
    i = seq_start_idx + 1
    while i < parent_end_idx:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        indent = len(lines[i]) - len(lines[i].lstrip(" "))  # leading-space count
        if indent <= 2:
            break
        if indent == 4 and not stripped.startswith("-"):
            break
        i += 1
    return i


def _is_newlib_include_path(value: str) -> bool:
    """Return True if *value* is an st-arm-clang newlib include path.

    The check is intentionally version-agnostic: it matches any path that
    contains ``/st-arm-clang/`` and the expected
    ``/lib/clang-runtimes/newlib/arm-none-eabi/include`` segment.  This allows
    stale paths from older toolchain versions to be detected and replaced.
    """
    # Strip quotes, unify path separators, and lower-case before comparing.
    normalized = value.strip().strip("'\"").replace("\\", "/").lower()
    return (
        "/st-arm-clang/" in normalized
        and "/lib/clang-runtimes/newlib/arm-none-eabi/include" in normalized
    )


def _is_clang_builtin_include_path(value: str) -> bool:
    """Return True if *value* is an st-arm-clang Clang built-in include path.

    The check is intentionally version-agnostic: it matches any path that
    contains ``/st-arm-clang/`` and the expected
    ``/lib/clang/<version>/include`` segment.  This allows stale paths from
    older Clang major versions to be detected and replaced.
    """
    # Strip quotes, unify path separators, and lower-case before comparing.
    normalized = value.strip().strip("'\"").replace("\\", "/").lower()
    return (
        "/st-arm-clang/" in normalized
        and bool(re.search(r"/lib/clang/\d+/include", normalized))
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _verbose_log(verbose: bool, message: str) -> None:
    """Print a ``[VERBOSE]`` diagnostic line when *verbose* is True."""
    if verbose:
        print(f"[VERBOSE] {message}")


# ---------------------------------------------------------------------------
# Core patching logic
# ---------------------------------------------------------------------------

def _ensure_compileflags_sections(
    content: str, include_c: str, include_cpp: str
) -> str:
    """Ensure the CompileFlags section contains all required entries.

    Performs the following operations on *content* (a raw YAML string):

    1. Creates a ``CompileFlags:`` top-level block if one does not exist.
    2. Creates an ``Add:`` sub-list if one does not exist.
    3. Removes any existing st-arm-clang ``-isystem`` entries (both Clang
       built-in and newlib paths).  This covers block-scalar (``>-``) and
       inline quote forms, and is version-agnostic so outdated paths are
       always replaced.
    4. Appends missing ``Add:`` entries (in this order):
         - ``--target=arm-none-eabi``
         - ``-stdlib=libc++``
         - ``-isystem <include_c>``   (Clang built-in headers)
         - ``-isystem <include_cpp>`` (newlib C++ headers)
    5. Creates a ``Remove:`` sub-list if one does not exist, and adds:
         - ``--sysroot``
         - ``--config=newlib.cfg``

    All existing entries that do not match the above rules are left unchanged.

    Args:
        content:     Raw text of the .clangd file.
        include_c:   Absolute path string for the Clang built-in headers
                     directory (``lib/clang/<version>/include``).
        include_cpp: Absolute path string for the newlib C++ headers directory
                     (``lib/clang-runtimes/newlib/arm-none-eabi/include/c++/v1``).

    Returns:
        Updated file content.  The trailing newline is preserved if present.
    """
    lines = content.splitlines()

    required_add_items = [
        "- '--target=arm-none-eabi'",
        "- '-stdlib=libc++'",
    ]
    required_remove_items = [
        "- '--sysroot'",
        "- '--config=newlib.cfg'",
    ]

    # ---- Locate or create CompileFlags block --------------------------------
    compile_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.rstrip() == "CompileFlags:":
            compile_idx = i
            break

    if compile_idx is None:
        lines = ["CompileFlags:", "  Add:"] + lines
        compile_idx = 0

    # Find where the CompileFlags block ends: first non-empty line back at column 0.
    compile_end = next(
        (i for i in range(compile_idx + 1, len(lines))
         if lines[i].strip() and not lines[i].startswith(" ")),
        len(lines),
    )

    # ---- Locate or create Add list ------------------------------------------
    add_idx = _find_child_key(lines, compile_idx, compile_end, "Add")
    if add_idx is None:
        lines.insert(compile_idx + 1, "  Add:")
        add_idx     = compile_idx + 1
        compile_end += 1

    add_end   = _find_sequence_end(lines, add_idx, compile_end)
    add_block = lines[add_idx + 1:add_end]

    # ---- Remove stale st-arm-clang -isystem entries -------------------------
    # Any -isystem entry whose associated path matches the st-arm-clang Clang
    # built-in or newlib pattern is removed so it can be re-added with the
    # current version below.  Both block-scalar (>-) and inline quote forms
    # are handled; the check is version-agnostic.
    filtered: List[str] = []
    i = 0
    while i < len(add_block):
        current = add_block[i].strip()

        if current == "- '-isystem'" and i + 1 < len(add_block):
            nxt = add_block[i + 1].strip()

            # Block-scalar form:
            #   - '-isystem'
            #   - >-
            #     the/path
            if nxt == "- >-" and i + 2 < len(add_block):
                path_val = add_block[i + 2].strip()
                if (_is_newlib_include_path(path_val) or
                        _is_clang_builtin_include_path(path_val)):
                    i += 3
                    continue

            # Inline form:
            #   - '-isystem'
            #   - 'the/path'
            if nxt.startswith("- ") and (
                _is_newlib_include_path(nxt[2:]) or
                _is_clang_builtin_include_path(nxt[2:])
            ):
                i += 2
                continue

        # Stand-alone form (no preceding -isystem):
        #   - 'the/path'
        if current.startswith("- ") and (
            _is_newlib_include_path(current[2:]) or
            _is_clang_builtin_include_path(current[2:])
        ):
            i += 1
            continue

        filtered.append(add_block[i])
        i += 1

    if filtered != add_block:
        lines[add_idx + 1:add_end] = filtered
        compile_end += len(filtered) - len(add_block)
        add_end      = add_idx + 1 + len(filtered)
        add_block    = filtered

    # ---- Insert missing Add entries -----------------------------------------
    to_insert_add: List[str] = []
    for item in required_add_items:
        # Each simple flag fits on exactly one line, so a direct list-membership
        # check (exact string match) is enough to detect duplicates.
        if f"    {item}" not in add_block:
            to_insert_add.append(f"    {item}")

    # Include paths can span two or three lines in block-scalar form, so we
    # join the block into a single string and use substring search.  Both
    # backslash and forward-slash variants are checked to handle Windows paths.
    include_c_posix   = include_c.replace("\\", "/")
    include_cpp_posix = include_cpp.replace("\\", "/")
    add_block_text    = "\n".join(add_block)

    # C (Clang built-in headers) first, then C++ (newlib headers) — this order
    # matches the reference .clangd and is required for correct header resolution.
    if include_c not in add_block_text and include_c_posix not in add_block_text:
        to_insert_add += ["    - '-isystem'", "    - >-", f"      {include_c}"]

    if include_cpp not in add_block_text and include_cpp_posix not in add_block_text:
        to_insert_add += ["    - '-isystem'", "    - >-", f"      {include_cpp}"]

    if to_insert_add:
        lines[add_end:add_end] = to_insert_add
        compile_end += len(to_insert_add)

    # ---- Locate or create Remove list ---------------------------------------
    remove_idx = _find_child_key(lines, compile_idx, compile_end, "Remove")
    if remove_idx is None:
        # Place Remove before CompilationDatabase if it exists, otherwise at
        # the end of the CompileFlags block.
        db_idx    = _find_child_key(lines, compile_idx, compile_end, "CompilationDatabase")
        insert_at = db_idx if db_idx is not None else compile_end
        lines.insert(insert_at, "  Remove:")
        remove_idx   = insert_at
        compile_end += 1

    remove_end   = _find_sequence_end(lines, remove_idx, compile_end)
    remove_block = lines[remove_idx + 1:remove_end]

    to_insert_remove = [
        f"    {item}"
        for item in required_remove_items
        if f"    {item}" not in remove_block
    ]
    if to_insert_remove:
        lines[remove_end:remove_end] = to_insert_remove

    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def process_file(
    filepath: Path,
    cube_path: Path,
    include_paths: Tuple[Path, Path],
    dry_run: bool,
    verbose: bool,
) -> bool:
    """Patch a single .clangd file and write a numbered backup if changed.

    Steps:
      1. Read the file (UTF-8; an optional BOM is handled transparently).
      2. Replace any ``${CUBE_BUNDLE_PATH}`` placeholder with the resolved path.
      3. Ensure all required CompileFlags entries are present and up to date.
      4. If anything changed, write a numbered backup and save the updated file
         (skipped when *dry_run* is True).

    Args:
        filepath:      Absolute path to the .clangd file.
        cube_path:     Resolved CUBE_BUNDLE_PATH value.
        include_paths: Tuple of ``(include_c, include_cpp)`` Path objects, where
                       *include_c* is the Clang built-in headers path
                       (``lib/clang/<version>/include``) and *include_cpp* is
                       the newlib C++ headers path
                       (``lib/clang-runtimes/newlib/arm-none-eabi/include/c++/v1``).
        dry_run:       If True, do not write any files.
        verbose:       If True, print step-by-step diagnostic messages.

    Returns:
        True if the file was changed (or would be changed in dry-run mode).
    """
    _verbose_log(verbose, f"Processing: {filepath}")

    # Read raw bytes first so we can detect and later preserve an optional BOM.
    # utf-8-sig decodes the content and silently strips the BOM from the string,
    # which prevents it from appearing as part of the first YAML key.
    # Line endings are normalised to LF so all comparisons and YAML parsing are
    # consistent across operating systems.  The tool always writes LF output.
    raw_bytes = filepath.read_bytes()
    has_bom   = raw_bytes.startswith(b"\xef\xbb\xbf")
    original  = raw_bytes.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    modified  = original
    changed   = False

    # ---- Replace placeholder ------------------------------------------------
    if "${CUBE_BUNDLE_PATH}" in modified:
        _verbose_log(verbose, "Replacing ${CUBE_BUNDLE_PATH} placeholder")
        modified = modified.replace(
            "${CUBE_BUNDLE_PATH}",
            str(cube_path).replace("\\", "/"),
        )
        changed = True

    # ---- Patch CompileFlags -------------------------------------------------
    include_c, include_cpp = include_paths
    rewritten = _ensure_compileflags_sections(
        modified, str(include_c), str(include_cpp)
    )
    if rewritten != modified:
        _verbose_log(verbose, "CompileFlags section updated")
        modified = rewritten
        changed  = True

    # ---- Write result -------------------------------------------------------
    if changed:
        print(f"  {'WOULD_PATCH' if dry_run else 'PATCHED'}: {filepath}")

        if not dry_run:
            write_encoding = "utf-8-sig" if has_bom else "utf-8"
            # Find the next unused backup path (.clangd_backup001, _backup002, …).
            backup_prefix  = f"{filepath.name}_backup"
            backup_pattern = re.compile(rf"^{re.escape(backup_prefix)}(\d{{3}})$")
            backup_max     = 0
            for entry in filepath.parent.iterdir():
                if entry.is_file():
                    m = backup_pattern.match(entry.name)
                    if m:
                        backup_max = max(backup_max, int(m.group(1)))
            backup = filepath.parent / f"{backup_prefix}{backup_max + 1:03d}"
            _verbose_log(verbose, f"Writing backup : {backup.name}")
            # Backup is an exact byte-for-byte copy of the original file
            # (preserves the original line endings and BOM).
            backup.write_bytes(raw_bytes)
            # Patched file is written without OS newline translation so the
            # result is always LF-only and idempotent on the next run.
            filepath.write_bytes(modified.encode(write_encoding))
            _verbose_log(verbose, "Done")
    else:
        _verbose_log(verbose, "No changes needed")

    return changed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments, discover .clangd files, and run the patcher."""
    parser = argparse.ArgumentParser(
        description=(
            "Patch .clangd files in the current project tree for use with "
            "the ST ARM Clang toolchain on STM32 targets."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without writing any files.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed diagnostic messages for each step.",
    )
    parser.add_argument(
        "--exclude", metavar="PATTERN", action="append", default=[],
        help=(
            "Skip .clangd files whose path contains PATTERN. "
            "Can be specified multiple times."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Ignore the STARM_TOOLCHAIN_CONFIG check in cmake/starm-clang.cmake "
            "and always run the patch, even for non-NEWLIB configurations."
        ),
    )
    args = parser.parse_args()

    # ---- Check environment --------------------------------------------------
    cube_path_env = os.environ.get("CUBE_BUNDLE_PATH")
    if not cube_path_env:
        print("ERROR: Environment variable CUBE_BUNDLE_PATH is not set.", file=sys.stderr)
        print("       Set it to the STM32CubeIDE bundle directory, for example:", file=sys.stderr)
        print("       $env:CUBE_BUNDLE_PATH = 'D:/dev/Tools/ST/STM32CubeRepo/bundles'", file=sys.stderr)
        sys.exit(1)

    cube_path    = Path(cube_path_env).resolve()
    project_root = Path.cwd()  # cwd = current working directory (set by the caller, e.g. cmake WORKING_DIRECTORY)

    print("Patching .clangd file(s):")
    _verbose_log(args.verbose, f"Project root    : {project_root}")
    _verbose_log(args.verbose, f"Mode            : {'dry-run' if args.dry_run else 'write'}")
    _verbose_log(args.verbose, f"CUBE_BUNDLE_PATH: {cube_path}")

    # ---- Check cmake/starm-clang.cmake toolchain configuration --------------
    # The newlib include patches are only meaningful when the project uses the
    # STARM_NEWLIB toolchain configuration.  For STARM_HYBRID or STARM_PICOLIBC
    # the flags would be wrong, so the patch is skipped.
    toolchain_config = read_toolchain_config(project_root)
    _verbose_log(args.verbose, f"STARM_TOOLCHAIN_CONFIG: {toolchain_config!r}")

    if toolchain_config is None:
        _verbose_log(
            args.verbose,
            "cmake/starm-clang.cmake not found or STARM_TOOLCHAIN_CONFIG not set "
            "— proceeding without config check",
        )
    elif toolchain_config != "STARM_NEWLIB":
        if args.force:
            _verbose_log(
                args.verbose,
                f"STARM_TOOLCHAIN_CONFIG is '{toolchain_config}' but --force is set "
                "— skipping config check",
            )
        else:
            print(
                f"patch_clangd: skipping — STARM_TOOLCHAIN_CONFIG is '{toolchain_config}'"
                f" (patch only applies to STARM_NEWLIB)"
            )
            return

    # ---- Detect toolchain ---------------------------------------------------
    try:
        toolchain_path = detect_toolchain_base(cube_path)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    _verbose_log(args.verbose, f"Toolchain       : {toolchain_path}")

    # Detect the Clang built-in headers directory (lib/clang/<version>/include).
    # The major version (e.g. "21") lives in a subdirectory of lib/clang/ and
    # must be discovered at runtime because it does not match the toolchain
    # version string (e.g. "21.1.1+st.7").
    clang_lib_dir = toolchain_path / "lib" / "clang"
    if not clang_lib_dir.exists():
        print(f"ERROR: clang directory not found: {clang_lib_dir}", file=sys.stderr)
        sys.exit(1)
    clang_versions = sorted(
        [p for p in clang_lib_dir.iterdir() if p.is_dir()],
        key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name)),
        reverse=True,
    )
    if not clang_versions:
        print(f"ERROR: No clang version directories found in: {clang_lib_dir}", file=sys.stderr)
        sys.exit(1)

    # include_c   — Clang built-in headers:  lib/clang/<clang-ver>/include
    # include_cpp — newlib C++ headers:      lib/clang-runtimes/newlib/arm-none-eabi/include/c++/v1
    include_c   = clang_versions[0] / "include"
    include_cpp = toolchain_path / "lib" / "clang-runtimes" / "newlib" / "arm-none-eabi" / "include" / "c++" / "v1"
    include_paths = (include_c, include_cpp)
    _verbose_log(args.verbose, f"Clang version   : {clang_versions[0].name}")
    _verbose_log(args.verbose, f"Include C       : {include_c}")
    _verbose_log(args.verbose, f"Include C++     : {include_cpp}")

    # ---- Discover .clangd files ---------------------------------------------
    all_files = list(project_root.rglob(".clangd"))
    if args.exclude:
        files = [
            f for f in all_files
            if not any(pat in str(f) for pat in args.exclude)
        ]
        excluded = len(all_files) - len(files)
        _verbose_log(args.verbose, f"Found {len(all_files)} .clangd file(s), {excluded} excluded by --exclude patterns")
    else:
        files = all_files
        _verbose_log(args.verbose, f"Found {len(files)} .clangd file(s)")

    if not files:
        _verbose_log(args.verbose, "Nothing to do.")
        return

    # ---- Process files ------------------------------------------------------
    error_count    = 0
    modified_count = 0

    for f in files:
        try:
            if process_file(f, cube_path, include_paths, args.dry_run, args.verbose):
                modified_count += 1
        except Exception as exc:
            print(f"ERROR: {f}: {exc}", file=sys.stderr)
            error_count += 1

    _verbose_log(args.verbose, f"Patched: {modified_count}  Errors: {error_count}")

    if modified_count == 0 and error_count == 0:
        print("  No changes needed.")

    print()

    if error_count:
        sys.exit(1)


if __name__ == "__main__":
    main()