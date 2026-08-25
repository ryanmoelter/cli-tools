"""Tests for wt's `list` rendering and pure helpers.

Pins the exact table text (column alignment, the → cwd marker, ↗ ↑↓ +
status clusters, detached rows, and the PR column in each of its states)
against hand-built fakes, plus the naming/matching helpers commands are built
on.
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeDone, FakeWtCtx  # noqa: E402
from load_script import load_script  # noqa: E402

wt = load_script("wt", "wt_mod")

# render_list takes the forge icon as an argument, so these pin the
# icon-present (Nerd Font) layout explicitly rather than depending on the
# ambient glyph mode. PlainGlyphRenderListTest covers the icon-less default.
GH = "\uf09b"  # nf-fa-github
NET_OFF = wt.ui.SYM_NET_OFF
LOADING = wt.ui.SYM_LOADING
NOT_CONN = wt.ui.NET_OFF_LABEL

ROWS = [
    ("dotfiles", "main", "4", 1, 2, 1, True),
    ("scratch", "ryanm/scratch", "", 0, 0, 0, True),
    ("det", "abc123 (detached)", "0", 0, 0, 0, False),
]

OPEN_PR = {
    "number": 7,
    "state": "OPEN",
    "isDraft": False,
    "reviewDecision": None,
    "statusCheckRollup": [],
}

CLOSED_PR = dict(OPEN_PR, number=3, state="CLOSED")


class RenderListTest(unittest.TestCase):
    def test_off_state_omits_pr_column(self):
        out = wt.render_list(ROWS, "dotfiles", "off", {}, "#")
        self.assertEqual(out, "\n".join([
            "→ dotfiles  main  ↗4 ↑2 ↓1 +   ",
            "  scratch   ryanm/scratch      ",
            "  det       abc123 (detached)  ",
        ]))

    def test_paths_column_appends_each_directory(self):
        dirs = [".", ".worktrees", "/elsewhere"]
        out = wt.render_list(ROWS, "dotfiles", "off", {}, "#", None, (), dirs)
        self.assertEqual(out, "\n".join([
            "→ dotfiles  main  ↗4 ↑2 ↓1 +  .",
            "  scratch   ryanm/scratch  .worktrees",
            "  det       abc123 (detached)  /elsewhere",
        ]))

    def test_no_paths_column_by_default(self):
        self.assertNotIn(".worktrees", wt.render_list(ROWS, "dotfiles", "off", {}, "#"))

    def test_wait_state_shows_net_off_chip(self):
        out = wt.render_list(ROWS, "dotfiles", "wait", {}, "#", GH)
        self.assertEqual(out, "\n".join([
            f"→ dotfiles  main  ↗4 ↑2 ↓1 +   {GH} {NET_OFF} {NOT_CONN}",
            f"  scratch   ryanm/scratch      {GH} {NET_OFF} {NOT_CONN}",
            f"  det       abc123 (detached)  {NET_OFF}",
        ]))

    def test_error_state_shows_net_off_chip(self):
        out = wt.render_list(ROWS, "dotfiles", "error", {}, "#", GH)
        self.assertEqual(out, "\n".join([
            f"→ dotfiles  main  ↗4 ↑2 ↓1 +   {GH} {NET_OFF} {NOT_CONN}",
            f"  scratch   ryanm/scratch      {GH} {NET_OFF} {NOT_CONN}",
            f"  det       abc123 (detached)  {NET_OFF}",
        ]))

    def test_loading_state_shows_loading_chip(self):
        out = wt.render_list(ROWS, "dotfiles", "loading", {}, "#", GH)
        self.assertEqual(out, "\n".join([
            f"→ dotfiles  main  ↗4 ↑2 ↓1 +   {GH} {LOADING}",
            f"  scratch   ryanm/scratch      {GH} {LOADING}",
            f"  det       abc123 (detached)  {NET_OFF}",
        ]))

    def test_local_only_branch_renders_local_in_every_state(self):
        # No upstream means the forge can't have a PR for it, so the fetch's
        # state is irrelevant — never show it as loading or unavailable.
        rows = [("solo", "ryanm/solo", "2", 0, 0, 0, False)]
        for pr_state in ("on", "loading", "wait", "error"):
            out = wt.render_list(rows, None, pr_state, {}, "#", GH)
            self.assertEqual(out, f"  solo  ryanm/solo  ↗2  {NET_OFF}", pr_state)

    def test_pushed_branch_still_reflects_the_fetch_state(self):
        rows = [("solo", "ryanm/solo", "2", 0, 0, 0, True)]
        loading = wt.render_list(rows, None, "loading", {}, "#", GH)
        self.assertEqual(loading, f"  solo  ryanm/solo  ↗2  {GH} {LOADING}")

    def test_loading_chip_differs_from_wait_chip(self):
        # A fetch that times out redraws loading → wait, so the two must not
        # render identically or the redraw would be invisible.
        loading = wt.render_list(ROWS, "dotfiles", "loading", {}, "#", GH)
        wait = wt.render_list(ROWS, "dotfiles", "wait", {}, "#", GH)
        self.assertNotEqual(loading, wait)

    def test_only_error_is_red_wait_and_loading_are_dim(self):
        wt.set_color(True)
        try:
            red, dim = wt.ui.RED, wt.ui.DIM
            err = wt.render_list(ROWS, "dotfiles", "error", {}, "#", GH)
            wait = wt.render_list(ROWS, "dotfiles", "wait", {}, "#", GH)
            loading = wt.render_list(ROWS, "dotfiles", "loading", {}, "#", GH)
        finally:
            wt.set_color(False)
        self.assertIn(red, err)
        self.assertNotIn(red, wait)
        self.assertIn(dim, wait)
        self.assertNotIn(red, loading)
        self.assertIn(dim, loading)

    def test_on_state_chips_lead_with_forge_icon(self):
        out = wt.render_list(ROWS, "dotfiles", "on", {"main": OPEN_PR}, "#", GH)
        self.assertEqual(out, "\n".join([
            f"→ dotfiles  main  ↗4 ↑2 ↓1 +   {GH} #7 ◉",
            f"  scratch   ryanm/scratch      {GH} –",
            f"  det       abc123 (detached)  {NET_OFF}",
        ]))

    def test_gitlab_number_prefix(self):
        gl = wt.ui.SYM_GITLAB
        out = wt.render_list(ROWS, "dotfiles", "on", {"main": OPEN_PR}, "!", gl)
        self.assertIn(f"{gl} !7 ◉", out)
        self.assertNotIn("#7", out)

    def test_trunk_branch_renders_as_no_pr(self):
        out = wt.render_list(ROWS, "dotfiles", "on", {"main": CLOSED_PR}, "#", GH,
                             {"main"})
        self.assertIn(f"{GH} –", out.splitlines()[0])
        self.assertNotIn("#3", out)

    def test_trunk_branch_without_upstream_falls_back_to_net_off(self):
        rows = [("dotfiles", "main", "", 0, 0, 0, False)]
        out = wt.render_list(rows, None, "on", {"main": CLOSED_PR}, "#", GH, {"main"})
        self.assertEqual(out, f"  dotfiles  main  {NET_OFF}")

    def test_non_trunk_branch_keeps_its_closed_chip(self):
        out = wt.render_list(ROWS, "dotfiles", "on", {"ryanm/scratch": CLOSED_PR},
                             "#", GH, {"main"})
        self.assertIn(f"{GH} #3 {wt.ui.SYM_CLOSED}", out.splitlines()[1])

    def test_every_trunk_branch_is_suppressed(self):
        rows = [
            ("staging", "staging", "", 0, 0, 0, True),
            ("main", "main", "", 0, 0, 0, True),
        ]
        prs = {"staging": CLOSED_PR, "main": OPEN_PR}
        out = wt.render_list(rows, None, "on", prs, "#", GH, {"staging", "main"})
        self.assertEqual(out, "\n".join([
            f"  staging  staging  {GH} –",
            f"  main     main     {GH} –",
        ]))

    def test_no_marker_when_cwd_outside_worktrees(self):
        out = wt.render_list(ROWS, None, "off", {}, "#")
        self.assertTrue(all(line.startswith("  ") for line in out.splitlines()))

    def test_dirty_only_cluster(self):
        out = wt.render_list([("a", "b", "", 1, 0, 0, True)], None, "off", {}, "#")
        self.assertEqual(out, "  a  b  +  ")

    def test_zero_ahead_renders_no_arrow(self):
        out = wt.render_list([("a", "b", "0", 0, 0, 0, True)], None, "off", {}, "#")
        self.assertEqual(out, "  a  b  ")

    def test_remote_ahead_behind_cluster(self):
        out = wt.render_list([("a", "b", "3", 0, 2, 1, True)], None, "off", {}, "#")
        self.assertEqual(out, "  a  b  ↗3 ↑2 ↓1  ")

    def test_current_row_is_bold(self):
        BOLD, RESET = "\033[1m", "\033[0m"
        wt.ui.set_color(True)
        self.addCleanup(wt.ui.set_color, False)
        out = wt.render_list(ROWS, "dotfiles", "on", {"main": OPEN_PR}, "#", GH)
        cur, scratch, det = out.splitlines()
        # The current row is bold-wrapped and re-arms bold after each interior
        # reset, so no bare reset survives except the final one.
        self.assertTrue(cur.startswith(BOLD))
        self.assertTrue(cur.endswith(RESET))
        interior = cur[: -len(RESET)]  # drop the final reset
        self.assertNotIn(RESET, interior.replace(RESET + BOLD, ""))
        # Other rows are untouched.
        self.assertNotIn(BOLD, scratch)
        self.assertNotIn(BOLD, det)


# Parallel to ROWS, not keyed by name — worktree names are not unique.
PATHS = ["/repo", "/repo/.worktrees/scratch", "/repo/.worktrees/det"]


class PlainGlyphRenderListTest(unittest.TestCase):
    """The default (no Nerd Font) layout: the forge icon collapses away, so
    every PR chip loses its two-column lead and the table stays aligned."""

    def test_chips_have_no_forge_icon_lead(self):
        out = wt.render_list(ROWS, "dotfiles", "on", {"main": OPEN_PR}, "#", "")
        self.assertEqual(out, "\n".join([
            f"→ dotfiles  main  ↗4 ↑2 ↓1 +   #7 {wt.ui.SYM_OPEN}",
            "  scratch   ryanm/scratch      –",
            f"  det       abc123 (detached)  {NET_OFF}",
        ]))

    def test_net_off_and_loading_lose_the_lead(self):
        wait = wt.render_list(ROWS, "dotfiles", "wait", {}, "#", "")
        self.assertEqual(wait, "\n".join([
            f"→ dotfiles  main  ↗4 ↑2 ↓1 +   {NET_OFF} {NOT_CONN}",
            f"  scratch   ryanm/scratch      {NET_OFF} {NOT_CONN}",
            f"  det       abc123 (detached)  {NET_OFF}",
        ]))
        loading = wt.render_list(ROWS, "dotfiles", "loading", {}, "#", "")
        self.assertEqual(loading, "\n".join([
            f"→ dotfiles  main  ↗4 ↑2 ↓1 +   {LOADING}",
            f"  scratch   ryanm/scratch      {LOADING}",
            f"  det       abc123 (detached)  {NET_OFF}",
        ]))


class BuildListJsonTest(unittest.TestCase):
    """The --json payload the Sublime plugin consumes."""

    def payload(self, *, cwd="dotfiles", pr_state="off", prs=None, trunk=()):
        return wt.build_list_json(ROWS, cwd, pr_state, prs or {}, PATHS, trunk)

    def test_shape_and_fields(self):
        got = self.payload()
        self.assertEqual(got["pr_state"], "off")
        self.assertEqual([w["name"] for w in got["worktrees"]],
                         ["dotfiles", "scratch", "det"])
        self.assertEqual(got["worktrees"][0], {
            "name": "dotfiles", "path": "/repo", "branch": "main", "current": True,
            "ahead": 4, "dirty": True, "remote_ahead": 2, "remote_behind": 1,
            "has_upstream": True, "pr": None,
        })

    def test_current_marks_only_the_cwd_row(self):
        got = self.payload()
        self.assertEqual([w["current"] for w in got["worktrees"]],
                         [True, False, False])

    def test_no_current_row_when_cwd_outside_worktrees(self):
        got = self.payload(cwd=None)
        self.assertTrue(all(not w["current"] for w in got["worktrees"]))

    def test_blank_ahead_becomes_zero(self):
        # build_rows blanks the base branch's ahead count; JSON keeps it numeric.
        got = self.payload()
        self.assertEqual(got["worktrees"][1]["ahead"], 0)

    def test_pr_included_when_state_on(self):
        got = self.payload(pr_state="on", prs={"main": OPEN_PR})
        self.assertEqual(got["worktrees"][0]["pr"], {
            "number": 7, "state": "OPEN", "is_draft": False,
            "url": None, "title": None,
        })
        self.assertIsNone(got["worktrees"][1]["pr"])

    def test_trunk_branches_report_no_pr(self):
        got = self.payload(pr_state="on", prs={"main": OPEN_PR}, trunk={"main"})
        self.assertIsNone(got["worktrees"][0]["pr"])

    def test_pr_omitted_in_every_non_on_state(self):
        # A consumer distinguishes "no PR" from "unavailable" via pr_state, so
        # these states must not leak a stale PR onto a row.
        for state in ("off", "wait", "error"):
            got = self.payload(pr_state=state, prs={"main": OPEN_PR})
            self.assertEqual(got["pr_state"], state)
            self.assertIsNone(got["worktrees"][0]["pr"], state)


class RedrawPrefixTest(unittest.TestCase):
    def test_moves_up_by_line_count_and_clears(self):
        self.assertEqual(wt.redraw_prefix(3), "\033[3A\033[J")



class PrintListLiveTest(unittest.TestCase):
    CURSOR_UP = "\033["

    def run_live(self, done, box, live=True):
        buf = io.StringIO()
        stdout, sys.stdout = sys.stdout, buf
        try:
            wt.print_list_live(ROWS, "dotfiles", (done, box, "#", GH), (), live)
        finally:
            sys.stdout = stdout
        return buf.getvalue()

    def test_no_forge_prints_the_local_table_once(self):
        buf = io.StringIO()
        stdout, sys.stdout = sys.stdout, buf
        try:
            wt.print_list_live(ROWS, "dotfiles", None, (), True)
        finally:
            sys.stdout = stdout
        out = buf.getvalue()
        self.assertEqual(out, wt.render_list(ROWS, "dotfiles", "off", {}, "#") + "\n")
        self.assertNotIn(self.CURSOR_UP, out)

    def test_tty_prints_loading_then_redraws_resolved(self):
        # The local table never waits on the forge, so a TTY always paints the
        # loading frame and redraws — there is no fast path that skips it.
        prs = {"main": OPEN_PR}
        done = FakeDone(True)
        out = self.run_live(done, {"prs": prs})
        loading = wt.render_list(ROWS, "dotfiles", "loading", {}, "#", GH)
        resolved = wt.render_list(ROWS, "dotfiles", "on", prs, "#", GH)
        self.assertEqual(
            out, f"{loading}\n{wt.redraw_prefix(len(ROWS))}{resolved}\n")
        self.assertEqual(done.waits, [wt._LIST_PR_TOTAL])

    def test_fetch_that_fails_redraws_the_error_state(self):
        out = self.run_live(FakeDone(True), {"prs": None})
        self.assertTrue(
            out.endswith(wt.render_list(ROWS, "dotfiles", "error", {}, "#", GH) + "\n"))

    def test_fetch_that_never_lands_ends_in_wait_not_loading(self):
        done = FakeDone(False)
        out = self.run_live(done, {})
        self.assertTrue(
            out.endswith(wt.render_list(ROWS, "dotfiles", "wait", {}, "#", GH) + "\n"))
        self.assertEqual(done.waits, [wt._LIST_PR_TOTAL])

    def test_non_tty_prints_once_with_no_cursor_codes(self):
        prs = {"main": OPEN_PR}
        done = FakeDone(True)
        out = self.run_live(done, {"prs": prs}, live=False)
        self.assertEqual(
            out, wt.render_list(ROWS, "dotfiles", "on", prs, "#", GH) + "\n")
        self.assertNotIn(self.CURSOR_UP, out)
        self.assertEqual(done.waits, [wt._LIST_PR_TOTAL])


class NameCapTest(unittest.TestCase):
    LONG = "ryanm/a-very-long-feature-branch-name-that-overflows"
    LONG_WT = "a-very-long-worktree-directory-name-that-overflows"

    def test_long_branch_truncated_to_max_with_ellipsis(self):
        rows = [("wt", self.LONG, "", 0, 0, 0, True)]
        out = wt.render_list(rows, None, "off", {}, "#")
        # The displayed name is clipped to the cap and ends in an ellipsis.
        self.assertIn(self.LONG[: wt._NAME_MAX - 1] + "…", out)
        self.assertNotIn(self.LONG, out)

    def test_long_worktree_name_truncated_to_max_with_ellipsis(self):
        rows = [(self.LONG_WT, "ryanm/feat", "", 0, 0, 0, True)]
        out = wt.render_list(rows, None, "off", {}, "#")
        self.assertIn(self.LONG_WT[: wt._NAME_MAX - 1] + "…", out)
        self.assertNotIn(self.LONG_WT, out)

    def test_long_worktree_name_does_not_widen_the_column(self):
        rows = [(self.LONG_WT, "b", "", 0, 0, 0, True),
                ("short", "c", "", 0, 0, 0, True)]
        out = wt.render_list(rows, None, "off", {}, "#")
        long_line, short_line = out.splitlines()
        self.assertEqual(len(long_line), len(short_line))
        # Column is the cap, not the untruncated name's length.
        self.assertLess(len(long_line), 2 + len(self.LONG_WT) + 5)

    def test_short_names_are_not_truncated(self):
        rows = [("wt", "ryanm/feat", "", 0, 0, 0, True)]
        out = wt.render_list(rows, None, "off", {}, "#")
        self.assertIn("ryanm/feat", out)
        self.assertNotIn("…", out)

    def test_pr_lookup_uses_full_name_despite_truncation(self):
        # The chip must still resolve even when the displayed name is clipped.
        rows = [("wt", self.LONG, "", 0, 0, 0, True)]
        out = wt.render_list(rows, None, "on", {self.LONG: OPEN_PR}, "#", GH)
        self.assertIn(f"{GH} #7 ◉", out)

    def test_cwd_marker_uses_full_name_despite_truncation(self):
        BOLD = "\033[1m"
        wt.ui.set_color(True)
        self.addCleanup(wt.ui.set_color, False)
        rows = [(self.LONG_WT, "ryanm/feat", "", 0, 0, 0, True)]
        out = wt.render_list(rows, self.LONG_WT, "off", {}, "#")
        self.assertIn("→", out)
        self.assertTrue(out.startswith(BOLD))


class BuildRowsTest(unittest.TestCase):
    def test_rows_follow_worktree_order_and_status(self):
        ctx = FakeWtCtx(worktrees=ROWS)
        self.assertEqual(wt.build_rows(ctx), ROWS)

    def test_base_branch_row_drops_ahead_of_base(self):
        # On the base branch, ahead-of-base just re-counts unpushed commits the
        # ↑ column already shows, so it's suppressed; ↑/↓ are kept.
        ctx = FakeWtCtx(worktrees=ROWS, base_branch="main")
        rows = wt.build_rows(ctx)
        self.assertEqual(rows[0], ("dotfiles", "main", "", 1, 2, 1, True))
        # Non-base rows keep their ahead count.
        self.assertEqual(rows[1], ROWS[1])
        self.assertEqual(rows[2], ROWS[2])


class StatusClusterTest(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(wt.status_cluster("4", 1), "↗4 +")
        self.assertEqual(wt.status_cluster("4", 0), "↗4")
        self.assertEqual(wt.status_cluster("", 1), "+")
        self.assertEqual(wt.status_cluster("0", 1), "+")
        self.assertEqual(wt.status_cluster("", 0), "")
        self.assertEqual(wt.status_cluster("0", 0), "")

    def test_remote_ahead_behind(self):
        self.assertEqual(wt.status_cluster("4", 1, 2, 1), "↗4 ↑2 ↓1 +")
        self.assertEqual(wt.status_cluster("", 0, 2, 0), "↑2")
        self.assertEqual(wt.status_cluster("", 0, 0, 3), "↓3")
        self.assertEqual(wt.status_cluster("4", 0, 0, 0), "↗4")


class FolderForBranchTest(unittest.TestCase):
    def test_strips_first_segment_and_flattens(self):
        self.assertEqual(wt.folder_for_branch("ryanm/foo"), "foo")
        self.assertEqual(wt.folder_for_branch("cc/feature/thing"), "feature-thing")
        self.assertEqual(wt.folder_for_branch("spike"), "spike")


class BranchForTest(unittest.TestCase):
    def test_configured_prefix_added_once(self):
        ctx = FakeWtCtx(prefix="ryanm/")
        self.assertEqual(wt.branch_for(ctx, "foo"), "ryanm/foo")
        self.assertEqual(wt.branch_for(ctx, "ryanm/foo"), "ryanm/foo")

    def test_explicit_empty_prefix_passes_through(self):
        ctx = FakeWtCtx(prefix="ryanm/")
        self.assertEqual(wt.branch_for(ctx, "foo", prefix=""), "foo")

    def test_empty_configured_prefix(self):
        ctx = FakeWtCtx(prefix="")
        self.assertEqual(wt.branch_for(ctx, "foo"), "foo")


class ResolveBranchTest(unittest.TestCase):
    def test_exact_match_wins_over_suffix(self):
        ctx = FakeWtCtx(branches=["wt", "ryanm/wt"])
        self.assertEqual(wt.resolve_branch(ctx, "wt"), "wt")

    def test_unique_suffix_match(self):
        ctx = FakeWtCtx(branches=["ryanm/wt", "ryanm/other"])
        self.assertEqual(wt.resolve_branch(ctx, "wt"), "ryanm/wt")

    def test_suffix_needs_full_segment(self):
        # "t" is a suffix of "wt" but not a full /-delimited segment.
        ctx = FakeWtCtx(branches=["ryanm/wt"])
        with self.assertRaises(SystemExit):
            wt.resolve_branch(ctx, "t")

    def test_zero_matches_dies(self):
        ctx = FakeWtCtx(branches=["ryanm/other"])
        with self.assertRaises(SystemExit):
            wt.resolve_branch(ctx, "wt")

    def test_ambiguous_dies(self):
        ctx = FakeWtCtx(branches=["ryanm/wt", "cc/wt"])
        with self.assertRaises(SystemExit):
            wt.resolve_branch(ctx, "wt")


if __name__ == "__main__":
    unittest.main()
