"""Helpers for reading agent instructions from AGENTS.md and CLAUDE.md."""

from __future__ import annotations

import asyncio
import logging
import shlex

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)


async def _read_md_file_in_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    filename: str,
) -> str | None:
    """Read a markdown file from the repo root if it exists."""
    safe_path = shlex.quote(f"{repo_dir}/{filename}")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        sandbox_backend.execute,
        f"test -f {safe_path} && cat {safe_path}",
    )
    if result.exit_code != 0:
        logger.debug("%s not found at %s", filename, safe_path)
        return None
    content = result.output or ""
    content = content.strip()
    return content or None


async def read_agents_md_in_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str | None,
) -> str | None:
    """Read AGENTS.md from the repo root if it exists."""
    if not repo_dir:
        return None
    return await _read_md_file_in_sandbox(sandbox_backend, repo_dir, "AGENTS.md")


async def read_claude_md_in_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str | None,
) -> str | None:
    """Read CLAUDE.md from the repo root if it exists."""
    if not repo_dir:
        return None
    return await _read_md_file_in_sandbox(sandbox_backend, repo_dir, "CLAUDE.md")
