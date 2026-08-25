"""In-memory fakes for stack's Git/Gh backends.

The commit graph is modelled as a dict mapping each commit id to its ordered
list of ancestor ids (newest-first, itself excluded). That's enough to answer
the questions render_stacks asks — is_ancestor, rev_list_count (a..b), and
merge_base — without shelling out to git. Branches are just named pointers into
that graph; the trunk is one of them.

Build a graph with a Commits helper (see build_linear / the tests) so the
per-commit ancestor sets stay consistent.
"""

class FakeGit:
    def __init__(self, *, trunk_name, branch_tips, ancestors, origin=None,
                 prefix="", branch_set=None):
        # branch_tips: {branch_name: commit_id}, including the trunk.
        # ancestors: {commit_id: [ancestor_id, ...]} newest-first, self excluded.
        # origin: {branch_name: (ahead, behind)} for origin_ahead_behind, or None.
        self._trunk = trunk_name
        self._tips = dict(branch_tips)
        self._anc = {c: list(a) for c, a in ancestors.items()}
        self._origin = dict(origin or {})
        self._prefix = prefix
        # Branches that "exist" as local refs (defaults to the tip keys).
        self._branches = set(branch_set) if branch_set is not None else set(branch_tips)

    # --- resolution ---

    def rev(self, ref):
        if ref in self._tips:
            return self._tips[ref]
        if ref in self._anc:  # already a commit id
            return ref
        raise AssertionError(f"FakeGit.rev: unknown ref {ref!r}")

    def branch_exists(self, b):
        return b in self._branches

    def local_branches(self):
        return sorted(self._branches)

    def trunk(self):
        return self._trunk

    def trunk_tip(self):
        return self._tips[self._trunk]

    def branch_prefix(self):
        return self._prefix

    # --- graph queries ---

    def _reachable(self, commit):
        """commit plus all its ancestors."""
        return {commit, *self._anc.get(commit, [])}

    def is_ancestor(self, a, b):
        a, b = self.rev(a), self.rev(b)
        return a in self._reachable(b)

    def rev_list_count(self, spec):
        lo, _, hi = spec.partition("..")
        lo, hi = self.rev(lo), self.rev(hi)
        return len(self._reachable(hi) - self._reachable(lo))

    def merge_base(self, a, b):
        a, b = self.rev(a), self.rev(b)
        common = self._reachable(a) & self._reachable(b)
        if not common:
            return None
        # Newest common ancestor = the one no other common ancestor descends past;
        # equivalently the common commit with the largest reachable set.
        return max(common, key=lambda c: len(self._reachable(c)))

    def origin_ahead_behind(self, b):
        return self._origin.get(b)


class _FakeForge:
    """Duck-typed stand-in for forge.Gh/Gl: an in-memory {branch: pr|None} map
    plus the have/prs interface stack calls. NUM_PREFIX/NOUN are overridden by
    the GitHub/GitLab subclasses."""

    NUM_PREFIX = "#"
    NOUN = "PR"

    def __init__(self, prs=None, installed=True, fetch_failed=False):
        self._prs = dict(prs or {})
        self._installed = installed
        self._fetch_failed = fetch_failed

    def have(self):
        return self._installed

    def prs_for(self, branches):
        if self._fetch_failed or not self._installed:
            return {b: None for b in branches}
        return {b: self._prs.get(b) for b in branches}

    def prs_for_or_none(self, branches):
        if self._fetch_failed or not self._installed:
            return None
        return {b: self._prs.get(b) for b in branches}


class FakeGh(_FakeForge):
    NUM_PREFIX = "#"
    NOUN = "PR"


class FakeGl(_FakeForge):
    NUM_PREFIX = "!"
    NOUN = "MR"


class _FakeWtGit:
    """The slice of wt's WtGit that the pure helpers (resolve_branch,
    branch_for via the prefix cache) touch."""

    def __init__(self, branches, branch_by_path=None):
        self._branches = set(branches)
        self._branch_by_path = dict(branch_by_path or {})

    def branch_exists(self, b):
        return b in self._branches

    def local_branches(self):
        return sorted(self._branches)

    def branch_of(self, path):
        return self._branch_by_path.get(path)

    # No origin and no wt.forge → forge_kind is None, so `wt list` skips the
    # PR fetch entirely and stays offline in tests.
    def origin_url(self):
        return None

    # Every key is unset, so the chain helpers always fall through to their
    # default. Spelled out rather than inherited to keep fakes.py dependency-free.
    def config_get(self, key):
        return None

    def config_get_all(self, key):
        return []

    def config_chain(self, tool_key, shared_key, default=None):
        return default

    def config_chain_all(self, tool_key, shared_key):
        return []

    def config_chain_bool(self, tool_key, shared_key, default=False):
        return default


class FakeWtCtx:
    """Duck-types the slice of wt's Ctx that `wt list` and the pure helpers
    use: the list-facing methods (list_worktree_names / worktree_path /
    worktree_paths_in_list_order / worktree_status /
    current_worktree_name_or_none / current_worktree_or_none) plus the
    lazily-cached branch prefix (pre-resolved here) and a minimal .git.

    worktrees: [(name, branch_display, ahead, dirty, remote_ahead,
    remote_behind, has_upstream)], in list order, optionally with an 8th
    element giving the worktree's absolute path. Without one it defaults under
    new_worktrees_dir (or root, for the main worktree) — pass it explicitly to
    model a worktree living somewhere else, or two that share a basename.
    current: the name reported as the cwd's worktree. branch_by_path:
    {path: branch} backing git.branch_of.
    """

    def __init__(self, *, worktrees=(), layout="standard", root="/repo",
                 main_name="repo", prefix="", branches=(), base_branch=None,
                 current=None, branch_by_path=None):
        self.layout = layout
        self.root = root
        self.main_name = main_name
        self.base_branch = base_branch
        self.new_worktrees_dir = f"{root}/.worktrees"
        self._worktrees = list(worktrees)
        self._prefix = prefix
        self._prefix_resolved = True
        self._current = current
        self.git = _FakeWtGit(branches, branch_by_path)

    def _default_path(self, name):
        if self.layout == "standard" and name == self.main_name:
            return self.root
        return f"{self.new_worktrees_dir}/{name}"

    def _path_of(self, wt):
        return wt[7] if len(wt) > 7 else self._default_path(wt[0])

    def list_worktree_names(self):
        return [wt[0] for wt in self._worktrees]

    def worktree_paths_in_list_order(self):
        return [self._path_of(wt) for wt in self._worktrees]

    def worktree_path(self, name):
        paths = [self._path_of(wt) for wt in self._worktrees if wt[0] == name]
        if len(paths) > 1:
            raise AssertionError(f"FakeWtCtx.worktree_path: ambiguous name {name!r}")
        return paths[0] if paths else self._default_path(name)

    def worktree_status(self, wt_path):
        for wt in self._worktrees:
            if self._path_of(wt) == wt_path:
                return wt[1:7]
        raise AssertionError(f"FakeWtCtx.worktree_status: unknown path {wt_path!r}")

    def current_worktree_name_or_none(self):
        return self._current

    def current_worktree_or_none(self):
        if self._current is None:
            return None, None
        return self._current, self.worktree_path(self._current)
