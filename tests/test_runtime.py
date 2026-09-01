#!/usr/bin/env python3
"""Unit tests for launcher discovery and the console guard.

These cannot be reached end to end: discovery is what decides *which* process the
end-to-end tests would run, and the console guard only misbehaves on a stream whose
encoding the test has to choose.
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "local-delegate" / "scripts"

spec = importlib.util.spec_from_file_location("runtime", SCRIPTS / "runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class LauncherOverrideTest(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get(runtime.CLAUDE_BIN_ENV)
        self.addCleanup(self.restore)

    def restore(self):
        if self.saved is None:
            os.environ.pop(runtime.CLAUDE_BIN_ENV, None)
        else:
            os.environ[runtime.CLAUDE_BIN_ENV] = self.saved

    def test_a_multi_word_launcher_survives_as_a_list(self):
        """An npm install is `node .../cli.js`, which a single path cannot express."""
        os.environ[runtime.CLAUDE_BIN_ENV] = '"/usr/bin/node" "/opt/claude/cli.js"'
        self.assertEqual(runtime.claude_command(),
                         ["/usr/bin/node", "/opt/claude/cli.js"])

    def test_a_plain_path_is_a_one_element_command(self):
        os.environ[runtime.CLAUDE_BIN_ENV] = "/usr/local/bin/claude"
        self.assertEqual(runtime.claude_command(), ["/usr/local/bin/claude"])


class VersionedDiscoveryTest(unittest.TestCase):
    """The desktop app installs under a directory that changes on every update."""

    def test_the_highest_version_wins(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        for v in ("2.1.9", "2.1.247", "2.1.13"):
            d = tmp / v
            d.mkdir()
            (d / "claude").write_text("", encoding="utf-8")
        picked = max(((runtime._version_key(p), p) for p in tmp.iterdir()),
                     key=lambda t: t[0])[1]
        self.assertEqual(picked.name, "2.1.247",
                         "2.1.247 must beat 2.1.13 and 2.1.9, not sort as a string")

    def test_an_unversioned_launcher_does_not_break_the_sort(self):
        """A bare launcher beside versioned folders used to raise TypeError:
        its sort key was a list of str against the versions' list of int."""
        keys = [[0], runtime._version_key(Path("2.1.247"))]
        keys.sort()                                  # must not raise
        self.assertEqual(keys[-1], [2, 1, 247])


class ConsoleGuardTest(unittest.TestCase):
    def test_non_ascii_survives_a_cp1252_stream(self):
        """The box-drawing characters killed the installer before it downloaded
        anything; after the guard they may degrade, but must not raise."""
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        saved = sys.stdout
        sys.stdout = buf
        try:
            runtime.use_utf8() if hasattr(runtime, "use_utf8") else runtime.fix_console()
            print("══ group code — 2 task(s) ══ ↓ ⚠")
        finally:
            sys.stdout = saved
        # Reaching here without UnicodeEncodeError is the assertion.


if __name__ == "__main__":
    unittest.main()
