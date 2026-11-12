"""UserPromptSubmit / Stop hook: context-size warning and live token counters.

Usage: tokens_hook.py prompt|stop   (hook JSON on stdin)

Thin shim over caveman_core.tokens; imports only what it needs so it stays fast
on every prompt. Silent when the package is missing: the SessionStart hook
already tells the user how to install it.
"""
import sys

try:
    from caveman_core import tokens
except ImportError:
    sys.exit(0)

data = sys.stdin.buffer.read().decode("utf-8", "replace")
if len(sys.argv) > 1 and sys.argv[1] == "prompt":
    out = tokens.hook_prompt(data)
    if out:
        sys.stdout.write(out + "\n")
else:
    tokens.hook_stop(data)
sys.exit(0)
