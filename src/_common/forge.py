"""Forge (GitHub/GitLab) PR fetching and check-rollup logic shared by the
the CLIs. The single home of IGNORED_WHEN_PENDING — stack and wt both
read it from here."""

import json
import re
import shutil
import subprocess
import threading

from gitcore import die, run_

# CI checks whose PENDING state is ignored when folding the rollup glyph — a
# pass/fail from them still counts, but a perpetual "pending" doesn't mask the
# real result. Matched on the check's name/context exactly. Empty by default;
# each CLI's main() populates it from config. Mutated by the setter rather than
# passed to rollup_checks(), whose single-argument signature is widely relied
# on — this is a process-wide setting resolved once at startup, like colors.
IGNORED_WHEN_PENDING = set()


def set_ignored_when_pending(names):
    global IGNORED_WHEN_PENDING
    IGNORED_WHEN_PENDING = set(names)


def start_fetch(fetch, branches):
    """Run `fetch(branches)` on a daemon thread so a slow/hung forge can't block
    or outlive the CLI. → (done, box): `done` is a threading.Event set when the
    worker finishes; `box["prs"]` then holds the result (or None if fetch raised
    or returned None). Callers poll with `done.wait(timeout)` and read box."""
    box, done = {}, threading.Event()

    def work():
        try:
            box["prs"] = fetch(branches)
        except Exception:
            box["prs"] = None
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    return done, box


def resolve_pr_state(box):
    """A finished fetch's result box → (pr_state, prs). "on" with the map once
    it lands, "error" with an empty map when the fetch failed."""
    prs = box.get("prs")
    return ("on", prs) if prs is not None else ("error", {})


def rollup_checks(pr):
    """Fold a PR's statusCheckRollup array into SUCCESS/FAILURE/PENDING/NONE.
    The rollup is a flat list of check contexts (StatusContext exposes
    context/state; CheckRun exposes name/conclusion/status), so we aggregate:
    any failure wins, else any non-ignored pending, else success. CANCELLED
    counts as neutral (matches GitHub's PR page — a cancelled auxiliary run,
    often an auto-superseded duplicate, doesn't fail or block a merge)."""
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        return "NONE"
    entries = [
        (c.get("name") or c.get("context") or "",
         (c.get("conclusion") or c.get("state") or c.get("status") or "").upper())
        for c in rollup
    ]
    if any(s in ("FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED")
           for _, s in entries):
        return "FAILURE"
    # An ignored check's perpetual pending doesn't count; its pass/fail still does.
    if any(s not in ("SUCCESS", "NEUTRAL", "SKIPPED", "CANCELLED")
           for name, s in entries if name not in IGNORED_WHEN_PENDING):
        return "PENDING"
    return "SUCCESS"


# -------- forge detection --------
# Pure URL-sniffers shared by stack and wt; each passes its own origin URL and
# (optional) *.forge config override.


def forge_kind(origin_url, override=None):
    """Which forge to talk to: "github", "gitlab", or "none". `override` (e.g.
    from a *.forge config) wins — an escape hatch for self-hosted domains the
    host sniff can't classify; otherwise sniffed from the origin URL's host."""
    if override:
        return override
    host = (origin_url or "").split("://", 1)[-1]  # drop scheme
    host = host.split("@", 1)[-1]                   # drop user@
    host = re.split(r"[/:]", host, maxsplit=1)[0]   # path (https) or :path (ssh)
    if "github" in host:
        return "github"
    if "gitlab" in host:
        return "gitlab"
    return "none"


def gitlab_fullpath(origin_url):
    """Project full path ("group/sub/repo") from an origin URL, for GraphQL's
    project(fullPath:). Handles ssh (git@host:path.git) and https forms. None
    when unresolvable."""
    if not origin_url:
        return None
    rest = origin_url.split("://", 1)[-1]
    rest = rest.split("@", 1)[-1]
    m = re.search(r"[/:]", rest)   # drop host + separator
    if m:
        rest = rest[m.end():]
    rest = rest.removesuffix(".git").removesuffix("/")
    return rest or None


# -------- normalized PR dicts --------
# Both forges reduce to one dict shape per branch (or None when the branch has
# no PR): number, state (OPEN|MERGED|CLOSED), isDraft, baseRefName, url, title,
# headRefOid, reviewDecision (APPROVED|CHANGES_REQUESTED|None),
# statusCheckRollup — a flat list of check contexts for rollup_checks() — and
# stack (GitHub's native stack membership, None off GitHub or when the PR isn't
# in one). Multiple PRs on a branch: prefer heads in this repo (not same-named
# fork branches), then open PRs, then the highest number.

# GitHub's native stacked PRs. On a repo without the feature these fields come
# back null rather than erroring, so they ride the normal query unconditionally
# — no capability probe, no extra request.
_GITHUB_PR_NODE = (
    "number state isDraft baseRefName url title headRefOid reviewDecision "
    "headRepositoryOwner { login } "
    "stack { number size } stackEntry { position } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) "
    "{ nodes { __typename ... on StatusContext { context state } "
    "... on CheckRun { name conclusion status } } } } } } }"
)

# Branches per GraphQL request. One aliased pullRequests field per branch keeps
# each query fast (`gh pr list` with rollups 504s on repos with big PR
# histories), but an unbounded alias count would eventually hit query limits.
CHUNK = 30


def build_github_query(branches):
    """→ (query string, {alias: branch}). Branch names are inlined as JSON
    strings (gh needs an alias per branch anyway); the alias map is how
    responses get back to branches — never trust the response's headRefName."""
    fields, alias_to_branch = [], {}
    for i, b in enumerate(branches):
        alias = f"b{i}"
        alias_to_branch[alias] = b
        fields.append(
            f"{alias}: pullRequests(headRefName: {json.dumps(b)}, first: 10, "
            "orderBy: {field: CREATED_AT, direction: DESC}) "
            f"{{ nodes {{ {_GITHUB_PR_NODE} }} }}"
        )
    q = (
        "query($owner: String!, $name: String!) { "
        "repository(owner: $owner, name: $name) { owner { login } "
        + " ".join(fields)
        + " } }"
    )
    return q, alias_to_branch


def pick_best(nodes, repo_owner=None):
    """The PR a branch's status column should show, or None."""
    if not nodes:
        return None
    return max(nodes, key=lambda n: (
        ((n.get("headRepositoryOwner") or {}).get("login")) == repo_owner,
        n.get("state") == "OPEN",
        n.get("number") or 0,
    ))


def _github_stack_dict(node):
    """{number, size, position} for a PR in a native GitHub stack, else None.
    position comes from stackEntry, so a stack without one folds to None."""
    stack = node.get("stack")
    entry = node.get("stackEntry")
    if not stack or not entry:
        return None
    return {
        "number": stack.get("number"),
        "size": stack.get("size"),
        "position": entry.get("position"),
    }


def _github_pr_dict(node):
    commits = (node.get("commits") or {}).get("nodes") or []
    contexts = []
    if commits:
        rollup = (commits[0].get("commit") or {}).get("statusCheckRollup") or {}
        contexts = (rollup.get("contexts") or {}).get("nodes") or []
    return {
        "number": node["number"],
        "state": node["state"],
        "isDraft": node["isDraft"],
        "baseRefName": node.get("baseRefName"),
        "url": node.get("url"),
        "title": node.get("title"),
        "headRefOid": node.get("headRefOid"),
        "reviewDecision": node.get("reviewDecision"),
        "statusCheckRollup": contexts,
        "stack": _github_stack_dict(node),
    }


def normalize_github(data, alias_to_branch):
    repo = (data.get("data") or {}).get("repository") or {}
    owner = (repo.get("owner") or {}).get("login")
    out = {}
    for alias, branch in alias_to_branch.items():
        nodes = ((repo.get(alias) or {}).get("nodes")) or []
        best = pick_best(nodes, owner)
        out[branch] = _github_pr_dict(best) if best else None
    return out


def _chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def fetch_github_prs(branches, cwd=None):
    """{branch: pr dict | None} in one batched GraphQL request per CHUNK, or
    None when any request fails (offline, unauthed) — callers that render a
    PR column can then drop it instead of showing every branch as PR-less."""
    out = {}
    for chunk in _chunks(list(branches), CHUNK):
        q, alias_to_branch = build_github_query(chunk)
        p = run_(
            ["gh", "api", "graphql", "-f", f"query={q}",
             "-F", "owner={owner}", "-F", "name={repo}"],
            check=False, cwd=cwd,
        )
        if p.returncode != 0:
            return None
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError:
            return None
        out.update(normalize_github(data, alias_to_branch))
    return out


class Gh:
    NUM_PREFIX = "#"
    NOUN = "PR"

    def __init__(self, cwd=None):
        # gh resolves the {owner}/{repo} placeholders from its cwd; pass the
        # repo dir when the caller may run from elsewhere.
        self.cwd = cwd

    def have(self):
        return shutil.which("gh") is not None

    def require(self):
        if not self.have():
            die("gh is required for this command (brew install gh)")
        p = subprocess.run(
            ["gh", "auth", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if p.returncode != 0:
            die("gh is not authenticated — run `gh auth login`")

    def pr_for(self, branch):
        return self.prs_for([branch]).get(branch)

    def prs_for(self, branches):
        """{branch: pr dict | None}. A failed request (offline, unauthed)
        maps every branch to None."""
        res = self.prs_for_or_none(branches)
        return res if res is not None else {b: None for b in branches}

    def prs_for_or_none(self, branches):
        """{branch: pr dict | None}, or None when the request itself failed
        (offline, unauthed) — lets a caller distinguish "no PR" from
        "unreachable". No gh installed also → None."""
        branches = list(branches)
        if not branches:
            return {}
        if not self.have():
            return None
        return fetch_github_prs(branches, cwd=self.cwd)

    def pr_edit(self, number, base):
        """Retarget a PR's base branch. → subprocess result."""
        return run_(["gh", "pr", "edit", str(number), "--base", base], check=False)

    def pr_create(self, head, base, title, body, draft):
        args = ["gh", "pr", "create", "--head", head, "--base", base,
                "--title", title, "--body", body]
        if draft:
            args.append("--draft")
        return run_(args, check=False)

    # -------- native stacks (GitHub only) --------
    # The server-side stack object drives the stack navigator in the PR UI and
    # GitHub's own base retargeting when a member merges. It's cosmetic as far
    # as this tool is concerned: branches and PR bases are already correct
    # without it, so every failure here degrades to a missing navigator.
    # Deliberately absent from Gl — callers gate on isinstance(forge, Gh).

    def _stack_api(self, path, *, method=None, body=None):
        """→ (ok, parsed json | None). Any 2xx is success; the response body is
        only ever read opportunistically, since its exact shape is unverified."""
        args = ["gh", "api", f"repos/{{owner}}/{{repo}}/{path}"]
        if method:
            args += ["-X", method]
        if body is not None:
            args += ["--input", "-"]
        p = run_(args, check=False, cwd=self.cwd,
                 input_=json.dumps(body) if body is not None else None)
        if p.returncode != 0:
            return False, None
        try:
            return True, json.loads(p.stdout)
        except json.JSONDecodeError:
            return True, None

    def stacks_enabled(self, git):
        """Whether this repo has native stacked PRs. Cached in git config since
        it's a per-repo, rarely-changing capability; the collection endpoint
        404s when the feature is off."""
        cached = git.config_get("stack.githubStacksEnabled")
        if cached in ("true", "false"):
            return cached == "true"
        ok, _ = self._stack_api("stacks")
        git.config_set("stack.githubStacksEnabled", "true" if ok else "false")
        return ok

    def mark_stacks_unavailable(self, git):
        """Flip the cached capability off after a call unexpectedly 404s."""
        git.config_set("stack.githubStacksEnabled", "false")

    def stack_for_pr(self, number):
        """The stack a PR belongs to, or None. Resolves even from a merged
        member — merged PRs keep their entry."""
        ok, data = self._stack_api(f"stacks?pull_request={number}")
        if not ok or not isinstance(data, list) or not data:
            return None
        return data[0]

    def stack_create(self, pr_numbers):
        return self._stack_api("stacks", method="POST",
                               body={"pull_requests": list(pr_numbers)})

    def stack_add(self, stack_number, pr_numbers):
        return self._stack_api(f"stacks/{stack_number}/add", method="POST",
                               body={"pull_requests": list(pr_numbers)})


# GitLab pipeline statuses that fold to PENDING; CANCELED/SKIPPED/MANUAL show
# no checks glyph rather than pretending they ran.
_GITLAB_PENDING = {
    "RUNNING", "PENDING", "CREATED", "PREPARING", "WAITING_FOR_RESOURCE", "SCHEDULED",
}


def build_gitlab_query(fullpath, branches):
    # Branch names are string-inlined: glab silently drops list-typed
    # variables passed with -f, ignoring the filter.
    branch_list = ", ".join(json.dumps(b) for b in branches)
    return (
        f"query {{ project(fullPath: {json.dumps(fullpath)}) {{ "
        f"mergeRequests(sourceBranches: [{branch_list}], first: 50, sort: CREATED_DESC) {{ "
        "nodes { iid state draft sourceBranch targetBranch webUrl title diffHeadSha "
        "approvedBy(first: 1) { nodes { username } } "
        "reviewers(first: 5) { nodes { mergeRequestInteraction { reviewState } } } "
        "headPipeline { status } } } } }"
    )


def _gitlab_pr_dict(node):
    # The `approved` boolean is useless (true whenever 0 approvals are
    # required), so approval = anyone in approvedBy.
    if ((node.get("approvedBy") or {}).get("nodes")) or []:
        review = "APPROVED"
    elif any(
        ((r.get("mergeRequestInteraction") or {}).get("reviewState")) == "REQUESTED_CHANGES"
        for r in ((node.get("reviewers") or {}).get("nodes")) or []
    ):
        review = "CHANGES_REQUESTED"
    else:
        review = None
    pipeline = ((node.get("headPipeline") or {}).get("status")) or ""
    if pipeline == "SUCCESS":
        rollup = [{"name": "pipeline", "state": "SUCCESS"}]
    elif pipeline == "FAILED":
        rollup = [{"name": "pipeline", "state": "FAILURE"}]
    elif pipeline in _GITLAB_PENDING:
        rollup = [{"name": "pipeline", "state": "PENDING"}]
    else:
        rollup = []
    return {
        "number": int(node["iid"]),  # iid is a string in GitLab's GraphQL
        "state": {"merged": "MERGED", "closed": "CLOSED"}.get(node["state"], "OPEN"),
        "isDraft": bool(node.get("draft")),
        "baseRefName": node.get("targetBranch"),
        "url": node.get("webUrl"),
        "title": node.get("title"),
        "headRefOid": node.get("diffHeadSha"),
        "reviewDecision": review,
        "statusCheckRollup": rollup,
        "stack": None,  # GitLab has no native stack object
    }


def normalize_gitlab(data):
    """{source branch: pr dict} for every branch present in the response."""
    nodes = (
        (((data.get("data") or {}).get("project") or {}).get("mergeRequests") or {})
        .get("nodes")
    ) or []
    by_branch = {}
    for n in nodes:
        by_branch.setdefault(n["sourceBranch"], []).append(n)
    return {
        b: _gitlab_pr_dict(max(ns, key=lambda n: (n.get("state") == "opened", int(n["iid"]))))
        for b, ns in by_branch.items()
    }


def fetch_gitlab_prs(fullpath, branches, cwd=None):
    """{branch: pr dict | None} in one glab GraphQL request, or None when the
    request fails."""
    branches = list(branches)
    q = build_gitlab_query(fullpath, branches)
    p = run_(["glab", "api", "graphql", "-f", f"query={q}"], check=False, cwd=cwd)
    if p.returncode != 0:
        return None
    try:
        found = normalize_gitlab(json.loads(p.stdout))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    return {b: found.get(b) for b in branches}


class Gl:
    """GitLab MR fetcher/mutator. Same normalized dict shape and method surface
    as Gh, so callers hold one backend and never branch on the forge."""

    NUM_PREFIX = "!"
    NOUN = "MR"

    def __init__(self, fullpath, cwd=None):
        self.fullpath = fullpath
        self.cwd = cwd

    def have(self):
        return shutil.which("glab") is not None

    def require(self):
        if not self.have():
            die("glab is required for this command (brew install glab)")
        p = subprocess.run(
            ["glab", "auth", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if p.returncode != 0:
            die("glab is not authenticated — run `glab auth login`")

    def pr_for(self, branch):
        return self.prs_for([branch]).get(branch)

    def prs_for(self, branches):
        """{branch: pr dict | None}. A failed request (offline, unauthed)
        maps every branch to None."""
        res = self.prs_for_or_none(branches)
        return res if res is not None else {b: None for b in branches}

    def prs_for_or_none(self, branches):
        """{branch: pr dict | None}, or None when the request itself failed
        (offline, unauthed) — lets a caller distinguish "no MR" from
        "unreachable". No glab installed also → None."""
        branches = list(branches)
        if not branches:
            return {}
        if not self.have():
            return None
        return fetch_gitlab_prs(self.fullpath, branches, cwd=self.cwd)

    def pr_edit(self, number, base):
        """Retarget an MR's target branch. → subprocess result."""
        return run_(
            ["glab", "mr", "update", str(number), "--target-branch", base],
            check=False, cwd=self.cwd,
        )

    def pr_create(self, head, base, title, body, draft):
        args = ["glab", "mr", "create", "--source-branch", head,
                "--target-branch", base, "--title", title,
                "--description", body, "--yes"]
        if draft:
            args.append("--draft")
        return run_(args, check=False, cwd=self.cwd)
