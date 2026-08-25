"""Tests for `wt current` and the --json flag parsing both list and current share.

`current` backs the Sublime status bar, which polls it for arbitrary files, so
the notable behavior is what it does OUTSIDE a wt-managed worktree: emit null
fields under --json rather than dying.
"""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeWtCtx  # noqa: E402
from load_script import load_script  # noqa: E402

wt = load_script("wt", "wt_mod")


def run(fn, ctx, argv):
    """→ (stdout, exit code or None). die() raises SystemExit."""
    out, code = io.StringIO(), None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            fn(ctx, argv)
    except SystemExit as e:
        code = e.code
    return out.getvalue(), code


def ctx_in(name):
    return FakeWtCtx(
        worktrees=[("repo", "main", "", 0, 0, 0, True)],
        current=name,
        branch_by_path={"/repo": "main"},
    )


class CurrentTest(unittest.TestCase):
    def test_json_inside_a_worktree(self):
        out, code = run(wt.cmd_current, ctx_in("repo"), ["--json"])
        self.assertIsNone(code)
        self.assertEqual(json.loads(out), {
            "worktree": "repo", "branch": "main", "path": "/repo",
            "root": "/repo", "layout": "standard",
        })

    def test_json_outside_a_worktree_reports_nulls_and_succeeds(self):
        # The status bar polls this for every file; "not a worktree" is a normal
        # answer, not an error it should have to parse off stderr.
        out, code = run(wt.cmd_current, ctx_in(None), ["--json"])
        self.assertIsNone(code)
        self.assertEqual(json.loads(out),
                         {"worktree": None, "branch": None, "path": None,
                          "root": "/repo", "layout": "standard"})

    def test_human_output_inside_a_worktree(self):
        out, code = run(wt.cmd_current, ctx_in("repo"), [])
        self.assertIsNone(code)
        self.assertEqual(out.strip(), "repo  main")

    def test_human_output_outside_a_worktree_exits_nonzero(self):
        out, code = run(wt.cmd_current, ctx_in(None), [])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_unknown_flag_dies(self):
        _, code = run(wt.cmd_current, ctx_in("repo"), ["--bogus"])
        self.assertEqual(code, 1)


class ListArgsTest(unittest.TestCase):
    def empty_ctx(self):
        return FakeWtCtx(worktrees=[], current=None)

    def test_json_on_an_empty_repo_is_still_valid_json(self):
        out, code = run(wt.cmd_list, self.empty_ctx(), ["--json"])
        self.assertIsNone(code)
        self.assertEqual(json.loads(out), {"pr_state": "off", "worktrees": []})

    def test_bare_list_on_an_empty_repo_stays_human(self):
        out, code = run(wt.cmd_list, self.empty_ctx(), [])
        self.assertIsNone(code)
        self.assertIn("no worktrees", out)

    def test_unknown_flag_dies(self):
        _, code = run(wt.cmd_list, self.empty_ctx(), ["--bogus"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
