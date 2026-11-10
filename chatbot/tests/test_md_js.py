"""Markdown renderer (widget/md.js) tested through Node — the chat-standard GFM subset
plus injection-safety assertions (L3: model output is escaped, never rendered as HTML)."""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
TEST = Path(__file__).resolve().parent.parent / "tests" / "md_js.node.js"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_md_js():
    r = subprocess.run([NODE, str(TEST)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"md.js tests failed:\n{r.stdout}\n{r.stderr}"
    assert "md.js tests OK" in r.stdout
