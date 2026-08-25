"""Tests for the settings wt and stack share: the git-config precedence chain
in gitcore.ConfigMixin, and the glyph set ui.set_glyphs swaps.

Run: python3 -m unittest discover script-tests

The chain tests drive ConfigMixin against a recorded fake rather than a real
repo — the mechanism under test is the tool-key-over-shared-key precedence, not
git's own config resolution.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_script import load_script  # noqa: E402

load_script("wt", "wt_mod")  # puts src/_common on sys.path
import forge  # noqa: E402
import gitcore  # noqa: E402
import ui  # noqa: E402

SHARED = gitcore.SHARED_SECTION


class FakeConfig(gitcore.ConfigMixin):
    """ConfigMixin over an in-memory {key: [values]} map, bypassing git."""

    def __init__(self, values=None):
        self._v = {k: (v if isinstance(v, list) else [v])
                   for k, v in (values or {}).items()}

    def config_get(self, key):
        vals = self._v.get(key)
        return vals[0] if vals else None

    def config_get_all(self, key):
        return list(self._v.get(key, []))


class ConfigChainTest(unittest.TestCase):
    def test_tool_key_wins_over_shared(self):
        g = FakeConfig({"wt.baseBranch": "tool", f"{SHARED}.baseBranch": "shared"})
        self.assertEqual(
            g.config_chain("wt.baseBranch", f"{SHARED}.baseBranch"), "tool")

    def test_shared_used_when_tool_key_unset(self):
        g = FakeConfig({f"{SHARED}.baseBranch": "shared"})
        self.assertEqual(
            g.config_chain("wt.baseBranch", f"{SHARED}.baseBranch"), "shared")

    def test_default_when_neither_set(self):
        g = FakeConfig()
        self.assertEqual(
            g.config_chain("wt.baseBranch", f"{SHARED}.baseBranch", "main"), "main")

    def test_explicit_empty_tool_value_beats_shared(self):
        """`git config wt.branchPrefix ""` means "no prefix" — it must not fall
        through to the shared key."""
        g = FakeConfig({"wt.branchPrefix": "", f"{SHARED}.branchPrefix": "me/"})
        self.assertEqual(
            g.config_chain("wt.branchPrefix", f"{SHARED}.branchPrefix"), "")

    def test_multi_value_chain(self):
        g = FakeConfig({f"{SHARED}.ignoredPendingChecks": ["a", "b"]})
        self.assertEqual(
            g.config_chain_all("wt.ignoredPendingChecks",
                               f"{SHARED}.ignoredPendingChecks"), ["a", "b"])

    def test_multi_value_tool_key_replaces_shared_list(self):
        g = FakeConfig({"wt.ignoredPendingChecks": ["only"],
                        f"{SHARED}.ignoredPendingChecks": ["a", "b"]})
        self.assertEqual(
            g.config_chain_all("wt.ignoredPendingChecks",
                               f"{SHARED}.ignoredPendingChecks"), ["only"])

    def test_bool_truthiness_follows_git(self):
        for raw in ("true", "yes", "on", "1", "TRUE"):
            self.assertTrue(FakeConfig({"k": raw}).config_get_bool("k"), raw)
        for raw in ("false", "no", "off", "0"):
            self.assertFalse(FakeConfig({"k": raw}).config_get_bool("k"), raw)

    def test_bool_default_when_unset(self):
        self.assertFalse(FakeConfig().config_get_bool("k"))
        self.assertTrue(FakeConfig().config_get_bool("k", default=True))

    def test_bool_chain_lets_tool_key_disable_a_shared_opt_in(self):
        g = FakeConfig({"wt.nerdFont": "false", f"{SHARED}.nerdFont": "true"})
        self.assertFalse(g.config_chain_bool("wt.nerdFont", f"{SHARED}.nerdFont"))


class IgnoredWhenPendingTest(unittest.TestCase):
    """The set must default to empty: a hardcoded check name is specific to one
    repo's CI and has no meaning anywhere else."""

    def tearDown(self):
        forge.set_ignored_when_pending(set())

    def test_defaults_to_empty(self):
        forge.set_ignored_when_pending(set())
        self.assertEqual(forge.IGNORED_WHEN_PENDING, set())

    def test_pending_masks_success_when_not_ignored(self):
        pr = {"statusCheckRollup": [
            {"name": "a", "conclusion": "SUCCESS"},
            {"name": "slow", "status": "IN_PROGRESS"},
        ]}
        self.assertEqual(forge.rollup_checks(pr), "PENDING")

    def test_configured_check_is_ignored_while_pending(self):
        forge.set_ignored_when_pending({"slow"})
        pr = {"statusCheckRollup": [
            {"name": "a", "conclusion": "SUCCESS"},
            {"name": "slow", "status": "IN_PROGRESS"},
        ]}
        self.assertEqual(forge.rollup_checks(pr), "SUCCESS")


class GlyphModeTest(unittest.TestCase):
    """Plain Unicode is the default so a fresh install never renders tofu;
    set_glyphs(True) opts into the Nerd Font private-use codepoints."""

    def tearDown(self):
        ui.set_glyphs(False)

    def test_plain_is_the_default(self):
        ui.set_glyphs(False)
        self.assertEqual(ui.SYM_CHECKS_PASS, "✔")
        self.assertEqual(ui.SYM_CHECKS_FAIL, "✘")
        self.assertEqual(ui.SYM_CHECKS_PENDING, "•")
        self.assertEqual(ui.SYM_MERGED, "⑃")
        self.assertEqual(ui.SYM_NET_OFF, "∅")

    def test_plain_drops_the_forge_icons(self):
        """The number prefix (# vs !) already names the forge."""
        ui.set_glyphs(False)
        self.assertEqual(ui.SYM_GITHUB, "")
        self.assertEqual(ui.SYM_GITLAB, "")

    def test_nerd_font_uses_private_use_codepoints(self):
        ui.set_glyphs(True)
        for glyph in (ui.SYM_CHECKS_PASS, ui.SYM_CHECKS_FAIL,
                      ui.SYM_CHECKS_PENDING, ui.SYM_MERGED,
                      ui.SYM_GITHUB, ui.SYM_GITLAB, ui.SYM_NET_OFF):
            self.assertEqual(len(glyph), 1)
            self.assertGreaterEqual(ord(glyph), 0xE000)

    def test_toggle_round_trips(self):
        ui.set_glyphs(True)
        nerd = ui.SYM_CHECKS_PASS
        ui.set_glyphs(False)
        self.assertNotEqual(ui.SYM_CHECKS_PASS, nerd)
        ui.set_glyphs(True)
        self.assertEqual(ui.SYM_CHECKS_PASS, nerd)

    def test_every_plain_glyph_is_one_column(self):
        """The chip widths are hand-counted as 1 per glyph, so a multi-codepoint
        or wide replacement would silently break column alignment."""
        import unicodedata
        ui.set_glyphs(False)
        for glyph in (ui.SYM_CHECKS_PASS, ui.SYM_CHECKS_FAIL,
                      ui.SYM_CHECKS_PENDING, ui.SYM_MERGED, ui.SYM_NET_OFF):
            self.assertEqual(len(glyph), 1, glyph)
            self.assertIn(unicodedata.east_asian_width(glyph), ("N", "A", "Na"))


class GlyphKeyTest(unittest.TestCase):
    """The help-page symbol legend. It reads the live glyph globals, so it must
    track whichever set set_glyphs() installed."""

    def tearDown(self):
        ui.set_glyphs(False)

    def _key(self, **kw):
        return "\n".join(ui.glyph_key(**kw))

    def test_lists_the_plain_glyphs_in_plain_mode(self):
        ui.set_glyphs(False)
        out = self._key()
        for glyph in ("✔", "✘", "•", "⑃", "∅"):
            self.assertIn(glyph, out)

    def test_lists_the_nerd_glyphs_in_nerd_mode(self):
        ui.set_glyphs(True)
        out = self._key(forge_icon=ui.SYM_GITHUB)
        for glyph in (ui.SYM_CHECKS_PASS, ui.SYM_MERGED, ui.SYM_GITHUB):
            self.assertIn(glyph, out)
        self.assertNotIn("✔", out)

    def test_forge_icon_row_only_when_an_icon_exists(self):
        ui.set_glyphs(False)
        self.assertNotIn("the forge the PR/MR lives on", self._key(forge_icon=""))
        ui.set_glyphs(True)
        self.assertIn("the forge the PR/MR lives on",
                      self._key(forge_icon=ui.SYM_GITHUB))

    def test_number_prefix_follows_the_forge(self):
        self.assertIn("#12", self._key(num_prefix="#"))
        self.assertIn("!12", self._key(num_prefix="!"))

    def test_explains_both_meanings_of_the_net_off_glyph(self):
        out = self._key()
        self.assertIn("forge unreachable", out)
        self.assertIn("local-only branch, never pushed", out)
        self.assertIn(ui.NET_OFF_LABEL, out)

    def test_extra_entries_get_their_own_section(self):
        out = self._key(extra=[("●", "a node")])
        self.assertIn("stack graph", out)
        self.assertIn("a node", out)
        self.assertNotIn("stack graph", self._key())

    def test_glyphs_carry_their_rendered_color(self):
        ui.set_color(True)
        self.addCleanup(ui.set_color, False)
        ui.set_glyphs(False)
        out = "\n".join(ui.glyph_key())
        # Each colored glyph is wrapped in the same escape the chips use.
        self.assertIn(f"{ui.GREEN}{ui.SYM_CHECKS_PASS}{ui.RESET}", out)
        self.assertIn(f"{ui.RED}{ui.SYM_CHECKS_FAIL}{ui.RESET}", out)
        self.assertIn(f"{ui.YELLOW}{ui.SYM_CHECKS_PENDING}{ui.RESET}", out)
        self.assertIn(f"{ui.MAGENTA}{ui.SYM_MERGED}{ui.RESET}", out)

    def test_alignment_survives_colored_glyphs(self):
        """Padding is computed on the raw glyph, so the zero-width escapes must
        not shift the meanings out of their column."""
        import re
        ui.set_glyphs(False)
        ui.set_color(False)
        plain = ui.glyph_key()
        ui.set_color(True)
        self.addCleanup(ui.set_color, False)
        colored = ui.glyph_key()
        strip = re.compile(r"\033\[[0-9;]*m")
        self.assertEqual([strip.sub("", ln) for ln in colored], plain)

    def test_meanings_align_on_a_common_column(self):
        """Every bare-glyph row indents its meaning to the same column; the
        net-off row is allowed to overhang rather than widening them all."""
        import re
        ui.set_glyphs(False)
        starts = set()
        for line in ui.glyph_key():
            if not line.startswith("    ") or ui.NET_OFF_LABEL in line:
                continue
            # The meaning starts after the run of >=2 spaces following the glyph.
            m = re.search(r"\S\s{2,}(\S)", line)
            self.assertIsNotNone(m, line)
            starts.add(m.start(1))
        self.assertEqual(len(starts), 1, starts)


class MainSetsColorBeforeHelpTest(unittest.TestCase):
    """Regression: stack set colors inside its dispatch path, which the help
    branch returns before reaching — so `stack help` printed its symbol key
    uncolored on a TTY. Both CLIs must decide color before any output."""

    def _main_source(self, name):
        import inspect
        mod = load_script(name, f"{name}_colorcheck_mod")
        return inspect.getsource(mod.main)

    def test_set_color_precedes_the_help_branch(self):
        for name in ("wt", "stack"):
            body = self._main_source(name)
            color_at = body.find("set_color(")
            self.assertNotEqual(color_at, -1, f"{name}: no set_color in main()")
            # The first early-return branch in main() is the help/dispatch fork;
            # color must be armed before it.
            first_return = body.find("return")
            self.assertLess(color_at, first_return,
                            f"{name}: set_color runs after main()'s first return")


class ForgeLeadWidthTest(unittest.TestCase):
    """The chips return their own visible width, which drives column padding in
    both tables — so an absent forge icon must collapse its lead entirely
    rather than emit a stray space."""

    def tearDown(self):
        ui.set_glyphs(False)

    def test_empty_icon_collapses_the_lead(self):
        for chip in (ui.pr_loading_chip(""),
                     ui.branch_pr_chip(None, True, "")):
            rendered, width = chip
            self.assertEqual(width, 1)
            self.assertFalse(rendered.startswith(" "), rendered)

    def test_empty_icon_collapses_the_lead_on_the_net_off_chip(self):
        """This chip carries a trailing label, so its width is the glyph plus
        the label rather than a bare 1."""
        rendered, width = ui.pr_net_off_chip("", failed=False)
        self.assertEqual(width, 1 + 1 + len(ui.NET_OFF_LABEL))
        self.assertFalse(rendered.startswith(" "), rendered)
        self.assertEqual(rendered, f"{ui.SYM_NET_OFF} {ui.NET_OFF_LABEL}")

    def test_present_icon_keeps_a_two_column_lead(self):
        icon = ""
        for chip in (ui.pr_loading_chip(icon),
                     ui.branch_pr_chip(None, True, icon)):
            rendered, width = chip
            self.assertEqual(width, 3)
            self.assertIn(f"{icon} ", rendered)
        rendered, width = ui.pr_net_off_chip(icon, failed=False)
        self.assertEqual(width, 2 + 1 + 1 + len(ui.NET_OFF_LABEL))
        self.assertIn(f"{icon} ", rendered)

    def test_label_marks_a_failed_fetch_not_a_local_only_branch(self):
        """Both render SYM_NET_OFF, but they mean different things: an
        unreachable forge is labelled, a never-pushed branch is not."""
        unreachable, _ = ui.pr_net_off_chip("", failed=True)
        self.assertIn(ui.NET_OFF_LABEL, unreachable)
        local_only, _ = ui.branch_pr_chip(None, False, "")
        self.assertIn(ui.SYM_NET_OFF, local_only)
        self.assertNotIn(ui.NET_OFF_LABEL, local_only)

    def test_label_shown_whether_the_fetch_failed_or_timed_out(self):
        for failed in (True, False):
            rendered, _ = ui.pr_net_off_chip("", failed=failed)
            self.assertIn(ui.NET_OFF_LABEL, rendered)

    def test_pr_chip_width_matches_plain_and_nerd_modes(self):
        pr = {"number": 7, "state": "OPEN", "isDraft": False, "url": "u",
              "reviewDecision": None, "statusCheckRollup": []}
        plain_w = ui.pr_chip(pr, "#", "")[1]
        nerd_w = ui.pr_chip(pr, "#", "\uf09b")[1]
        self.assertEqual(nerd_w - plain_w, 2)


if __name__ == "__main__":
    unittest.main()
