"""Tests for first-class Project management and CLI commands."""

import asyncio
from pathlib import Path
import pytest
from orchestrator.config import get_default_config
from orchestrator.db import Database
from orchestrator.engine import OrchestrationEngine
from orchestrator.events import EventBus
from orchestrator.models import Project


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_state.db"
    return Database(str(db_file))


@pytest.mark.asyncio
async def test_project_crud(test_db: Database, tmp_path: Path):
    await test_db.initialize()
    ws = tmp_path / "famas_desktop"
    ws.mkdir()

    proj = Project(
        id="famas-desktop",
        name="FAMAS Desktop",
        workspace_root=str(ws),
        description="Desktop application for FAMAS",
        default_workflow="default_review_dev_loop",
    )
    await test_db.save_project(proj)

    # Retrieve by ID
    loaded = await test_db.get_project("famas-desktop")
    assert loaded is not None
    assert loaded.name == "FAMAS Desktop"
    assert loaded.workspace_root == str(ws)
    assert loaded.status == "active"

    # Retrieve by Workspace
    by_ws = await test_db.get_project_by_workspace(str(ws))
    assert by_ws is not None
    assert by_ws.id == "famas-desktop"

    # Active Project management
    await test_db.set_active_project("famas-desktop")
    active = await test_db.get_active_project()
    assert active is not None
    assert active.id == "famas-desktop"

    # Soft delete / Archive
    await test_db.archive_project("famas-desktop")
    archived = await test_db.get_project("famas-desktop")
    assert archived is not None
    assert archived.status == "archived"

    # Physical workspace must still exist!
    assert ws.exists()

    # Active project list excludes archived by default
    active_list = await test_db.list_projects(include_archived=False)
    assert len(active_list) == 0

    all_list = await test_db.list_projects(include_archived=True)
    assert len(all_list) == 1
    assert all_list[0].id == "famas-desktop"


@pytest.mark.asyncio
async def test_hybrid_project_resolution(test_db: Database, tmp_path: Path):
    cfg = get_default_config()
    events = EventBus()
    engine = OrchestrationEngine(config=cfg, db=test_db, events=events)
    await engine.initialize()

    ws = tmp_path / "my_codebase"
    ws.mkdir()
    sub_dir = ws / "src" / "deep"
    sub_dir.mkdir(parents=True)

    proj = Project(
        id="my-codebase",
        name="My Codebase",
        workspace_root=str(ws),
    )
    await test_db.save_project(proj)

    # 1. Detection via child path containment
    detected = await engine.resolve_active_project(str(sub_dir))
    assert detected is not None
    assert detected.id == "my-codebase"

    # 2. Fallback to active project if outside workspace
    outside = tmp_path / "somewhere_else"
    outside.mkdir()
    await test_db.set_active_project("my-codebase")
    fallback = await engine.resolve_active_project(str(outside))
    assert fallback is not None
    assert fallback.id == "my-codebase"


@pytest.mark.asyncio
async def test_workflow_run_links_project(test_db: Database, tmp_path: Path):
    cfg = get_default_config()
    events = EventBus()
    engine = OrchestrationEngine(config=cfg, db=test_db, events=events)
    await engine.initialize()

    ws = tmp_path / "project_alpha"
    ws.mkdir()
    proj = Project(id="alpha", name="Alpha", workspace_root=str(ws))
    await test_db.save_project(proj)

    # Run auto-detects project from workspace root
    run = await engine.create_workflow_run(workspace_root=str(ws))
    assert run.project_id == "alpha"

    # Stored run preserves project_id
    loaded_run = await test_db.get_workflow_run(run.run_id)
    assert loaded_run is not None
    assert loaded_run.project_id == "alpha"
