"""MCP client for Bitbucket operations via the dormakaba AI Platform gateway.

Uses Streamable HTTP transport to communicate with the MCP server.
All Bitbucket read/write operations go through this gateway.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MCP_ENDPOINT = os.environ.get(
    "DK_MCP_ENDPOINT", "https://dk-ai-platform-apim.azure-api.net/mcp"
)

_REQUEST_TIMEOUT = 60


async def _mcp_call(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    mcp_endpoint: str | None = None,
    mcp_token: str | None = None,
) -> dict[str, Any]:
    """Call an MCP tool on the AI Platform gateway.

    Args:
        tool_name: MCP tool name (e.g. "add_comment", "get_pull_request").
        arguments: Tool arguments as a dict.
        mcp_endpoint: MCP server URL. Falls back to DK_MCP_ENDPOINT env var.
        mcp_token: Bearer token for MCP authentication.

    Returns:
        The tool result as a dict.

    Raises:
        RuntimeError: If the MCP call fails.
    """
    endpoint = mcp_endpoint or DEFAULT_MCP_ENDPOINT
    token = mcp_token or os.environ.get("DK_MCP_TOKEN", "")

    if not token:
        raise RuntimeError("No MCP token provided and DK_MCP_TOKEN env var not set")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        response = await client.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()

        if "error" in result:
            error = result["error"]
            msg = f"MCP error ({error.get('code', 'unknown')}): {error.get('message', 'unknown')}"
            raise RuntimeError(msg)

        return result.get("result", {})


async def add_comment(
    project_key: str,
    repo_slug: str,
    pr_id: int,
    comment_text: str,
    *,
    mcp_endpoint: str | None = None,
    mcp_token: str | None = None,
) -> dict[str, Any]:
    """Post a comment to a Bitbucket PR via MCP."""
    return await _mcp_call(
        "add_comment",
        {
            "projectKey": project_key,
            "repositorySlug": repo_slug,
            "pullRequestId": pr_id,
            "text": comment_text,
        },
        mcp_endpoint=mcp_endpoint,
        mcp_token=mcp_token,
    )


async def get_pull_request(
    project_key: str,
    repo_slug: str,
    pr_id: int,
    *,
    mcp_endpoint: str | None = None,
    mcp_token: str | None = None,
) -> dict[str, Any]:
    """Get PR metadata from Bitbucket via MCP."""
    return await _mcp_call(
        "get_pull_request",
        {
            "projectKey": project_key,
            "repositorySlug": repo_slug,
            "pullRequestId": pr_id,
        },
        mcp_endpoint=mcp_endpoint,
        mcp_token=mcp_token,
    )


async def get_diff(
    project_key: str,
    repo_slug: str,
    pr_id: int,
    *,
    mcp_endpoint: str | None = None,
    mcp_token: str | None = None,
) -> dict[str, Any]:
    """Get the unified diff for a PR via MCP."""
    return await _mcp_call(
        "get_diff",
        {
            "projectKey": project_key,
            "repositorySlug": repo_slug,
            "pullRequestId": pr_id,
        },
        mcp_endpoint=mcp_endpoint,
        mcp_token=mcp_token,
    )


async def get_comments(
    project_key: str,
    repo_slug: str,
    pr_id: int,
    *,
    mcp_endpoint: str | None = None,
    mcp_token: str | None = None,
) -> dict[str, Any]:
    """Get existing PR comments via MCP."""
    return await _mcp_call(
        "get_comments",
        {
            "projectKey": project_key,
            "repositorySlug": repo_slug,
            "pullRequestId": pr_id,
        },
        mcp_endpoint=mcp_endpoint,
        mcp_token=mcp_token,
    )
