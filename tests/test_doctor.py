"""Unit tests for the doctor diagnostic suite."""

from orchestrator.doctor import DiagnosticStatus, Doctor


def test_doctor_runs_all_checks(tmp_path):
    doc = Doctor(workspace_root=str(tmp_path))
    results = doc.run_all()
    assert len(results) >= 8

    statuses = {r.status for r in results}
    # Verify our 4-level status taxonomy is represented
    assert DiagnosticStatus.PASS in statuses or DiagnosticStatus.WARN in statuses

    # Verify key checks are present
    names = [r.name for r in results]
    assert any("Codex" in n for n in names)
    assert any("Agy" in n for n in names)
    assert any("Python" in n for n in names)
    assert any("Git" in n for n in names)
    assert any("Claude" in n for n in names)

    # Optional Claude adapter check should be marked UNSUPPORTED
    claude_check = next(r for r in results if "Claude" in r.name)
    assert claude_check.status == DiagnosticStatus.UNSUPPORTED
