"""Deprecated - use tests/render_page.py, which renders any page.

    python tests/render_page.py /admin/ /tmp/dash.html
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_page import render  # noqa: E402

if __name__ == "__main__":
    render("/admin/", Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/dash.html"))
