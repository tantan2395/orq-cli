"""Session manager for canonical orchestrator sessions mapping to native CLI sessions."""

import uuid
from typing import Optional
from orchestrator.db import Database
from orchestrator.models import AgentSession, utc_now_iso


class SessionManager:
    """Manages lifecycle and normalization of agent sessions."""

    def __init__(self, db: Database):
        self.db = db

    async def get_or_create_session(
        self,
        workflow_run_id: str,
        role: str,
        agent_name: str,
        workspace_root: str,
        initial_native_id: Optional[str] = None,
    ) -> AgentSession:
        """Retrieves existing session for role in workflow run, or creates a new canonical session."""
        existing = await self.db.get_session(workflow_run_id, role)
        if existing:
            if initial_native_id and not existing.native_session_id:
                existing.native_session_id = initial_native_id
                existing.last_activity = utc_now_iso()
                await self.db.save_session(existing)
            return existing

        session = AgentSession(
            id=f"sess_{uuid.uuid4().hex[:12]}",
            workflow_run_id=workflow_run_id,
            role=role,
            agent_name=agent_name,
            native_session_id=initial_native_id,
            workspace_root=workspace_root,
            status="idle",
            created_at=utc_now_iso(),
            last_activity=utc_now_iso(),
        )
        await self.db.save_session(session)
        return session

    async def update_native_id(
        self, workflow_run_id: str, role: str, native_session_id: str
    ) -> Optional[AgentSession]:
        """Binds a captured native CLI ID (Codex UUID or Agy conversation ID) to the session."""
        session = await self.db.get_session(workflow_run_id, role)
        if session:
            session.native_session_id = native_session_id
            session.last_activity = utc_now_iso()
            await self.db.save_session(session)
        return session

    async def set_status(
        self, workflow_run_id: str, role: str, status: str
    ) -> Optional[AgentSession]:
        """Updates session status (idle, running, paused, closed)."""
        session = await self.db.get_session(workflow_run_id, role)
        if session:
            session.status = status
            session.last_activity = utc_now_iso()
            await self.db.save_session(session)
        return session
