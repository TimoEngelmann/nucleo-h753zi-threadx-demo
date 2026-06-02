#!/usr/bin/env python3
"""
Functional tests for patch_clangd.py.

The tests focus on the current product behavior only:
- user-level config.yaml management
- managed section append/update/idempotency
- backup creation and numbering
- cmake STARM_TOOLCHAIN_CONFIG guard behavior
- toolchain path detection behavior
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MANAGED_BEGIN = "# --- BEGIN patch_clangd.py managed section ---"
MANAGED_END = "# --- END patch_clangd.py managed section ---"


class PatchClangdTests(unittest.TestCase):
    """Functional integration tests for patch_clangd.py."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="patch_clangd_test_"))
        self.project_dir = self.temp_dir / "project"
        self.project_dir.mkdir(parents=True, exist_ok=True)

        self.bundle_dir = self.temp_dir / "bundles"
        self.toolchain_dir = self.bundle_dir / "st-arm-clang" / "21.1.1+st.7"

        # Build the minimal mock toolchain tree required by the patcher.
        (self.toolchain_dir / "lib" / "clang" / "21" / "include").mkdir(parents=True, exist_ok=True)
        (self.toolchain_dir / "lib" / "clang-runtimes" / "newlib" / "arm-none-eabi" / "include" / "c++" / "v1").mkdir(
            parents=True,
            exist_ok=True,
        )

        self.config_path = self.temp_dir / "user" / "clangd" / "config.yaml"
        self.script_path = Path(__file__).with_name("patch_clangd.py")

        self.base_env = os.environ.copy()
        self.base_env["CUBE_BUNDLE_PATH"] = str(self.bundle_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_script(self, extra_args=None, env=None):
        """Run patch_clangd.py and return CompletedProcess."""
        args = [sys.executable, str(self.script_path)]
        if extra_args:
            args.extend(extra_args)

        final_env = dict(self.base_env)
        if env:
            final_env.update(env)

        return subprocess.run(
            args,
            cwd=self.project_dir,
            env=final_env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _run_with_test_config(self, extra_args=None, env=None):
        """Run patch_clangd.py with --config-path redirected to temp location."""
        args = ["--config-path", str(self.config_path)]
        if extra_args:
            args.extend(extra_args)
        return self._run_script(extra_args=args, env=env)

    def _write_toolchain_config(self, value: str) -> None:
        cmake_dir = self.project_dir / "cmake"
        cmake_dir.mkdir(parents=True, exist_ok=True)
        (cmake_dir / "starm-clang.cmake").write_text(
            f'set(STARM_TOOLCHAIN_CONFIG "{value}")\n',
            encoding="utf-8",
        )

    def test_fresh_config_created(self) -> None:
        result = self._run_with_test_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.config_path.exists())

        content = self.config_path.read_text(encoding="utf-8")
        self.assertIn(MANAGED_BEGIN, content)
        self.assertIn(MANAGED_END, content)
        self.assertIn("--target=arm-none-eabi", content)
        self.assertIn("-nostdinc", content)
        self.assertIn("-nostdinc++", content)
        self.assertIn("-nostdlibinc", content)
        self.assertIn("- '-x'", content)
        self.assertIn("- 'c++'", content)

        normalized = content.replace("\\", "/")
        idx_hdr_cpp = normalized.find("# 1. C++ STL:")
        idx_hdr_newlib = normalized.find("# 2. C headers (newlib):")
        idx_hdr_clang = normalized.find("# 3. clang builtin:")

        self.assertNotEqual(idx_hdr_cpp, -1)
        self.assertNotEqual(idx_hdr_newlib, -1)
        self.assertNotEqual(idx_hdr_clang, -1)
        self.assertTrue(idx_hdr_cpp < idx_hdr_newlib < idx_hdr_clang)

        self.assertIn("# 1. C++ STL:\n    - '-isystem'\n    - >-\n      ", normalized)
        self.assertIn("lib/clang-runtimes/newlib/arm-none-eabi/include/c++/v1", normalized)
        self.assertIn("# 2. C headers (newlib):\n    - '-isystem'\n    - >-\n      ", normalized)
        self.assertIn("lib/clang-runtimes/newlib/arm-none-eabi/include\n# 3. clang builtin:", normalized)
        self.assertIn("lib/clang/21/include", normalized)
        self.assertIn("PATCHED", result.stdout)

    def test_append_existing_via_separator_and_backup(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            "Diagnostics:\n  Suppress:\n    - unused-includes\n",
            encoding="utf-8",
        )

        result = self._run_with_test_config()
        self.assertEqual(result.returncode, 0, result.stderr)

        content = self.config_path.read_text(encoding="utf-8")
        self.assertIn("Diagnostics:\n  Suppress:\n    - unused-includes", content)
        self.assertIn("\n---\n", content)
        self.assertIn(MANAGED_BEGIN, content)
        self.assertTrue((self.config_path.parent / "config.yaml_backup001").exists())

    def test_idempotent_second_run_no_change(self) -> None:
        first = self._run_with_test_config()
        self.assertEqual(first.returncode, 0, first.stderr)

        before = self.config_path.read_bytes()
        second = self._run_with_test_config()
        after = self.config_path.read_bytes()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(before, after)
        self.assertIn("No changes needed", second.stdout)
        backups = list(self.config_path.parent.glob("config.yaml_backup*"))
        self.assertEqual(len(backups), 0)

    def test_update_existing_managed_block_when_paths_change(self) -> None:
        create = self._run_with_test_config()
        self.assertEqual(create.returncode, 0, create.stderr)

        stale_content = self.config_path.read_text(encoding="utf-8").replace("21.1.1+st.7", "00.0.0+st.0")
        self.config_path.write_text(stale_content, encoding="utf-8")

        update = self._run_with_test_config()
        self.assertEqual(update.returncode, 0, update.stderr)

        content = self.config_path.read_text(encoding="utf-8")
        self.assertIn("21.1.1+st.7", content)
        self.assertNotIn("00.0.0+st.0", content)
        self.assertTrue((self.config_path.parent / "config.yaml_backup001").exists())

    def test_dry_run_makes_no_changes(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        initial = "Diagnostics:\n  Suppress:\n    - unknown_typename\n"
        self.config_path.write_text(initial, encoding="utf-8")

        result = self._run_with_test_config(extra_args=["--dry-run"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WOULD_PATCH", result.stdout)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), initial)
        self.assertFalse((self.config_path.parent / "config.yaml_backup001").exists())

    def test_verbose_mode_reports_resolved_path(self) -> None:
        result = self._run_with_test_config(extra_args=["-v"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[VERBOSE]", result.stdout)
        self.assertIn("Config path", result.stdout)

    def test_backup_numbering(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text("Diagnostics:\n  Suppress:\n    - a\n", encoding="utf-8")

        first = self._run_with_test_config()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((self.config_path.parent / "config.yaml_backup001").exists())

        stale_content = self.config_path.read_text(encoding="utf-8").replace("21.1.1+st.7", "20.0.0+st.1")
        self.config_path.write_text(stale_content, encoding="utf-8")

        second = self._run_with_test_config()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue((self.config_path.parent / "config.yaml_backup002").exists())

    def test_cmake_guard_newlib_proceeds(self) -> None:
        self._write_toolchain_config("STARM_NEWLIB")
        result = self._run_with_test_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PATCHED", result.stdout)
        self.assertTrue(self.config_path.exists())

    def test_cmake_guard_non_newlib_skips(self) -> None:
        self._write_toolchain_config("STARM_PICOLIBC")
        result = self._run_with_test_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipping", result.stdout.lower())
        self.assertFalse(self.config_path.exists())

    def test_force_overrides_non_newlib_guard(self) -> None:
        self._write_toolchain_config("STARM_PICOLIBC")
        result = self._run_with_test_config(extra_args=["--force"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PATCHED", result.stdout)
        self.assertTrue(self.config_path.exists())

    def test_toolchain_missing_fails(self) -> None:
        bad_env = {"CUBE_BUNDLE_PATH": str(self.temp_dir / "missing_bundles")}
        result = self._run_with_test_config(env=bad_env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR:", result.stderr)

    def test_unmanaged_user_content_stays_unchanged(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        user_prefix = "Diagnostics:\n  Suppress:\n    - user-rule\n"

        # Build a stale managed section by first creating one and then modifying it.
        created = self._run_with_test_config()
        self.assertEqual(created.returncode, 0, created.stderr)
        managed = self.config_path.read_text(encoding="utf-8").replace("21.1.1+st.7", "19.9.9+st.0")
        self.config_path.write_text(f"{user_prefix}\n---\n{managed}", encoding="utf-8")

        updated = self._run_with_test_config()
        self.assertEqual(updated.returncode, 0, updated.stderr)

        final = self.config_path.read_text(encoding="utf-8")
        self.assertTrue(final.startswith(user_prefix))
        self.assertIn("21.1.1+st.7", final)
        self.assertNotIn("19.9.9+st.0", final)


if __name__ == "__main__":
    unittest.main(verbosity=2)
