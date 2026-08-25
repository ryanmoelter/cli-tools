# Working in this repo

`wt` (worktree helper) and `stack` (stacked-branch helper): two stdlib-only Python CLIs,
published via Homebrew from the `ryanmoelter/homebrew-tap` tap. Both are extensionless
executables in `src/`, sharing `src/_common/`.

This repo is also vendored as a submodule of Ryan's dotfiles, which symlinks
`src/{wt,stack}` onto PATH (and `_common/{gitcore,forge,ui}.py` into its own `.scripts/.common/`,
since the other scripts there use the same `die`/`warn`/`run_` and color helpers). So **editing
a file here changes the live system immediately** on his machine.

## Layout

- `src/wt`, `src/stack` — the two entrypoints. Each puts `src/_common` on `sys.path` via its own
  realpath, so they work from any install prefix and through any chain of symlinks.
- `src/_common/` — deliberately not a package; the flat modules are imported after the path
  insert. Dependency direction: `ui` → `forge` → `gitcore`; gitcore imports nothing local.
- `tests/` — kept outside `src/` so packaging never ships them.

## Versioning

`VERSION` at the root and `gitcore.VERSION` must agree — `tests/test_version.py` enforces it.
A release bumps both plus a `CHANGELOG.md` section, then tags `vN.N.N`. Both Homebrew formulas
pin the same tarball, so their `url`/`sha256` are identical and must be bumped together.

## Shared infrastructure

  - `gitcore.py` — `die`/`warn`/`run_`, the `Git` backend, and the shared config layer: `ConfigMixin` (one implementation of `config_get`/`config_get_all`/`config_set`/`config_add`/`config_unset` for both `Git` and wt's `WtGit`, which differ only in the `_git_cmd` they build), `SHARED_SECTION`, and `apply_display_settings`. See the shared-settings bullet below.
  - `forge.py` — batched GraphQL PR/MR fetch for GitHub + GitLab behind the interchangeable `Gh`/`Gl` backends; forge detection via `forge_kind`/`gitlab_fullpath`; `rollup_checks`; `IGNORED_WHEN_PENDING` + `set_ignored_when_pending` (empty by default, filled from config at startup — never hardcode a check name, it's specific to one repo's CI); GitHub-only native-stack methods on `Gh` (see below).
  - `ui.py` — colors + `set_color`, PR glyphs + `set_glyphs`, `pr_chip`, `glyph_key` (the help-page symbol legend, shared so both tools document the same vocabulary), `pick`.

- **Settings shared by `wt` and `stack`** live in a `ryanmoelter-cli-tools.*` git config section, read through `gitcore.ConfigMixin.config_chain`/`config_chain_all`/`config_chain_bool`. Precedence is **tool-specific key > shared key > derived default**, so `wt.baseBranch` still wins over `ryanmoelter-cli-tools.baseBranch` and nothing set previously breaks — the shared section is purely additive. Shared keys: `baseBranch`, `branchPrefix`, `forge`, `nerdFont`, `protectedWorktrees`, `ignoredPendingChecks`. `ryanmoelter-cli-tools.*` is **the only documented namespace** — the help pages name no `wt.*`/`stack.*` key, which are read purely for backwards compatibility. `protectedWorktrees` matches worktree *folder names* (basenames), never branches; it's inert for stack, which never reads it. Deliberately not shared: `stack.githubStacksEnabled` (a capability cache, not a user setting). `apply_display_settings(git, tool)` applies the display-affecting pair (`nerdFont`, `ignoredPendingChecks`) in each `main()` once a git backend exists — it imports `ui`/`forge` lazily because gitcore sits at the bottom of the dependency order.
  - The `config_get` contract is **`None` when a key is truly unset, `""` when explicitly empty**. That distinction is load-bearing: `git config wt.branchPrefix ""` means "no prefix" rather than falling through to the email-derived default, and `config_chain` preserves it by testing `is not None`.
  - Help styling: **bold** the program name, the `usage:` line, and every command signature; **dim** trailing `#` comments and parenthetical notes. Section headers stay unstyled. Both pages follow it — keep them in step. Both print directly, never through a pager: `less` mangles the Nerd Font glyphs in the symbol key.
  - Both help pages share one tail shape: `git config ryanmoelter-cli-tools.*` examples, the symbol key, then a resolved-values block. `stack`'s `usage(forge, git)` takes both backends to render it and returns early when run outside a repo. Keep the two aligned — a change to one page's tail belongs in the other.
  - Both help pages end with a symbol key from `ui.glyph_key()`, which reads the live glyph globals so it always documents the set actually in use, and paints each glyph the color it renders in (entries are `(glyph, color, meaning)`; `extra` also accepts a 2-tuple). Padding is computed on the raw glyph before the ANSI wrap, since escapes are zero-width but would be counted by a format field. `stack`'s `help` path therefore resolves a git backend and forge before printing, and tolerates being run outside a repo. Add a new glyph to `glyph_key` in the same change that introduces it, or the key silently goes stale.
  - `SYM_NET_OFF` means two different things and the key spells out both: from `pr_net_off_chip` it is a failed/timed-out forge fetch, labelled `NET_OFF_LABEL` ("not connected"); from `branch_pr_chip` it is a branch that was never pushed, left bare. Don't label the second.

## Tests

- Run with `python3 -m unittest discover tests` — stdlib only, no deps, 3.9+.
  - Every CLI is built on fakeable backends, constructed in `main()` and threaded through every command: `Git` + a `Gh`/`Gl` forge backend for stack, the `Ctx` seam + `TabOpener` for wt, the `Sys` seam for the app CLIs.
  - Tests swap in the in-memory fakes from `tests/fakes.py` (`FakeGit`/`FakeGh`/`FakeGl`/`FakeWtCtx`), loading the extensionless scripts via `load_script.py`. Output is pinned as plain text (colors forced off via `set_color(False)`).
  - `test_forge.py` covers the shared GraphQL query building and response normalization.
  - **`wt` discovers worktrees from git's own registry**, never by scanning a directory: `WtGit.worktree_entries` parses `git worktree list --porcelain` (flushing each record on the *next* `worktree` line, so detached worktrees — which have no `branch` line — survive), and `Ctx._registry` keys those paths by basename. `worktree_records` is the branch-only filter over it, for callers that act on the branch itself. So a worktree anywhere on disk is recognized, including ones other tools made and legacy ones under `.claude/worktrees/`; `Ctx.new_worktrees_dir` (`<root>/.worktrees`, kept self-ignored) only decides where *new* ones go. Two consequences to respect: names are no longer unique, so `worktree_path` refuses an ambiguous one and anything walking the list must use `worktree_paths_in_list_order` rather than resolving by name; and `.`-prefixed entries are skipped so bare layout's `.bare` doesn't show up as a worktree.
  - **`wt` opens a tab, not a workspace.** `TabOpener.open` defaults to a background surface in the *current* cmux workspace (`new-surface` + a typed `cd` + `rename-tab`), because a worktree belongs to the task at hand. `--workspace` is the explicit opt-in to a whole new grouped workspace; a failed surface falls back to Ghostty, never silently to a workspace. `--surface` is kept as a no-op alias so older invocations still work.
  - `wt`'s machine-readable output (`list --json`, `current --json`, consumed by the Sublime plugin) forks at the same pure-function seam as the table: `build_list_json` is the twin of `render_list`, both fed by `build_rows`. Test the payload by parsing it, never by string-matching. `wt list` paints the local table immediately, never waiting on the forge, and redraws the PR column in place once the fetch lands, so the fetch gets a budget (`_LIST_PR_TOTAL`) far past what a blocking wait could afford; `render_list` stays pure and `print_list_live` owns the cursor work, gated on `sys.stdout.isatty()` alone (not the `set_color` condition — `NO_COLOR` turns off color, not cursor addressing). `list --json` and `prune` still block once and never redraw. `wt current` deliberately reports null fields instead of dying outside a worktree — the status bar polls it for arbitrary files.
  - `test_stack_sync.py` and `test_stack_restack.py` are the exceptions to the fakes-only rule: each builds a real repo (plus a bare origin) in a tmpdir and drives `cmd_sync` / `cmd_restack` end-to-end, since the rebase arithmetic is the thing under test — `FakeGit` has no `run`/`git` seam and cannot rebase. They must set `commit.gpgsign=false` — the global config signs via 1Password and would hang.
  - The replay range is the subtle part of a restack. `replay_floor` decides where a branch replays from, and the two suites pull in opposite directions: sync's squash-merge tests need the *stored* base kept (it is what excludes an absorbed parent's commits), while the restack tests need it *raised* to the fork point when the branch merged its parent in. Change one and run both.
  - `test_init.py` runs both CLIs as subprocesses (not via `main()`, which reconfigures real stdout) and pipes each `init zsh` payload through `zsh -n`. That guard matters: a stray apostrophe inside a single-quoted completion description breaks every new shell.
  - Add a test when you touch list rendering, predicates, a backend seam, arg parsing, a command builder, worktree discovery, or the forge normalizers.

- **GitHub native stacked PRs.** `stack` uses GitHub's server-side stack object additively, never as the source of truth.
  - `Gh` gains `stacks_enabled` (cached in `stack.githubStacksEnabled`; the collection endpoint 404s when the feature is off), `stack_for_pr`, `stack_create`, and `stack_add`. `Gl` deliberately has none of these; callers gate on `isinstance(forge, Gh)`.
  - The read path is free: `stack`/`stackEntry` ride the existing batched query and come back `null` (not an error) on repos without the feature, so no capability probe is needed to render.
  - Rebasing still runs off the local `base`. GitHub's `headRefOid`/`base` describe the PR; ours records what has already been replayed.

- **Scratch repos for `stack`**: `stack` auto-detects the forge from origin (GitHub via `gh`, GitLab via `glab`; override with `git config stack.forge`). Two throwaway repos exercise it end-to-end (real branches, PRs/MRs, trunk moves) without touching real work. Clone either outside this checkout (e.g. a scratch dir) to test:
  - GitHub: `git@github.com:ryanmoelter/stack-script-test.git`
  - GitLab: `git@gitlab.com:ryanmoelter/stack-script-test.git`

## `wt switch` needs a shell wrapper

A subprocess cannot cd its parent shell, so `wt switch` is split in two:

- `wt path <name>` prints the path and is what the wrapper (and any script) calls.
- `wt switch` exits non-zero telling the user to add `eval "$(wt init zsh)"`.

Because the wrapper calls `path` and never `switch`, `switch` reaching the Python code always
means the wrapper is absent — no sentinel needed. `ZSH_INIT` emits plain `cd`; if a user has
aliased `cd`, that's theirs to own.

## Consumers outside this repo

- The dotfiles Sublime plugin (`sublime/plugins/Worktrees.py`) consumes `wt list --json` and
  `wt current --json`. **Changing either payload breaks it, across repo boundaries** — the most
  likely thing to break silently here.
- The dotfiles `worktrees` skill documents `wt path` for agents.
- Both Homebrew formulas assert on real behavior in `test do`, including `wt list --json`
  fields and `stack list --json`. Changing those payloads means updating the tap too.
