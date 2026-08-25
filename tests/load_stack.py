"""Back-compat shim: stack tests import load_stack; the shared loader lives
in load_script.py."""

from load_script import load_script


def load_stack():
    return load_script("stack", "stack_mod")
