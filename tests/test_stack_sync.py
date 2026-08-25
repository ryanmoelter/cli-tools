"""End-to-end sync tests against a real git repo in a tmpdir.

Unlike the other suites (pure functions + in-memory fakes), these drive the
real Git backend so the rebase arithmetic itself is exercised — only the forge
is faked. They cover the flow that must never lose work: a parent PR is
squash-merged, GitHub retargets the child's base on its own, and `stack sync`
absorbs the parent and replays the child.

The guarantee under test: sync replays exactly the child's own commits,
including any added after the merge, onto the squashed trunk — no duplicates,
nothing dropped.
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from load_stack import load_stack  # noqa: E402

stack = load_stack()
gitcore = sys.modules["gitcore"]


def git(*args, cwd, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
    return p.stdout.strip()


def commit(cwd, name):
    """Create a file and commit it; → the new sha."""
    with open(os.path.join(cwd, name), "w") as f:
        f.write(name)
    git("add", name, cwd=cwd)
    git("commit", "-m", name, cwd=cwd)
    return git("rev-parse", "HEAD", cwd=cwd)


def subjects(cwd, rev):
    out = git("log", "--format=%s", rev, cwd=cwd)
    return out.splitlines() if out else []


class StackForge:
    """Forge stand-in for sync: a {branch: pr} map plus recording of the
    mutations sync can attempt."""

    NUM_PREFIX = "#"
    NOUN = "PR"

    def __init__(self, prs):
        self._prs = dict(prs)
        self.edits = []

    def have(self):
        return True

    def require(self):
        pass

    def prs_for(self, branches):
        return {b: self._prs.get(b) for b in branches}

    def prs_for_or_none(self, branches):
        return self.prs_for(branches)

    def pr_edit(self, number, base):
        self.edits.append((number, base))
        return subprocess.CompletedProcess([], 0, "", "")


def pr(number, state, head_oid, base):
    return {"number": number, "state": state, "isDraft": False,
            "baseRefName": base, "url": "u", "title": "t",
            "headRefOid": head_oid, "reviewDecision": None,
            "statusCheckRollup": [], "stack": None}


class SquashMergeSyncTest(unittest.TestCase):
    """trunk ← b1 ← b2, b1 squash-merged, then a new commit lands on b2."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        prev_cwd = os.getcwd()
        self.addCleanup(lambda: os.chdir(prev_cwd))
        self.addCleanup(
            lambda: subprocess.run(["rm", "-rf", self.dir], check=False)
        )
        d = self.dir
        # A bare repo as origin: cmd_sync fetches, and its trunk-advance uses
        # origin refs. Signing off — the user's global config may require a
        # hardware/agent signer that isn't available to a test.
        self.origin = os.path.join(d, "origin.git")
        os.makedirs(self.origin)
        git("init", "-q", "--bare", "-b", "main", cwd=self.origin)

        self.work = os.path.join(d, "work")
        os.makedirs(self.work)
        d = self.work
        git("init", "-q", "-b", "main", cwd=d)
        for k, v in (("user.email", "t@test"), ("user.name", "t"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git("config", k, v, cwd=d)
        git("remote", "add", "origin", self.origin, cwd=d)
        git("commit", "-q", "--allow-empty", "-m", "root", cwd=d)
        self.trunk0 = git("rev-parse", "HEAD", cwd=d)

        git("switch", "-qc", "b1", cwd=d)
        commit(d, "b1-one")
        self.b1_tip = commit(d, "b1-two")

        git("switch", "-qc", "b2", cwd=d)
        commit(d, "b2-one")
        self.b2_tip = commit(d, "b2-two")

        # Squash-merge b1 into main out-of-band, as the forge would.
        git("switch", "-q", "main", cwd=d)
        git("merge", "--squash", "b1", cwd=d)
        git("commit", "-m", "squashed b1", cwd=d)
        self.squash = git("rev-parse", "HEAD", cwd=d)
        git("push", "-q", "origin", "main", cwd=d)

        os.chdir(d)
        self.git = gitcore.Git()
        self.state = {
            "b1": {"parent": "main", "base": self.trunk0},
            "b2": {"parent": "b1", "base": self.b1_tip},
        }

    def _write_state(self, brs):
        os.makedirs(stack.stack_dir(self.git), exist_ok=True)
        stack.save_state(self.git, brs, True)

    def _sync(self, forge, argv=()):
        git("switch", "-q", "b2", cwd=self.work)
        self._write_state(self.state)
        stack.cmd_sync(self.git, forge, list(argv))
        return stack.load_state(self.git)[0]

    def test_absorbs_parent_and_keeps_all_child_commits(self):
        # A commit lands on b2 after the merge: the replay range is bounded
        # below by the stored base and open above, so it rides along.
        git("switch", "-q", "b2", cwd=self.work)
        commit(self.work, "b2-three")

        forge = StackForge({
            # headRefOid is b1's PRE-squash tip, as GitHub reports it.
            "b1": pr(1, "MERGED", self.b1_tip, "main"),
            # GitHub already retargeted b2 onto the stack base.
            "b2": pr(2, "OPEN", self.b2_tip, "main"),
        })
        brs = self._sync(forge)

        self.assertNotIn("b1", brs)
        self.assertEqual(brs["b2"]["parent"], "main")
        self.assertNotIn("b1", git("branch", "--format=%(refname:short)",
                                   cwd=self.work).split())

        # b2's three commits, exactly once each, atop the squash commit.
        got = subjects(self.work, "b2")
        self.assertEqual(got[:3], ["b2-three", "b2-two", "b2-one"])
        self.assertIn("squashed b1", got)
        for s in ("b2-one", "b2-two", "b2-three"):
            self.assertEqual(got.count(s), 1, f"{s} duplicated: {got}")
        # b1's commits are gone as distinct commits — only the squash remains.
        self.assertNotIn("b1-one", got)
        self.assertNotIn("b1-two", got)
        self.assertTrue(
            self.git.is_ancestor(self.squash, self.git.rev("b2")),
            "b2 must sit on the squashed trunk",
        )
        self.assertEqual(brs["b2"]["base"], self.squash)

    def test_skips_stack_when_local_commits_predate_the_merge_record(self):
        """If b1 has commits the merge didn't include, absorbing would drop
        them — sync must skip instead of deleting the branch."""
        git("switch", "-q", "b1", cwd=self.work)
        commit(self.work, "b1-unmerged")

        forge = StackForge({
            "b1": pr(1, "MERGED", self.b1_tip, "main"),  # stale head
            "b2": pr(2, "OPEN", self.b2_tip, "b1"),
        })
        with self.assertRaises(SystemExit):
            self._sync(forge)
        brs = stack.load_state(self.git)[0]
        self.assertIn("b1", brs)
        self.assertIn("b1", git("branch", "--format=%(refname:short)",
                                cwd=self.work).split())

    def test_no_redundant_retarget_when_github_already_did_it(self):
        """After absorption b2's parent is main and GitHub already set its base
        to main — sync --push must not issue an edit."""
        forge = StackForge({
            "b1": pr(1, "MERGED", self.b1_tip, "main"),
            "b2": pr(2, "OPEN", self.b2_tip, "main"),
        })
        git("switch", "-q", "b2", cwd=self.work)
        self._write_state(self.state)
        # --push would hit the network; exercise the retarget decision directly
        # against the same post-absorption state sync computes.
        stack.cmd_sync(self.git, forge, [])
        brs = stack.load_state(self.git)[0]
        fresh = forge.prs_for(["b2"])["b2"]
        stack.retarget_pr(forge, "b2", fresh, brs["b2"]["parent"], False)
        self.assertEqual(forge.edits, [])

    def test_retargets_when_base_is_still_the_absorbed_branch(self):
        """The stale-data case the fresh re-fetch exists to prevent: if the PR
        still points at the absorbed branch, the edit must fire."""
        forge = StackForge({
            "b1": pr(1, "MERGED", self.b1_tip, "main"),
            "b2": pr(2, "OPEN", self.b2_tip, "b1"),
        })
        brs = self._sync(forge)
        stack.retarget_pr(forge, "b2", forge.prs_for(["b2"])["b2"],
                          brs["b2"]["parent"], False)
        self.assertEqual(forge.edits, [(2, "main")])

    def test_replay_excludes_reworked_parent_commits(self):
        """The case where the stored base genuinely carries the weight.

        When the squash is a faithful copy of the parent, git's patch-id
        detection drops the duplicates on its own, so any sane lower bound
        works. It stops rescuing you once the landed version *differs* from
        what was reviewed (a maintainer edit, a conflict resolution): the
        patch-ids no longer match, and a lower bound derived from the merge
        base would replay the parent's stale commits on top of the corrected
        ones. Only the stored base — the parent's actual old tip — excludes
        them."""
        d = self.work
        # main advances with a *modified* version of b1's change, so b1's
        # commits are not patch-identical to anything on trunk.
        git("switch", "-q", "main", cwd=d)
        with open(os.path.join(d, "b1-one"), "w") as f:
            f.write("reworked by the maintainer")
        git("add", "b1-one", cwd=d)
        git("commit", "-m", "rework b1-one", cwd=d)
        reworked = git("rev-parse", "HEAD", cwd=d)
        git("push", "-q", "-f", "origin", "main", cwd=d)

        forge = StackForge({
            "b1": pr(1, "MERGED", self.b1_tip, "main"),
            "b2": pr(2, "OPEN", self.b2_tip, "main"),
        })
        brs = self._sync(forge)

        got = subjects(self.work, "b2")
        self.assertEqual(got.count("b1-one"), 0,
                         f"parent's superseded commit replayed: {got}")
        for s in ("b2-one", "b2-two"):
            self.assertEqual(got.count(s), 1, f"{s} wrong count: {got}")
        self.assertTrue(self.git.is_ancestor(reworked, self.git.rev("b2")))
        # The maintainer's version survives; b2 did not clobber it.
        with open(os.path.join(d, "b1-one")) as f:
            self.assertEqual(f.read(), "reworked by the maintainer")
        self.assertEqual(brs["b2"]["base"], self.git.trunk_tip())


if __name__ == "__main__":
    unittest.main()
