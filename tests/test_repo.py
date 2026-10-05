"""The house rules from CONTRIBUTING.md that a script can check."""

import ast
import subprocess
import sys
import unittest

from helpers import ROOT

MACROS = sorted(d for d in ROOT.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != "tests" and any(d.glob("*.py")))


class Repo(unittest.TestCase):
    def test_every_macro_has_a_readme_and_a_row_in_the_table(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for macro in MACROS:
            with self.subTest(macro=macro.name):
                self.assertTrue((macro / "README.md").exists(), "each macro folder needs a README.md")
                self.assertIn(f"| [{macro.name}]({macro.name}) |", readme, "add the folder to the root README table")

    def test_issue_form_lists_every_macro(self):
        form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
        for macro in MACROS:
            with self.subTest(macro=macro.name):
                self.assertIn(f"        - {macro.name}\n", form, "add the folder to the bug report's macro list")

    @unittest.skipUnless(hasattr(sys, "stdlib_module_names"), "needs Python 3.10+")
    def test_scripts_use_only_the_standard_library(self):
        for script in (s for macro in MACROS for s in macro.glob("*.py")):
            tree = ast.parse(script.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module]
                for name in names:
                    with self.subTest(script=script.name, module=name):
                        self.assertIn(name.split(".")[0], sys.stdlib_module_names)

    def test_no_generated_files_are_committed(self):
        try:
            files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        generated = [f for f in files if f.endswith((".nc", ".tools")) or "/tools/" in f]
        self.assertEqual(generated, [], "generated files belong in the folder's .gitignore")


if __name__ == "__main__":
    unittest.main()
