"""Guard: a literal line break inside a JS string literal breaks the whole page script."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "services" / "agent_core" / "web" / "templates"


def test_no_line_break_inside_js_strings():
    bad = []
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for script in re.findall(r"<script[^>]*>(.*?)</script>", text, flags=re.S):
            for line in script.splitlines():
                # string opened with ' or " right before the line ends, e.g. .split('
                if re.search(r"[(,=:]\s*['\"]$", line.rstrip()):
                    bad.append(f"{path.name}: {line.strip()[:80]}")
    assert not bad, bad
