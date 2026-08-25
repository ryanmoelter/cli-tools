# Releasing

We need to release both in this repo and in the [homebrew-tap](https://github.com/ryanmoelter/homebrew-tap).

## Common pitfall

Both formulas install from the **same tarball**, so `url` and `sha256` are identical in `wt.rb` and `stack.rb`. We need to be sure to bump the values for both tools. Step 5 has a grep that catches it.

## Steps

Set the version once:

```sh
V=X.Y.Z
```

### 1. Land the changes

Commit whatever is going out, in `cli-tools/`.

### 2. Bump the version

- `src/_common/gitcore.py`: `VERSION = "X.Y.Z"`
- `VERSION`: `X.Y.Z`
- `CHANGELOG.md`: a new `## X.Y.Z` section, maybe replacing an `## Unreleased` section
- `CHANGELOG.md`: also write the changelog, if necessary

Humans are responsible for the changelog entries, but agents can draft them as long as they wait for explicit approval from a human.

`tests/test_version.py` checks all three places agree.

### 3. Test, then tag

```sh
cd ~/dotfiles/cli-tools
python3 -m unittest discover tests 2>&1 | grep -E '^(Ran |OK|FAILED)'
git commit -am "Release v$V"
git tag -a "v$V" -m "v$V" && git push && git push --tags
```

Note that the tests write to both STDIN and STDERR, so `tail` doesn't always catch the test results.

```sh
python3 -m unittest discover tests >/dev/null 2>&1; echo "exit=$?"
```

The tag must exist before step 4: `brew audit --online` and the formulas' `url` both fetch it.

### 4. Hash the tarball

```sh
URL="https://github.com/ryanmoelter/cli-tools/archive/refs/tags/v$V.tar.gz"
curl -sL "$URL" | shasum -a 256
```

### 5. Update both formulas

In `~/dotfiles/homebrew-tap/Formula/`, set `url` and `sha256` in **`wt.rb` and `stack.rb`** to the same values. Then confirm:

```sh
cd ~/dotfiles/homebrew-tap
grep -h 'sha256\|url ' Formula/*.rb | sort -u    # must print exactly 2 lines
```

More than two lines means the formulas disagree — see the warning above.

### 6. Verify through Homebrew

Homebrew reads its **own** clone of the tap, so push first and refresh:

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

The last `brew uninstall` is to avoid the brew install overwriting the local development install.

`--formula` is needed for `stack`, which collides with a Homebrew cask of the same name.

`brew test` is quiet on success and its output is all `==>` command echoes, so read its exit code rather than its last lines (and don't pipe it, since `PIPESTATUS` comes back empty here):

```sh
brew test ryanmoelter/tap/wt    >/tmp/wt-test.log    2>&1; echo "wt exit=$?"
brew test ryanmoelter/tap/stack >/tmp/stack-test.log 2>&1; echo "stack exit=$?"
```

Run `brew style` **before pushing** when a formula changed. It only reads Homebrew's own tap clone, so the loop is edit → push → `brew update` → `brew style`, and a style fix means going round again. Two rules it enforces that are easy to trip: `include` goes above `desc`, and use `formula_opt_bin("python@3.14")` rather than `Formula["python@3.14"].opt_bin`.

### 7. Record the new SHAs in a parent git repo

If this repo is in a submodule, update the parent repo's pointer to the new commit.

### 8. Publish the GitHub release

The release notes are the changelog section for this version, used as-is:

```sh
cd ~/dotfiles/cli-tools
python3 - "$V" <<'EOF' > /tmp/notes-$V.md
import re, sys
ver = sys.argv[1]
body = open("CHANGELOG.md").read()
m = re.search(rf"^## {re.escape(ver)}\n(.*?)(?=^## |\Z)", body, re.M | re.S)
if not m:
    sys.exit(f"no ## {ver} section in CHANGELOG.md")
print(m.group(1).strip())
EOF
gh release create "v$V" --title "v$V" --notes-file /tmp/notes-$V.md --verify-tag
gh release view "v$V"
```

`--verify-tag` makes the command fail if the tag is missing rather than creating one, so a skipped step 3 cannot invent a release off the wrong commit. Publishing a release does not move the tag or change the tarball — the `sha256` in the formulas stays valid.

One release covers both tools: they ship from this repo's single tarball.

## Notes + troubleshooting

- **If a version test fails on a value you can't find in any file**, it's macOS's system Python caching bytecode *outside* the source tree, at `~/Library/Caches/com.apple.python/<abs-path>/`. Clearing in-tree `__pycache__` does not touch it:
  ```sh
  rm -rf ~/Library/Caches/com.apple.python/Users/$USER/dotfiles
  ```

- **Never edit `$(brew --repository)/Library/Taps/<user>/homebrew-tap`.** That clone is derived; `brew update` overwrites it. Author in the `homebrew-tap` repo/submodule.
