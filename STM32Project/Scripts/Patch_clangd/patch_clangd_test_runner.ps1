#Requires -Version 5.1
<#
.SYNOPSIS
    Functional test suite for patch_clangd.py.

.DESCRIPTION
    Creates isolated test fixtures in <script-dir>/patch_clangd_test_output,
    runs the patcher against them, and reports PASS or FAIL for each scenario.
    The output folder is kept after the run for manual inspection.

.PARAMETER BundlePath
    Optional path to the STM32CubeIDE bundle directory.
    Defaults to the value of the CUBE_BUNDLE_PATH environment variable.
    If neither is available, a built-in fallback path is tried.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File patch_clangd_test_runner.ps1
#>
[CmdletBinding()]
param(
    [string]$BundlePath = ''
)

$ErrorActionPreference = 'Stop'

$scriptRoot  = Split-Path -Parent $MyInvocation.MyCommand.Path
$patchScript = Join-Path $scriptRoot 'patch_clangd.py'

# Python command resolution (cross-platform)
$pyCmd = if (Get-Command 'python3' -ErrorAction SilentlyContinue) { 'python3' } else { 'python' }

$testRoot = Join-Path $scriptRoot 'patch_clangd_test_output'
if (Test-Path $testRoot) { Remove-Item -Recurse -Force $testRoot }
New-Item -ItemType Directory -Path $testRoot | Out-Null

# Resolve bundle path: parameter > environment variable > mock fallback
if ($BundlePath -eq '') {
    $BundlePath = if ($env:CUBE_BUNDLE_PATH -and (Test-Path $env:CUBE_BUNDLE_PATH)) { $env:CUBE_BUNDLE_PATH }
                  else {
                      # Create an isolated mock toolchain for testing.
                      # lib/clang/99/include is required by the include_c path logic.
                      $mockBundle = Join-Path $testRoot 'mock_bundle'
                      $mockSt = Join-Path $mockBundle 'st-arm-clang/99.0.0+st.9'
                      New-Item -ItemType Directory -Path (Join-Path $mockSt 'lib/clang/99/include') -Force | Out-Null
                      $mockBundle
                  }
}
$bundle = $BundlePath
if (-not (Test-Path $bundle)) { throw "Bundle path not found: $bundle" }

$st = Join-Path $bundle 'st-arm-clang'
if (-not (Test-Path $st)) { throw "Toolchain path not found: $st" }

# Use zero-padded numeric sort so that version 21 ranks above version 9.
# This matches the tuple-based numeric comparison used in the Python script.
$latestObj = Get-ChildItem -Path $st -Directory |
    Sort-Object { ($_.Name -split '\D+' | Where-Object { $_ } | ForEach-Object { $_.PadLeft(10, '0') }) -join '.' } -Descending |
    Select-Object -First 1
if (-not $latestObj) { throw 'No st-arm-clang versions found' }
$latest = $latestObj.Name

# Detect the Clang built-in include version (the major-version subdirectory
# inside lib/clang/, e.g. "21" for toolchain "21.1.1+st.7").
# Use nested Join-Path for PowerShell 5.1 compatibility (no multi-child syntax).
$clangBasePath = Join-Path (Join-Path (Join-Path $st $latest) 'lib') 'clang'
$clangVerObj = if (Test-Path $clangBasePath) {
    Get-ChildItem -Path $clangBasePath -Directory -ErrorAction SilentlyContinue |
        Sort-Object { ($_.Name -split '\D+' | Where-Object { $_ } | ForEach-Object { $_.PadLeft(10, '0') }) -join '.' } -Descending |
        Select-Object -First 1
}
$clangVer = if ($clangVerObj) { $clangVerObj.Name } else { '' }

New-Item -ItemType Directory -Path (Join-Path $testRoot 'subA/subB') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $testRoot 'subC') -Force | Out-Null

@'
CompileFlags:
  Add:
    - '-ferror-limit=0'
    - '-Wno-implicit-int'
    - '-isystem'
    - >-
      d:\dev\Tools\ST\STM32CubeRepo\bundles\st-arm-clang\00.0.0+st.0\lib\clang\0\include
    - '-isystem'
    - >-
      d:\dev\Tools\ST\STM32CubeRepo\bundles\st-arm-clang\00.0.0+st.0\lib\clang-runtimes\newlib\arm-none-eabi\include\c++\v1\
Diagnostics:
  Suppress:
    - unused-includes
'@ | Set-Content -Path (Join-Path $testRoot '.clangd') -Encoding UTF8

@'
CompileFlags:
  Add:
    - '-isystem'
    - 'd:/dev/Tools/ST/STM32CubeRepo/bundles/st-arm-clang/00.0.0+st.0/lib/clang/0/include'
    - '-isystem'
    - 'd:/dev/Tools/ST/STM32CubeRepo/bundles/st-arm-clang/00.0.0+st.0/lib/clang-runtimes/newlib/arm-none-eabi/include/c++/v1'
  CompilationDatabase: ${CUBE_BUNDLE_PATH}/dummy
Diagnostics:
  Suppress:
    - unknown_typename
'@ | Set-Content -Path (Join-Path $testRoot 'subA/subB/.clangd') -Encoding UTF8

@'
CompileFlags:
  Add:
    - '--target=arm-none-eabi'
    - '-stdlib=libc++'
Diagnostics:
  Suppress:
    - typename_requires_specqual
'@ | Set-Content -Path (Join-Path $testRoot 'subC/.clangd') -Encoding UTF8

$env:CUBE_BUNDLE_PATH = $bundle
Push-Location $testRoot
$run1 = & $pyCmd $patchScript 2>&1
Pop-Location

$files = @(
    (Join-Path $testRoot '.clangd'),
    (Join-Path $testRoot 'subA/subB/.clangd'),
    (Join-Path $testRoot 'subC/.clangd')
)

$allContent = $files | ForEach-Object { Get-Content -Raw $_ }
$allOkFlags = $true
foreach ($c in $allContent) {
    if ($c -notmatch '--target=arm-none-eabi' -or $c -notmatch '-stdlib=libc\+\+' -or $c -notmatch '--sysroot' -or $c -notmatch '--config=newlib\.cfg') {
        $allOkFlags = $false
    }
}

$oldVersionAbsent = -not (($allContent -join "`n") -match '00\.0\.0\+st\.0')
$normalizedAll    = (($allContent -join "`n").ToLower().Replace('\', '/'))
# Both the Clang built-in C path and the newlib C++ path must use the new version.
$expectedNeedleC   = ("st-arm-clang/{0}/lib/clang/{1}/include" -f $latest.ToLower(), $clangVer)
$expectedNeedleCpp = ("st-arm-clang/{0}/lib/clang-runtimes/newlib/arm-none-eabi/include/c++/v1" -f $latest.ToLower())
$newVersionPresent = $normalizedAll.Contains($expectedNeedleC) -and $normalizedAll.Contains($expectedNeedleCpp)

$backupOk = $true
foreach ($f in $files) {
    $b = Join-Path (Split-Path $f -Parent) ((Split-Path $f -Leaf) + '_backup001')
    if (-not (Test-Path $b)) { $backupOk = $false }
}

$diagPreserved = ((Get-Content -Raw (Join-Path $testRoot '.clangd')) -match "Diagnostics:\s*\r?\n\s*Suppress:\s*\r?\n\s*- unused-includes") -and
    ((Get-Content -Raw (Join-Path $testRoot 'subA/subB/.clangd')) -match 'unknown_typename') -and
    ((Get-Content -Raw (Join-Path $testRoot 'subC/.clangd')) -match 'typename_requires_specqual')

# subA/subB had ${CUBE_BUNDLE_PATH} in CompilationDatabase — it must be replaced after patching.
$placeholderReplaced = (Get-Content -Raw (Join-Path $testRoot 'subA/subB/.clangd')) -notmatch '\$\{CUBE_BUNDLE_PATH\}'

$patchedLines = @($run1 | Where-Object { $_ -match '^  PATCHED:' }).Count
$lineCountRun1 = @($run1).Count
$minimalRun1 = ($lineCountRun1 -eq (2 + $patchedLines)) -and ($patchedLines -eq 3) -and (("$($run1[0])" -match '^Patching .clangd'))

Push-Location $testRoot
$run2 = & $pyCmd $patchScript 2>&1
Pop-Location
$patchedRun2 = @($run2 | Where-Object { $_ -match '^  PATCHED:' }).Count
$idempotent = ($patchedRun2 -eq 0) -and (@($run2).Count -eq 3) -and ($run2[1] -match 'No changes needed')

New-Item -ItemType Directory -Path (Join-Path $testRoot 'subD') -Force | Out-Null
@'
CompileFlags:
  Add:
    - '-Wno-implicit-int'
'@ | Set-Content -Path (Join-Path $testRoot 'subD/.clangd') -Encoding UTF8
Push-Location $testRoot
$run3 = & $pyCmd $patchScript --dry-run -v 2>&1
Pop-Location
$dryWouldPatchSubD = ($run3 | Where-Object { $_ -match '^\s+WOULD_PATCH.*subD.*\.clangd' }).Count -ge 1
$dryNoBackupSubD = -not (Test-Path (Join-Path $testRoot 'subD/.clangd_backup001'))
$verbosePrinted = ($run3 | Where-Object { $_ -match '^\[VERBOSE\]' }).Count -gt 0

# ---- --exclude flag test ---------------------------------------------------
# Create two .clangd files in separate subdirectories; run the patcher with
# --exclude pointing at one of them and verify only the other is patched.
$excDir = Join-Path $testRoot 'exc_test'
New-Item -ItemType Directory -Path (Join-Path $excDir 'keep') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $excDir 'skip') -Force | Out-Null
@'
CompileFlags:
  Add:
    - '-Wno-implicit-int'
'@ | Set-Content -Path (Join-Path $excDir 'keep/.clangd') -Encoding UTF8
@'
CompileFlags:
  Add:
    - '-Wno-implicit-int'
'@ | Set-Content -Path (Join-Path $excDir 'skip/.clangd') -Encoding UTF8
Push-Location $excDir
$runExclude = & $pyCmd $patchScript --exclude skip 2>&1
Pop-Location
$excludeKeptPatched = ($runExclude | Where-Object { $_ -match '^  PATCHED:' }).Count -eq 1
$excludeSkipNoBackup = -not (Test-Path (Join-Path $excDir 'skip/.clangd_backup001'))

# ---- Backup numbering test --------------------------------------------------
# When backup001 already exists the patcher must create backup002, not overwrite
# backup001.  Pre-create the backup001 file to simulate a previous run.
$bkpDir = Join-Path $testRoot 'bkp_test'
New-Item -ItemType Directory -Path $bkpDir -Force | Out-Null
@'
CompileFlags:
  Add:
    - '-Wno-implicit-int'
'@ | Set-Content -Path (Join-Path $bkpDir '.clangd') -Encoding UTF8
'previous backup' | Set-Content -Path (Join-Path $bkpDir '.clangd_backup001') -Encoding UTF8
Push-Location $bkpDir
$runBkp = & $pyCmd $patchScript 2>&1
Pop-Location
$backup002Created  = Test-Path (Join-Path $bkpDir '.clangd_backup002')
$backup001Untouched = (Get-Content -Raw (Join-Path $bkpDir '.clangd_backup001')).Trim() -eq 'previous backup'

# ---- cmake config check tests -----------------------------------------------
# Test: STARM_NEWLIB in cmake file  →  patching must proceed normally.
$cmakeDir = Join-Path $testRoot 'cmake_check_newlib/cmake'
New-Item -ItemType Directory -Path $cmakeDir -Force | Out-Null
@'
set(STARM_TOOLCHAIN_CONFIG "STARM_NEWLIB")
'@ | Set-Content -Path (Join-Path $cmakeDir 'starm-clang.cmake') -Encoding UTF8
@'
CompileFlags:
  Add:
    - '-Wno-implicit-int'
'@ | Set-Content -Path (Join-Path $testRoot 'cmake_check_newlib/.clangd') -Encoding UTF8
Push-Location (Join-Path $testRoot 'cmake_check_newlib')
$runNewlib = & $pyCmd $patchScript 2>&1
Pop-Location
$cmakeNewlibPatched = ($runNewlib | Where-Object { $_ -match '^  PATCHED:' }).Count -ge 1

# Test: STARM_PICOLIBC in cmake file  →  patching must be skipped entirely.
$cmakeDir2 = Join-Path $testRoot 'cmake_check_picolibc/cmake'
New-Item -ItemType Directory -Path $cmakeDir2 -Force | Out-Null
@'
set(STARM_TOOLCHAIN_CONFIG "STARM_PICOLIBC")
'@ | Set-Content -Path (Join-Path $cmakeDir2 'starm-clang.cmake') -Encoding UTF8
@'
CompileFlags:
  Add:
    - '-Wno-implicit-int'
'@ | Set-Content -Path (Join-Path $testRoot 'cmake_check_picolibc/.clangd') -Encoding UTF8
Push-Location (Join-Path $testRoot 'cmake_check_picolibc')
$runPicolibc = & $pyCmd $patchScript 2>&1
Pop-Location
$cmakePicolicbSkipped  = ($runPicolibc | Where-Object { $_ -match 'skipping' }).Count -ge 1
$cmakePicolicbNoBackup = -not (Test-Path (Join-Path $testRoot 'cmake_check_picolibc/.clangd_backup001'))

# ---- --force flag test ------------------------------------------------------
# With --force the patcher must run even when STARM_TOOLCHAIN_CONFIG is not STARM_NEWLIB.
$forceDir = Join-Path $testRoot 'force_test'
New-Item -ItemType Directory -Path (Join-Path $forceDir 'cmake') -Force | Out-Null
@'
set(STARM_TOOLCHAIN_CONFIG "STARM_PICOLIBC")
'@ | Set-Content -Path (Join-Path $forceDir 'cmake/starm-clang.cmake') -Encoding UTF8
@'
CompileFlags:
  Add:
    - '-Wno-implicit-int'
'@ | Set-Content -Path (Join-Path $forceDir '.clangd') -Encoding UTF8
Push-Location $forceDir
$runForce = & $pyCmd $patchScript --force 2>&1
Pop-Location
$forcePatched = ($runForce | Where-Object { $_ -match '^  PATCHED:' }).Count -ge 1

# Collect individual results for the summary
$results = [ordered]@{
    'recursive_and_patched_lines' = @{
        pass   = ($patchedLines -eq 3)
        desc   = 'Recursive discovery — Finds and patches all .clangd files in all subdirectories.'
        detail = "$patchedLines / 3 files patched"
    }
    'backup_created_001'          = @{
        pass   = $backupOk
        desc   = 'Backup creation — A .clangd_backup001 file is written next to each patched file.'
        detail = "All 3 backups present: $backupOk"
    }
    'flags_added'                 = @{
        pass   = $allOkFlags
        desc   = 'Required flags — Adds --target=arm-none-eabi, -stdlib=libc++, --sysroot and --config=newlib.cfg to every file.'
        detail = "All flags present in all files: $allOkFlags"
    }
    'placeholder_replaced'        = @{
        pass   = $placeholderReplaced
        desc   = 'Placeholder replaced — ${CUBE_BUNDLE_PATH} in the file is substituted with the resolved bundle path.'
        detail = "Placeholder absent after patch: $placeholderReplaced"
    }
    'version_changed'             = @{
        pass   = ($oldVersionAbsent -and $newVersionPresent)
        desc   = 'Version update — Replaces stale C and C++ include paths with the latest installed versions.'
        detail = "Old paths gone: $oldVersionAbsent  |  $latest / clang $clangVer present: $newVersionPresent"
    }
    'minimal_output_run1'         = @{
        pass   = $minimalRun1
        desc   = 'Minimal output — Prints only one header line plus one PATCHED line per file, nothing else.'
        detail = "$lineCountRun1 lines printed (expected: $( 2 + $patchedLines ))"
    }
    'idempotent_run2'             = @{
        pass   = $idempotent
        desc   = 'Idempotency — A second run on already-patched files produces zero changes.'
        detail = "$patchedRun2 files re-patched, 'No changes needed.' printed: $($run2[1] -match 'No changes needed')"
    }
    'dryrun_no_write'             = @{
        pass   = ($dryWouldPatchSubD -and $dryNoBackupSubD)
        desc   = 'Dry-run mode — WOULD_PATCH is logged, but no file is written and no backup is created.'
        detail = "WOULD_PATCH logged: $dryWouldPatchSubD  |  No backup created: $dryNoBackupSubD"
    }
    'verbose_mode'                = @{
        pass   = $verbosePrinted
        desc   = 'Verbose mode — The -v flag prints [VERBOSE] diagnostic lines.'
        detail = "[VERBOSE] lines present: $verbosePrinted"
    }
    'diagnostics_preserved'       = @{
        pass   = $diagPreserved
        desc   = 'Diagnostics preserved — Existing Diagnostics/Suppress entries survive patching unchanged.'
        detail = "All Suppress entries intact: $diagPreserved"
    }
    'cmake_newlib_proceeds'       = @{
        pass   = $cmakeNewlibPatched
        desc   = 'cmake guard (NEWLIB) — STARM_TOOLCHAIN_CONFIG=STARM_NEWLIB in starm-clang.cmake causes patching to run.'
        detail = ".clangd patched: $cmakeNewlibPatched"
    }
    'cmake_picolibc_skipped'      = @{
        pass   = ($cmakePicolicbSkipped -and $cmakePicolicbNoBackup)
        desc   = 'cmake guard (other) — STARM_TOOLCHAIN_CONFIG=STARM_PICOLIBC in starm-clang.cmake causes patching to be skipped.'
        detail = "Skip message printed: $cmakePicolicbSkipped  |  No backup created: $cmakePicolicbNoBackup"
    }
    'force_flag'                  = @{
        pass   = $forcePatched
        desc   = '--force flag — Patching runs even when STARM_TOOLCHAIN_CONFIG is not STARM_NEWLIB.'
        detail = ".clangd patched despite PICOLIBC config: $forcePatched"
    }
    'exclude_flag'                = @{
        pass   = ($excludeKeptPatched -and $excludeSkipNoBackup)
        desc   = '--exclude flag — Files whose path matches --exclude are skipped; others are still patched.'
        detail = "keep/ patched: $excludeKeptPatched  |  skip/ backup absent: $excludeSkipNoBackup"
    }
    'backup_numbering'            = @{
        pass   = ($backup002Created -and $backup001Untouched)
        desc   = 'Backup numbering — When backup001 already exists, the patcher creates backup002 and leaves backup001 untouched.'
        detail = "backup002 created: $backup002Created  |  backup001 unchanged: $backup001Untouched"
    }
}

""
Write-Host "=== patch_clangd test results  (toolchain $latest / clang $clangVer) ===" -ForegroundColor Cyan
""

$idx = 0
foreach ($name in $results.Keys) {
    $idx++
    $r     = $results[$name]
    $color = if ($r.pass) { 'Green' } else { 'Red' }
    $label = if ($r.pass) { 'PASS'  } else { 'FAIL' }

    # Split desc at " — ": title goes to the header line, body to Description.
    $descParts = $r.desc -split ' — ', 2
    $descTitle = $descParts[0]
    $descBody  = if ($descParts.Count -eq 2) { $descParts[1] } else { $r.desc }

    Write-Host ("Test {0,2}/{1} - {2}:" -f $idx, $results.Count, $descTitle) -ForegroundColor Cyan
    Write-Host ("  Description : $descBody")
    Write-Host ("  Detail      : $($r.detail)")
    Write-Host ("  Result      : $label") -ForegroundColor $color
    ""
}

$passCount = ($results.Values | Where-Object { $_.pass }).Count
$total     = $results.Count
$summaryColor = if ($passCount -eq $total) { 'Green' } else { 'Red' }

Write-Host ("--- {0} / {1} passed ---" -f $passCount, $total) -ForegroundColor $summaryColor
if ($passCount -lt $total) {
    ""
    Write-Host "--- Verbose output for failed runs ---" -ForegroundColor Yellow
    "RUN1:"; $run1
    "RUN2:"; $run2
    "RUN3:"; $run3
    "RUN_NEWLIB:"; $runNewlib
    "RUN_PICOLIBC:"; $runPicolibc
}

""
$answer = Read-Host "Delete test output folder and __pycache__? [y/N]"
if ($answer -match '^[Yy]') {
    Remove-Item -Recurse -Force $testRoot
    Write-Host "Deleted: $testRoot" -ForegroundColor DarkGray
    $pycache = Join-Path $scriptRoot '__pycache__'
    if (Test-Path $pycache) {
        Remove-Item -Recurse -Force $pycache
        Write-Host "Deleted: $pycache" -ForegroundColor DarkGray
    }
} else {
    Write-Host "Kept for inspection: $testRoot" -ForegroundColor DarkGray
}
