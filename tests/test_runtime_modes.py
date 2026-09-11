from pathlib import Path
import subprocess
import unittest

from ops.release_manager import ReleaseError, validate_version


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeModeTests(unittest.TestCase):
    def test_release_version_accepts_safe_names(self):
        for version in ("v1.0.0", "2026.09.09", "release_1-rc1"):
            with self.subTest(version=version):
                self.assertEqual(validate_version(version), version)

    def test_release_version_rejects_unsafe_names(self):
        for version in ("../v1", "production/v1", "", ".hidden", "v1 2"):
            with self.subTest(version=version):
                with self.assertRaises(ReleaseError):
                    validate_version(version)

    def test_development_launcher_reports_project_local_worktree(self):
        completed = subprocess.run(
            ["bash", "ops/run-development", "--check"],
            cwd=PROJECT_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        expected = PROJECT_ROOT / ".runtime" / "development" / "worktree"
        self.assertIn("mode=development", completed.stdout)
        self.assertIn(str(expected), completed.stdout)

    def test_daily_application_entrypoint_is_separate_from_stable_shim(self):
        shim = (PROJECT_ROOT / "my_scripts" / "roguetrader1.py").read_text(
            encoding="utf-8"
        )
        application = PROJECT_ROOT / "my_scripts" / "daily_analysis.py"
        self.assertTrue(application.is_file())
        self.assertIn("os.execv", shim)
        self.assertNotIn("RogueTraderGraph", shim)

    def test_version_launcher_checks_frozen_environment(self):
        launcher = (PROJECT_ROOT / "ops" / "run-version").read_text(encoding="utf-8")
        self.assertIn("uv sync", launcher)
        self.assertIn("--frozen", launcher)
        self.assertIn("--check", launcher)

    def test_production_panel_uses_active_release_and_production_state(self):
        launcher = (PROJECT_ROOT / "ops" / "production-control-panel-service").read_text(
            encoding="utf-8"
        )
        self.assertIn("production/current", launcher)
        self.assertIn("production/control-panel", launcher)
        self.assertIn("ROGUETRADER_RUNTIME_MODE=production", launcher)
        self.assertNotIn("development/worktree", launcher)


if __name__ == "__main__":
    unittest.main()
