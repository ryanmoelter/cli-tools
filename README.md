# cli-tools

Two git CLIs: `wt` for worktrees, `stack` for stacked branches. Both are
stdlib-only Python, and both are built to be driven by AI agents as comfortably
as by a human — explicit arguments, `--json` output, distinct exit codes.

## `wt` — worktree helper

Makes it easy to run several agents at once, each in its own worktree. It needs
no special setup in a repo, and it finds every worktree git knows about
wherever it lives on disk, including ones made by other tools or by hand. On
macOS it opens each new worktree in a new terminal tab.

## `stack` — stacked-branch helper

Manages chains of branches where each branch's PR targets the one below it and
the forge squash-merges every one. Works on GitHub (via `gh`) or GitLab (via
`glab`), auto-detected from the origin URL. No state in the repo or in git
config — a single JSON file under `<git-common-dir>/stack/` is shared by all
worktrees of the repo.

## Install

```sh
brew install ryanmoelter/tap/wt
brew install ryanmoelter/tap/stack
```

Then add the shell integration to `~/.zshrc`:

```sh
eval "$(wt init zsh)"      # required for `wt switch`; also installs completions
eval "$(stack init zsh)"   # completions only
```

**`wt init zsh` is not optional if you want `wt switch`.** Changing your
shell's directory needs a function in that shell — a subprocess cannot do it.
Without the wrapper, `wt switch` exits with this instruction. Everything else
works either way, and `wt path <name>` prints a worktree's path for scripts and
other shells.

## Requirements

- **python3 3.9+** — the system python on macOS is fine. No pip dependencies.
- **git**.
- **`gh` or `glab`** — optional. Only PR/MR status and `stack submit`/`sync`
  need them; every local command works without.
- **cmux or Ghostty**, on macOS — optional. Only for opening a worktree in a
  new terminal tab. Elsewhere `wt` skips the tab and carries on.

## Configuration

Both tools read git config under `ryanmoelter-cli-tools.*`, shared between
them, so a setting written once applies to both:

```sh
git config ryanmoelter-cli-tools.baseBranch staging   # default: origin/HEAD
git config ryanmoelter-cli-tools.branchPrefix me/     # default: <email local-part>/
git config ryanmoelter-cli-tools.nerdFont true        # default: plain Unicode glyphs
```

Per-tool `wt.*` and `stack.*` keys override the shared ones. Run `wt setup` or
`stack setup` for an interactive pass, and `wt help` / `stack help` to see the
full key list alongside your resolved values.

## Everything else

`wt help` and `stack help` are the reference — they list current subcommands,
print resolved config, and render a symbol key for the status glyphs. They are
kept current with the code; this README deliberately does not duplicate them.

## Developing

```sh
python3 -m unittest discover tests    # stdlib only, no deps
```

Symlink `src/wt` and `src/stack` onto your PATH rather than installing the
formula. Homebrew's prefix sorts ahead of most personal bin directories, so an
installed copy will shadow your checkout and edits will appear to do nothing.

## License

MIT
