"""Diagnostic suite for agent-orchestrator environment and CLI capabilities."""

import importlib
import os
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import List
from pydantic import BaseModel


class DiagnosticStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNSUPPORTED = "UNSUPPORTED"


class DiagnosticResult(BaseModel):
    """Result of a single diagnostic check."""
    name: str
    status: DiagnosticStatus
    details: str


class Doctor:
    """Runs capability and environment verification checks."""

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root).resolve()

    def run_all(self) -> List[DiagnosticResult]:
        """Executes all diagnostics and returns the collection of results."""
        results: List[DiagnosticResult] = []
        results.extend(self._check_python_env())
        results.extend(self._check_codex())
        results.extend(self._check_agy())
        results.extend(self._check_git())
        results.extend(self._check_ipc_and_storage())
        results.extend(self._check_existing_mcps())
        results.extend(self._check_optional_adapters())
        return results

    def _check_python_env(self) -> List[DiagnosticResult]:
        results = []
        import sys
        if sys.version_info >= (3, 12):
            results.append(DiagnosticResult(
                name="Python runtime (>= 3.12)",
                status=DiagnosticStatus.PASS,
                details=f"Python {sys.version.split()[0]}",
            ))
        else:
            results.append(DiagnosticResult(
                name="Python runtime (>= 3.12)",
                status=DiagnosticStatus.WARN,
                details=f"Python {sys.version.split()[0]} (recommended >= 3.12)",
            ))

        for pkg in ["pydantic", "mcp", "rich", "textual", "aiosqlite", "yaml"]:
            try:
                mod = importlib.import_module(pkg)
                ver = getattr(mod, "__version__", "installed")
                results.append(DiagnosticResult(
                    name=f"Python package: {pkg}",
                    status=DiagnosticStatus.PASS,
                    details=f"Version {ver}",
                ))
            except ImportError as e:
                results.append(DiagnosticResult(
                    name=f"Python package: {pkg}",
                    status=DiagnosticStatus.FAIL,
                    details=str(e),
                ))
        return results

    def _check_codex(self) -> List[DiagnosticResult]:
        results = []
        codex_bin = shutil.which("codex") or "/home/tantan/.npm-global/bin/codex"
        if not os.path.exists(codex_bin):
            results.append(DiagnosticResult(
                name="Codex executable",
                status=DiagnosticStatus.FAIL,
                details=f"Not found at {codex_bin}",
            ))
            return results

        results.append(DiagnosticResult(
            name="Codex executable",
            status=DiagnosticStatus.PASS,
            details=codex_bin,
        ))

        try:
            res = subprocess.run([codex_bin, "exec", "--help"], capture_output=True, text=True, timeout=5)
            help_text = res.stdout + res.stderr
            if "--json" in help_text:
                results.append(DiagnosticResult(
                    name="Codex JSON streaming (--json)",
                    status=DiagnosticStatus.PASS,
                    details="Supported",
                ))
            else:
                results.append(DiagnosticResult(
                    name="Codex JSON streaming (--json)",
                    status=DiagnosticStatus.WARN,
                    details="--json flag not found in exec --help",
                ))

            if "resume" in help_text:
                results.append(DiagnosticResult(
                    name="Codex session resume (exec resume)",
                    status=DiagnosticStatus.PASS,
                    details="Supported",
                ))
            else:
                results.append(DiagnosticResult(
                    name="Codex session resume (exec resume)",
                    status=DiagnosticStatus.WARN,
                    details="resume subcommand not found in exec --help",
                ))
        except Exception as e:
            results.append(DiagnosticResult(
                name="Codex capability check",
                status=DiagnosticStatus.WARN,
                details=f"Failed to inspect codex exec: {e}",
            ))

        return results

    def _check_agy(self) -> List[DiagnosticResult]:
        results = []
        agy_bin = shutil.which("agy") or "/home/tantan/.local/bin/agy"
        if not os.path.exists(agy_bin):
            results.append(DiagnosticResult(
                name="Agy executable",
                status=DiagnosticStatus.FAIL,
                details=f"Not found at {agy_bin}",
            ))
            return results

        results.append(DiagnosticResult(
            name="Agy executable",
            status=DiagnosticStatus.PASS,
            details=agy_bin,
        ))

        try:
            res = subprocess.run([agy_bin, "--help"], capture_output=True, text=True, timeout=5)
            help_text = res.stdout + res.stderr
            if "stream-json" in help_text:
                results.append(DiagnosticResult(
                    name="Agy stream-json output",
                    status=DiagnosticStatus.PASS,
                    details="Supported",
                ))
            else:
                results.append(DiagnosticResult(
                    name="Agy stream-json output",
                    status=DiagnosticStatus.WARN,
                    details="stream-json format not found in help",
                ))

            if "--conversation" in help_text or "-c" in help_text:
                results.append(DiagnosticResult(
                    name="Agy conversation resume",
                    status=DiagnosticStatus.PASS,
                    details="Supported",
                ))
            else:
                results.append(DiagnosticResult(
                    name="Agy conversation resume",
                    status=DiagnosticStatus.WARN,
                    details="--conversation flag not found in help",
                ))
        except Exception as e:
            results.append(DiagnosticResult(
                name="Agy capability check",
                status=DiagnosticStatus.WARN,
                details=f"Failed to inspect agy: {e}",
            ))

        return results

    def _check_git(self) -> List[DiagnosticResult]:
        results = []
        git_bin = shutil.which("git")
        if not git_bin:
            results.append(DiagnosticResult(
                name="Git binary",
                status=DiagnosticStatus.FAIL,
                details="git command not found",
            ))
            return results

        results.append(DiagnosticResult(
            name="Git binary",
            status=DiagnosticStatus.PASS,
            details=git_bin,
        ))

        try:
            res = subprocess.run(["git", "worktree", "list"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                results.append(DiagnosticResult(
                    name="Git worktree support",
                    status=DiagnosticStatus.PASS,
                    details="Functional",
                ))
            else:
                results.append(DiagnosticResult(
                    name="Git worktree support",
                    status=DiagnosticStatus.WARN,
                    details="Current directory is not inside a git repository",
                ))
        except Exception as e:
            results.append(DiagnosticResult(
                name="Git worktree support",
                status=DiagnosticStatus.WARN,
                details=str(e),
            ))

        return results

    def _check_ipc_and_storage(self) -> List[DiagnosticResult]:
        results = []
        tmp_dir = Path("/tmp")
        if os.access(tmp_dir, os.W_OK):
            results.append(DiagnosticResult(
                name="Local IPC socket directory (/tmp)",
                status=DiagnosticStatus.PASS,
                details="Writable",
            ))
        else:
            results.append(DiagnosticResult(
                name="Local IPC socket directory (/tmp)",
                status=DiagnosticStatus.FAIL,
                details="Not writable",
            ))

        dot_orchestrator = self.workspace_root / ".orchestrator"
        try:
            dot_orchestrator.mkdir(parents=True, exist_ok=True)
            results.append(DiagnosticResult(
                name="Runtime state directory (.orchestrator)",
                status=DiagnosticStatus.PASS,
                details=str(dot_orchestrator),
            ))
        except Exception as e:
            results.append(DiagnosticResult(
                name="Runtime state directory (.orchestrator)",
                status=DiagnosticStatus.FAIL,
                details=str(e),
            ))

        return results

    def _check_existing_mcps(self) -> List[DiagnosticResult]:
        results = []
        memoryoss_cfg = Path("/home/tantan/memoryoss.toml")
        if memoryoss_cfg.exists():
            results.append(DiagnosticResult(
                name="Existing MCP: MemoryOSS config",
                status=DiagnosticStatus.PASS,
                details=str(memoryoss_cfg),
            ))
        else:
            results.append(DiagnosticResult(
                name="Existing MCP: MemoryOSS config",
                status=DiagnosticStatus.WARN,
                details="memoryoss.toml not found",
            ))

        codex_cfg = Path("/home/tantan/.codex/config.toml")
        if codex_cfg.exists() and "socraticode" in codex_cfg.read_text(encoding="utf-8"):
            results.append(DiagnosticResult(
                name="Existing MCP: SocratiCode in Codex",
                status=DiagnosticStatus.PASS,
                details="Found in ~/.codex/config.toml",
            ))
        else:
            results.append(DiagnosticResult(
                name="Existing MCP: SocratiCode in Codex",
                status=DiagnosticStatus.WARN,
                details="Not detected in ~/.codex/config.toml",
            ))

        return results

    def _check_optional_adapters(self) -> List[DiagnosticResult]:
        return [
            DiagnosticResult(
                name="Optional Claude CLI adapter",
                status=DiagnosticStatus.UNSUPPORTED,
                details="Not configured (future backend)",
            ),
            DiagnosticResult(
                name="Optional PTY Multiplexer adapter",
                status=DiagnosticStatus.UNSUPPORTED,
                details="Deferred to post-MVP",
            ),
        ]
