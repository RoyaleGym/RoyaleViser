"""One line at the end of a run that names every test skipped for a missing recording of a
real battle, so a fresh clone's "N passed, M skipped" is never read as all green: those
tests pin numbers only the recording has, and a skip there is not a pass."""

from __future__ import annotations

import pytest

NOT_A_PASS = "SKIPPED, NOT PASSED"


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    skipped = [
        rep
        for rep in terminalreporter.stats.get("skipped", [])
        if NOT_A_PASS in str(getattr(rep, "longrepr", ""))
    ]
    if not skipped:
        return
    terminalreporter.write_sep("-", f"{len(skipped)} recorded-battle tests {NOT_A_PASS}")
    for rep in skipped:
        terminalreporter.write_line(f"  {rep.nodeid}")
    terminalreporter.write_line(
        "  These pin numbers only a recording of a real battle has. Point ROYALELIVE_REPORTS at"
        " the folder holding the recordings to run them (README: Tests)."
    )
