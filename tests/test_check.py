import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class CheckTests(unittest.TestCase):
    def test_make_check_only_runs_environment_checker(self):
        with tempfile.TemporaryDirectory() as temp:
            bindir = Path(temp) / "bin"
            bindir.mkdir()
            calls = Path(temp) / "calls"
            python = bindir / "python3"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
            python.chmod(0o755)
            node = bindir / "node"
            node.write_text('#!/bin/sh\nprintf "node called\\n" >> "$CALLS"\nexit 99\n')
            node.chmod(0o755)
            env = dict(os.environ, CALLS=str(calls), PATH=str(bindir) + os.pathsep + os.environ["PATH"])
            subprocess.run(["make", "check"], cwd=ROOT, env=env, check=True, capture_output=True)
            self.assertEqual(calls.read_text().splitlines(), ["scripts/check_environment.py"])

    def test_run_target_works_when_a_file_named_run_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            (fixture / "run").touch()
            bindir = fixture / "bin"
            bindir.mkdir()
            calls = fixture / "calls"
            python = bindir / "python3"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
            python.chmod(0o755)
            env = dict(os.environ, CALLS=str(calls), PATH=str(bindir) + os.pathsep + os.environ["PATH"])
            subprocess.run(["make", "-f", str(ROOT / "Makefile"), "run"], cwd=fixture,
                           env=env, check=True, capture_output=True)
            self.assertEqual(calls.read_text().splitlines(), ["overlay_server.py"])

    def test_missing_runtime_module_fails_check(self):
        path = ROOT / "scripts/check_environment.py"
        spec = importlib.util.spec_from_file_location("check_environment", path)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        actual_find_spec = importlib.util.find_spec

        def find_spec(name):
            return None if name == "json" else actual_find_spec(name)

        stderr = io.StringIO()
        with patch.object(checker.importlib.util, "find_spec", side_effect=find_spec):
            with contextlib.redirect_stderr(stderr):
                result = checker.main()
        self.assertEqual(result, 1)
        self.assertIn("standard-library modules missing: json", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
