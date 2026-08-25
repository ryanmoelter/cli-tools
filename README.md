# Ryan Moelter's cli-tools

I got tired of managing git worktrees and PR stacks, so I made a couple CLI tools. I intend to add more. Both of these are installable via Homebrew. Both of them use `gh`/`glab` for interactions with GitHub/GitLab, so they don't need specific authentication with those platforms.

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

**`wt switch` requires the `wt init zsh` line in your `.zshrc`.** It needs to be a shell function in order to change the current directory, and errors out if it's not.

## `wt` — worktrees

Easily create, switch, and otherwise manage worktrees.

- List all worktrees with their GitHub PR or GitLab MR statuses
- Use `foreground` and `background` to move branches + changes in and out of the main worktree
- Open new worktrees in a new terminal tab (cmux or ghostty) if available
- Automatically prefix branch names for new worktrees (e.g. `ryanm/my-new-feature`)
- Co-exist with Claude Code desktop or any other tool that uses worktrees
- Work with agents using the agent-friendly `--json` commands

Commands:
```
checkout|co   [-b] <branch> [--no-tab|--workspace]
                                     check out a branch in a new worktree (-b creates)
cob           <branch> [--no-tab|--workspace]
                                     shorthand for `co -b` (create + check out)
open|o        <name> [--workspace]   open a worktree in a new terminal tab
background|bg [name] [--no-tab|--workspace]
                                     relocate current branch + changes to a new worktree
list|l        [--json] [--paths]     show worktrees + change/PR markers
delete|d      <name> [-f|--force]    remove worktree (branch left intact)
prune|p       [-y|--yes]             delete worktrees whose PR/MR is merged or closed
rename|r      [name] <new> [-f|--folder]
                                     rename branch (optionally folder too)
commit|cm     <name> [git-args]      commit -a in another worktree
switch|s      <name|branch>          cd there (needs the shell wrapper)
path          <name|branch>          print the path; for scripts
current       [--json]               report the cwd's worktree + branch
foreground|fg <name>                 pull another worktree's branch into cwd
setup                                interactively configure options
help|-h|--help                       show usage + resolved config
__complete worktrees|branches        hidden: emit completion candidates
```

## `stack` — stacked-branches

Manage stacks of branches and their PRs/MRs.

- List all stacked branches in a visual graph, including their PR/MR statuses
- Submit PRs/MRs for all branches in a stack at the same time
- Split, restack, and sync changes to manage the stack
- Integrate with GitHub's new PR stacks feature
- Work with agents using the non-interactive versions of every interactive command

```
list|ls [--all] [--json] [--no-pr]     show stack(s) + PR status
create|c <name> [--insert]             new branch atop the current one
split <name>:<commit> ...              split current branch at boundaries
track [<branch>] [--parent <p>]        start tracking (chain auto-discovery)
untrack [<branch>]                     stop tracking
checkout|co [<branch>]                 switch to a stack branch
prev | next | first | last             navigate the stack
restack [--all] [--trunk] [-i] [--dry-run]  rebase current branch + ancestors onto their parents
submit [--all] [--ready] [--dry-run]   push + create/retarget PRs for current + ancestors
sync [--all] [--push] [--dry-run]      fetch + absorb merged PRs + restack current + ancestors
setup                                  interactively configure options
__complete <what>                      hidden: completion candidates
```

## Requirements

- **python3 3.9+**: the system python on macOS is fine
- **git**
- Optional: **`gh` or `glab`**. Only PR/MR status and `stack submit`/`sync` need them; all local commands work without them
- Optional: **A font that supports [nerd font symbols](https://www.nerdfonts.com/)** for nice symbols. I personally like [Cascadia Code](https://github.com/microsoft/cascadia-code)
- Optional: **cmux or Ghostty** on macOS. Only for opening a worktree in a new terminal tab

## Configuration

Both tools read git config under `ryanmoelter-cli-tools.*` and mostly share settings. These can be configured per-repo, all through the `setup` commands.

```sh
wt setup
# or
stack setup
```

## Help

`wt help` and `stack help` list commands, show the current config values, and provide a key for all symbols used.

## Developing

```sh
python3 -m unittest discover tests    # stdlib only, no deps
```

If you're building/editing locally and also have the Homebrew version installed, check your PATH to make sure you're running the expected version.

Feel free to make an issue for anything you want to see!

## License

MIT
