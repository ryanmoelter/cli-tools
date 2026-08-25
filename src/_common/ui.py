"""Shared terminal UI for the CLIs: ANSI colors, the PR-status glyph
vocabulary, PR chip rendering, and the interactive picker.

Colors are module globals mutated by set_color(). Code outside this module
must read them as attributes (ui.GREEN) — `from ui import GREEN` would freeze
the value bound at import time. The same applies to the seven glyphs
set_glyphs() swaps (SYM_CHECKS_PASS/FAIL/PENDING, SYM_MERGED, SYM_GITHUB,
SYM_GITLAB, SYM_NET_OFF): read them as ui.SYM_*, and never as a function's
default argument, which is evaluated once at definition time. The remaining
glyph constants never change and are safe to import by name.
"""

import shutil
import subprocess
import sys

from forge import rollup_checks
from gitcore import die

# ANSI color constants, populated by set_color(). They start empty (no color)
# so importing this module as a library — e.g. from the test suite — never
# emits escapes; each CLI's main() calls set_color(), and tests can force it
# off explicitly rather than relying on the ambient TTY state.
GREEN = RED = YELLOW = MAGENTA = CYAN = DIM = BOLD = RESET = ""

# Whether to emit OSC 8 terminal hyperlinks, set by set_color() from the same
# "writing to an interactive terminal" flag colors use — links only make sense
# there, never when piped or in tests.
_LINKS = False


def set_color(enabled):
    global GREEN, RED, YELLOW, MAGENTA, CYAN, DIM, BOLD, RESET, _LINKS
    _LINKS = enabled

    def c(code):
        return f"\033[{code}m" if enabled else ""

    GREEN = c("32")
    RED = c("31")
    YELLOW = c("33")
    MAGENTA = c("35")
    CYAN = c("36")
    DIM = c("2")
    BOLD = c("1")
    RESET = c("0")


def hyperlink(text, url):
    """Wrap text in an OSC 8 hyperlink so cmd+click opens url. Zero visible
    width. No-op when links are disabled or url is missing."""
    if not (_LINKS and url):
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


# PR-column glyphs, shared by stack and wt. The seven set by set_glyphs()
# below default to plain Unicode; the rest are plain already and never change.
SYM_CHECKS_PASS = "✔"
SYM_CHECKS_FAIL = "✘"
SYM_CHECKS_PENDING = "•"
SYM_MERGED = "⑃"
SYM_CLOSED = "⊘"
SYM_OPEN = "◉"
SYM_DRAFT = "◌"
SYM_APPROVED = "✓"
SYM_CHANGES = "±"
# The forge icons are empty in plain mode: the PR-number prefix ("#" on GitHub,
# "!" on GitLab) already says which forge a chip belongs to.
SYM_GITHUB = ""
SYM_GITLAB = ""
SYM_NET_OFF = "∅"
SYM_LOADING = "…"

# Nerd Font replacements, keyed by constant name.
_NERD_GLYPHS = {
    "SYM_CHECKS_PASS": "",    # nf-fa-check_circle
    "SYM_CHECKS_FAIL": "",    # nf-fa-circle_xmark
    "SYM_CHECKS_PENDING": "",  # nf-fa-circle
    "SYM_MERGED": "",         # nf-fa-code_merge
    "SYM_GITHUB": "",         # nf-fa-github
    "SYM_GITLAB": "",         # nf-fa-gitlab
    "SYM_NET_OFF": "󰪎",     # nf-md-web_off
}
_PLAIN_GLYPHS = {
    "SYM_CHECKS_PASS": SYM_CHECKS_PASS,
    "SYM_CHECKS_FAIL": SYM_CHECKS_FAIL,
    "SYM_CHECKS_PENDING": SYM_CHECKS_PENDING,
    "SYM_MERGED": SYM_MERGED,
    "SYM_GITHUB": SYM_GITHUB,
    "SYM_GITLAB": SYM_GITLAB,
    "SYM_NET_OFF": SYM_NET_OFF,
}


# Local git-status glyphs, shared by stack and wt.
SYM_AHEAD = "↗"   # ahead of parent (commits on this branch not on its parent)
SYM_UP = "↑"      # ahead of remote (unpushed commits)
SYM_DOWN = "↓"    # behind remote (remote commits not local)


def set_glyphs(nerd_font):
    """Swap the seven font-dependent glyphs. Every replacement is a single
    narrow-width codepoint, so the hand-counted chip widths hold either way —
    except the forge icons, which are empty in plain mode and collapse their
    lead (see pr_chip)."""
    globals().update(_NERD_GLYPHS if nerd_font else _PLAIN_GLYPHS)


def redraw_prefix(n_lines):
    """ANSI to rewind over an n_lines-tall block already printed: cursor up,
    then clear from there to the end of the screen."""
    return f"\033[{n_lines}A\033[J"


def glyph_key(*, forge_icon=None, num_prefix="#", extra=()):
    """The symbol legend for a help page, as lines. Reads the live glyph
    globals, so it always shows whichever set set_glyphs() installed.

    forge_icon: the resolved forge icon, listed only when non-empty (plain mode
    drops it, and the num_prefix entry explains the forge instead).
    extra: [(glyph, meaning)] or [(glyph, color, meaning)] appended for
    tool-specific symbols; the color is optional."""
    # Each glyph is painted the color it actually renders in, so the key reads
    # as a sample of the output rather than a plain list. Glyphs that carry no
    # fixed color in the chips (open, draft) stay uncolored here too.
    pr_col = [
        (SYM_OPEN, "", "open, no checks or review yet"),
        (SYM_DRAFT, "", "draft"),
        (SYM_CHECKS_PASS, GREEN, "checks passing"),
        (SYM_CHECKS_FAIL, RED, "checks failing"),
        (SYM_CHECKS_PENDING, YELLOW, "checks running"),
        (SYM_APPROVED, GREEN, "approved"),
        (SYM_CHANGES, RED, "changes requested"),
        (SYM_MERGED, MAGENTA, "merged"),
        (SYM_CLOSED, RED, "closed without merging"),
        ("–", DIM, "pushed, no PR/MR yet"),
        (SYM_LOADING, DIM, "fetching from the forge"),
        (f"{SYM_NET_OFF} {NET_OFF_LABEL}", RED, "forge unreachable"),
        (SYM_NET_OFF, DIM, "local-only branch, never pushed"),
    ]
    local = [
        (SYM_AHEAD, DIM, "commits not on the parent branch"),
        (SYM_UP, GREEN, "commits not pushed"),
        (SYM_DOWN, GREEN, "commits on the remote, not local"),
        ("+", GREEN, "uncommitted changes"),
    ]
    # The number prefix names the forge in either mode; the icon is listed on
    # top of it only when the Nerd Font set is in use.
    pr_col.insert(0, (f"{num_prefix}12", "", "PR/MR number (# GitHub, ! GitLab)"))
    if forge_icon:
        pr_col.insert(0, (forge_icon, DIM, "the forge the PR/MR lives on"))

    # Width from the widest bare glyph, not from the net-off entry's trailing
    # label — that one entry is allowed to overhang rather than indenting every
    # meaning past it. Glyphs count as one column each, which len() gets right
    # for every glyph in this vocabulary.
    # Accept 2- or 3-tuples from callers, normalizing to (glyph, color, meaning).
    extra = [e if len(e) == 3 else (e[0], "", e[1]) for e in extra]
    rows = [*pr_col, *local, *extra]
    w = max(len(g) for g, _, _ in rows if NET_OFF_LABEL not in g)

    def section(title, entries):
        yield f"  {title}"
        for glyph, color, meaning in entries:
            # Pad on the raw glyph, then wrap in color: the escapes are
            # zero-width but would otherwise be counted by the field width.
            pad = " " * max(0, w - len(glyph))
            yield f"    {color}{glyph}{RESET if color else ''}{pad}  {DIM}{meaning}{RESET}"

    lines = ["Symbols:"]
    lines += section("PR/MR column", pr_col)
    lines.append("")
    lines += section("local status", local)
    if extra:
        lines.append("")
        lines += section("stack graph", extra)
    return lines


def pr_chip(pr, num_prefix="#", forge_icon=None):
    """→ (rendered string incl. ANSI, visible width) so alignment ignores
    escapes: an optional dim forge icon, then <prefix><num> ("#" on GitHub,
    "!" on GitLab), a leading draft marker when the PR is a draft, then checks +
    review glyphs, or the bare open marker when a non-draft PR has nothing
    else."""
    if pr is None:
        return f"{DIM}(no PR){RESET}", len("(no PR)")
    lead, lead_w = (f"{DIM}{forge_icon}{RESET} ", 2) if forge_icon else ("", 0)
    prefix = f"{num_prefix}{pr['number']} "
    state = pr["state"]
    if state == "MERGED":
        glyphs, width = f"{MAGENTA}{SYM_MERGED}{RESET}", 1
    elif state == "CLOSED":
        glyphs, width = f"{RED}{SYM_CLOSED}{RESET}", 1
    else:  # OPEN
        parts, width = [], 0
        checks = rollup_checks(pr)
        if checks == "SUCCESS":
            parts.append(f"{GREEN}{SYM_CHECKS_PASS}{RESET}"); width += 1
        elif checks == "FAILURE":
            parts.append(f"{RED}{SYM_CHECKS_FAIL}{RESET}"); width += 1
        elif checks == "PENDING":
            parts.append(f"{YELLOW}{SYM_CHECKS_PENDING}{RESET}"); width += 1
        review = pr.get("reviewDecision") or "NONE"
        if review == "APPROVED":
            parts.append(f"{GREEN}{SYM_APPROVED}{RESET}"); width += 1
        elif review == "CHANGES_REQUESTED":
            parts.append(f"{RED}{SYM_CHANGES}{RESET}"); width += 1
        if pr["isDraft"]:
            # Draft marker leads; a space separates it from any check/review glyphs.
            sep = " " if parts else ""
            glyphs = f"{SYM_DRAFT}{sep}" + "".join(parts)
            width += 1 + len(sep)
        elif parts:
            glyphs = "".join(parts)
        else:
            glyphs, width = SYM_OPEN, 1
    return hyperlink(f"{lead}{prefix}{glyphs}", pr.get("url")), lead_w + len(prefix) + width


def _forge_lead(forge_icon):
    """→ (rendered lead, visible width) for a chip's optional forge icon. Empty
    in plain-glyph mode, where the icon collapses away entirely."""
    return (f"{forge_icon} ", 2) if forge_icon else ("", 0)


def pr_loading_chip(forge_icon):
    """→ (rendered, width): forge icon + ellipsis, for while the forge fetch is
    still in flight. Distinct from pr_net_off_chip, which means the fetch has
    already timed out or failed."""
    lead, lead_w = _forge_lead(forge_icon)
    return f"{DIM}{lead}{SYM_LOADING}{RESET}", lead_w + 1


# Spelled out next to the glyph, which is otherwise hard to read as "the forge
# was unreachable" — especially in plain mode, where it is a bare "∅".
NET_OFF_LABEL = "not connected"


def pr_net_off_chip(forge_icon, *, failed):
    """→ (rendered, width): forge icon + network-off icon + "not connected", for
    when the forge can't be reached. Red when the fetch failed, dim when it
    timed out. Distinct from branch_pr_chip's bare net-off glyph, which means a
    branch was never pushed rather than that the network is down."""
    color = RED if failed else DIM
    lead, lead_w = _forge_lead(forge_icon)
    return (f"{color}{lead}{SYM_NET_OFF} {NET_OFF_LABEL}{RESET}",
            lead_w + 1 + 1 + len(NET_OFF_LABEL))


def branch_pr_chip(pr, has_upstream, forge_icon, num_prefix="#"):
    """→ (rendered, width) for a branch's PR column once the forge fetch has
    succeeded, shared by stack and wt so both render identically:
    - a PR/MR exists → the pr_chip;
    - pushed, no PR → dim "<forge icon> –";
    - local-only (no upstream) → dim network-off glyph."""
    if pr is not None:
        return pr_chip(pr, num_prefix, forge_icon)
    if has_upstream:
        lead, lead_w = _forge_lead(forge_icon)
        return f"{DIM}{lead}–{RESET}", lead_w + 1
    return f"{DIM}{SYM_NET_OFF}{RESET}", 1


def pick(candidates, what):
    """candidates: [(value, display)]. Interactive only on a TTY; otherwise
    errors with the candidates so a non-interactive caller can self-correct."""
    if not candidates:
        die(f"no {what} to pick from")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        names = "\n".join(f"  {v}" for v, _ in candidates)
        die(f"no TTY — pass the {what} as an argument. Candidates:\n{names}")
    if shutil.which("fzf"):
        inp = "\n".join(d for _, d in candidates)
        p = subprocess.run(
            ["fzf", "--height=40%", "--reverse", "--prompt", f"{what}> "],
            input=inp,
            text=True,
            stdout=subprocess.PIPE,
        )
        sel = p.stdout.rstrip("\n")
        if p.returncode != 0 or not sel:
            die("cancelled")
        for v, d in candidates:
            if d == sel:
                return v
        die("cancelled")
    print(f"pick a {what}:")
    for i, (_, d) in enumerate(candidates, 1):
        print(f"  {i:2}) {d}")
    try:
        raw = input("> ").strip()
    except EOFError:
        die("cancelled")
    if not raw.isdigit() or not 1 <= int(raw) <= len(candidates):
        die(f"invalid choice: {raw!r}")
    return candidates[int(raw) - 1][0]
