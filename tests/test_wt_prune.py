"""Tests for wt's `prune` selection predicate and flag parsing.

`prune_candidates` decides which worktrees get proposed for deletion — only
those whose PR/MR is MERGED or CLOSED, never the protected/cwd/base worktrees.
It's a pure function, so it's pinned directly against hand-built records and PR
dicts (the same PR shape the list tests use).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_script import load_script  # noqa: E402

wt = load_script("wt", "wt_mod")


def pr(number, state):
    return {
        "number": number,
        "state": state,
        "isDraft": False,
        "reviewDecision": None,
        "statusCheckRollup": [],
    }


RECORDS = [
    ("main", "/repo"),
    ("ryanm/merged", "/repo/.wt/merged"),
    ("ryanm/closed", "/repo/.wt/closed"),
    ("ryanm/open", "/repo/.wt/open"),
    ("ryanm/nopr", "/repo/.wt/nopr"),
]

PRS = {
    "ryanm/merged": pr(1, "MERGED"),
    "ryanm/closed": pr(2, "CLOSED"),
    "ryanm/open": pr(3, "OPEN"),
    "ryanm/nopr": None,
}


class PruneCandidatesTest(unittest.TestCase):
    def _run(self, records=RECORDS, prs=PRS, protected=("repo",),
             cwd_name=None, base_branch="main"):
        return wt.prune_candidates(records, prs, list(protected), cwd_name, base_branch)

    def test_selects_only_merged_and_closed(self):
        names = [name for name, *_ in self._run()]
        self.assertEqual(names, ["merged", "closed"])

    def test_returns_name_branch_path_pr(self):
        merged = self._run()[0]
        self.assertEqual(
            merged, ("merged", "ryanm/merged", "/repo/.wt/merged", PRS["ryanm/merged"])
        )

    def test_skips_open_and_missing_pr(self):
        names = [name for name, *_ in self._run()]
        self.assertNotIn("open", names)
        self.assertNotIn("nopr", names)

    def test_excludes_protected(self):
        names = [name for name, *_ in self._run(protected=("repo", "merged"))]
        self.assertEqual(names, ["closed"])

    def test_excludes_cwd(self):
        names = [name for name, *_ in self._run(cwd_name="closed")]
        self.assertEqual(names, ["merged"])

    def test_excludes_base_branch_even_if_merged(self):
        records = RECORDS + [("main", "/repo/.wt/main-copy")]
        prs = dict(PRS, main=pr(9, "MERGED"))
        names = [name for name, *_ in self._run(records=records, prs=prs)]
        self.assertNotIn("main-copy", names)

    def test_empty_when_nothing_qualifies(self):
        prs = {"ryanm/open": pr(3, "OPEN")}
        self.assertEqual(self._run(records=[("ryanm/open", "/repo/.wt/open")], prs=prs), [])


class CmdPruneFlagTest(unittest.TestCase):
    def test_unknown_flag_dies(self):
        with self.assertRaises(SystemExit):
            wt.cmd_prune(None, ["--nope"])


if __name__ == "__main__":
    unittest.main()
