# Releasing

A release spans two repos: this one (the source) and
[homebrew-tap](https://github.com/ryanmoelter/homebrew-tap) (the formulas). Both are
submodules of the dotfiles repo, so everything below can be done from `~/dotfiles`.

## The one thing that fails silently

Both formulas install from the **same tarball**, so `url` and `sha256` are identical in
`wt.rb` and `stack.rb`. If you bump only one, **nothing errors** — the old tag still exists
and still hashes correctly, so that formula keeps installing the previous version forever.
Step 5 has a grep that catches it.

## Steps

Set the version once:

```sh
V=0.1.1
```

### 1. Land the changes

Commit whatever is going out, in `cli-tools/`.

### 2. Bump the version in three places

- `src/_common/gitcore.py` — `VERSION = "0.1.1"`
- `VERSION` — `0.1.1`
- `CHANGELOG.md` — a new `## 0.1.1` section

`tests/test_version.py` checks all three agree, so a missed one fails the suite rather
than shipping.

### 3. Test, then tag

```sh
cd ~/dotfiles/cli-tools
python3 -m unittest discover tests            # must be green before tagging
git commit -am "Release v$V"
git tag -a "v$V" -m "v$V" && git push && git push --tags
```

The tag must exist before step 4: `brew audit --online` and the formulas' `url` both fetch
it.

### 4. Hash the tarball

```sh
URL="https://github.com/ryanmoelter/cli-tools/archive/refs/tags/v$V.tar.gz"
curl -sL "$URL" | shasum -a 256
```

### 5. Update both formulas

In `~/dotfiles/homebrew-tap/Formula/`, set `url` and `sha256` in **`wt.rb` and `stack.rb`**
to the same values. Then confirm:

```sh
cd ~/dotfiles/homebrew-tap
grep -h 'sha256\|url ' Formula/*.rb | sort -u    # must print exactly 2 lines
```

More than two lines means the formulas disagree — see the warning above.

### 6. Verify through Homebrew

Homebrew reads its **own** clone of the tap, not the submodule, so push first and refresh:

```sh
git commit -am "wt, stack $V" && git push
brew update

brew style ryanmoelter/tap
brew audit --strict --online ryanmoelter/tap/wt ryanmoelter/tap/stack
brew install --build-from-source ryanmoelter/tap/wt ryanmoelter/tap/stack
brew test ryanmoelter/tap/wt
brew test ryanmoelter/tap/stack
brew uninstall --formula wt stack
```

That last `brew uninstall` is **not optional on Ryan's machine**: `/opt/homebrew/bin` sorts
ahead of `~/.scripts` on PATH, so an installed copy silently shadows the dotfiles checkout
and local edits appear to do nothing.

`--formula` is needed for `stack`, which collides with a Homebrew cask of the same name.

### 7. Record the new SHAs in dotfiles

```sh
cd ~/dotfiles
git add cli-tools homebrew-tap
git commit -m "Bump cli-tools to $V"
git push
```

Without this a fresh `git clone --recurse-submodules` of the dotfiles repo still checks out
the previous version.

## Notes

- **If a version test fails on a value you can't find in any file**, it's macOS's system
  Python caching bytecode *outside* the source tree, at
  `~/Library/Caches/com.apple.python/<abs-path>/`. Clearing in-tree `__pycache__` does not
  touch it:
  ```sh
  rm -rf ~/Library/Caches/com.apple.python/Users/$USER/dotfiles
  ```

- **Never edit `$(brew --repository)/Library/Taps/ryanmoelter/homebrew-tap`.** That clone is
  derived; `brew update` overwrites it, and git-in-dotfiles cannot see changes made there.
  Author in the `homebrew-tap` submodule.
- **The Python pin is deliberate.** The formulas declare `python@3.14` and rewrite each
  script's shebang to that absolute path at install time, so the tools never depend on
  whatever `python3` happens to be first on PATH. Moving to a new Python is an explicit
  formula change — run the suite under the new interpreter first:
  `/opt/homebrew/opt/python@3.NN/bin/python3.NN -W error::DeprecationWarning -m unittest discover tests`.
  The code's actual floor is 3.9.
- **The Sublime plugin lives in the dotfiles repo** and consumes `wt list --json` and
  `wt current --json`. Changing either payload breaks it across a repo boundary — the
  likeliest thing to break quietly. The formulas' `test do` blocks assert on those fields
  too, so the tap needs updating in the same release.
