# Changelog

## Unreleased
- `stack sync` no longer rebases the stack root onto the latest trunk by default — pass `--trunk` for the old behavior. Being behind a fast-moving trunk isn't a restack, and rebasing (and force-pushing, under `--push`) on every sync was churn. A root whose base has fallen off trunk is still moved, as before
- `wt prune` now also deletes merged branches that have no worktree, which previously accumulated forever
- `wt prune` no longer aborts when `gh`/`glab` is unavailable — it warns and falls back to the branches git can prove are merged
- `wt` and `stack` fetch PR/MR status faster on repos with many branches, by running the batched queries in parallel
- Fix `stack` repeatedly warning `pruned deleted branch '<name>' from stack state` on every command: the cleanup is now written back the first time it's noticed, instead of only in memory

## 0.1.2
- `stack list` is now quicker, showing the local state first and filling in the remote/PR/MR state once the network calls finish (matching `wt list`)
- `stack submit` and `stack sync` complete faster by running their PR/MR queries in parallel with other actions

## 0.1.1
- Fix `stack restack` when restacking onto a branch that has merged in the trunk (i.e. `main`)
- Use `python@3.14` instead of macOS's built-in python

## 0.1.0
- Initial release
- `wt` for managing worktrees
- `stack` for managing stacked branches/PRs/MRs

