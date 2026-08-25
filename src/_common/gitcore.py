"""Shared subprocess + git plumbing for the cli-tools CLIs (stack, wt).

Lives in src/_common/, deliberately not a package: the scripts put this
directory on sys.path and import the flat modules. Dependency direction:
ui → forge → gitcore; gitcore imports nothing local.
"""

import os
import subprocess
import sys

PROG = "script"  # error-message prefix; each script calls set_prog()
QUIET = False  # suppress warn() — set for __complete so notices don't pollute completions


def set_prog(name):
    global PROG
    PROG = name


def set_quiet(flag):
    global QUIET
    QUIET = flag


def die(msg, code=1):
    print(f"{PROG}: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg):
    if not QUIET:
        print(f"{PROG}: {msg}", file=sys.stderr)


def run_(args, check=True, capture=True, input_=None, cwd=None):
    """Run a subprocess. Shared by the Git backend and the forge mutations; not
    itself git-specific."""
    p = subprocess.run(
        args,
        text=True,
        input=input_,
        cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and p.returncode != 0:
        err = (p.stderr or "").strip() if capture else ""
        die(f"`{' '.join(args)}` failed" + (f": {err}" if err else ""))
    return p


# -------- shared git config --------
# One implementation of the config API for both backends (gitcore.Git and wt's
# WtGit). They differ only in how a git command is built — cwd-relative here,
# `git -C <repo_dir>` in wt — so subclasses supply that via _git_cmd.
#
# Reads resolve the merged local+global view, so a repo-local value overrides a
# global one. Writes take an explicit scope.
#
# SHARED_SECTION holds the settings both CLIs honor. Each tool also keeps its
# own namespace (wt.*, stack.*), which wins when set: config_chain implements
# tool key > shared key > default, so a per-tool value set years ago keeps
# working while new settings need writing only once.

VERSION = "0.1.1"

SHARED_SECTION = "ryanmoelter-cli-tools"


class ConfigMixin:
    def _git_cmd(self, *args):
        """Build the git argv for a config call. Overridden by backends that
        anchor to a repo directory instead of the cwd."""
        return ["git", *args]

    def config_get(self, key):
        """Value, or None when the key is truly unset — an explicit empty value
        returns "". Callers rely on that distinction to honor
        `git config <tool>.branchPrefix ""` as "no prefix"."""
        p = run_(self._git_cmd("config", "--get", key), check=False)
        return p.stdout.rstrip("\n") if p.returncode == 0 else None

    def config_get_all(self, key):
        """Every value of a repeatable key, in config order; [] when unset."""
        p = run_(self._git_cmd("config", "--get-all", key), check=False)
        out = p.stdout.rstrip("\n") if p.returncode == 0 else None
        return out.splitlines() if out else []

    def config_get_bool(self, key, default=False):
        """A boolean key per git's own truthiness (true/yes/on/1)."""
        raw = self.config_get(key)
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in ("true", "yes", "on", "1")

    def _scoped(self, global_scope, *args):
        if global_scope:
            return ["git", "config", "--global", *args]
        return self._git_cmd("config", *args)

    def config_set(self, key, value, global_scope=False):
        """→ True on success."""
        return run_(self._scoped(global_scope, key, value), check=False).returncode == 0

    def config_add(self, key, value, global_scope=False):
        return run_(self._scoped(global_scope, "--add", key, value), check=False).returncode == 0

    def config_unset(self, key, global_scope=False, all_values=False):
        """→ True when something was removed."""
        flag = "--unset-all" if all_values else "--unset"
        return run_(self._scoped(global_scope, flag, key), check=False).returncode == 0

    # --- tool key > shared key > default ---

    def config_chain(self, tool_key, shared_key, default=None):
        for key in (tool_key, shared_key):
            v = self.config_get(key)
            if v is not None:
                return v
        return default

    def config_chain_all(self, tool_key, shared_key):
        return self.config_get_all(tool_key) or self.config_get_all(shared_key)

    def config_chain_bool(self, tool_key, shared_key, default=False):
        for key in (tool_key, shared_key):
            if self.config_get(key) is not None:
                return self.config_get_bool(key, default)
        return default


def apply_display_settings(git, tool):
    """Apply the config-driven display settings both CLIs share: the Nerd Font
    glyph set and the checks whose PENDING state is ignored. Called from main()
    once a git backend exists. `tool` is "wt" or "stack", naming the
    tool-specific override namespace.

    Imported lazily: gitcore sits at the bottom of the dependency order
    (ui → forge → gitcore), so importing them at module scope would cycle."""
    import forge
    import ui

    ui.set_glyphs(git.config_chain_bool(
        f"{tool}.nerdFont", f"{SHARED_SECTION}.nerdFont"))
    forge.set_ignored_when_pending(git.config_chain_all(
        f"{tool}.ignoredPendingChecks", f"{SHARED_SECTION}.ignoredPendingChecks"))


# -------- git backend --------
# Every git subprocess call goes through a Git instance. The real one shells
# out; tests substitute an in-memory fake. Per-instance caching (trunk, common
# dir) replaces the former module-level _cache global.


class Git(ConfigMixin):
    def __init__(self):
        self._cache = {}

    def run(self, args, check=True, capture=True, input_=None):
        return run_(args, check=check, capture=capture, input_=input_)

    def git(self, *args, check=True):
        return self.run(["git", *args], check=check).stdout.strip()

    def git_ok(self, *args):
        return (
            subprocess.run(
                ["git", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode
            == 0
        )

    def git_common_dir(self):
        if "common" not in self._cache:
            self._cache["common"] = os.path.abspath(self.git("rev-parse", "--git-common-dir"))
        return self._cache["common"]

    def trunk(self):
        """The trunk branch: a configured base branch wins, else origin/HEAD,
        else "main"."""
        if "trunk" not in self._cache:
            configured = self.config_chain(
                "stack.baseBranch", f"{SHARED_SECTION}.baseBranch"
            )
            if configured:
                self._cache["trunk"] = configured
            else:
                p = self.run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
                name = p.stdout.strip() if p.returncode == 0 else ""
                self._cache["trunk"] = name.removeprefix("origin/") if name else "main"
        return self._cache["trunk"]

    def trunk_tip(self):
        for ref in (f"refs/remotes/origin/{self.trunk()}", f"refs/heads/{self.trunk()}"):
            p = self.run(["git", "rev-parse", "--verify", "--quiet", ref], check=False)
            if p.returncode == 0:
                return p.stdout.strip()
        die(f"cannot resolve trunk '{self.trunk()}' — no origin/{self.trunk()} or local branch")

    def current_branch(self):
        p = self.run(["git", "symbolic-ref", "--short", "-q", "HEAD"], check=False)
        return p.stdout.strip() or None

    def local_branches(self):
        out = self.git("for-each-ref", "refs/heads", "--format=%(refname:short)")
        return out.splitlines() if out else []

    def branch_exists(self, b):
        return self.git_ok("rev-parse", "--verify", "--quiet", f"refs/heads/{b}")

    def rev(self, ref):
        p = self.run(["git", "rev-parse", "--verify", "--quiet", ref], check=False)
        if p.returncode != 0:
            die(f"unknown ref: {ref}")
        return p.stdout.strip()

    def is_ancestor(self, a, b):
        return self.git_ok("merge-base", "--is-ancestor", a, b)

    def merge_base(self, a, b):
        p = self.run(["git", "merge-base", a, b], check=False)
        return p.stdout.strip() if p.returncode == 0 else None

    def rev_list_count(self, spec):
        """Commit count for a range like 'base..tip'."""
        return int(self.git("rev-list", "--count", spec))

    def worktree_map(self):
        """branch name → worktree path, for branches checked out somewhere."""
        out = self.git("worktree", "list", "--porcelain")
        m, wt_path = {}, None
        for line in out.splitlines():
            if line.startswith("worktree "):
                wt_path = line[len("worktree "):]
            elif line.startswith("branch refs/heads/"):
                m[line[len("branch refs/heads/"):]] = wt_path
        return m

    def origin_url(self):
        p = self.run(["git", "remote", "get-url", "origin"], check=False)
        return p.stdout.strip() if p.returncode == 0 else ""

    def branch_prefix(self):
        return self.config_chain(
            "stack.branchPrefix", f"{SHARED_SECTION}.branchPrefix"
        ) or ""

    def apply_prefix(self, name):
        """Prepend stack.branchPrefix to a new branch name (default: none).
        Names that already carry the prefix are left alone."""
        pre = self.branch_prefix()
        if pre and not name.startswith(pre):
            return pre + name
        return name

    def switch_to(self, branch, quiet=False):
        args = ["git", "switch"] + (["-q"] if quiet else []) + [branch]
        p = self.run(args, capture=False, check=False)
        if p.returncode != 0:
            sys.exit(p.returncode)

    def origin_ahead_behind(self, b):
        """(ahead, behind) of `b` vs origin/<b>, or None when there's no such
        remote branch. ahead = local commits not pushed; behind = remote commits
        not local. Local only — reads the last-fetched origin ref, never fetches."""
        if not self.git_ok("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{b}"):
            return None
        out = self.git("rev-list", "--count", "--left-right", f"{b}...origin/{b}")
        ahead, behind = out.split("\t")
        return int(ahead), int(behind)
