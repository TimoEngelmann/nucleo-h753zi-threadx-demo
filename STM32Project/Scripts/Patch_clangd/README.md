# clangd Patch Tool

Related ST community discussion:  
- https://community.st.com/t5/stm32cubeide-for-visual-studio/clangd-assumes-compiler-target-is-x86-64-pc-windows-msvc-for-cpp/m-p/855030

## Why this patch is needed

One of the clang**d** services allows you to click on `#include<headername>` in the source code, which opens the correct file in a new VS Code editor window.  

Unfortunately, the following current configuration causes issues in this regard:
- `STM32CubeMX` v6.17.0
- `STM32CubeIDE for Visual Studio Code` v3.9.0
- Clang compiler with `STARM_NEWLIB` (configured in `starm-clang.cmake`)   
- C++ source code

The links point to headers that are not used by the compiler.

## In detail

1. C++ STL:  
   The correct include path for the C++ Standard Library is:  
   `...\st-arm-clang\21.1.1+st.7\lib\clang-runtimes\newlib\arm-none-eabi\include\c++\v1\`  
   however, clangd links `#include<math.h>` to:  
   `...\st-arm-clang\21.1.1+st.7\lib\clang-runtimes\arm-none-eabi\include\c++\v1\math.h`  
   so the `newlib` path branch is missing!

2. C Standard Library:  
   The `newlib` path component is also missing in the C Standard Library.  
   `#include<search.h>` should link to  
   `...\st-arm-clang\21.1.1+st.7\lib\clang-runtimes\newlib\arm-none-eabi\include\search.h`  
   but the following path is used instead:  
   `..\st-arm-clang\21.1.1+st.7\lib\clang-runtimes\arm-none-eabi\include\search.h`

3. Clang compiler built-in:  
   `#include<arm_acle.h>` is located in:  
   `...\st-arm-clang\21.1.1+st.7\lib\clang\21\include\arm_acle.h`  
   but is linked to:  
   `...\st-arm-clangd\21.1.0+st.2\lib\clang\21\include\arm_acle.h`  
   
## STM32Cube clangd log   

When looking at the ‘STM32Cube clangd’ log, several errors stand out:  
- System include extraction: driver clang not found in PATH
- `...\multilib.yaml:47:3: error: unknown key 'IncludeDirs'`  
- `...\multilib.yaml:21:1: error: multilib “arm-none-eabi/armv4t_exn_rtti_size” specifies undefined group name “stdlibs”
MultilibVersion: '1.0'`

It is also noticeable that the STM32CubeIDE Extension Pack Bundles Manager 
has  
- `st-arm-clang` 21.1.1+st.7 installed, but  
- `st-arm-clangd` 21.1.0+st.2 is installed.  

It is likely that different API versions are the cause of the multilib errors. 

## Workaround

The workaround solution is to configure clangd via **user-level `config.yaml` file placed in `...\[User]\AppData\Local\clangd\` by using the `patch_clangd.py` script.  
It creates the file or if still existing it adds the following content to the `config.yaml` file:

``` YAML
# --- BEGIN patch_clangd.py managed section ---
# Applies ARM toolchain include paths for ST ARM Clang projects.
# Managed automatically. Re-run patch_clangd.py to update paths.

CompileFlags:
  Add:
    - '--target=arm-none-eabi'
    - '-nostdinc'
    - '-nostdinc++'
    - '-nostdlibinc'
    - '-x'
    - 'c++'

# === Path config to headers:
# ATTENTION: Order of these entries is important!
# 1. C++ STL:
    - '-isystem'
    - >-
      D:\dev\Tools\ST\STM32CubeRepo\bundles\st-arm-clang\21.1.1+st.7\lib\clang-runtimes\newlib\arm-none-eabi\include\c++\v1
# 2. C headers (newlib):
    - '-isystem'
    - >-
      D:\dev\Tools\ST\STM32CubeRepo\bundles\st-arm-clang\21.1.1+st.7\lib\clang-runtimes\newlib\arm-none-eabi\include
# 3. clang builtin:
    - '-isystem'
    - >-
      D:\dev\Tools\ST\STM32CubeRepo\bundles\st-arm-clang\21.1.1+st.7\lib\clang\21\include
# --- END patch_clangd.py managed section ---
```

## CMake integration

To start the `patch_clangd.py` script, the `CMakeLists.txt` is the best place.
The following section added to `CMakeLists.txt` starts the script at each CMake reconfiguration.

```cmake
# Run clangd patch tool to ensure the user clangd config.yaml contains
# the include-path sections for ST ARM Clang toolchain headers.
# See: Scripts/Patch_clangd/README.md
find_package(Python3 COMPONENTS Interpreter REQUIRED QUIET)
if(DEFINED ENV{CUBE_BUNDLE_PATH})
  message("")
  message("!!! ATTENTION !!!")
  message("Check if a bug fix for 'wrong clangd path' is available!")
  message("If it is, please remove this workaround part in \\Application\\CMakeLists.txt file and the patch_clangd.py script in \\Scripts\\Patch_clangd folder.")
  message("Reference: https://community.st.com/t5/stm32cubeide-for-visual-studio/clangd-assumes-compiler-target-is-x86-64-pc-windows-msvc-for-cpp/m-p/855030#M1471")
  message("")

  execute_process(
        COMMAND ${Python3_EXECUTABLE}
            "${CMAKE_SOURCE_DIR}/Scripts/Patch_clangd/patch_clangd.py"
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
        RESULT_VARIABLE PATCH_CLANGD_RESULT
    )
    if(NOT PATCH_CLANGD_RESULT EQUAL 0)
        message(WARNING "patch_clangd: Python script failed with exit code ${PATCH_CLANGD_RESULT}.")
    endif()
else()
    message(WARNING "CUBE_BUNDLE_PATH is not set. clangd config update is skipped.")
endif()
```

The script is idempotent, so running it on each reconfigure is safe.


# Internal stuff

## Files in this folder

| File | Description |
|---|---|
| `patch_clangd.py` | Main patch tool (config.yaml-only) |
| `patch_clangd_test.py` | Functional Python test suite |
| `README.md` | This document |

## Platform support

`patch_clangd.py` runs on **Windows, Linux, and macOS**.

User config path resolution is fully platform-aware:

| Platform | User config location |
|---|---|
| Windows | `%LOCALAPPDATA%\clangd\config.yaml` (fallback: `%USERPROFILE%\AppData\Local\clangd\config.yaml`) |
| Linux | `$XDG_CONFIG_HOME/clangd/config.yaml` or `~/.config/clangd/config.yaml` |
| macOS | `~/Library/Application Support/clangd/config.yaml` |

## Prerequisites

- **Python 3.6 or newer** available in `PATH`.
- **Environment variable `CUBE_BUNDLE_PATH`** set to the ST bundles directory.

## Parameters

### Normal run

Run from project root:

```powershell
python ./Framework/Toolchain/Scripts/Patch_clangd/patch_clangd.py
```

Typical output when changes are applied:

```text
Updating clangd user config:
  PATCHED: C:\Users\<user>\AppData\Local\clangd\config.yaml
```

Typical output when nothing changes:

```text
Updating clangd user config:
  No changes needed.
```

### Dry-run mode

```powershell
python ./Framework/Toolchain/Scripts/Patch_clangd/patch_clangd.py --dry-run
```

Shows `WOULD_PATCH` and writes nothing.

### Verbose mode

```powershell
python ./Framework/Toolchain/Scripts/Patch_clangd/patch_clangd.py -v
```

Prints detailed diagnostics, including the resolved user config path.

### Force mode

```powershell
python ./Framework/Toolchain/Scripts/Patch_clangd/patch_clangd.py --force
```

Bypasses the check of `STARM_TOOLCHAIN_CONFIG` and updates config even for non-`STARM_NEWLIB` settings. See below.

### Test override for config path

```powershell
python ./Framework/Toolchain/Scripts/Patch_clangd/patch_clangd.py --config-path C:/temp/config.yaml
```

Primarily intended for test isolation and CI verification.

## Managed section behavior

For existing `config.yaml` files, the script behaves as follows:

1. If the managed section already exists: it updates only that section when paths changed.
2. If the managed section does not exist: it appends a new YAML document using `---`.
3. If no changes are needed: it does nothing.

The script keeps non-managed user content untouched.

## Backup behavior

Before changing an existing `config.yaml`, the script writes a numbered backup in the same folder:

```text
config.yaml_backup001
config.yaml_backup002
```

The backup is a byte-for-byte copy of the original file.

## cmake/starm-clang.cmake guard

If `cmake/starm-clang.cmake` contains:

```cmake
set(STARM_TOOLCHAIN_CONFIG "STARM_NEWLIB")
```

the script runs normally. For other values (for example `STARM_PICOLIBC`), the script prints a skip message and exits without changes.

If the cmake file is missing or the variable is unset, the script proceeds without guard checks.

Use `--force` to bypass the guard.

## How to run tests

```powershell
python ./Framework/Toolchain/Scripts/Patch_clangd/patch_clangd_test.py
```

The test suite uses isolated temporary directories and mock toolchain paths. No real user config is modified.

## Test cases

| # | Name | What is verified |
|---|---|---|
| 1 | Fresh config creation | New `config.yaml` is created with managed section and required flags. |
| 2 | Append, separator and backup | Existing user content is preserved, managed section is appended via `---`, and `config.yaml_backup001` is created. |
| 3 | Idempotency | Second run without changes prints `No changes needed.` |
| 4 | Managed path update | Existing managed section with stale paths is updated in place. |
| 5 | Dry-run mode | `WOULD_PATCH` is printed and no files are written. |
| 6 | Verbose mode | Diagnostic output includes resolved config path. |
| 7 | Backup numbering | Existing backup001 leads to backup002 on next change. |
| 8 | CMake guard NEWLIB | Script runs when `STARM_TOOLCHAIN_CONFIG` is `STARM_NEWLIB`. |
| 9 | CMake guard non-NEWLIB | Script skips for non-NEWLIB values. |
| 10 | Force flag | `--force` bypasses non-NEWLIB guard. |
| 11 | Toolchain missing | Invalid `CUBE_BUNDLE_PATH` returns error. |
| 12 | User content preserved | Non-managed content remains unchanged during managed updates. |

