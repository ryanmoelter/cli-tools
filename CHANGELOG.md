# Changelog

## Unreleased
- `stack restack`: replay a branch from its fork point off the parent. A branch
  that merged its parent (or the trunk) in rather than rebasing no longer
  replays the whole merged-in range — only its own commits.

## 0.1.0
- Initial release
- `wt` for managing worktrees
- `stack` for managing stacked branches/PRs/MRs

