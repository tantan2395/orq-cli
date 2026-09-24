"""Unit tests for the command-line interface."""

from pathlib import Path
import pytest
from orchestrator.cli import cmd_doctor, cmd_profile_import, cmd_profile_inspect
from argparse import Namespace


def test_cli_doctor(tmp_path: Path):
    args = Namespace(workspace=str(tmp_path))
    exit_code = cmd_doctor(args)
    assert exit_code == 0


def test_cli_profile_import_and_inspect(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import_args = Namespace(
        source="codex:test_uuid",
        target="test_target_prof",
    )
    res = cmd_profile_import(import_args)
    assert res == 0

    inspect_args = Namespace(target="test_target_prof")
    res_insp = cmd_profile_inspect(inspect_args)
    assert res_insp == 0


def test_cli_parser_task_args():
    from orchestrator.cli import build_parser

    parser = build_parser()

    # tui positional
    args = parser.parse_args(["tui", "Build a login page"])
    assert args.command == "tui"
    assert args.task_pos == "Build a login page"
    assert args.task is None

    # tui --task
    args = parser.parse_args(["tui", "--task", "Refactor auth module"])
    assert args.command == "tui"
    assert args.task == "Refactor auth module"

    # run positional
    args = parser.parse_args(["run", "Execute tests"])
    assert args.command == "run"
    assert args.task_pos == "Execute tests"

    # run --task
    args = parser.parse_args(["run", "-t", "Run linter"])
    assert args.command == "run"
    assert args.task == "Run linter"

