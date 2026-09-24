"""Unix domain socket IPC helpers for communication between MCP stdio bridge and Engine."""

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

DEFAULT_SOCKET_PATH = Path("/tmp/agent_orchestrator.sock")


class IPCServer:
    """Async Unix Domain Socket server running in the Orchestrator Engine process."""

    def __init__(
        self,
        handler: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]],
        socket_path: Optional[Path] = None,
    ):
        self.handler = handler
        self.socket_path = socket_path or DEFAULT_SOCKET_PATH
        self._server: Optional[asyncio.Server] = None

    async def start(self) -> None:
        """Starts the Unix domain socket server."""
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Processes incoming requests from MCP bridge."""
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                req = json.loads(line.decode("utf-8"))
                method = req.get("method", "")
                params = req.get("params", {})
                resp = await self.handler(method, params)
            except Exception as e:
                resp = {"status": "error", "error": str(e)}

            out_bytes = json.dumps(resp).encode("utf-8") + b"\n"
            writer.write(out_bytes)
            await writer.drain()

        writer.close()
        await writer.wait_closed()

    async def stop(self) -> None:
        """Stops the socket server and removes the socket file."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass


class IPCClient:
    """Client used by the MCP stdio bridge to forward tool calls to the running Engine."""

    def __init__(self, socket_path: Optional[Path] = None):
        self.socket_path = socket_path or DEFAULT_SOCKET_PATH

    async def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sends an IPC request to the engine and returns the response."""
        if not self.socket_path.exists():
            return {
                "status": "error",
                "error": f"Orchestrator IPC socket not found at {self.socket_path}. Is the Engine running?",
            }

        try:
            reader, writer = await asyncio.open_unix_connection(path=str(self.socket_path))
            payload = json.dumps({"method": method, "params": params}).encode("utf-8") + b"\n"
            writer.write(payload)
            await writer.drain()

            resp_line = await reader.readline()
            writer.close()
            await writer.wait_closed()

            if not resp_line:
                return {"status": "error", "error": "Empty response from Orchestrator engine"}

            return json.loads(resp_line.decode("utf-8"))
        except Exception as e:
            return {"status": "error", "error": f"Failed to communicate with engine: {e}"}
