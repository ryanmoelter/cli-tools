"""Tests for wt's TabOpener driver-selection logic, with the two
subprocess-touching seams (_which/_run) faked so no terminal is harmed."""

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

GROUPS_JSON = json.dumps({
    "groups": [
        {"id": "g-other", "member_workspace_ids": ["ws-x"]},
        {"id": "g-mine", "member_workspace_ids": ["ws-1", "ws-2"]},
    ]
})


def ws_list_json(*entries):
    """`workspace list --json` payload from (id, ref, custom_color) triples."""
    return json.dumps({
        "workspaces": [
            {"id": wid, "ref": ref, "custom_color": color}
            for wid, ref, color in entries
        ]
    })


def make_opener(env, platform="darwin", have=(), responses=()):
    """A TabOpener whose _which knows only `have` and whose _run answers from
    `responses` [(args_prefix, (rc, stdout))], recording every call."""

    class FakeTabOpener(wt.TabOpener):
        def __init__(self):
            super().__init__(env=env, platform=platform)
            self.calls = []

        def _which(self, name):
            return f"/bin/{name}" if name in have else None

        def _run(self, args, input_=None):
            self.calls.append((list(args), input_))
            for prefix, result in responses:
                if args[:len(prefix)] == list(prefix):
                    return result
            return 1, ""

    return FakeTabOpener()


def open_capturing(opener, dir_, workspace=False):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        opener.open(dir_, workspace=workspace)
    return out.getvalue()


class CmuxTest(unittest.TestCase):
    CMUX_ENV = {"CMUX_WORKSPACE_ID": "ws-1"}

    @staticmethod
    def _set_color_call(opener):
        """The set-color workspace-action call's args, or None."""
        return next(
            (args for args, _ in opener.calls
             if args[1:4] == ["workspace-action", "--action", "set-color"]),
            None,
        )

    def test_grouped_create_in_current_workspaces_group(self):
        # No workspace-list response stubbed, so the color step reads (1, "")
        # and no-ops — keeping this test focused on the create args.
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "workspace", "create"), (0, "OK workspace:9\n")),
        ])
        out = open_capturing(opener, "/wt/foo", workspace=True)
        self.assertEqual(out, "")
        create = next(args for args, _ in opener.calls if args[1:3] == ["workspace", "create"])
        self.assertIn("g-mine", create)
        self.assertEqual(create[create.index("--cwd") + 1], "/wt/foo")
        self.assertEqual(create[create.index("--group-reference") + 1], "ws-1")
        self.assertIsNone(self._set_color_call(opener))

    def test_grouped_create_applies_dominant_group_color(self):
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "workspace", "create"), (0, "OK workspace:9\n")),
            (("cmux", "--id-format", "both", "workspace", "list", "--json"),
             (0, ws_list_json(("ws-1", "workspace:1", "#AED83F"),
                              ("ws-2", "workspace:2", "#AED83F")))),
            (("cmux", "workspace-action"), (0, "")),
        ])
        open_capturing(opener, "/wt/foo", workspace=True)
        call = self._set_color_call(opener)
        self.assertIsNotNone(call)
        self.assertEqual(call[call.index("--color") + 1], "#AED83F")
        self.assertEqual(call[call.index("--workspace") + 1], "workspace:9")

    def test_grouped_create_tie_prefers_reference_workspace(self):
        # ws-1 is the reference (CMUX_WORKSPACE_ID); its color wins the 1-1 tie.
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "workspace", "create"), (0, "OK workspace:9\n")),
            (("cmux", "--id-format", "both", "workspace", "list", "--json"),
             (0, ws_list_json(("ws-2", "workspace:2", "#222222"),
                              ("ws-1", "workspace:1", "#111111")))),
            (("cmux", "workspace-action"), (0, "")),
        ])
        open_capturing(opener, "/wt/foo", workspace=True)
        call = self._set_color_call(opener)
        self.assertIsNotNone(call)
        self.assertEqual(call[call.index("--color") + 1], "#111111")

    def test_grouped_create_no_colored_members_skips_set_color(self):
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "workspace", "create"), (0, "OK workspace:9\n")),
            (("cmux", "--id-format", "both", "workspace", "list", "--json"),
             (0, ws_list_json(("ws-1", "workspace:1", None),
                              ("ws-2", "workspace:2", None)))),
        ])
        open_capturing(opener, "/wt/foo", workspace=True)
        self.assertIsNone(self._set_color_call(opener))

    def test_grouped_create_color_step_failure_is_silent(self):
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "workspace", "create"), (0, "OK workspace:9\n")),
            # workspace list fails → falls through to the (1, "") default.
        ])
        out = open_capturing(opener, "/wt/foo", workspace=True)
        self.assertEqual(out, "")
        self.assertIsNone(self._set_color_call(opener))

    def test_failed_create_falls_back_to_bare_cmux(self):
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "workspace", "create"), (1, "")),
            (("cmux", "/wt/foo"), (0, "")),
        ])
        out = open_capturing(opener, "/wt/foo", workspace=True)
        self.assertEqual(out, "")
        self.assertEqual(opener.calls[-1][0], ["cmux", "/wt/foo"])

    def test_no_group_membership_skips_create(self):
        opener = make_opener({"CMUX_WORKSPACE_ID": "ws-elsewhere"}, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "/wt/foo"), (0, "")),
        ])
        open_capturing(opener, "/wt/foo", workspace=True)
        self.assertNotIn(
            ["workspace", "create"], [args[1:3] for args, _ in opener.calls]
        )

    def test_missing_cmux_binary_falls_through(self):
        opener = make_opener(self.CMUX_ENV, platform="linux", have=())
        out = open_capturing(opener, "/wt/foo", workspace=True)
        self.assertEqual(opener.calls, [])
        self.assertIn("wt: /wt/foo", out)

    def test_socket_path_alone_triggers_cmux_without_group_lookup(self):
        opener = make_opener({"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}, have=("cmux",), responses=[
            (("cmux", "/wt/foo"), (0, "")),
        ])
        open_capturing(opener, "/wt/foo", workspace=True)
        self.assertEqual(opener.calls, [(["cmux", "/wt/foo"], None)])


class CmuxSurfaceTest(unittest.TestCase):
    CMUX_ENV = {"CMUX_WORKSPACE_ID": "ws-1"}

    def test_surface_creates_cds_and_renames(self):
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "new-surface"), (0, "OK surface:7 pane:3 workspace:2\n")),
            (("cmux", "send"), (0, "")),
            (("cmux", "send-key"), (0, "")),
            (("cmux", "rename-tab"), (0, "")),
        ])
        out = open_capturing(opener, "/wt/foo")
        self.assertEqual(out, "")
        calls = [args for args, _ in opener.calls]
        self.assertEqual(calls, [
            ["cmux", "new-surface", "--type", "terminal", "--focus", "false"],
            ["cmux", "send", "--surface", "surface:7", 'cd "/wt/foo"'],
            ["cmux", "send-key", "--surface", "surface:7", "enter"],
            ["cmux", "rename-tab", "--surface", "surface:7", "foo"],
        ])

    def test_cd_and_rename_failures_are_tolerated(self):
        # Only new-surface succeeds; the surface still counts as opened.
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "new-surface"), (0, "OK surface:7 pane:3 workspace:2\n")),
        ])
        out = open_capturing(opener, "/wt/foo")
        self.assertEqual(out, "")

    def test_failed_new_surface_never_creates_a_workspace(self):
        opener = make_opener(self.CMUX_ENV, platform="darwin",
                             have=("cmux", "osascript"), responses=[
            (("cmux", "new-surface"), (1, "")),
            (("osascript",), (0, "")),
        ])
        out = open_capturing(opener, "/wt/foo")
        self.assertEqual(out, "")
        self.assertEqual([args[0] for args, _ in opener.calls], ["cmux", "osascript"])

    def test_unparseable_new_surface_output_falls_back_to_ghostty(self):
        opener = make_opener(self.CMUX_ENV, platform="darwin",
                             have=("cmux", "osascript"), responses=[
            (("cmux", "new-surface"), (0, "boom")),
            (("osascript",), (0, "")),
        ])
        open_capturing(opener, "/wt/foo")
        self.assertEqual(opener.calls[-1][0], ["osascript"])

    def test_outside_cmux_uses_ghostty(self):
        opener = make_opener({}, platform="darwin", have=("osascript",), responses=[
            (("osascript",), (0, "")),
        ])
        out = open_capturing(opener, "/wt/foo")
        self.assertEqual(out, "")
        self.assertEqual(opener.calls[0][0], ["osascript"])

    def test_workspace_flag_never_touches_new_surface(self):
        opener = make_opener(self.CMUX_ENV, have=("cmux",), responses=[
            (("cmux", "rpc", "workspace.group.list"), (0, GROUPS_JSON)),
            (("cmux", "workspace", "create"), (0, "OK workspace:9\n")),
        ])
        open_capturing(opener, "/wt/foo", workspace=True)
        self.assertNotIn(
            "new-surface", [args[1] for args, _ in opener.calls if len(args) > 1]
        )


class CmdOpenFlagTest(unittest.TestCase):
    """cmd_open's flag parsing and how it threads `workspace` into tabs.open —
    the same parse-and-thread shape cmd_checkout/cmd_background use."""

    class RecordingTabs:
        def __init__(self):
            self.opened = []

        def open(self, dir_, workspace=False):
            self.opened.append((dir_, workspace))

    def open_ctx(self):
        ctx = FakeWtCtx(worktrees=[("foo", "ryanm/foo", 0, False, 0, 0)])
        ctx.tabs = self.RecordingTabs()
        return ctx

    def test_default_opens_a_tab_in_the_current_workspace(self):
        ctx = self.open_ctx()
        wt.cmd_open(ctx, ["foo"])
        self.assertEqual(ctx.tabs.opened, [(f"{ctx.new_worktrees_dir}/foo", False)])

    def test_workspace_flag_threads_through(self):
        ctx = self.open_ctx()
        wt.cmd_open(ctx, ["foo", "--workspace"])
        self.assertEqual(ctx.tabs.opened, [(f"{ctx.new_worktrees_dir}/foo", True)])

    def test_surface_is_accepted_as_a_no_op_alias(self):
        ctx = self.open_ctx()
        wt.cmd_open(ctx, ["foo", "--surface"])
        self.assertEqual(ctx.tabs.opened, [(f"{ctx.new_worktrees_dir}/foo", False)])

    def test_unknown_flag_dies(self):
        with self.assertRaises(SystemExit):
            wt.cmd_open(self.open_ctx(), ["foo", "--no-tab"])


class ParseSurfaceRefTest(unittest.TestCase):
    def test_extracts_ref_from_ok_line(self):
        self.assertEqual(
            wt.TabOpener._parse_surface_ref("OK surface:55 pane:24 workspace:24\n"),
            "surface:55",
        )

    def test_unrecognized_output_returns_empty(self):
        self.assertEqual(wt.TabOpener._parse_surface_ref("boom"), "")


class GhosttyTest(unittest.TestCase):
    def test_osascript_with_escaped_path(self):
        opener = make_opener({}, platform="darwin", have=("osascript",), responses=[
            (("osascript",), (0, "")),
        ])
        out = open_capturing(opener, '/wt/we"ird\\dir')
        self.assertEqual(out, "")
        args, script = opener.calls[0]
        self.assertEqual(args, ["osascript"])
        self.assertIn('"/wt/we\\"ird\\\\dir"', script)
        self.assertIn('tell application "Ghostty"', script)

    def test_not_darwin_prints_fallback(self):
        opener = make_opener({}, platform="linux", have=("osascript",))
        out = open_capturing(opener, "/wt/foo")
        self.assertEqual(out, 'wt: /wt/foo\nwt: (cd "/wt/foo")\n')

    def test_everything_failing_still_prints_and_never_raises(self):
        opener = make_opener(
            {"CMUX_WORKSPACE_ID": "ws-1"}, platform="darwin",
            have=("cmux", "osascript"),
        )  # every _run fails
        out = open_capturing(opener, "/wt/foo")
        self.assertIn('wt: (cd "/wt/foo")', out)


class GroupOfTest(unittest.TestCase):
    def test_bad_json_is_no_group(self):
        self.assertEqual(wt.TabOpener._group_of("not json", "ws-1"), "")

    def test_first_matching_group_wins(self):
        doubled = json.dumps({"groups": [
            {"id": "g1", "member_workspace_ids": ["ws-1"]},
            {"id": "g2", "member_workspace_ids": ["ws-1"]},
        ]})
        self.assertEqual(wt.TabOpener._group_of(doubled, "ws-1"), "g1")


class MembersOfTest(unittest.TestCase):
    def test_returns_members_for_matching_group(self):
        self.assertEqual(
            wt.TabOpener._members_of(GROUPS_JSON, "g-mine"), ["ws-1", "ws-2"]
        )

    def test_missing_group_returns_empty_list(self):
        self.assertEqual(wt.TabOpener._members_of(GROUPS_JSON, "g-nope"), [])

    def test_bad_json_returns_empty_list(self):
        self.assertEqual(wt.TabOpener._members_of("not json", "g-mine"), [])


class ParseCreatedRefTest(unittest.TestCase):
    def test_extracts_ref_from_ok_line(self):
        self.assertEqual(
            wt.TabOpener._parse_created_ref("OK workspace:12\n"), "workspace:12"
        )

    def test_unrecognized_output_returns_empty(self):
        self.assertEqual(wt.TabOpener._parse_created_ref("boom"), "")


class DominantColorTest(unittest.TestCase):
    def dom(self, entries, members, ref=""):
        return wt.TabOpener._dominant_color(ws_list_json(*entries), members, ref)

    def test_bad_json_is_no_color(self):
        self.assertEqual(wt.TabOpener._dominant_color("not json", {"ws-1"}, ""), "")

    def test_most_common_color_wins(self):
        color = self.dom(
            [("ws-1", "workspace:1", "#A"),
             ("ws-2", "workspace:2", "#A"),
             ("ws-3", "workspace:3", "#B")],
            {"ws-1", "ws-2", "ws-3"},
        )
        self.assertEqual(color, "#A")

    def test_tie_breaks_to_reference_color(self):
        color = self.dom(
            [("ws-1", "workspace:1", "#A"),
             ("ws-2", "workspace:2", "#B")],
            {"ws-1", "ws-2"},
            ref="ws-2",
        )
        self.assertEqual(color, "#B")

    def test_tie_without_reference_color_breaks_to_first_seen(self):
        color = self.dom(
            [("ws-1", "workspace:1", "#A"),
             ("ws-2", "workspace:2", "#B")],
            {"ws-1", "ws-2"},
        )
        self.assertEqual(color, "#A")

    def test_no_colored_members_returns_empty(self):
        color = self.dom(
            [("ws-1", "workspace:1", None), ("ws-2", "workspace:2", None)],
            {"ws-1", "ws-2"},
        )
        self.assertEqual(color, "")

    def test_non_member_workspaces_ignored(self):
        # ws-9 is more numerous but not a member; the lone member's color wins.
        color = self.dom(
            [("ws-9", "workspace:9", "#Z"),
             ("ws-9b", "workspace:10", "#Z"),
             ("ws-1", "workspace:1", "#A")],
            {"ws-1"},
        )
        self.assertEqual(color, "#A")


if __name__ == "__main__":
    unittest.main()
