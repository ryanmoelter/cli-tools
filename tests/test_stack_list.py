"""Tests for stack's `list` log-output rendering (render_stacks).

Run: python3 -m unittest discover script-tests

These pin the exact tree text — glyphs, connectors, column alignment, PR chips,
and the needs-restack / behind-trunk notes — against hand-built in-memory git/gh
fakes, so a change that shifts a column or swaps a symbol is caught without a
real repo.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import FakeGit, FakeGh, FakeGl  # noqa: E402
from load_stack import load_stack  # noqa: E402

stack = load_stack()
# The forge module itself, not stack's local `forge` variable (a backend
# instance), which shadows the name inside the script.
import forge as forge_mod  # noqa: E402


def chain(*commits):
    """{commit: [ancestors newest-first]} for a linear history given oldest-first.
    chain('m0','a1','a2') → m0 has no ancestors, a1→[m0], a2→[a1,m0]."""
    anc = {}
    for i, c in enumerate(commits):
        anc[c] = list(reversed(commits[:i]))
    return anc


_UNSET = object()


def render(git, brs, cur, prs=_UNSET, with_pr=False, roots=None, pr_state=None):
    if roots is None:
        roots = stack.roots_of(brs)
    # prs=None means "forge unreachable"; the _UNSET default is the empty map.
    prs = {} if prs is _UNSET else prs
    if pr_state is None:
        # Mirror cmd_list's derivation: no fetch → off, None → error, else on.
        pr_state = "off" if not with_pr else ("error" if prs is None else "on")
    return stack.render_stacks(git, brs, roots, cur, prs, pr_state)


class LinearStackTest(unittest.TestCase):
    def setUp(self):
        # main(m0) → feat-a (a1,a2) → feat-b (b1)
        self.anc = chain("m0", "a1", "a2", "b1")
        self.git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat-a": "a2", "feat-b": "b1"},
            ancestors=self.anc,
        )
        self.brs = {
            "feat-a": {"parent": "main", "base": "m0"},
            "feat-b": {"parent": "feat-a", "base": "a2"},
        }

    def test_linear_current_is_tip(self):
        out = render(self.git, self.brs, cur="feat-b")
        self.assertEqual(
            out,
            "\n".join([
                "│ ➜ feat-b",
                "│ │ ↗1 commit",
                "│ ● feat-a",
                "├─╯ ↗2 commits",
                "● main",
            ]),
        )

    def test_current_marker_moves_to_root(self):
        out = render(self.git, self.brs, cur="feat-a")
        self.assertEqual(
            out,
            "\n".join([
                "│ ● feat-b",
                "│ │ ↗1 commit",
                "│ ➜ feat-a",
                "├─╯ ↗2 commits",
                "● main",
            ]),
        )


class ForkTest(unittest.TestCase):
    """feat-a forks into two children feat-b and feat-c — exercises the sibling
    merge connector ├─╯ at the inner column."""

    def test_fork(self):
        # main(m0) → feat-a(a1) → { feat-b(b1), feat-c(c1) }
        anc = {
            "m0": [],
            "a1": ["m0"],
            "b1": ["a1", "m0"],
            "c1": ["a1", "m0"],
        }
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat-a": "a1", "feat-b": "b1", "feat-c": "c1"},
            ancestors=anc,
        )
        brs = {
            "feat-a": {"parent": "main", "base": "m0"},
            "feat-b": {"parent": "feat-a", "base": "a1"},
            "feat-c": {"parent": "feat-a", "base": "a1"},
        }
        out = render(git, brs, cur="feat-a")
        # children sort alphabetically; feat-c continues the spine (on top),
        # feat-b tees off into the inner column below it.
        self.assertEqual(
            out,
            "\n".join([
                "│ ● feat-c",
                "│ │ ↗1 commit",
                "│ │ ● feat-b",
                "│ ├─╯ ↗1 commit",
                "│ ➜ feat-a",
                "├─╯ ↗1 commit",
                "● main",
            ]),
        )

    def test_multi_child_and_nesting(self):
        # base(x1) → { c-a(ca), c-b(cb), c-c(cc) → leaf(lf) }.
        # Spine child = alphabetically-last (c-c) with its grandchild above it;
        # the teed siblings c-a, c-b render below the spine child, each teeing
        # into the inner column.
        anc = {
            "m0": [], "x1": ["m0"],
            "ca": ["x1", "m0"], "cb": ["x1", "m0"], "cc": ["x1", "m0"],
            "lf": ["cc", "x1", "m0"],
        }
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "base": "x1", "c-a": "ca",
                         "c-b": "cb", "c-c": "cc", "leaf": "lf"},
            ancestors=anc,
        )
        brs = {
            "base": {"parent": "main", "base": "m0"},
            "c-a": {"parent": "base", "base": "x1"},
            "c-b": {"parent": "base", "base": "x1"},
            "c-c": {"parent": "base", "base": "x1"},
            "leaf": {"parent": "c-c", "base": "cc"},
        }
        out = render(git, brs, cur="base")
        self.assertEqual(
            out,
            "\n".join([
                "│ ● leaf",
                "│ │ ↗1 commit",
                "│ ● c-c",
                "│ │ ↗1 commit",
                "│ │ ● c-a",
                "│ ├─╯ ↗1 commit",
                "│ │ ● c-b",
                "│ ├─╯ ↗1 commit",
                "│ ➜ base",
                "├─╯ ↗1 commit",
                "● main",
            ]),
        )


class BelowTrunkTest(unittest.TestCase):
    """A root that forked before the trunk HEAD renders below the HEAD node,
    with a trunk-gap 'N commits' row on the spine."""

    def test_root_below_head(self):
        # trunk: t0 → t1 → t2(HEAD). feat forks at t0, so it's 2 behind on trunk.
        anc = {
            "t0": [],
            "t1": ["t0"],
            "t2": ["t1", "t0"],
            "f1": ["t0"],
        }
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "t2", "feat": "f1"},
            ancestors=anc,
        )
        brs = {"feat": {"parent": "main", "base": "t0"}}
        out = render(git, brs, cur="feat")
        # feat forked before HEAD, so it's behind trunk — the note is expected.
        self.assertEqual(
            out,
            "\n".join([
                "● main",
                "┆2",
                "│ ➜ feat",
                "├─╯ ↗1 commit  ↻ behind main",
                "●",
            ]),
        )


class NotesTest(unittest.TestCase):
    def test_needs_restack(self):
        # feat-a moved (tip a2) but feat-b's base still names a1 and b1 doesn't
        # descend from a2 → feat-b needs restack.
        anc = {
            "m0": [],
            "a1": ["m0"],
            "a2": ["a1", "m0"],
            "b1": ["a1", "m0"],  # off a1, not a2
        }
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat-a": "a2", "feat-b": "b1"},
            ancestors=anc,
        )
        brs = {
            "feat-a": {"parent": "main", "base": "m0"},
            "feat-b": {"parent": "feat-a", "base": "a1"},
        }
        out = render(git, brs, cur="feat-b")
        self.assertIn("↻ needs restack", out)
        # The note sits on feat-b's own-commit label row.
        self.assertTrue(any(
            line.endswith("↻ needs restack") and "commit" in line
            for line in out.splitlines()
        ), out)

    def test_behind_trunk(self):
        # A root behind a moved trunk gets the dim 'behind main' note, not
        # 'needs restack' (roots don't cascade-restack).
        anc = {"t0": [], "t1": ["t0"], "f1": ["t0"]}
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "t1", "feat": "f1"},
            ancestors=anc,
        )
        brs = {"feat": {"parent": "main", "base": "t0"}}
        out = render(git, brs, cur="feat")
        self.assertIn("↻ behind main", out)
        self.assertNotIn("needs restack", out)


class OriginAheadBehindTest(unittest.TestCase):
    def test_ahead_behind_suffix(self):
        anc = chain("m0", "a1")
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat": "a1"},
            ancestors=anc,
            origin={"feat": (2, 1)},
        )
        brs = {"feat": {"parent": "main", "base": "m0"}}
        out = render(git, brs, cur="feat")
        # ↑/↓ ride the connector row after the commit count, not the name row.
        self.assertIn("↗1 commit ↑2 ↓1", out)
        self.assertNotIn("feat ↑", out)


class PrefixTest(unittest.TestCase):
    def test_branch_prefix_stripped_visually(self):
        # With color off the dim-prefix wrapper is empty, so the name renders in
        # full; this just confirms the prefixed name still shows and aligns.
        anc = chain("m0", "a1")
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "ryanm/feat": "a1"},
            ancestors=anc,
            prefix="ryanm/",
        )
        brs = {"ryanm/feat": {"parent": "main", "base": "m0"}}
        out = render(git, brs, cur="ryanm/feat")
        self.assertIn("ryanm/feat", out)


class PrChipTest(unittest.TestCase):
    """pr_chip / rollup_checks folding, asserted through the rendered chip
    column (colors off, so only glyphs and #num remain)."""

    def _one_branch(self, pr, *, pushed=True):
        anc = chain("m0", "a1")
        # pushed → give the branch an origin so branch_pr_chip treats it as
        # having an upstream (matters only for the no-PR case).
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat": "a1"},
            ancestors=anc,
            origin={"feat": (0, 0)} if pushed else None,
        )
        brs = {"feat": {"parent": "main", "base": "m0"}}
        gh = FakeGh(prs={"feat": pr})
        prs = gh.prs_for(["feat"])
        return render(git, brs, cur="feat", prs=prs, with_pr=True)

    def _open(self, **extra):
        pr = {"number": 7, "state": "OPEN", "isDraft": False,
              "baseRefName": "main", "url": "u", "reviewDecision": None,
              "statusCheckRollup": []}
        pr.update(extra)
        return pr

    def test_pushed_no_pr_shows_dim_dash(self):
        out = self._one_branch(None, pushed=True)
        self.assertIn("–", out)
        self.assertNotIn("(no PR)", out)

    def test_local_only_no_pr_shows_net_off(self):
        out = self._one_branch(None, pushed=False)
        self.assertIn(stack.ui.SYM_NET_OFF, out)
        self.assertNotIn("(no PR)", out)

    def test_chip_leads_with_forge_icon(self):
        out = self._one_branch(self._open())
        self.assertIn(f"{stack.ui.SYM_GITHUB} #7 {stack.SYM_OPEN}", out)

    def test_merged(self):
        pr = self._open(state="MERGED")
        out = self._one_branch(pr)
        self.assertIn(f"#7 {stack.ui.SYM_MERGED}", out)

    def test_closed(self):
        pr = self._open(state="CLOSED")
        out = self._one_branch(pr)
        self.assertIn(f"#7 {stack.SYM_CLOSED}", out)

    def test_open_no_checks_is_bare_open_marker(self):
        out = self._one_branch(self._open())
        self.assertIn(f"#7 {stack.SYM_OPEN}", out)

    def test_checks_pass(self):
        pr = self._open(statusCheckRollup=[{"name": "ci", "conclusion": "SUCCESS"}])
        out = self._one_branch(pr)
        self.assertIn(stack.ui.SYM_CHECKS_PASS, out)

    def test_checks_fail_wins(self):
        pr = self._open(statusCheckRollup=[
            {"name": "ci", "conclusion": "SUCCESS"},
            {"name": "lint", "conclusion": "FAILURE"},
        ])
        out = self._one_branch(pr)
        self.assertIn(stack.ui.SYM_CHECKS_FAIL, out)

    def test_checks_pending(self):
        pr = self._open(statusCheckRollup=[{"name": "ci", "status": "IN_PROGRESS"}])
        out = self._one_branch(pr)
        self.assertIn(stack.ui.SYM_CHECKS_PENDING, out)

    def test_approved(self):
        pr = self._open(reviewDecision="APPROVED")
        out = self._one_branch(pr)
        self.assertIn(stack.SYM_APPROVED, out)

    def test_changes_requested(self):
        pr = self._open(reviewDecision="CHANGES_REQUESTED")
        out = self._one_branch(pr)
        self.assertIn(stack.SYM_CHANGES, out)

    def test_draft_marker_leads(self):
        pr = self._open(isDraft=True,
                        statusCheckRollup=[{"name": "ci", "conclusion": "SUCCESS"}])
        out = self._one_branch(pr)
        # draft glyph then a space then the checks glyph
        self.assertIn(f"{stack.SYM_DRAFT} {stack.ui.SYM_CHECKS_PASS}", out)

    def test_ignored_check_pending_does_not_mask_success(self):
        ignored = "ci/slow-integration-suite"
        forge_mod.set_ignored_when_pending({ignored})
        self.addCleanup(forge_mod.set_ignored_when_pending, set())
        pr = self._open(statusCheckRollup=[
            {"name": "ci", "conclusion": "SUCCESS"},
            {"name": ignored, "status": "IN_PROGRESS"},
        ])
        out = self._one_branch(pr)
        self.assertIn(stack.ui.SYM_CHECKS_PASS, out)
        self.assertNotIn(stack.ui.SYM_CHECKS_PENDING, out)


class GitlabChipTest(unittest.TestCase):
    """The MR chip renders with the GitLab icon and `!`-prefixed number. The
    normalized dict is identical across forges, so only the icon/prefix that
    cmd_list threads into render_stacks differ from PrChipTest."""

    def _one_branch(self, pr):
        anc = chain("m0", "a1")
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat": "a1"},
            ancestors=anc,
            origin={"feat": (0, 0)},
        )
        brs = {"feat": {"parent": "main", "base": "m0"}}
        prs = FakeGl(prs={"feat": pr}).prs_for(["feat"])
        return stack.render_stacks(
            git, brs, stack.roots_of(brs), "feat", prs, "on",
            forge_icon=stack.ui.SYM_GITLAB, num_prefix="!",
        )

    def _open(self, **extra):
        pr = {"number": 7, "state": "OPEN", "isDraft": False,
              "baseRefName": "main", "url": "u", "reviewDecision": None,
              "statusCheckRollup": []}
        pr.update(extra)
        return pr

    def test_chip_leads_with_gitlab_icon_and_bang_number(self):
        out = self._one_branch(self._open())
        self.assertIn(f"!7 {stack.SYM_OPEN}", out)
        self.assertNotIn("#7", out)

    def test_merged_mr(self):
        out = self._one_branch(self._open(state="MERGED"))
        self.assertIn(f"!7 {stack.ui.SYM_MERGED}", out)

    def test_pipeline_pass_glyph(self):
        pr = self._open(statusCheckRollup=[{"name": "pipeline", "state": "SUCCESS"}])
        out = self._one_branch(pr)
        self.assertIn(stack.ui.SYM_CHECKS_PASS, out)


class RollupChecksUnitTest(unittest.TestCase):
    """rollup_checks is pure; assert its folding directly."""

    def r(self, rollup):
        return stack.rollup_checks({"statusCheckRollup": rollup})

    def test_none(self):
        self.assertEqual(self.r([]), "NONE")

    def test_failure_wins(self):
        self.assertEqual(self.r([
            {"name": "a", "conclusion": "SUCCESS"},
            {"name": "b", "conclusion": "TIMED_OUT"},
        ]), "FAILURE")

    def test_pending(self):
        self.assertEqual(self.r([{"name": "a", "status": "QUEUED"}]), "PENDING")

    def test_success_treats_neutral_and_skipped_as_pass(self):
        self.assertEqual(self.r([
            {"name": "a", "conclusion": "SUCCESS"},
            {"name": "b", "conclusion": "NEUTRAL"},
            {"name": "c", "conclusion": "SKIPPED"},
        ]), "SUCCESS")

    def test_cancelled_treated_as_neutral(self):
        self.assertEqual(self.r([
            {"name": "a", "conclusion": "SUCCESS"},
            {"name": "b", "conclusion": "CANCELLED"},
        ]), "SUCCESS")

    def test_cancelled_does_not_mask_pending(self):
        self.assertEqual(self.r([
            {"name": "a", "conclusion": "CANCELLED"},
            {"name": "b", "status": "QUEUED"},
        ]), "PENDING")

    def test_ignored_when_pending(self):
        ignored = "ci/slow-integration-suite"
        forge_mod.set_ignored_when_pending({ignored})
        self.addCleanup(forge_mod.set_ignored_when_pending, set())
        self.assertEqual(self.r([
            {"name": "a", "conclusion": "SUCCESS"},
            {"name": ignored, "status": "IN_PROGRESS"},
        ]), "SUCCESS")


class NoPrPathTest(unittest.TestCase):
    def test_with_pr_false_omits_chip_column(self):
        anc = chain("m0", "a1")
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat": "a1"},
            ancestors=anc,
        )
        brs = {"feat": {"parent": "main", "base": "m0"}}
        # Even if a PR exists, with_pr=False must not render a chip.
        prs = {"feat": {"number": 9, "state": "OPEN", "isDraft": False,
                        "baseRefName": "main", "url": "u",
                        "reviewDecision": None, "statusCheckRollup": []}}
        out = render(git, brs, cur="feat", prs=prs, with_pr=False)
        self.assertNotIn("#9", out)
        self.assertNotIn("(no PR)", out)


class ForgeUnreachableTest(unittest.TestCase):
    """A failed fetch (prs=None) shows a net-off chip on every node, not the
    misleading (no PR) marker."""

    def _render(self, prs):
        anc = chain("m0", "a1", "a2")
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat-a": "a1", "feat-b": "a2"},
            ancestors=anc,
        )
        brs = {"feat-a": {"parent": "main", "base": "m0"},
               "feat-b": {"parent": "feat-a", "base": "a1"}}
        return render(git, brs, cur="feat-b", prs=prs, with_pr=True)

    def test_net_off_chip_on_every_node(self):
        out = self._render(None)
        self.assertEqual(out.count(stack.ui.SYM_NET_OFF), 2)
        self.assertIn(f"{stack.ui.SYM_GITHUB} {stack.ui.SYM_NET_OFF}", out)
        self.assertNotIn("(no PR)", out)

    def test_fetch_failure_is_red(self):
        stack.set_color(True)
        try:
            red = stack.ui.RED
            out = self._render(None)
        finally:
            stack.set_color(False)
        self.assertIn(red, out)

    def test_wait_state_net_off_is_dim_not_red(self):
        anc = chain("m0", "a1")
        git = FakeGit(trunk_name="main",
                      branch_tips={"main": "m0", "feat": "a1"}, ancestors=anc)
        brs = {"feat": {"parent": "main", "base": "m0"}}
        stack.set_color(True)
        try:
            dim, red = stack.ui.DIM, stack.ui.RED
            out = render(git, brs, cur="feat", prs={}, pr_state="wait")
        finally:
            stack.set_color(False)
        self.assertIn(stack.ui.SYM_NET_OFF, out)
        self.assertIn(dim, out)
        self.assertNotIn(red, out)


class ScopeTest(unittest.TestCase):
    """path_to is the primitive that scopes restack (default) and sync
    (default) to the current branch + ancestors, deliberately excluding
    siblings and descendants — the branches that usually live in other
    worktrees and can't be moved from here."""

    def setUp(self):
        # main → a → { b → d, c }.  d is a descendant of b; c is a sibling of b.
        self.brs = {
            "a": {"parent": "main", "base": "m0"},
            "b": {"parent": "a", "base": "a1"},
            "c": {"parent": "a", "base": "a1"},
            "d": {"parent": "b", "base": "b1"},
        }

    def test_chain_is_ancestors_parents_first(self):
        self.assertEqual(stack.path_to(self.brs, "b"), ["a", "b"])
        self.assertEqual(stack.path_to(self.brs, "d"), ["a", "b", "d"])

    def test_chain_excludes_siblings_and_descendants(self):
        chain = stack.path_to(self.brs, "b")
        self.assertNotIn("c", chain)  # sibling
        self.assertNotIn("d", chain)  # descendant

    def test_root_chain_is_just_the_root(self):
        self.assertEqual(stack.path_to(self.brs, "a"), ["a"])

    def test_subtree_still_covers_the_whole_tree(self):
        # --all restack/sync uses subtree(root); it must include siblings and
        # descendants that path_to omits.
        self.assertEqual(
            sorted(stack.subtree(self.brs, "a")), ["a", "b", "c", "d"]
        )


def _pr(number, stack_info=None, base="main"):
    return {"number": number, "state": "OPEN", "isDraft": False,
            "baseRefName": base, "url": "u", "reviewDecision": None,
            "statusCheckRollup": [], "stack": stack_info}


class GithubStackChipTest(unittest.TestCase):
    """The native-stack marker appended to the PR chip."""

    def _render(self, prs):
        git = FakeGit(
            trunk_name="main",
            branch_tips={"main": "m0", "feat-a": "a2", "feat-b": "b1"},
            ancestors=chain("m0", "a1", "a2", "b1"),
            origin={"feat-a": (0, 0), "feat-b": (0, 0)},
        )
        brs = {"feat-a": {"parent": "main", "base": "m0"},
               "feat-b": {"parent": "feat-a", "base": "a2"}}
        return render(git, brs, cur="feat-b", prs=prs, with_pr=True)

    def test_chip_shows_stack_number(self):
        out = self._render({
            "feat-a": _pr(1, {"number": 58215, "size": 2, "position": 1}),
            "feat-b": _pr(2, {"number": 58215, "size": 2, "position": 2}),
        })
        self.assertIn(f"{stack.SYM_STACK}58215", out)

    def test_no_marker_without_stack_membership(self):
        out = self._render({"feat-a": _pr(1), "feat-b": _pr(2)})
        self.assertNotIn(stack.SYM_STACK, out)


class GithubStackDriftTest(unittest.TestCase):
    brs = {"a": {"parent": "main", "base": "m0"},
           "b": {"parent": "a", "base": "a1"}}

    def test_no_drift_when_ordered_consistently(self):
        prs = {"a": _pr(1, {"number": 5, "size": 2, "position": 1}),
               "b": _pr(2, {"number": 5, "size": 2, "position": 2})}
        self.assertEqual(stack.github_stack_drift(self.brs, prs), {})

    def test_child_in_a_different_stack_is_drift(self):
        prs = {"a": _pr(1, {"number": 5, "size": 1, "position": 1}),
               "b": _pr(2, {"number": 9, "size": 1, "position": 1})}
        self.assertIn("b", stack.github_stack_drift(self.brs, prs))

    def test_child_ordered_below_parent_is_drift(self):
        prs = {"a": _pr(1, {"number": 5, "size": 2, "position": 2}),
               "b": _pr(2, {"number": 5, "size": 2, "position": 1})}
        self.assertIn("b", stack.github_stack_drift(self.brs, prs))

    def test_partial_registration_is_not_drift(self):
        # Only part of a chain being registered is normal, not a disagreement.
        prs = {"a": _pr(1, None),
               "b": _pr(2, {"number": 5, "size": 1, "position": 1})}
        self.assertEqual(stack.github_stack_drift(self.brs, prs), {})

    def test_no_stack_data_at_all_is_not_drift(self):
        self.assertEqual(
            stack.github_stack_drift(self.brs, {"a": _pr(1), "b": _pr(2)}), {}
        )


class PlanStackUpdateTest(unittest.TestCase):
    """The create/add/drift decision, as pure list math."""

    def test_creates_when_none_exists(self):
        self.assertEqual(stack.plan_stack_update([], [1, 2]), ("create", [1, 2]))

    def test_single_pr_is_not_a_stack(self):
        self.assertEqual(stack.plan_stack_update([], [1]), ("noop", None))

    def test_noop_when_identical(self):
        self.assertEqual(stack.plan_stack_update([1, 2], [1, 2]), ("noop", None))

    def test_adds_only_the_delta(self):
        self.assertEqual(stack.plan_stack_update([1, 2], [1, 2, 3]), ("add", [3]))

    def test_merged_members_still_read_as_a_prefix(self):
        # GitHub keeps merged PRs in the stack, so a chain that has partly
        # landed must still resolve to add — never drift.
        self.assertEqual(stack.plan_stack_update([1, 2], [1, 2, 3, 4]), ("add", [3, 4]))

    def test_reorder_is_drift(self):
        action, _ = stack.plan_stack_update([1, 2], [2, 1])
        self.assertEqual(action, "drift")

    def test_removal_is_drift(self):
        action, _ = stack.plan_stack_update([1, 2, 3], [1, 3])
        self.assertEqual(action, "drift")

    def test_divergent_prefix_is_drift(self):
        action, _ = stack.plan_stack_update([1, 2], [1, 9, 3])
        self.assertEqual(action, "drift")


class _StackApiForge:
    """Records the native-stack mutations sync_github_stack attempts."""

    NUM_PREFIX = "#"
    NOUN = "PR"

    def __init__(self, existing=None, create_ok=True, add_ok=True):
        self._existing = existing
        self.created = []
        self.added = []
        self.marked_unavailable = False
        self._create_ok = create_ok
        self._add_ok = add_ok

    def stack_for_pr(self, number):
        return self._existing

    def stack_create(self, prs):
        self.created.append(list(prs))
        return self._create_ok, ({"number": 77} if self._create_ok else None)

    def stack_add(self, stack_number, prs):
        self.added.append((stack_number, list(prs)))
        return self._add_ok, None

    def mark_stacks_unavailable(self, git):
        self.marked_unavailable = True


class SyncGithubStackTest(unittest.TestCase):
    order = ["a", "b"]
    prs = {"a": _pr(10), "b": _pr(11)}

    def test_creates_when_no_stack_exists(self):
        f = _StackApiForge(existing=None)
        stack.sync_github_stack(None, f, self.order, self.prs, dry=False)
        self.assertEqual(f.created, [[10, 11]])

    def test_dry_run_sends_nothing(self):
        f = _StackApiForge(existing=None)
        stack.sync_github_stack(None, f, self.order, self.prs, dry=True)
        self.assertEqual(f.created, [])
        self.assertEqual(f.added, [])

    def test_appends_only_the_new_tip(self):
        f = _StackApiForge(existing={"number": 5, "pull_requests": [{"number": 10}]})
        stack.sync_github_stack(
            None, f, ["a", "b", "c"],
            {"a": _pr(10), "b": _pr(11), "c": _pr(12)}, dry=False)
        self.assertEqual(f.added, [(5, [11, 12])])
        self.assertEqual(f.created, [])

    def test_single_pr_chain_is_left_alone(self):
        f = _StackApiForge(existing=None)
        stack.sync_github_stack(None, f, ["a"], {"a": _pr(10)}, dry=False)
        self.assertEqual(f.created, [])

    def test_drift_is_never_destructive(self):
        f = _StackApiForge(existing={"number": 5, "pull_requests": [
            {"number": 11}, {"number": 10}]})  # reversed vs local
        stack.sync_github_stack(None, f, self.order, self.prs, dry=False)
        self.assertEqual(f.created, [])
        self.assertEqual(f.added, [])

    def test_failed_create_flips_the_capability_cache(self):
        f = _StackApiForge(existing=None, create_ok=False)
        stack.sync_github_stack(None, f, self.order, self.prs, dry=False)
        self.assertTrue(f.marked_unavailable)

    def test_branches_without_prs_are_skipped(self):
        f = _StackApiForge(existing=None)
        stack.sync_github_stack(
            None, f, ["a", "b", "c"],
            {"a": _pr(10), "b": None, "c": _pr(12)}, dry=False)
        self.assertEqual(f.created, [[10, 12]])


if __name__ == "__main__":
    unittest.main()
