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

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

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


class SyncRepoTest(unittest.TestCase):
    """trunk ← b1 ← b2, b1 squash-merged."""

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
        stack.save_state(self.git, brs)

    def _sync(self, forge, argv=()):
        git("switch", "-q", "b2", cwd=self.work)
        self._write_state(self.state)
        stack.cmd_sync(self.git, forge, list(argv))
        return stack.load_state(self.git)[0]


class SquashMergeSyncTest(SyncRepoTest):
    """trunk ← b1 ← b2, b1 squash-merged, then a new commit lands on b2."""

    def test_tracks_an_untracked_branch_with_an_open_pr(self):
        """Fresh-clone recovery: a local branch with an open PR based on the
        trunk gets adopted into the stack. The PR query is started before the
        git fetches, so this also covers the overlapped-fetch path."""
        git("switch", "-qc", "loose", "main", cwd=self.work)
        loose_tip = commit(self.work, "loose-one")
        forge = StackForge({
            "b1": pr(1, "OPEN", self.b1_tip, "main"),
            "loose": pr(9, "OPEN", loose_tip, "main"),
        })
        brs = self._sync(forge)
        self.assertIn("loose", brs)
        self.assertEqual(brs["loose"]["parent"], "main")

    def test_does_not_track_a_branch_whose_pr_is_closed(self):
        git("switch", "-qc", "loose", "main", cwd=self.work)
        loose_tip = commit(self.work, "loose-one")
        forge = StackForge({"loose": pr(9, "CLOSED", loose_tip, "main")})
        self.assertNotIn("loose", self._sync(forge))

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

    def _advance_trunk(self):
        """Land an unrelated commit on origin's trunk; → its sha."""
        git("switch", "-q", "main", cwd=self.work)
        sha = commit(self.work, "trunk-moved")
        git("push", "-q", "origin", "main", cwd=self.work)
        return sha

    def test_leaves_the_root_behind_a_moved_trunk_by_default(self):
        """Being behind a fast-moving trunk isn't a restack, so plain `sync`
        must not rebase the root onto it — same rule as `stack restack`."""
        moved = self._advance_trunk()
        forge = StackForge({"b1": pr(1, "OPEN", self.b1_tip, "main"),
                            "b2": pr(2, "OPEN", self.b2_tip, "b1")})
        brs = self._sync(forge)

        self.assertEqual(self.git.rev("b1"), self.b1_tip)
        self.assertFalse(self.git.is_ancestor(moved, self.git.rev("b1")))
        self.assertEqual(brs["b1"]["base"], self.trunk0)

    def test_trunk_flag_rebases_the_root_onto_the_moved_trunk(self):
        moved = self._advance_trunk()
        forge = StackForge({"b1": pr(1, "OPEN", self.b1_tip, "main"),
                            "b2": pr(2, "OPEN", self.b2_tip, "b1")})
        brs = self._sync(forge, ["--trunk"])

        self.assertTrue(self.git.is_ancestor(moved, self.git.rev("b1")))
        self.assertTrue(self.git.is_ancestor(moved, self.git.rev("b2")))
        self.assertEqual(brs["b1"]["base"], moved)
        # b2 rides along on its parent, keeping its own commits exactly once.
        got = subjects(self.work, "b2")
        self.assertEqual(got[:2], ["b2-two", "b2-one"])
        self.assertEqual(brs["b2"]["base"], self.git.rev("b1"))

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


class AdoptRemoteRestackTest(SyncRepoTest):
    """trunk ← b1 ← b2 ← b3, all pushed; b1 squash-merged. A forge that
    restacks server-side has already replayed the rest onto the squash and
    force-pushed it, while the local branches still sit on b1's old tip.
    Sync must adopt those remote heads rather than rebase and push again."""

    def setUp(self):
        super().setUp()
        d = self.work
        git("switch", "-qc", "b3", "b2", cwd=d)
        self.b3_tip = commit(d, "b3-one")
        git("switch", "-q", "main", cwd=d)
        for b, sha in (("b1", self.b1_tip), ("b2", self.b2_tip), ("b3", self.b3_tip)):
            git("push", "-q", "origin", f"{sha}:refs/heads/{b}", cwd=d)
        git("fetch", "-q", "origin", cwd=d)
        self.state["b3"] = {"parent": "b2", "base": self.b2_tip}
        self.forge = StackForge({
            "b1": pr(1, "MERGED", self.b1_tip, "main"),
            "b2": pr(2, "OPEN", self.b2_tip, "main"),
            "b3": pr(3, "OPEN", self.b3_tip, "b2"),
        })

    def remote_rebase(self, b, onto, old_base, then=None):
        """Replay `b` onto `onto` and force-push it, as the forge does, leaving
        the local branch alone. `then` may add commits first; → the new head."""
        d = self.work
        git("switch", "-qc", "tmp", b, cwd=d)
        git("-c", "rebase.updateRefs=false", "rebase", "-q", "--onto", onto, old_base, cwd=d)
        if then:
            then()
        sha = git("rev-parse", "HEAD", cwd=d)
        git("push", "-q", "-f", "origin", "tmp:" + b, cwd=d)
        git("switch", "-q", "main", cwd=d)
        git("branch", "-D", "tmp", cwd=d)
        return sha

    def restack_remotely(self):
        """→ (b2, b3) remote heads after the forge restacks both."""
        r2 = self.remote_rebase("b2", self.squash, self.b1_tip)
        r3 = self.remote_rebase("b3", r2, self.b2_tip)
        return r2, r3

    def remote(self, b):
        return git("rev-parse", f"refs/heads/{b}", cwd=self.origin)

    def run_sync(self, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            brs = self._sync(self.forge, argv)
        return brs, out.getvalue(), err.getvalue()

    def test_adopts_a_matching_remote_and_skips_its_push(self):
        r2, _ = self.restack_remotely()
        brs, out, _ = self.run_sync(["--push"])
        self.assertEqual(self.git.rev("b2"), r2)
        self.assertEqual(self.remote("b2"), r2)
        self.assertEqual(brs["b2"]["base"], self.squash)
        self.assertIn("b2: adopted origin/b2 (already restacked on the remote)", out)
        self.assertIn("b2: up to date on origin — not pushing", out)
        self.assertNotIn("pushed b2", out)

    def test_adopts_a_remote_with_extra_commits(self):
        r2 = self.remote_rebase("b2", self.squash, self.b1_tip,
                                then=lambda: commit(self.work, "bot-translations"))
        brs, out, _ = self.run_sync(["--push"])
        self.assertEqual(self.git.rev("b2"), r2)
        self.assertEqual(subjects(self.work, "b2")[0], "bot-translations")
        self.assertNotIn("pushed b2", out)

    def test_replays_unpushed_local_commits_onto_the_adopted_head(self):
        r2, _ = self.restack_remotely()
        git("switch", "-q", "b2", cwd=self.work)
        commit(self.work, "b2-unpushed")
        brs, out, _ = self.run_sync(["--push"])
        self.assertEqual(git("rev-parse", "b2^", cwd=self.work), r2)
        got = subjects(self.work, "b2")
        self.assertEqual(got[0], "b2-unpushed")
        for s in ("b2-one", "b2-two", "b2-unpushed"):
            self.assertEqual(got.count(s), 1, f"{s} wrong count: {got}")
        self.assertIn("replayed 1 local commit(s)", out)
        self.assertEqual(self.remote("b2"), self.git.rev("b2"))
        self.assertIn("pushed b2", out)

    def test_rebases_when_the_remote_was_not_restacked(self):
        brs, out, _ = self.run_sync()
        self.assertIn("b2: rebased onto main", out)
        self.assertTrue(self.git.is_ancestor(self.squash, self.git.rev("b2")))
        self.assertEqual(self.remote("b2"), self.b2_tip)

    def test_rebases_when_the_remote_restack_differs(self):
        def rework():
            with open(os.path.join(self.work, "b2-two"), "w") as f:
                f.write("different")
            git("commit", "-qam", "b2-two", "--amend", cwd=self.work)
        r2 = self.remote_rebase("b2", self.squash, self.b1_tip, then=rework)
        brs, out, _ = self.run_sync()
        self.assertIn("b2: rebased onto main", out)
        self.assertNotEqual(self.git.rev("b2"), r2)
        self.assertTrue(self.git.is_ancestor(self.squash, self.git.rev("b2")))

    def test_all_adopts_the_whole_remaining_chain(self):
        r2, r3 = self.restack_remotely()
        brs, out, _ = self.run_sync(["--all", "--push"])
        self.assertEqual(self.git.rev("b2"), r2)
        self.assertEqual(self.git.rev("b3"), r3)
        self.assertEqual(brs["b3"]["base"], r2)
        self.assertNotIn("pushed", out)

    def test_default_scope_names_the_children_left_behind(self):
        self.restack_remotely()
        brs, out, err = self.run_sync()
        self.assertEqual(self.git.rev("b3"), self.b3_tip)
        self.assertIn("branches above 'b2' may need their own restack: b3", err)

    def add_worktree(self, b):
        wt = os.path.join(self.dir, "wt-" + b)
        git("worktree", "add", "-q", wt, b, cwd=self.work)
        return wt

    def test_adopts_a_branch_checked_out_in_another_worktree(self):
        _, r3 = self.restack_remotely()
        wt = self.add_worktree("b3")
        self.run_sync(["--all"])
        self.assertEqual(self.git.rev("b3"), r3)
        self.assertEqual(git("rev-parse", "HEAD", cwd=wt), r3)

    def test_rebases_a_branch_checked_out_in_another_worktree(self):
        wt = self.add_worktree("b3")
        self.run_sync(["--all"])
        self.assertTrue(self.git.is_ancestor(self.git.rev("b2"), self.git.rev("b3")))
        self.assertEqual(git("rev-parse", "HEAD", cwd=wt), self.git.rev("b3"))
        self.assertEqual(subjects(wt, "HEAD")[:3], ["b3-one", "b2-two", "b2-one"])

    def test_dirty_worktree_stops_with_its_path(self):
        self.restack_remotely()
        wt = self.add_worktree("b3")
        with open(os.path.join(wt, "b3-one"), "w") as f:
            f.write("wip")
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stderr(err), redirect_stdout(io.StringIO()):
            self._sync(self.forge, ["--all"])
        self.assertIn(f"'b3' is checked out in {os.path.realpath(wt)} with uncommitted changes",
                      err.getvalue())
        self.assertEqual(self.git.rev("b3"), self.b3_tip)

    def test_dry_run_reports_each_decision(self):
        self.restack_remotely()
        brs, out, _ = self.run_sync(["--all", "--push", "--dry-run"])
        self.assertIn("b2: would adopt origin/b2 (already restacked on the remote)", out)
        self.assertIn("b3: would adopt origin/b3 (already restacked on the remote)", out)
        self.assertIn("b2: up to date on origin — not pushing", out)
        self.assertNotIn("would push", out)
        self.assertEqual(self.git.rev("b2"), self.b2_tip)
        self.assertEqual(self.git.rev("b3"), self.b3_tip)

    def test_dry_run_rebase_cascades_to_children(self):
        brs, out, _ = self.run_sync(["--all", "--dry-run"])
        self.assertIn("b2: would rebase onto main", out)
        self.assertIn("b3: would rebase onto b2", out)

    def restack_after_absorb(self):
        """`stack restack` from b2 once b1 is gone, with origin unreachable."""
        git("switch", "-q", "b2", cwd=self.work)
        git("branch", "-D", "b1", cwd=self.work)
        git("remote", "set-url", "origin", "/nonexistent", cwd=self.work)
        self._write_state({"b2": {"parent": "main", "base": self.b1_tip}})
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            stack.cmd_restack(self.git, None, [])
        return out.getvalue()

    def test_restack_adopts_from_fetched_refs_without_network(self):
        r2, _ = self.restack_remotely()
        git("fetch", "-q", "origin", cwd=self.work)
        out = self.restack_after_absorb()
        self.assertIn("b2: adopted origin/b2", out)
        self.assertEqual(self.git.rev("b2"), r2)

    def test_restack_rebases_without_a_remote_branch(self):
        git("update-ref", "-d", "refs/remotes/origin/b2", cwd=self.work)
        out = self.restack_after_absorb()
        self.assertIn("b2: rebased onto main", out)
        self.assertTrue(self.git.is_ancestor(self.squash, self.git.rev("b2")))


if __name__ == "__main__":
    unittest.main()
