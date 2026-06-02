#!/usr/bin/env python3
"""
patch_clangd.py
Workaround script to fix clangd header link issue by setting up the user-level clangd config.yaml for STM32 ARM Clang projects.
See README.md for details about the clangd header parsing issue and the rationale for this patch.

Usage
-----
    python patch_clangd.py [--dry-run] [-v | --verbose] [--force] [--config-path PATH]

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

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple


MANAGED_BEGIN = "# --- BEGIN patch_clangd.py managed section ---"
MANAGED_END = "# --- END patch_clangd.py managed section ---"


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
# Logging
# ---------------------------------------------------------------------------

def _verbose_log(verbose: bool, message: str) -> None:
    """Print a ``[VERBOSE]`` diagnostic line when *verbose* is True."""
    if verbose:
        print(f"[VERBOSE] {message}")


# ---------------------------------------------------------------------------
# config.yaml helpers
# ---------------------------------------------------------------------------

def _normalize_text(raw_bytes: bytes) -> str:
    """Decode UTF-8 text and normalise line endings to LF.

    The normalised format is used for all comparisons to keep behavior
    deterministic across operating systems and newline styles.
    """
    return raw_bytes.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _next_backup_path(filepath: Path) -> Path:
    """Return the next available backup path for *filepath*.

    Example:
        config.yaml -> config.yaml_backup001, config.yaml_backup002, ...
    """
    backup_prefix = f"{filepath.name}_backup"
    backup_pattern = re.compile(rf"^{re.escape(backup_prefix)}(\d{{3}})$")
    backup_max = 0
    for entry in filepath.parent.iterdir():
        if not entry.is_file():
            continue
        match = backup_pattern.match(entry.name)
        if match:
            backup_max = max(backup_max, int(match.group(1)))
    return filepath.parent / f"{backup_prefix}{backup_max + 1:03d}"


def _build_managed_block(include_c: Path, include_cpp: Path, include_newlib: Path) -> str:
    """Return the managed config.yaml section for clangd.

    The order of include paths is intentional and must remain stable:
    1) C++ headers, 2) newlib C headers, 3) Clang built-ins.
    """
    return (
        f"{MANAGED_BEGIN}\n"
        "# Applies ARM toolchain include paths for ST ARM Clang projects.\n"
        "# Managed automatically. Re-run patch_clangd.py to update paths.\n"
        "\n"
        "CompileFlags:\n"
        "  Add:\n"
        "    - '--target=arm-none-eabi'\n"
        "    - '-nostdinc'\n"
        "    - '-nostdinc++'\n"
        "    - '-nostdlibinc'\n"
        "    - '-x'\n"
        "    - 'c++'\n"
        "\n"
        "# === Path config to headers:\n"
        "# ATTENTION: Order of these entries is important!\n"
        "# 1. C++ STL:\n"
        "    - '-isystem'\n"
        "    - >-\n"
        f"      {include_cpp}\n"
        "# 2. C headers (newlib):\n"
        "    - '-isystem'\n"
        "    - >-\n"
        f"      {include_newlib}\n"
        "# 3. clang builtin:\n"
        "    - '-isystem'\n"
        "    - >-\n"
        f"      {include_c}\n"
        f"{MANAGED_END}\n"
    )


def _merge_managed_block(existing: str, managed_block: str, verbose: bool) -> Tuple[str, bool]:
    """Merge *managed_block* into existing config.yaml text.

    Behavior:
    - Empty file: write only managed block.
    - Existing file without managed markers: append via YAML document separator.
    - Existing managed section: replace only that section.

    Returns:
        (updated_text, changed)
    """
    begin_idx = existing.find(MANAGED_BEGIN)

    if begin_idx == -1:
        # Existing user config stays untouched; add managed block as second
        # YAML document. If the file is empty, no separator is needed.
        if existing.strip() == "":
            return managed_block, True
        return f"{existing.rstrip()}\n---\n{managed_block}", True

    end_idx = existing.find(MANAGED_END, begin_idx)
    if end_idx == -1:
        # Corrupt marker case: keep behavior deterministic by replacing from
        # BEGIN marker to EOF with the new managed block.
        _verbose_log(verbose, "Existing managed section has no END marker. Replacing from BEGIN to EOF.")
        updated = f"{existing[:begin_idx].rstrip()}\n{managed_block}"
        return updated, updated != existing

    end_idx = end_idx + len(MANAGED_END)
    if end_idx < len(existing) and existing[end_idx:end_idx + 1] == "\n":
        end_idx += 1

    current_block = existing[begin_idx:end_idx]
    if current_block == managed_block:
        return existing, False

    updated = f"{existing[:begin_idx]}{managed_block}{existing[end_idx:]}"
    return updated, True


# ---------------------------------------------------------------------------
# User-level clangd config.yaml generation
# ---------------------------------------------------------------------------

def _get_user_clangd_config_path() -> Optional[Path]:
    """Return the platform-specific path for the user-level clangd config.yaml.

    Platform locations (from clangd documentation):
    - Windows : %LocalAppData%\\clangd\\config.yaml
    - Linux   : $XDG_CONFIG_HOME/clangd/config.yaml  (default: ~/.config/clangd/config.yaml)
    - macOS   : ~/Library/Application Support/clangd/config.yaml

    Returns None if the location cannot be determined.
    """
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "clangd" / "config.yaml"
        # Fallback for uncommon environments where LOCALAPPDATA is missing.
        return Path.home() / "AppData" / "Local" / "clangd" / "config.yaml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "clangd" / "config.yaml"
    # Linux and other Unix-like systems
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "clangd" / "config.yaml"
    return Path.home() / ".config" / "clangd" / "config.yaml"


def patch_user_config_yaml(
    include_c: Path,
    include_cpp: Path,
    include_newlib: Path,
    dry_run: bool,
    verbose: bool,
    config_path_override: Optional[Path] = None,
) -> bool:
    """Write or update the user-level clangd config.yaml.

    This file is needed because clangd only applies project-level .clangd
    files to files that live underneath the project root.  Toolchain headers
    (opened via Follow Link) live in the st-arm-clang bundle directory, which
    is outside the project tree.  Without a user-level config those files fall
    back to clangd's built-in heuristic, which on Windows incorrectly picks
    MSVC includes.

    The generated managed section injects the required ARM target and include
    paths so toolchain headers are parsed with the correct standard library.

    Args:
        include_c:      Path to the Clang built-in headers directory.
        include_cpp:    Path to the newlib C++ headers directory (c++/v1).
        include_newlib: Path to the newlib C headers directory.
        dry_run:        If True, do not write any files.
        verbose:        If True, print step-by-step diagnostic messages.

    Returns:
        True if the file was changed (or would be changed in dry-run mode).
    """
    config_path = config_path_override if config_path_override is not None else _get_user_clangd_config_path()
    config_dir = config_path.parent
    _verbose_log(verbose, f"Config path     : {config_path}")

    managed_block = _build_managed_block(include_c, include_cpp, include_newlib)

    original_raw = b""
    original = ""
    if config_path.exists():
        original_raw = config_path.read_bytes()
        original = _normalize_text(original_raw)
        _verbose_log(verbose, "Existing config.yaml detected.")
    else:
        _verbose_log(verbose, "config.yaml does not exist yet. A new file will be created.")

    updated, changed = _merge_managed_block(original, managed_block, verbose)
    if not changed:
        _verbose_log(verbose, f"config.yaml already up to date: {config_path}")
        return False

    print(f"  {'WOULD_PATCH' if dry_run else 'PATCHED'}: {config_path}")
    if not dry_run:
        config_dir.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            backup = _next_backup_path(config_path)
            _verbose_log(verbose, f"Writing backup : {backup.name}")
            backup.write_bytes(original_raw)
        config_path.write_bytes(updated.encode("utf-8"))
        _verbose_log(verbose, f"Written: {config_path}")

    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments and update the user-level clangd config.yaml."""
    parser = argparse.ArgumentParser(
        description=(
            "Manage the user-level clangd config.yaml for ST ARM Clang "
            "STM32 projects."
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
        "--force",
        action="store_true",
        help=(
            "Ignore the STARM_TOOLCHAIN_CONFIG check in cmake/starm-clang.cmake "
            "and always run the patch, even for non-NEWLIB configurations."
        ),
    )
    parser.add_argument(
        "--config-path",
        type=str,
        default="",
        help="Optional absolute path to config.yaml (primarily for testing).",
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

    print("Updating clangd user config:")
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

    # include_c       — Clang built-in headers:  lib/clang/<clang-ver>/include
    # include_cpp     — newlib C++ headers:      lib/clang-runtimes/newlib/arm-none-eabi/include/c++/v1
    # include_newlib  — newlib C headers:        lib/clang-runtimes/newlib/arm-none-eabi/include
    include_c      = clang_versions[0] / "include"
    include_cpp    = toolchain_path / "lib" / "clang-runtimes" / "newlib" / "arm-none-eabi" / "include" / "c++" / "v1"
    include_newlib = toolchain_path / "lib" / "clang-runtimes" / "newlib" / "arm-none-eabi" / "include"
    _verbose_log(args.verbose, f"Clang version   : {clang_versions[0].name}")
    _verbose_log(args.verbose, f"Include C++     : {include_cpp}")
    _verbose_log(args.verbose, f"Include C       : {include_c}")
    _verbose_log(args.verbose, f"Include newlib  : {include_newlib}")

    config_override = Path(args.config_path).resolve() if args.config_path else None

    changed = patch_user_config_yaml(
        include_c=include_c,
        include_cpp=include_cpp,
        include_newlib=include_newlib,
        dry_run=args.dry_run,
        verbose=args.verbose,
        config_path_override=config_override,
    )

    if not changed:
        print("  No changes needed.")

    print()


if __name__ == "__main__":
    main()