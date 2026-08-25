"""Tests for wt's git-native worktree discovery.

Covers the `git worktree list --porcelain` parser (including detached
worktrees, which have no `branch` line) and the Ctx registry built on it: a
worktree anywhere on disk is discoverable, the cwd is recognized by registry
membership rather than a path prefix, and a name shared by two worktrees is
refused rather than silently resolved.
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_script import load_script  # noqa: E402

wt = load_script("wt", "wt_mod")
# Loading wt puts src/_common on sys.path.
import gitcore  # noqa: E402

ROOT = "/repo"


def porcelain(*records):
    """Join (path, branch|None) records into --porcelain text: blank-line
    separated, `detached` standing in for the branch line."""
    blocks = []
    for path, branch in records:
        ref = f"branch refs/heads/{branch}" if branch else "detached"
        blocks.append(f"worktree {path}\nHEAD {'a' * 40}\n{ref}\n")
    return "\n".join(blocks)


class FakeGit(gitcore.ConfigMixin):
    """The slice of wt's Git that Ctx's discovery uses. Inherits the real
    config API so the chain helpers stay in step; config_get is stubbed
    unset below, so every lookup falls through to its default."""

    def __init__(self, entries, top=None):
        self._entries = list(entries)
        self._top = top
        self.repo_dir = ROOT

    def worktree_entries(self):
        return list(self._entries)

    def toplevel(self):
        return self._top

    def origin_head(self):
        return None

    def config_get(self, key):
        return None

    def config_get_all(self, key):
        return []


def make_ctx(entries, top=None, layout="standard", main_name="repo"):
    return wt.Ctx(FakeGit(entries, top), layout, ROOT,
                  repo_dir=ROOT,
                  new_worktrees_dir=os.path.join(ROOT, ".worktrees"),
                  main_name=main_name)


class PorcelainParseTest(unittest.TestCase):
    """Git.worktree_entries must keep detached worktrees; worktree_records
    must drop them."""

    def entries_from(self, text):
        git = wt.WtGit.__new__(wt.WtGit)
        git.out_or_none = lambda args: text
        git._repo_git = lambda *a: list(a)
        return git.worktree_entries()

    def test_detached_worktree_survives_with_no_branch(self):
        text = porcelain(("/repo", "main"), ("/repo/.worktrees/det", None),
                         ("/repo/.worktrees/a", "ryanm/a"))
        self.assertEqual(self.entries_from(text), [
            ("/repo", "main"),
            ("/repo/.worktrees/det", None),
            ("/repo/.worktrees/a", "ryanm/a"),
        ])

    def test_detached_does_not_absorb_the_next_branch(self):
        """The record is flushed by the next `worktree` line, so a detached
        entry can't swallow the branch belonging to the one after it."""
        text = porcelain(("/repo/det", None), ("/repo/a", "ryanm/a"))
        entries = self.entries_from(text)
        self.assertIsNone(dict(entries)["/repo/det"])
        self.assertEqual(dict(entries)["/repo/a"], "ryanm/a")

    def test_records_drops_detached_and_flips_the_pair(self):
        git = wt.WtGit.__new__(wt.WtGit)
        text = porcelain(("/repo", "main"), ("/repo/det", None))
        git.out_or_none = lambda args: text
        git._repo_git = lambda *a: list(a)
        self.assertEqual(git.worktree_records(), [("main", "/repo")])

    def test_no_output_is_empty(self):
        self.assertEqual(self.entries_from(""), [])


class RegistryTest(unittest.TestCase):
    """Discovery is git's registry, not a directory scan."""

    def test_worktree_outside_new_dir_is_found(self):
        ctx = make_ctx([("/repo", "main"), ("/elsewhere/foreign", "someone/x")])
        self.assertIn("foreign", ctx.list_worktree_names())
        self.assertEqual(ctx.worktree_path("foreign"), "/elsewhere/foreign")
        self.assertTrue(ctx.worktree_exists("foreign"))

    def test_main_worktree_sorts_first(self):
        ctx = make_ctx([("/repo/.worktrees/aaa", "ryanm/aaa"),
                        ("/repo", "main"),
                        ("/repo/.worktrees/zzz", "ryanm/zzz")])
        self.assertEqual(ctx.list_worktree_names(), ["repo", "aaa", "zzz"])

    def test_detached_worktree_is_listed(self):
        ctx = make_ctx([("/repo", "main"), ("/repo/.worktrees/det", None)])
        self.assertEqual(ctx.list_worktree_names(), ["repo", "det"])

    def test_bare_repo_dir_is_excluded(self):
        """Bare layout registers .bare as a worktree, but it holds no checkout
        and isn't somewhere to work."""
        ctx = make_ctx([("/repo/.bare", "main"), ("/repo/a", "ryanm/a")],
                       layout="bare", main_name="")
        self.assertEqual(ctx.list_worktree_names(), ["a"])
        self.assertFalse(ctx.worktree_exists(".bare"))

    def test_dot_named_main_worktree_is_kept(self):
        """The hidden-entry filter applies to linked worktrees only — a repo
        checked out in a dot-directory (~/.dotfiles) still lists its root."""
        ctx = wt.Ctx(FakeGit([("/home/.dotfiles", "main"),
                              ("/home/.dotfiles/.worktrees/a", "ryanm/a")]),
                     "standard", "/home/.dotfiles", repo_dir="/home/.dotfiles",
                     new_worktrees_dir="/home/.dotfiles/.worktrees",
                     main_name=".dotfiles")
        self.assertEqual(ctx.list_worktree_names(), [".dotfiles", "a"])
        self.assertEqual(ctx.worktree_path(".dotfiles"), "/home/.dotfiles")

    def test_unknown_name_dies(self):
        ctx = make_ctx([("/repo", "main")])
        with self.assertRaises(SystemExit):
            ctx.worktree_path("nope")

    def test_paths_in_list_order_match_names(self):
        ctx = make_ctx([("/repo", "main"),
                        ("/repo/.worktrees/a", "ryanm/a"),
                        ("/elsewhere/b", "ryanm/b")])
        names = ctx.list_worktree_names()
        paths = ctx.worktree_paths_in_list_order()
        self.assertEqual(len(names), len(paths))
        self.assertEqual(dict(zip(names, paths)),
                         {"repo": "/repo", "a": "/repo/.worktrees/a",
                          "b": "/elsewhere/b"})


class AmbiguousNameTest(unittest.TestCase):
    """A basename shared by two worktrees has no single right answer."""

    def ctx(self):
        return make_ctx([("/repo", "main"),
                         ("/repo/.worktrees/foo", "ryanm/foo"),
                         ("/elsewhere/foo", "other/foo")])

    def test_resolving_an_ambiguous_name_dies_listing_candidates(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit):
            stderr, sys.stderr = sys.stderr, err
            try:
                self.ctx().worktree_path("foo")
            finally:
                sys.stderr = stderr
        msg = err.getvalue()
        self.assertIn("ambiguous", msg)
        self.assertIn("/repo/.worktrees/foo", msg)
        self.assertIn("/elsewhere/foo", msg)

    def test_both_colliding_worktrees_still_list(self):
        ctx = self.ctx()
        self.assertEqual(ctx.list_worktree_names(), ["repo", "foo", "foo"])
        self.assertEqual(ctx.worktree_paths_in_list_order(),
                         ["/repo", "/elsewhere/foo", "/repo/.worktrees/foo"])

    def test_describe_existing_names_every_location(self):
        got = self.ctx().describe_existing("foo")
        self.assertIn("/repo/.worktrees/foo", got)
        self.assertIn("/elsewhere/foo", got)


class CurrentWorktreeTest(unittest.TestCase):
    """The cwd is identified by registry membership, not a path prefix."""

    def test_cwd_in_a_worktree_outside_the_new_dir_is_recognized(self):
        ctx = make_ctx([("/repo", "main"), ("/elsewhere/foreign", "someone/x")],
                       top="/elsewhere/foreign")
        self.assertEqual(ctx.current_worktree_name_or_none(), "foreign")
        self.assertEqual(ctx.current_worktree_path(), "/elsewhere/foreign")

    def test_main_worktree_reports_its_name(self):
        ctx = make_ctx([("/repo", "main")], top="/repo")
        self.assertEqual(ctx.current_worktree_name_or_none(), "repo")

    def test_unregistered_cwd_is_none(self):
        ctx = make_ctx([("/repo", "main")], top="/somewhere/else")
        self.assertIsNone(ctx.current_worktree_name_or_none())

    def test_cwd_outside_a_repo_is_none(self):
        ctx = make_ctx([("/repo", "main")], top=None)
        self.assertIsNone(ctx.current_worktree_name_or_none())

    def test_ambiguous_name_still_reports_its_own_path(self):
        """`wt current` must answer inside a worktree whose name it shares:
        the path comes from git's toplevel, not a name lookup."""
        entries = [("/repo", "main"), ("/repo/.worktrees/foo", "ryanm/foo"),
                   ("/elsewhere/foo", "other/foo")]
        for top in ("/repo/.worktrees/foo", "/elsewhere/foo"):
            ctx = make_ctx(entries, top=top)
            self.assertEqual(ctx.current_worktree_or_none(), ("foo", top))

    def test_bare_layout_names_by_basename(self):
        ctx = make_ctx([("/repo/a", "ryanm/a")], top="/repo/a",
                       layout="bare", main_name="")
        self.assertEqual(ctx.current_worktree_name_or_none(), "a")


class DisplayLocationTest(unittest.TestCase):
    """The --paths column: short inside the repo, absolute outside it."""

    def test_root_renders_as_dot(self):
        self.assertEqual(wt.display_location("/repo", "/repo"), ".")

    def test_inside_the_repo_is_relative(self):
        self.assertEqual(
            wt.display_location("/repo/.worktrees/foo", "/repo"), ".worktrees")

    def test_directly_under_the_root_is_dot(self):
        self.assertEqual(wt.display_location("/repo/foo", "/repo"), ".")

    def test_outside_the_repo_stays_absolute(self):
        self.assertEqual(
            wt.display_location("/elsewhere/foo", "/repo"), "/elsewhere")

    def test_missing_path_is_blank(self):
        self.assertEqual(wt.display_location(None, "/repo"), "")


class EnsureWorktreesDirTest(unittest.TestCase):
    """The new-worktree dir is created self-ignored."""

    def test_writes_a_star_gitignore(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctx = wt.Ctx(FakeGit([]), "standard", tmp, repo_dir=tmp,
                         new_worktrees_dir=os.path.join(tmp, ".worktrees"),
                         main_name=os.path.basename(tmp))
            ctx.ensure_worktrees_dir()
            with open(os.path.join(tmp, ".worktrees", ".gitignore")) as f:
                self.assertEqual(f.read(), "*\n")

    def test_bare_layout_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctx = wt.Ctx(FakeGit([]), "bare", tmp, repo_dir=tmp,
                         new_worktrees_dir=tmp, main_name="")
            ctx.ensure_worktrees_dir()
            self.assertFalse(os.path.exists(os.path.join(tmp, ".gitignore")))


if __name__ == "__main__":
    unittest.main()
