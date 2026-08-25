"""The VERSION file and gitcore.VERSION must agree: the release flow bumps both,
and the formula's `test do` asserts on the CLI output."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_script import load_script  # noqa: E402

load_script("wt", "wt_mod")  # puts src/_common on sys.path
import gitcore  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class VersionTest(unittest.TestCase):
    def test_version_file_matches_gitcore(self):
        with open(os.path.join(ROOT, "VERSION")) as f:
            self.assertEqual(f.read().strip(), gitcore.VERSION)


if __name__ == "__main__":
    unittest.main()
