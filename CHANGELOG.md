# Changelog

## Unreleased
- `stack list` is now quicker, showing the local state first and filling in the remote/PR/MR state once the network calls finish (matching `wt list`)
- `stack submit` and `stack sync` complete faster by running their PR/MR queries in parallel with other actions

## 0.1.1
- Fix `stack restack` when restacking onto a branch that has merged in the trunk (i.e. `main`)
- Use `python@3.14` instead of macOS's built-in python

## 0.1.0
- Initial release
- `wt` for managing worktrees
- `stack` for managing stacked branches/PRs/MRs

