"""Scope budget: fail the build when code outgrows the limits in README."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _count(paths) -> int:
    """Non-blank, non-comment lines."""
    n = 0
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                n += 1
    return n


def _files(sub: str, pattern: str) -> list[Path]:
    d = ROOT / sub
    return [p for p in d.rglob(pattern) if p.is_file()] if d.exists() else []


def test_app_python_budget():
    assert _count(_files("finres", "*.py")) <= 1500


def test_templates_css_budget():
    assert _count(_files("finres/templates", "*") + _files("finres/static", "*")) <= 600


def test_lab_budget():
    assert _count(_files("lab", "*.py")) <= 850  # ADR-008, raised by the TL for ADR-007 H2 + ADR-007a


def test_research_budget():
    assert _count(_files("research", "*.py")) <= 450  # ADR-008
