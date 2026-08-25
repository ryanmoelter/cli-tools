"""Load the extensionless CLI scripts in src/ as importable modules.

SourceFileLoader executes the script body, including its own sys.path insert
for src/_common/, so the shared modules resolve without extra setup here.
Loads are cached by module name — the scripts' module bodies are idempotent,
but re-executing them per test file would be wasted work.
"""

import os
import sys
from importlib.machinery import SourceFileLoader

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"
)


def load_script(fname, modname):
    if modname in sys.modules:
        return sys.modules[modname]
    mod = SourceFileLoader(modname, os.path.join(_SCRIPTS_DIR, fname)).load_module()
    # Force color off so rendered text in tests is plain, independent of the
    # test runner's TTY state. (Both scripts re-export ui.set_color.)
    mod.set_color(False)
    return mod
