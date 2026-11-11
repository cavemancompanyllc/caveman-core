"""SessionStart hook: print workspace context and every enabled plugin's rules.

Thin shim over the caveman-core package so the logic is tested in one place.
It must never fail a session start, so a missing package is a one-line note.
"""
import sys

try:
    from caveman_core.cli import main
except ImportError:
    print("caveman-core: the caveman-core Python package isn't installed for the `python` on PATH, so the "
          "ledger and plugin rules weren't loaded. Install it (see "
          "https://github.com/cavemancompanyllc/caveman-core#install), then run `cavecore doctor`.")
    sys.exit(0)

sys.exit(main(["hook", "session-start"]))
