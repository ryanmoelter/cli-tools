"""`init zsh` is what the formula's caveats tell users to eval, so both tools
must emit their shell integration from any cwd, outside a repo.

Runs each CLI as a subprocess rather than calling main(): wt's main() calls
sys.stdout.reconfigure(), which a StringIO can't satisfy.
"""

import os
import shutil
import subprocess
import sys
import unittest

SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)


def emit(tool, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SRC, tool), *args],
        capture_output=True, text=True, cwd="/",
    )


class InitTest(unittest.TestCase):
    def test_stack_emits_completion_only(self):
        r = emit("stack", "init", "zsh")
        self.assertEqual(r.returncode, 0)
        self.assertIn("compdef _stack stack", r.stdout)
        # stack changes no directories, so it ships no wrapper function.
        self.assertNotIn("\nstack() {", r.stdout)

    def test_wt_emits_wrapper_and_completion(self):
        r = emit("wt", "init", "zsh")
        self.assertEqual(r.returncode, 0)
        self.assertIn("compdef _wt wt", r.stdout)
        self.assertIn("wt() {", r.stdout)

    def test_unsupported_shell_exits_nonzero(self):
        for tool in ("wt", "stack"):
            r = emit(tool, "init", "fish")
            self.assertNotEqual(r.returncode, 0, tool)
            self.assertIn("zsh", r.stderr)

    def test_emitted_zsh_parses(self):
        """The init payload is eval'd at shell startup, so a quoting slip (an
        apostrophe inside a single-quoted completion description, say) would
        break every new shell."""
        if not shutil.which("zsh"):
            self.skipTest("zsh not installed")
        for tool in ("wt", "stack"):
            r = emit(tool, "init", "zsh")
            check = subprocess.run(["zsh", "-n"], input=r.stdout,
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0,
                             f"{tool} init zsh: {check.stderr}")

    def test_wrapper_delegates_to_path(self):
        """The wrapper must call `wt path`, not `wt switch` — bare `wt switch`
        is the error that tells users to install the wrapper."""
        out = emit("wt", "init", "zsh").stdout
        self.assertIn("command wt path", out)

    def test_version_runs_outside_a_repo(self):
        for tool in ("wt", "stack"):
            r = emit(tool, "--version")
            self.assertEqual(r.returncode, 0, tool)
            self.assertTrue(r.stdout.startswith(tool + " "), r.stdout)


if __name__ == "__main__":
    unittest.main()
