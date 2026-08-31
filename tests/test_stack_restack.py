"""End-to-end restack tests against a real git repo in a tmpdir.

Like test_stack_sync.py, these drive the real Git backend so the rebase
arithmetic itself is exercised. They cover the replay range: which commits a
restack actually replays onto the new parent tip.

The guarantee under test: a branch that merged its parent (or the trunk) in
rather than rebasing replays only its *own* commits — not the range it merged.
The stored base stops being the fork point the moment such a merge lands, and
using it as the replay floor drags the whole merged-in range along.
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


class RestackRepoTest(unittest.TestCase):
    """A bare origin plus a work clone, with trunk on `main`."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        prev_cwd = os.getcwd()
        self.addCleanup(lambda: os.chdir(prev_cwd))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.dir], check=False))

        self.origin = os.path.join(self.dir, "origin.git")
        os.makedirs(self.origin)
        git("init", "-q", "--bare", "-b", "main", cwd=self.origin)

        self.work = os.path.join(self.dir, "work")
        os.makedirs(self.work)
        d = self.work
        git("init", "-q", "-b", "main", cwd=d)
        for k, v in (("user.email", "t@test"), ("user.name", "t"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git("config", k, v, cwd=d)
        git("remote", "add", "origin", self.origin, cwd=d)
        git("commit", "-q", "--allow-empty", "-m", "root", cwd=d)

        os.chdir(d)
        self.git = gitcore.Git()

    def push_trunk(self):
        git("push", "-q", "-f", "origin", "main", cwd=self.work)

    def write_state(self, brs):
        os.makedirs(stack.stack_dir(self.git), exist_ok=True)
        stack.save_state(self.git, brs)


class MergedInTrunkTest(RestackRepoTest):
    """The reported bug: `feat` merges trunk in instead of rebasing, so its
    stored base falls behind the real fork point."""

    def setUp(self):
        super().setUp()
        d = self.work
        for i in (1, 2, 3):
            commit(d, f"trunk-{i}")
        # The sha feat forks from, and so the base stack stores for it.
        self.fork = git("rev-parse", "HEAD", cwd=d)

        git("switch", "-qc", "feat", cwd=d)
        commit(d, "feat-one")

        git("switch", "-q", "main", cwd=d)
        for i in (4, 5, 6):
            commit(d, f"trunk-{i}")

        # The merge that moves the fork point while the stored base stays put.
        git("switch", "-q", "feat", cwd=d)
        git("merge", "-q", "--no-edit", "main", cwd=d)
        commit(d, "feat-two")

        git("switch", "-q", "main", cwd=d)
        for i in (7, 8):
            commit(d, f"trunk-{i}")
        self.push_trunk()

        git("switch", "-q", "feat", cwd=d)
        self.state = {"feat": {"parent": "main", "base": self.fork}}

    def test_replays_only_the_branchs_own_commits(self):
        self.write_state(self.state)
        stack.cmd_restack(self.git, None, ["--trunk"])

        got = subjects(self.work, "feat")
        # Own commits, newest first, sitting directly on the trunk tip.
        self.assertEqual(got[:2], ["feat-two", "feat-one"])
        for s in ("feat-one", "feat-two"):
            self.assertEqual(got.count(s), 1, f"{s} wrong count: {got}")
        # The merged-in trunk commits came along in the old replay range; each
        # must now appear exactly once, from the trunk itself.
        for i in range(1, 9):
            self.assertEqual(got.count(f"trunk-{i}"), 1,
                             f"trunk-{i} duplicated by the replay: {got}")

        trunk_tip = self.git.trunk_tip()
        self.assertTrue(self.git.is_ancestor(trunk_tip, self.git.rev("feat")))
        self.assertEqual(stack.load_state(self.git)[0]["feat"]["base"], trunk_tip)
        # The merge is flattened away: feat is linear on top of trunk.
        self.assertEqual(
            git("rev-list", "--count", "--merges", f"{trunk_tip}..feat",
                cwd=self.work),
            "0",
        )

    def test_floor_rises_to_the_fork_point(self):
        floor = stack.replay_floor(self.git, self.state, "feat",
                                   self.git.trunk_tip())
        self.assertNotEqual(floor, self.fork, "stale base used as replay floor")
        self.assertEqual(
            floor, self.git.merge_base(self.git.trunk_tip(), self.git.rev("feat"))
        )
        # Two own commits to replay, not the five the stored base would give.
        self.assertEqual(
            git("rev-list", "--count", "--no-merges", f"{floor}..feat",
                cwd=self.work),
            "2",
        )


class ConflictAttributionTest(RestackRepoTest):
    """Git's patch-id detection usually drops the merged-in commits on its own,
    so the stale floor often still lands correctly — just after replaying work
    it did not need to. It stops being harmless the moment one of those commits
    conflicts: the user is dropped into a conflict on a commit somebody else
    wrote, which is already in the base they are rebasing onto.

    Both floors conflict here; what matters is which commit stops the rebase."""

    def setUp(self):
        super().setUp()
        d = self.work
        with open(os.path.join(d, "shared"), "w") as f:
            f.write("base")
        git("add", "shared", cwd=d)
        git("commit", "-m", "trunk-one", cwd=d)
        self.fork = git("rev-parse", "HEAD", cwd=d)

        git("switch", "-qc", "feat", cwd=d)
        commit(d, "feat-one")

        git("switch", "-q", "main", cwd=d)
        with open(os.path.join(d, "shared"), "w") as f:
            f.write("trunk version")
        git("add", "shared", cwd=d)
        git("commit", "-m", "trunk-two", cwd=d)

        # feat merges trunk in and resolves `shared` toward its own content.
        git("switch", "-q", "feat", cwd=d)
        git("merge", "-q", "--no-edit", "main", cwd=d, check=False)
        with open(os.path.join(d, "shared"), "w") as f:
            f.write("feat version")
        git("add", "shared", cwd=d)
        git("commit", "-m", "feat-two", cwd=d)

        git("switch", "-q", "main", cwd=d)
        with open(os.path.join(d, "shared"), "w") as f:
            f.write("trunk version two")
        git("add", "shared", cwd=d)
        git("commit", "-m", "trunk-three", cwd=d)
        self.push_trunk()

        git("switch", "-q", "feat", cwd=d)

    def _conflicting_subject(self, floor):
        git("rebase", "--onto", "main", floor, "feat", cwd=self.work, check=False)
        self.addCleanup(
            lambda: git("rebase", "--abort", cwd=self.work, check=False)
        )
        return git("log", "-1", "--format=%s", "REBASE_HEAD", cwd=self.work)

    def test_conflict_lands_on_the_users_own_commit(self):
        floor = stack.replay_floor(self.git, {"feat": {"parent": "main",
                                                       "base": self.fork}},
                                   "feat", self.git.trunk_tip())
        self.assertEqual(self._conflicting_subject(floor), "feat-two")

    def test_stale_floor_conflicts_on_a_trunk_commit(self):
        """What the stored base does today — the behaviour being fixed."""
        self.assertEqual(self._conflicting_subject(self.fork), "trunk-two")


class MergedInParentTest(RestackRepoTest):
    """Same shape one level up: a child merges its tracked parent in, then the
    parent grows. Only the child's own commits may replay."""

    def setUp(self):
        super().setUp()
        d = self.work
        commit(d, "trunk-one")
        self.trunk0 = git("rev-parse", "HEAD", cwd=d)
        self.push_trunk()

        git("switch", "-qc", "parent", cwd=d)
        commit(d, "parent-one")

        git("switch", "-qc", "child", cwd=d)
        commit(d, "child-one")
        # child forked here, so this is the base stack records for it.
        self.child_base = git("rev-parse", "HEAD~1", cwd=d)

        git("switch", "-q", "parent", cwd=d)
        commit(d, "parent-two")

        git("switch", "-q", "child", cwd=d)
        git("merge", "-q", "--no-edit", "parent", cwd=d)
        commit(d, "child-two")

        git("switch", "-q", "parent", cwd=d)
        commit(d, "parent-three")

        git("switch", "-q", "child", cwd=d)
        self.state = {
            "parent": {"parent": "main", "base": self.trunk0},
            "child": {"parent": "parent", "base": self.child_base},
        }

    def test_replays_only_the_childs_own_commits(self):
        self.write_state(self.state)
        stack.cmd_restack(self.git, None, [])

        got = subjects(self.work, "child")
        self.assertEqual(got[:2], ["child-two", "child-one"])
        for s in ("child-one", "child-two"):
            self.assertEqual(got.count(s), 1, f"{s} wrong count: {got}")
        for s in ("parent-one", "parent-two", "parent-three"):
            self.assertEqual(got.count(s), 1,
                             f"{s} duplicated by the replay: {got}")

        ptip = self.git.rev("parent")
        self.assertTrue(self.git.is_ancestor(ptip, self.git.rev("child")))
        self.assertEqual(stack.load_state(self.git)[0]["child"]["base"], ptip)


class SquashMergeFloorTest(RestackRepoTest):
    """The stored base must survive a squash-merged parent: it is the only
    thing keeping the parent's commits out of the child's replay. Mirrors
    test_stack_sync.py's test_replay_excludes_reworked_parent_commits, but
    asserts on the floor directly."""

    def setUp(self):
        super().setUp()
        d = self.work
        self.trunk0 = git("rev-parse", "HEAD", cwd=d)

        git("switch", "-qc", "b1", cwd=d)
        commit(d, "b1-one")
        self.b1_tip = commit(d, "b1-two")

        git("switch", "-qc", "b2", cwd=d)
        commit(d, "b2-one")

        # b1 lands on trunk as a squash, then a maintainer reworks it so the
        # patch-ids no longer match b1's commits.
        git("switch", "-q", "main", cwd=d)
        git("merge", "-q", "--squash", "b1", cwd=d)
        git("commit", "-q", "-m", "squashed b1", cwd=d)
        with open(os.path.join(d, "b1-one"), "w") as f:
            f.write("reworked by the maintainer")
        git("add", "b1-one", cwd=d)
        git("commit", "-q", "-m", "rework b1-one", cwd=d)
        self.push_trunk()

        git("switch", "-q", "b2", cwd=d)
        # Post-absorption state: b2 reparented onto main, base still b1's old
        # tip (cmd_sync leaves it there on purpose).
        self.state = {"b2": {"parent": "main", "base": self.b1_tip}}

    def test_keeps_the_stored_base(self):
        floor = stack.replay_floor(self.git, self.state, "b2",
                                   self.git.trunk_tip())
        self.assertEqual(floor, self.b1_tip,
                         "stored base dropped — the parent's commits would replay")

    def test_restack_does_not_replay_the_parents_commits(self):
        self.write_state(self.state)
        stack.cmd_restack(self.git, None, ["--trunk"])

        got = subjects(self.work, "b2")
        self.assertEqual(got.count("b2-one"), 1, f"b2-one wrong count: {got}")
        for s in ("b1-one", "b1-two"):
            self.assertEqual(got.count(s), 0,
                             f"superseded parent commit replayed: {got}")
        with open(os.path.join(self.work, "b1-one")) as f:
            self.assertEqual(f.read(), "reworked by the maintainer",
                             "b2 clobbered the maintainer's version")


class ReplayFloorUnitTest(RestackRepoTest):
    """The remaining replay_floor branches."""

    def test_ordinary_fork_point_is_unchanged(self):
        d = self.work
        commit(d, "trunk-one")
        base = git("rev-parse", "HEAD", cwd=d)
        git("switch", "-qc", "feat", cwd=d)
        commit(d, "feat-one")
        git("switch", "-q", "main", cwd=d)
        commit(d, "trunk-two")
        git("switch", "-q", "feat", cwd=d)

        state = {"feat": {"parent": "main", "base": base}}
        self.assertEqual(
            stack.replay_floor(self.git, state, "feat", self.git.rev("main")),
            base,
        )

    def test_unreachable_base_falls_back_to_the_merge_base(self):
        d = self.work
        commit(d, "trunk-one")
        git("switch", "-qc", "feat", cwd=d)
        commit(d, "feat-one")
        # A sha on no branch reachable from feat: the rewritten-base case.
        git("switch", "-q", "main", cwd=d)
        orphan = commit(d, "trunk-two")
        git("switch", "-q", "feat", cwd=d)

        state = {"feat": {"parent": "main", "base": orphan}}
        ptip = self.git.rev("main")
        self.assertFalse(self.git.is_ancestor(orphan, self.git.rev("feat")))
        self.assertEqual(
            stack.replay_floor(self.git, state, "feat", ptip),
            self.git.merge_base(ptip, self.git.rev("feat")),
        )


if __name__ == "__main__":
    unittest.main()
