#!/usr/bin/env python3
"""Correct the T8 Director module URL for ComfyUI's installed node name."""

from pathlib import Path
import sys

OLD_MODULE_PATH = "/extensions/minimax-h3-audio-T8/director/workbench.mjs"
INSTALLED_MODULE_PATH = "/extensions/comfyui-minimax-h3-audio-T8/director/workbench.mjs"


def fix_page(path: Path) -> None:
    html = path.read_text(encoding="utf-8")
    if OLD_MODULE_PATH in html:
        path.write_text(html.replace(OLD_MODULE_PATH, INSTALLED_MODULE_PATH), encoding="utf-8")
        print(f"[OK] Fixed T8 Director extension URL in {path}")
    elif INSTALLED_MODULE_PATH in html:
        print(f"[OK] T8 Director extension URL is already correct in {path}")
    else:
        raise SystemExit(f"[ERROR] T8 Director module URL was not found in {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: h3_t8_director_webui.py <director-index.html>")
    fix_page(Path(sys.argv[1]))
