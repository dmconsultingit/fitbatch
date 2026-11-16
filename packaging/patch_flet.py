"""Work around a Flet 1.1.2 packaging bug on Windows (safe to run more than once).

flet_cli/__pyinstaller/win_utils.py opens the executable with
``open(exe_path, "ab", encoding="utf-8")``, which always fails (binary mode takes no encoding).
Remove this script once a fixed Flet release is pinned.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BROKEN = 'open(exe_path, "ab", encoding="utf-8")'
FIXED = 'open(exe_path, "ab")'


def main() -> int:
    spec = importlib.util.find_spec("flet_cli")  # a namespace package: no __init__.py
    locations = list(spec.submodule_search_locations or []) if spec else []
    targets = [Path(p) / "__pyinstaller" / "win_utils.py" for p in locations]
    target = next((t for t in targets if t.is_file()), None)
    if target is None:
        print("flet_cli is not installed")
        return 1
    text = target.read_text(encoding="utf-8")
    if BROKEN in text:
        target.write_text(text.replace(BROKEN, FIXED), encoding="utf-8")
        print(f"Patched {target}")
    else:
        print(f"Nothing to patch in {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
