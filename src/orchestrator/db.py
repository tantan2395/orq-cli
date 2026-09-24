"""Async SQLite database store for workflow state, tasks, events, and artifacts."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiosqlite

from orchestrator.models import (
    AgentSession,
    Artifact,
    ArtifactType,
    OrchestrationEvent,
    OrchestrationTask,
    WorkflowRun,
)


class Database:
    """Async SQLite persistence layer."""

    def __init__(self, db_path: str = ".orchestrator/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Creates tables and unique constraints."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    definition_id TEXT NOT NULL,
                    definition_version INTEGER NOT NULL,
                    workspace_root TEXT NOT NULL,
                    worktree_path TEXT,
                    current_stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL,
                    stage_id TEXT,
                    idempotency_key TEXT,
                    type TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    target_role TEXT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)

            # Unique constraint: workflow_run_id + idempotency_key (ignoring NULLs)
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_run_idempotency
                ON tasks(workflow_run_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL,
                    stage_id TEXT,
                    task_id TEXT,
                    turn_id TEXT,
                    session_id TEXT,
                    role TEXT,
                    timestamp TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL,
                    task_id TEXT,
                    type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    native_session_id TEXT,
                    workspace_root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_activity TEXT NOT NULL,
                    UNIQUE(workflow_run_id, role)
                )
            """)

            await db.commit()

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        """Upserts a workflow run."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO workflow_runs (
                    run_id, definition_id, definition_version, workspace_root,
                    worktree_path, current_stage, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    current_stage=excluded.current_stage,
                    status=excluded.status,
                    worktree_path=excluded.worktree_path,
                    updated_at=excluded.updated_at
            """, (
                run.run_id, run.definition_id, run.definition_version,
                run.workspace_root, run.worktree_path, run.current_stage,
                run.status, run.created_at, run.updated_at
            ))
            await db.commit()

    async def get_workflow_run(self, run_id: str) -> Optional[WorkflowRun]:
        """Retrieves a workflow run by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return WorkflowRun(
                    run_id=row["run_id"],
                    definition_id=row["definition_id"],
                    definition_version=row["definition_version"],
                    workspace_root=row["workspace_root"],
                    worktree_path=row["worktree_path"],
                    current_stage=row["current_stage"],
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    async def save_task(self, task: OrchestrationTask) -> None:
        """Upserts an orchestration task."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO tasks (
                    task_id, workflow_run_id, stage_id, idempotency_key, type,
                    requested_by, target_role, status, payload, result, error,
                    created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    result=excluded.result,
                    error=excluded.error,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at
            """, (
                task.task_id, task.workflow_run_id, task.stage_id, task.idempotency_key,
                task.type, task.requested_by, task.target_role, task.status,
                json.dumps(task.payload),
                json.dumps(task.result) if task.result else None,
                task.error, task.created_at, task.started_at, task.completed_at
            ))
            await db.commit()

    async def get_task(self, task_id: str) -> Optional[OrchestrationTask]:
        """Retrieves a task by task_id."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return OrchestrationTask(
                    task_id=row["task_id"],
                    workflow_run_id=row["workflow_run_id"],
                    stage_id=row["stage_id"],
                    idempotency_key=row["idempotency_key"],
                    type=row["type"],
                    requested_by=row["requested_by"],
                    target_role=row["target_role"],
                    status=row["status"],
                    payload=json.loads(row["payload"]),
                    result=json.loads(row["result"]) if row["result"] else None,
                    error=row["error"],
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                )

    async def get_task_by_idempotency(
        self, workflow_run_id: str, idempotency_key: str
    ) -> Optional[OrchestrationTask]:
        """Finds existing task with matching workflow_run_id and idempotency_key."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE workflow_run_id = ? AND idempotency_key = ?",
                (workflow_run_id, idempotency_key)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return OrchestrationTask(
                    task_id=row["task_id"],
                    workflow_run_id=row["workflow_run_id"],
                    stage_id=row["stage_id"],
                    idempotency_key=row["idempotency_key"],
                    type=row["type"],
                    requested_by=row["requested_by"],
                    target_role=row["target_role"],
                    status=row["status"],
                    payload=json.loads(row["payload"]),
                    result=json.loads(row["result"]) if row["result"] else None,
                    error=row["error"],
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                )

    async def list_tasks(self, workflow_run_id: str) -> List[OrchestrationTask]:
        """Lists all tasks for a workflow run."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    OrchestrationTask(
                        task_id=row["task_id"],
                        workflow_run_id=row["workflow_run_id"],
                        stage_id=row["stage_id"],
                        idempotency_key=row["idempotency_key"],
                        type=row["type"],
                        requested_by=row["requested_by"],
                        target_role=row["target_role"],
                        status=row["status"],
                        payload=json.loads(row["payload"]),
                        result=json.loads(row["result"]) if row["result"] else None,
                        error=row["error"],
                        created_at=row["created_at"],
                        started_at=row["started_at"],
                        completed_at=row["completed_at"],
                    )
                    for row in rows
                ]

    async def save_event(self, event: OrchestrationEvent) -> None:
        """Appends an event to the persistent event log."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO events (
                    event_id, workflow_run_id, stage_id, task_id, turn_id,
                    session_id, role, timestamp, type, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id, event.workflow_run_id, event.stage_id,
                event.task_id, event.turn_id, event.session_id, event.role,
                event.timestamp, event.type, json.dumps(event.payload)
            ))
            await db.commit()

    async def list_events(
        self, workflow_run_id: str, limit: int = 200
    ) -> List[OrchestrationEvent]:
        """Retrieves recent events for a workflow run."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM events WHERE workflow_run_id = ? ORDER BY timestamp ASC LIMIT ?",
                (workflow_run_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    OrchestrationEvent(
                        event_id=row["event_id"],
                        workflow_run_id=row["workflow_run_id"],
                        stage_id=row["stage_id"],
                        task_id=row["task_id"],
                        turn_id=row["turn_id"],
                        session_id=row["session_id"],
                        role=row["role"],
                        timestamp=row["timestamp"],
                        type=row["type"],
                        payload=json.loads(row["payload"]),
                    )
                    for row in rows
                ]

    async def save_artifact(self, artifact: Artifact) -> None:
        """Records an artifact."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO artifacts (
                    artifact_id, workflow_run_id, task_id, type, path, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    path=excluded.path,
                    description=excluded.description
            """, (
                artifact.artifact_id, artifact.workflow_run_id, artifact.task_id,
                artifact.type.value, artifact.path, artifact.description, artifact.created_at
            ))
            await db.commit()

    async def list_artifacts(self, workflow_run_id: str) -> List[Artifact]:
        """Lists artifacts for a workflow run."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM artifacts WHERE workflow_run_id = ? ORDER BY created_at ASC",
                (workflow_run_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    Artifact(
                        artifact_id=row["artifact_id"],
                        workflow_run_id=row["workflow_run_id"],
                        task_id=row["task_id"],
                        type=ArtifactType(row["type"]),
                        path=row["path"],
                        description=row["description"],
                        created_at=row["created_at"],
                    )
                    for row in rows
                ]

    async def save_session(self, session: AgentSession) -> None:
        """Upserts a canonical agent session."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO sessions (
                    id, workflow_run_id, role, agent_name, native_session_id,
                    workspace_root, status, created_at, last_activity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_run_id, role) DO UPDATE SET
                    native_session_id=excluded.native_session_id,
                    status=excluded.status,
                    last_activity=excluded.last_activity
            """, (
                session.id, session.workflow_run_id, session.role, session.agent_name,
                session.native_session_id, session.workspace_root, session.status,
                session.created_at, session.last_activity
            ))
            await db.commit()

    async def get_session(self, workflow_run_id: str, role: str) -> Optional[AgentSession]:
        """Gets active session for a specific role in a workflow run."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM sessions WHERE workflow_run_id = ? AND role = ?",
                (workflow_run_id, role)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return AgentSession(
                    id=row["id"],
                    workflow_run_id=row["workflow_run_id"],
                    role=row["role"],
                    agent_name=row["agent_name"],
                    native_session_id=row["native_session_id"],
                    workspace_root=row["workspace_root"],
                    status=row["status"],
                    created_at=row["created_at"],
                    last_activity=row["last_activity"],
                )
