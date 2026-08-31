"""Tests for stack's state file: loading, the deleted-branch prune, and that
the prune is written back rather than recomputed on every command.

Run: python3 -m unittest discover tests

The prune used to happen in memory only, so a read-only command re-detected and
re-warned about the same deleted branch forever. These pin the repair landing on
disk on the first load that notices it.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import FakeGit  # noqa: E402
from load_stack import load_stack  # noqa: E402

stack = load_stack()
import gitcore  # noqa: E402


def chain(*commits):
    anc = {}
    for i, c in enumerate(commits):
        anc[c] = list(reversed(commits[:i]))
    return anc


class StateTest(unittest.TestCase):
    """A three-branch stack (main → a → b → c) whose middle branch can be made
    to look deleted by leaving it out of branch_set."""

    def setUp(self):
        gitcore.set_quiet(False)  # cmd_complete leaves it set on the shared module
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = os.path.join(self.tmp.name, "stack")
        os.makedirs(d)
        real_stack_dir = stack.stack_dir
        stack.stack_dir = lambda git: d
        self.addCleanup(setattr, stack, "stack_dir", real_stack_dir)
        self.dir = d

        self.tips = {"main": "m0", "a": "a1", "b": "b1", "c": "c1"}
        self.git = FakeGit(
            trunk_name="main",
            branch_tips=self.tips,
            ancestors=chain("m0", "a1", "b1", "c1"),
        )
        self.state = {
            "a": {"parent": "main", "base": "m0"},
            "b": {"parent": "a", "base": "a1"},
            "c": {"parent": "b", "base": "b1"},
        }

    def write(self, brs):
        with open(os.path.join(self.dir, "branches.json"), "w") as f:
            json.dump({"version": 2, "branches": brs}, f)

    def on_disk(self):
        with open(os.path.join(self.dir, "branches.json")) as f:
            return json.load(f)["branches"]

    def delete_branch(self, name):
        """Drop a branch from the local refs, leaving state naming it."""
        self.git._branches.discard(name)

    def flush(self):
        """load_and_flush, returning (brs, stderr)."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            brs = stack.load_and_flush(self.git)
        return brs, err.getvalue()

    def test_prune_is_written_back(self):
        """The regression: pruning 'b' must land on disk, not just in memory."""
        self.write(self.state)
        self.delete_branch("b")
        brs, _ = self.flush()
        self.assertNotIn("b", brs)
        self.assertNotIn("b", self.on_disk())

    def test_prune_warns_only_once(self):
        self.write(self.state)
        self.delete_branch("b")
        _, first = self.flush()
        _, second = self.flush()
        self.assertIn("pruned deleted branch 'b'", first)
        self.assertEqual(second, "")

    def test_reparenting_survives_the_round_trip(self):
        """'c' inherits 'b''s parent, and keeps its base pointing at b's old tip."""
        self.write(self.state)
        self.delete_branch("b")
        self.flush()
        disk = self.on_disk()
        self.assertEqual(disk["c"]["parent"], "a")
        self.assertEqual(disk["c"]["base"], "b1")

    def test_clean_state_is_not_rewritten(self):
        self.write(self.state)
        path = os.path.join(self.dir, "branches.json")
        before = os.stat(path).st_mtime_ns
        brs, err = self.flush()
        self.assertEqual(sorted(brs), ["a", "b", "c"])
        self.assertEqual(err, "")
        self.assertEqual(os.stat(path).st_mtime_ns, before)

    def test_locked_variant_does_not_deadlock(self):
        """The mutating commands call this with the lock already held."""
        self.write(self.state)
        self.delete_branch("b")
        with stack.state_lock(self.git):
            brs = stack.load_and_flush_locked(self.git)
        self.assertNotIn("b", brs)
        self.assertNotIn("b", self.on_disk())

    def test_completion_neither_writes_nor_warns(self):
        """__complete runs on every TAB: it stays silent and read-only."""
        self.write(self.state)
        self.delete_branch("b")
        path = os.path.join(self.dir, "branches.json")
        before = os.stat(path).st_mtime_ns
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            stack.cmd_complete(self.git, None, ["stack-branches"])
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(os.stat(path).st_mtime_ns, before)
        self.assertEqual(out.getvalue().split(), ["a", "c"])


if __name__ == "__main__":
    unittest.main()
