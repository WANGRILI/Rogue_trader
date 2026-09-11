"""Stable compatibility entrypoint for existing project schedulers.

The external command remains unchanged. This tiny control-plane shim delegates
to the currently activated, immutable project release. Application development
must happen in the dedicated development worktree, not in this production root.
"""

from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / ".runtime" / "bin" / "run-version"


def main() -> None:
    if not LAUNCHER.is_file():
        raise SystemExit(
            f"RogueTrader runtime is not initialized: {LAUNCHER}. "
            "Run './ops/release_manager.py bootstrap'."
        )
    os.execv(str(LAUNCHER), [str(LAUNCHER), "--foreground", *sys.argv[1:]])


if __name__ == "__main__":
    main()
