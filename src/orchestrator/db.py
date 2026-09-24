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
    Project,
    TaskDependency,
    WorkflowRun,
    utc_now_iso,
)


class Database:
    """Async SQLite persistence layer."""

    def __init__(self, db_path: str = ".orchestrator/state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Creates tables, indices, and applies non-destructive schema migrations."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    workspace_root TEXT NOT NULL UNIQUE,
                    description TEXT,
                    default_workflow TEXT NOT NULL,
                    config TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT,
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
                    title TEXT,
                    description TEXT,
                    type TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    target_role TEXT,
                    status TEXT NOT NULL,
                    kanban_column TEXT NOT NULL DEFAULT 'ready',
                    payload TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id TEXT NOT NULL,
                    depends_on_task_id TEXT NOT NULL,
                    workflow_run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, depends_on_task_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Unique constraint: workflow_run_id + type + idempotency_key (ignoring NULLs)
            await db.execute("DROP INDEX IF EXISTS idx_tasks_run_idempotency")
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_run_type_idempotency
                ON tasks(workflow_run_id, type, idempotency_key)
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

            # Non-destructive migrations for existing databases
            async with db.execute("PRAGMA table_info(workflow_runs)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
                if "project_id" not in columns:
                    await db.execute("ALTER TABLE workflow_runs ADD COLUMN project_id TEXT")

            async with db.execute("PRAGMA table_info(tasks)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
                if "title" not in columns:
                    await db.execute("ALTER TABLE tasks ADD COLUMN title TEXT")
                if "description" not in columns:
                    await db.execute("ALTER TABLE tasks ADD COLUMN description TEXT")
                if "kanban_column" not in columns:
                    await db.execute("ALTER TABLE tasks ADD COLUMN kanban_column TEXT NOT NULL DEFAULT 'ready'")

            await db.commit()

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        """Upserts a workflow run."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO workflow_runs (
                    run_id, project_id, definition_id, definition_version, workspace_root,
                    worktree_path, current_stage, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    project_id=COALESCE(excluded.project_id, workflow_runs.project_id),
                    current_stage=excluded.current_stage,
                    status=excluded.status,
                    worktree_path=excluded.worktree_path,
                    updated_at=excluded.updated_at
            """, (
                run.run_id, run.project_id, run.definition_id, run.definition_version,
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
                keys = row.keys()
                return WorkflowRun(
                    run_id=row["run_id"],
                    project_id=row["project_id"] if "project_id" in keys else None,
                    definition_id=row["definition_id"],
                    definition_version=row["definition_version"],
                    workspace_root=row["workspace_root"],
                    worktree_path=row["worktree_path"],
                    current_stage=row["current_stage"],
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    async def list_workflow_runs(
        self, project_id: Optional[str] = None, limit: int = 50
    ) -> List[WorkflowRun]:
        """Lists recent workflow runs, optionally filtered by project_id."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if project_id:
                query = "SELECT * FROM workflow_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?"
                params = (project_id, limit)
            else:
                query = "SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?"
                params = (limit,)
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [
                    WorkflowRun(
                        run_id=r["run_id"],
                        project_id=r["project_id"] if "project_id" in r.keys() else None,
                        definition_id=r["definition_id"],
                        definition_version=r["definition_version"],
                        workspace_root=r["workspace_root"],
                        worktree_path=r["worktree_path"],
                        current_stage=r["current_stage"],
                        status=r["status"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    )
                    for r in rows
                ]

    def _row_to_task(self, row: aiosqlite.Row) -> OrchestrationTask:
        """Helper to deserialize task row with backward compatibility."""
        keys = row.keys()
        return OrchestrationTask(
            task_id=row["task_id"],
            workflow_run_id=row["workflow_run_id"],
            stage_id=row["stage_id"],
            idempotency_key=row["idempotency_key"],
            title=row["title"] if "title" in keys and row["title"] else "",
            description=row["description"] if "description" in keys and row["description"] else "",
            type=row["type"],
            requested_by=row["requested_by"],
            target_role=row["target_role"],
            status=row["status"],
            kanban_column=row["kanban_column"] if "kanban_column" in keys and row["kanban_column"] else "ready",
            payload=json.loads(row["payload"]),
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    async def save_task(self, task: OrchestrationTask) -> None:
        """Upserts an orchestration task."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO tasks (
                    task_id, workflow_run_id, stage_id, idempotency_key, title, description,
                    type, requested_by, target_role, status, kanban_column, payload, result, error,
                    created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    title=COALESCE(excluded.title, tasks.title),
                    description=COALESCE(excluded.description, tasks.description),
                    status=excluded.status,
                    kanban_column=excluded.kanban_column,
                    result=excluded.result,
                    error=excluded.error,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at
            """, (
                task.task_id, task.workflow_run_id, task.stage_id, task.idempotency_key,
                task.title or "", task.description or "", task.type, task.requested_by,
                task.target_role, task.status, task.kanban_column,
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
                return self._row_to_task(row)

    async def get_task_by_idempotency(
        self, workflow_run_id: str, idempotency_key: str, task_type: Optional[str] = None
    ) -> Optional[OrchestrationTask]:
        """Finds existing task with matching workflow_run_id, idempotency_key, and optionally task_type."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if task_type:
                query = "SELECT * FROM tasks WHERE workflow_run_id = ? AND idempotency_key = ? AND type = ?"
                params = (workflow_run_id, idempotency_key, task_type)
            else:
                query = "SELECT * FROM tasks WHERE workflow_run_id = ? AND idempotency_key = ?"
                params = (workflow_run_id, idempotency_key)
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return self._row_to_task(row)

    async def list_tasks(
        self, workflow_run_id: str, kanban_column: Optional[str] = None
    ) -> List[OrchestrationTask]:
        """Lists tasks for a workflow run, optionally filtered by kanban_column."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if kanban_column:
                query = "SELECT * FROM tasks WHERE workflow_run_id = ? AND kanban_column = ? ORDER BY created_at ASC"
                params = (workflow_run_id, kanban_column)
            else:
                query = "SELECT * FROM tasks WHERE workflow_run_id = ? ORDER BY created_at ASC"
                params = (workflow_run_id,)
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_task(r) for r in rows]

    async def delete_task(self, task_id: str) -> bool:
        """Deletes a task and its dependency edges."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM task_dependencies WHERE task_id = ? OR depends_on_task_id = ?", (task_id, task_id))
            cursor = await db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            await db.commit()
            return cursor.rowcount > 0

    # -------------------------------------------------------------
    # Project Operations
    # -------------------------------------------------------------
    async def save_project(self, project: Project) -> None:
        """Upserts a project."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO projects (
                    id, name, workspace_root, description, default_workflow,
                    config, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    workspace_root=excluded.workspace_root,
                    description=excluded.description,
                    default_workflow=excluded.default_workflow,
                    config=excluded.config,
                    status=excluded.status,
                    updated_at=excluded.updated_at
            """, (
                project.id, project.name, str(Path(project.workspace_root).resolve()),
                project.description or "", project.default_workflow,
                json.dumps(project.config), project.status,
                project.created_at, project.updated_at
            ))
            await db.commit()

    async def get_project(self, project_id: str) -> Optional[Project]:
        """Retrieves a project by ID or slug."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return Project(
                    id=row["id"],
                    name=row["name"],
                    workspace_root=row["workspace_root"],
                    description=row["description"],
                    default_workflow=row["default_workflow"],
                    config=json.loads(row["config"]),
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    async def get_project_by_workspace(self, workspace_path: str) -> Optional[Project]:
        """Finds active project whose workspace_root contains or equals workspace_path."""
        resolved = Path(workspace_path).resolve()
        projects = await self.list_projects(include_archived=False)
        for p in sorted(projects, key=lambda x: len(x.workspace_root), reverse=True):
            p_root = Path(p.workspace_root).resolve()
            try:
                resolved.relative_to(p_root)
                return p
            except ValueError:
                continue
        return None

    async def list_projects(self, include_archived: bool = False) -> List[Project]:
        """Lists projects, filtering out archived projects unless requested."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM projects ORDER BY updated_at DESC" if include_archived else "SELECT * FROM projects WHERE status = 'active' ORDER BY updated_at DESC"
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [
                    Project(
                        id=row["id"],
                        name=row["name"],
                        workspace_root=row["workspace_root"],
                        description=row["description"],
                        default_workflow=row["default_workflow"],
                        config=json.loads(row["config"]),
                        status=row["status"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                    for row in rows
                ]

    async def archive_project(self, project_id: str) -> bool:
        """Soft-deletes/archives a project by setting status to 'archived'."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE projects SET status = 'archived', updated_at = ? WHERE id = ?",
                (utc_now_iso(), project_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def set_active_project(self, project_id: str) -> None:
        """Sets the active project in app_metadata."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO app_metadata (key, value) VALUES ('active_project_id', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (project_id,))
            await db.commit()

    async def get_active_project(self) -> Optional[Project]:
        """Retrieves the active project set in app_metadata."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT value FROM app_metadata WHERE key = 'active_project_id'"
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return await self.get_project(row["value"])

    # -------------------------------------------------------------
    # Task Dependency Operations
    # -------------------------------------------------------------
    async def save_task_dependency(self, dep: TaskDependency) -> None:
        """Saves a task dependency edge."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR IGNORE INTO task_dependencies (
                    task_id, depends_on_task_id, workflow_run_id, created_at
                ) VALUES (?, ?, ?, ?)
            """, (dep.task_id, dep.depends_on_task_id, dep.workflow_run_id, dep.created_at))
            await db.commit()

    async def delete_task_dependency(self, task_id: str, depends_on_task_id: str) -> bool:
        """Deletes a specific dependency edge."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
                (task_id, depends_on_task_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_task_dependencies(self, task_id: str) -> List[str]:
        """Returns list of task IDs that task_id depends on (prerequisites)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
                (task_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    async def get_task_dependents(self, task_id: str) -> List[str]:
        """Returns list of task IDs that depend on task_id (downstream tasks)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT task_id FROM task_dependencies WHERE depends_on_task_id = ?",
                (task_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    async def list_all_dependencies(self, workflow_run_id: str) -> List[TaskDependency]:
        """Lists all dependency edges in a workflow run."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM task_dependencies WHERE workflow_run_id = ?",
                (workflow_run_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    TaskDependency(
                        task_id=r["task_id"],
                        depends_on_task_id=r["depends_on_task_id"],
                        workflow_run_id=r["workflow_run_id"],
                        created_at=r["created_at"],
                    )
                    for r in rows
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
