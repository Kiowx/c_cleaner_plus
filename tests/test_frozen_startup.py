"""Regression coverage for DLL resolution in the Windows release bundle."""
import ast
import os
import re
import unittest
from pathlib import Path


class FrozenDependencyTests(unittest.TestCase):
    def test_bundle_does_not_shadow_windows_icu_with_poppler_dlls(self):
        spec = Path(__file__).resolve().parents[1] / "c_cleaner_plus.spec"
        tree = ast.parse(spec.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "should_keep")
        namespace = {"os": os, "re": re, "exclude_keywords": []}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(spec), "exec"), namespace)
        keep = namespace["should_keep"]
        for name in ("icuuc.dll", "icuin.dll", "icu.dll", "icudt78.dll"):
            with self.subTest(name=name):
                self.assertFalse(keep(name, str(Path(r"C:\tools\poppler\bin") / name)))
        self.assertTrue(keep("PySide6/Qt6Core.dll", r"C:\venv\PySide6\Qt6Core.dll"))

    def test_release_pipeline_launches_the_frozen_application_before_archiving(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/smoke_frozen.py --bundle dist/c_cleaner_plus", workflow)
        self.assertLess(workflow.index("python tools/smoke_frozen.py"), workflow.index("Compress-Archive"))


if __name__ == "__main__":
    unittest.main()
