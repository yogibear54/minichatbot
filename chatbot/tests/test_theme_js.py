"""Widget theming (widget/theme.js) tested through Node — the data-* -> postMessage
-> CSS-custom-prop pipeline's allowlist + color validation (PLAN §3.4, L3)."""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
TEST = Path(__file__).resolve().parent.parent / "tests" / "theme_js.node.js"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_theme_js():
    r = subprocess.run([NODE, str(TEST)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"theme.js tests failed:\n{r.stdout}\n{r.stderr}"
    assert "theme.js tests OK" in r.stdout
