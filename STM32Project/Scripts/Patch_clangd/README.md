# clangd Patch Tool

## Why this patch is needed

Historically, clangd picked the wrong default target (like `x86_64-pc-windows-msvc` on Windows) for ST ARM Clang projects. While this specific target mismatch has been largely addressed, two include-path issues remain when using the `STARM_NEWLIB` configuration:

1. **Missing Clang built-in headers** — clangd does not automatically inject the Clang compiler's own built-in include directory (`lib/clang/<version>/include`). This directory contains Clang-specific headers such as `stdarg.h`, `stdint.h`, and intrinsic headers that the compiler itself provides.

2. **Missing `newlib` path segment in C++ headers** — clangd searches in `arm-none-eabi/include/c++/v1` instead of `newlib/arm-none-eabi/include/c++/v1`, so the `libc++` standard-library headers are not found.

Both issues result in incorrect file associations in the editor, even if the project compiles successfully. This can be misleading during debugging or code analysis, as the wrong file is opened.

This script fixes all `.clangd` files in the project tree to ensure clangd uses the correct ARM target, injects both required `-isystem` include paths, removes conflicting flags, and updates stale toolchain version paths.

Related ST community discussion:  
https://community.st.com/t5/stm32cubeide-for-visual-studio/clangd-assumes-compiler-target-is-x86-64-pc-windows-msvc-for-cpp/m-p/855030

## Files in this folder

| File | Description |
|---|---|
| `patch_clangd.py` | Main patch tool |
| `patch_clangd_test_runner.ps1` | Functional test suite (15 tests) |
| `README.md` | This document |


> **Note:** Python creates a `__pycache__` folder here after the first run.
> Add `__pycache__/` to your `.gitignore` so it is not committed.

## Platform support

`patch_clangd.py` works on **Windows, Linux, and macOS**.  It uses only the
Python standard library and `pathlib`, with no platform-specific calls.  The
embedded paths in the patched `.clangd` files will use the native path
separator of the host system, which clangd handles correctly on all platforms.

The test runner (`patch_clangd_test_runner.ps1`) requires **PowerShell 5.1+**
on Windows or **PowerShell Core (`pwsh`)** on Linux/macOS.

## How to use

### Prerequisites

- **Python 3.6 or newer** available in `PATH`
- **Environment variable `CUBE_BUNDLE_PATH`** set to the ST bundles directory
  - STM32CubeIDE sets this automatically when it launches a terminal.
  - When running manually (e.g. from VS Code), set it yourself:
    ```powershell
    $env:CUBE_BUNDLE_PATH = 'C:/path/to/STM32CubeRepo/bundles'
    ```

### Normal run
Call the script from project root.

```powershell
python ./path/to/patch_clangd.py
```

Output when files are patched:

```
Patching .clangd file(s):
  PATCHED: C:\[...]\STM32Project\.clangd

```

Output when nothing needs to change:

```
Patching .clangd file(s):
  No changes needed.

```

### Dry-run mode — preview without writing

```powershell
python ./path/to/patch_clangd.py --dry-run
```

Shows `WOULD_PATCH` instead of `PATCHED`; no files or backups are written.

### Verbose mode — step-by-step diagnostics

```powershell
python ./path/to/patch_clangd.py -v
```

Prints a `[VERBOSE]` line for every decision: which file is processed, which
section was updated, which backup was created, etc.

### Exclude directories

```powershell
python ./path/to/patch_clangd.py --exclude test_output
```

Skips any `.clangd` file whose path contains the given pattern.
`--exclude` can be specified multiple times.

### Force patching regardless of cmake config

```powershell
python ./path/to/patch_clangd.py --force
```

Bypasses the `STARM_TOOLCHAIN_CONFIG` check in `cmake/starm-clang.cmake` and
always patches, even when the config is not `STARM_NEWLIB`.  Useful for
testing or for projects where the cmake file is not present.

## Backup behavior

Before modifying a file, the script writes a numbered backup in the same
folder:

```
.clangd_backup001   ← first patch
.clangd_backup002   ← second patch (if .clangd is changed again later)
```

The backup is a byte-for-byte copy of the original (preserves line endings and
BOM).  The index is determined by scanning the directory, so gaps or
out-of-order numbers are handled correctly.

## cmake/starm-clang.cmake guard

If `cmake/starm-clang.cmake` contains:

```cmake
set(STARM_TOOLCHAIN_CONFIG "STARM_NEWLIB")
```

the script patches normally.  For any other value (e.g. `STARM_PICOLIBC`,
`STARM_HYBRID`) the script prints a skip message and exits without touching
any files, because the newlib include paths would be wrong for those
configurations.

If the cmake file does not exist or the variable is not set, the script
proceeds without a config check (safe fallback).

To bypass this check entirely, pass `--force` (see [Force patching](#force-patching-regardless-of-cmake-config) above).

## CMake integration

The recommended way is to run the patcher automatically using both a
**configure-time** and a **build-time** step in `CMakeLists.txt`.

### Why two steps are needed

The VS Code STM32 extension generates (or regenerates) the `.clangd` file
**after** the CMake configure step completes — not before.  This means that
on the very first configure run the file does not exist yet, so a pure
configure-time `execute_process` call finds nothing to patch.

The solution is to run the patcher a second time as a build-time
`add_custom_target`, so that the **first build** after the file is created
applies the patch automatically — without requiring a manual second configure.

Because the patch is idempotent (it only writes when a change is actually
needed), running it on every build costs negligible time and never produces
duplicate backups.

### Recommended CMakeLists.txt snippet

```cmake
find_package(Python3 COMPONENTS Interpreter REQUIRED QUIET)

if(DEFINED ENV{CUBE_BUNDLE_PATH})
  # Configure-time patch: runs immediately if .clangd already exists.
  if(EXISTS "${CMAKE_SOURCE_DIR}/.clangd")
    execute_process(
      COMMAND ${Python3_EXECUTABLE}
        "${CMAKE_SOURCE_DIR}/path/to/patch_clangd.py"
        --exclude patch_clangd_test_output
      WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    )
  else()
    message(STATUS "patch_clangd: .clangd not yet present — will be patched on the first build.")
  endif()

  # Build-time patch: covers the first-run case where .clangd is generated
  # by the VS Code extension after the configure step.
  add_custom_target(patch_clangd ALL
    COMMAND ${CMAKE_COMMAND} -E env
      "CUBE_BUNDLE_PATH=$ENV{CUBE_BUNDLE_PATH}"
      ${Python3_EXECUTABLE}
      "${CMAKE_SOURCE_DIR}/path/to/patch_clangd.py"
      --exclude patch_clangd_test_output
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    COMMENT "Patching .clangd file(s)"
    VERBATIM
  )
else()
  message(WARNING "CUBE_BUNDLE_PATH is not set. clangd patch step is skipped.")
endif()
```

### Execution flow

```
First configure
  └─► .clangd does not exist yet → configure-time patch skipped

VS Code extension generates .clangd  (after configure)

First build
  └─► add_custom_target runs patch → .clangd patched ✓

Every subsequent build
  └─► add_custom_target runs patch → no changes → nothing written ✓
```

If `.clangd` already exists when configure runs (e.g. from a previous
session), the configure-time `execute_process` patches it immediately and the
build-time target finds no further changes — no duplicate work.

### Key points

- `execute_process` runs **during `cmake configure`**, not during the build.
- `add_custom_target(... ALL ...)` runs **at the start of every build** before
  compilation begins.
- `WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"` ensures all project `.clangd`
  files are found via `rglob`.
- `--exclude patch_clangd_test_output` prevents the test fixture directory
  from being patched.
- `$ENV{CUBE_BUNDLE_PATH}` is captured at configure time and embedded in the
  build rule, so the environment variable does not need to be set again at
  build time.
- `QUIET` suppresses the "found Python" message during configure.

## How to run tests

```powershell
powershell -ExecutionPolicy Bypass -File .\path\to\patch_clangd_test_runner.ps1
```

The test runner creates an isolated output folder (`patch_clangd_test_output`)
next to the script, runs the patcher against test fixtures, and reports
**PASS** or **FAIL** for each scenario.

At the end it asks whether to delete
the output folder and the `__pycache__` directory.

> **Note:**
> - Parameter `-ExecutionPolicy Bypass`  
>   Windows blocks unsigned PowerShell scripts by default. This flag lifts that restriction for this single invocation without changing the system policy.  
> - Parameter `-File`  
> tells PowerShell to run the argument as a script file (instead of an inline command string).

> **Note:**  
> The test runner is 100% standalone and CI/CD-ready. If it cannot find a real ST toolchain via the `CUBE_BUNDLE_PATH` environment variable, it will automatically generate an isolated mock toolchain to run its verifications against.


### Test cases

| # | Name | What is verified |
|---|---|---|
| 1 | Recursive discovery | All `.clangd` files in all subdirectories are found and patched — not just the one in the project root. |
| 2 | Backup creation | A `.clangd_backup001` file is created next to every patched file. |
| 3 | Required flags | `--target=arm-none-eabi`, `-stdlib=libc++`, `--sysroot`, and `--config=newlib.cfg` are present in every file after patching. |
| 4 | Placeholder replaced | A `${CUBE_BUNDLE_PATH}` placeholder in a `CompilationDatabase:` line is replaced with the resolved bundle path. |
| 5 | Version update | Stale st-arm-clang toolchain paths (old version in both the Clang built-in C path `lib/clang/<old-ver>/include` and the newlib C++ path) are removed and replaced with the latest installed versions. |
| 6 | Minimal output | Normal output is exactly one header line (`Patching .clangd file(s):`) plus one `PATCHED:` line per changed file — nothing more. |
| 7 | Idempotency | Running the patcher a second time on already-patched files produces zero changes and prints `No changes needed.` |
| 8 | Dry-run mode | With `--dry-run`, `WOULD_PATCH` is printed but no file is written and no backup is created. |
| 9 | Verbose mode | With `-v`, `[VERBOSE]` diagnostic lines are printed for each processing step. |
| 10 | Diagnostics preserved | Existing `Diagnostics: Suppress:` entries in a `.clangd` file survive patching completely unchanged. |
| 11 | cmake guard — NEWLIB | When `cmake/starm-clang.cmake` sets `STARM_TOOLCHAIN_CONFIG "STARM_NEWLIB"`, patching runs normally. |
| 12 | cmake guard — other | When `cmake/starm-clang.cmake` sets any other value (e.g. `STARM_PICOLIBC`), the patcher prints a skip message and writes nothing. |
| 13 | `--force` flag | With `--force`, patching runs even when `STARM_TOOLCHAIN_CONFIG` is set to a non-NEWLIB value (e.g. `STARM_PICOLIBC`). |
| 14 | `--exclude` flag | Files whose path contains the `--exclude` pattern are skipped entirely; other files are still patched. |
| 15 | Backup numbering | When `.clangd_backup001` already exists, the next patch creates `.clangd_backup002` and leaves `backup001` untouched. |

