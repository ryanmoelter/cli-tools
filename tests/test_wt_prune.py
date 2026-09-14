"""Tests for wt's `prune` selection predicates and flag parsing.

`prune_candidates` decides which worktrees get proposed for deletion, and
`bare_branch_candidates` does the same for branches that have no worktree.
Both qualify a branch whose PR/MR is MERGED or CLOSED, or that is already
merged into the base; both refuse the protected/cwd/base entries. They're pure
functions, so they're pinned directly against hand-built records and PR dicts
(the same PR shape the list tests use).
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


class FakeMergeGit:
    """The is_ancestor slice is_merged_locally needs."""

    def __init__(self, merged=()):
        self._merged = set(merged)

    def is_ancestor(self, ref, of_ref):
        return ref in self._merged


def never_merged(_branch):
    return False


class IsMergedLocallyTest(unittest.TestCase):
    def test_ancestor_is_merged(self):
        git = FakeMergeGit(merged=["ryanm/done"])
        self.assertTrue(wt.is_merged_locally(git, "ryanm/done", "origin/main"))

    def test_non_ancestor_is_not_merged(self):
        git = FakeMergeGit()
        self.assertFalse(wt.is_merged_locally(git, "ryanm/wip", "origin/main"))


class PruneCandidatesTest(unittest.TestCase):
    def _run(self, records=RECORDS, prs=PRS, protected=("repo",),
             cwd_name=None, base_branch="main", locally_merged=never_merged):
        return wt.prune_candidates(records, prs, list(protected), cwd_name,
                                   base_branch, locally_merged)

    def test_selects_only_merged_and_closed(self):
        names = [name for name, *_ in self._run()]
        self.assertEqual(names, ["merged", "closed"])

    def test_returns_name_branch_path_pr_reason(self):
        merged = self._run()[0]
        self.assertEqual(
            merged,
            ("merged", "ryanm/merged", "/repo/.wt/merged", PRS["ryanm/merged"], "merged")
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

    def test_locally_merged_branch_without_pr_qualifies(self):
        rows = self._run(locally_merged=lambda b: b == "ryanm/nopr")
        self.assertIn("nopr", [name for name, *_ in rows])

    def test_reason_distinguishes_pr_state_from_local_merge(self):
        rows = self._run(locally_merged=lambda b: b == "ryanm/nopr")
        reasons = {name: reason for name, _, _, _, reason in rows}
        self.assertEqual(reasons["merged"], "merged")
        self.assertEqual(reasons["closed"], "closed")
        self.assertEqual(reasons["nopr"], "merged into main")


BRANCHES = ["main", "ryanm/merged", "ryanm/closed", "ryanm/open", "ryanm/nopr",
            "ryanm/in-worktree"]


class BareBranchCandidatesTest(unittest.TestCase):
    def _run(self, branches=BRANCHES, worktree_branches=("ryanm/in-worktree",),
             prs=PRS, current_branch=None, trunk_branches=("main",),
             base_branch="main", locally_merged=never_merged):
        return wt.bare_branch_candidates(
            branches, list(worktree_branches), prs, current_branch,
            set(trunk_branches), base_branch, locally_merged)

    def test_selects_merged_and_closed(self):
        self.assertEqual([b for b, *_ in self._run()],
                         ["ryanm/merged", "ryanm/closed"])

    def test_excludes_branch_checked_out_in_a_worktree(self):
        prs = dict(PRS, **{"ryanm/in-worktree": pr(7, "MERGED")})
        self.assertNotIn("ryanm/in-worktree", [b for b, *_ in self._run(prs=prs)])

    def test_excludes_current_branch(self):
        branches = [b for b, *_ in self._run(current_branch="ryanm/merged")]
        self.assertEqual(branches, ["ryanm/closed"])

    def test_excludes_trunk_branches(self):
        prs = dict(PRS, main=pr(9, "MERGED"))
        self.assertNotIn("main", [b for b, *_ in self._run(prs=prs)])

    def test_includes_locally_merged_branch_without_pr(self):
        rows = self._run(locally_merged=lambda b: b == "ryanm/nopr")
        self.assertIn("ryanm/nopr", [b for b, *_ in rows])

    def test_excludes_open_pr_not_locally_merged(self):
        self.assertNotIn("ryanm/open", [b for b, *_ in self._run()])


class CmdPruneFlagTest(unittest.TestCase):
    def test_unknown_flag_dies(self):
        with self.assertRaises(SystemExit):
            wt.cmd_prune(None, ["--nope"])


if __name__ == "__main__":
    unittest.main()
