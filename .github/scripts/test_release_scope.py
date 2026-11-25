"""Git-level regression tests for the main release policy."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

POLICY = Path(__file__).with_name("release_scope.py")


class ReleaseScopeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "test@localhost")
        self.git("config", "commit.gpgsign", "false")
        self.write("implementation/experiments/planner.py", "print(1)")
        self.write("implementation/evaluation/legacy.csv", "legacy")
        self.commit()
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def check(self):
        return subprocess.run([sys.executable, "-I", str(POLICY), self.base, "HEAD"],
                              cwd=self.repo, capture_output=True, text=True)

    def test_code_maintenance_and_legacy_deletion_pass(self):
        self.write("implementation/tests/test_planner.py", "assert True")
        self.write("implementation/environment/requirements-control.txt", "numpy")
        self.write("README.md", "Run the tests")
        (self.repo / "implementation/evaluation/legacy.csv").unlink()
        self.commit()
        self.assertEqual(self.check().returncode, 0)

    def test_artifacts_and_edited_legacy_results_are_all_reported(self):
        files = ["implementation/evaluation/results/latest.py", "paired.csv",
                 "implementation/experiments/report.md", "implementation/evaluation/legacy.csv",
                 "implementation/experiments/request.json", "experiment.mp4"]
        for name in files:
            self.write(name, "snapshot")
        self.commit()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        for name in files:
            self.assertIn(name, result.stdout)

    def test_rename_into_output_and_link_under_code_are_rejected(self):
        self.git("mv", "implementation/experiments/planner.py", "report.csv")
        (self.repo / "implementation/experiments/link.py").symlink_to("../../report.csv")
        self.commit()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("report.csv", result.stdout)
        self.assertIn("link.py", result.stdout)

    def test_changes_only_on_base_branch_do_not_count_against_pr(self):
        self.git("checkout", "-qb", "release")
        self.write("implementation/experiments/planner.py", "print(2)")
        self.commit()
        self.git("checkout", "-qb", "advanced-base", self.base)
        self.write("existing-on-base.csv", "archives")
        self.commit()
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "release")
        self.assertEqual(self.check().returncode, 0)


if __name__ == "__main__":
    unittest.main()
