# Changelog

## 0.1.1
- `stack restack`: replay a branch from its fork point off the parent. A branch
  that merged its parent (or the trunk) in rather than rebasing no longer
  replays the whole merged-in range — only its own commits.
- Homebrew installs now run on a pinned `python@3.14` rather than whatever
  `python3` is first on `PATH`.

## 0.1.0
- Initial release
- `wt` for managing worktrees
- `stack` for managing stacked branches/PRs/MRs

