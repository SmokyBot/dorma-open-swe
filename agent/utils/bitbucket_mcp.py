"""Bitbucket and Jira MCP client for the dormakaba AI Platform.

Connects to the AI Platform MCP server via standard MCP protocol (Streamable HTTP)
and provides typed wrappers around the Bitbucket and Jira tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# Default MCP endpoint (via Azure APIM)
DEFAULT_MCP_ENDPOINT = "https://dk-ai-platform-apim.azure-api.net/mcp"


@dataclass
class PRDetails:
    """Pull request metadata."""

    pr_id: int
    title: str
    description: str
    state: str
    source_branch: str
    target_branch: str
    author: str
    author_email: str
    reviewers: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class PRFile:
    """A file changed in a pull request."""

    path: str
    change_type: str  # "ADD", "MODIFY", "DELETE", "RENAME"


class BitbucketMCPClient:
    """Client for Bitbucket operations via the dormakaba AI Platform MCP server.

    Usage:
        async with BitbucketMCPClient(token="...") as client:
            pr = await client.get_pull_request("PROJ", "my-repo", 123)
            diff = await client.get_diff("PROJ", "my-repo", 123)
    """

    def __init__(self, token: str, endpoint: str | None = None):
        self.token = token
        self.endpoint = endpoint or DEFAULT_MCP_ENDPOINT
        self._session: ClientSession | None = None
        self._cm = None

    async def __aenter__(self) -> BitbucketMCPClient:
        headers = {"Authorization": f"Bearer {self.token}"}
        self._cm = streamablehttp_client(url=self.endpoint, headers=headers)
        read_stream, write_stream, _ = await self._cm.__aenter__()
        self._session = ClientSession(read_stream, write_stream)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.__aexit__(exc_type, exc_val, exc_tb)
        if self._cm:
            await self._cm.__aexit__(exc_type, exc_val, exc_tb)

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool and return the parsed result."""
        if not self._session:
            raise RuntimeError("MCP client not connected. Use 'async with' context manager.")

        logger.debug("Calling MCP tool: %s(%s)", tool_name, arguments)
        result = await self._session.call_tool(tool_name, arguments)

        if result.isError:
            error_text = result.content[0].text if result.content else "Unknown error"
            logger.error("MCP tool %s failed: %s", tool_name, error_text)
            raise MCPToolError(tool_name, error_text)

        # Return the text content from the first content block
        if result.content and hasattr(result.content[0], "text"):
            import json

            try:
                return json.loads(result.content[0].text)
            except (json.JSONDecodeError, TypeError):
                return result.content[0].text

        return result.content

    # ── Bitbucket tools ──────────────────────────────────────────────

    async def get_pull_request(
        self, project: str, repo: str, pr_id: int
    ) -> PRDetails:
        """Get pull request details."""
        raw = await self._call_tool("get_pull_request", {
            "projectKey": project,
            "repositorySlug": repo,
            "pullRequestId": pr_id,
        })

        if isinstance(raw, str):
            # If the response is a string, it's an error or unexpected format
            raise MCPToolError("get_pull_request", f"Unexpected response: {raw}")

        from_ref = raw.get("fromRef", {})
        to_ref = raw.get("toRef", {})
        author_data = raw.get("author", {}).get("user", {})
        reviewers = [
            r.get("user", {}).get("displayName", "")
            for r in raw.get("reviewers", [])
        ]

        return PRDetails(
            pr_id=raw.get("id", pr_id),
            title=raw.get("title", ""),
            description=raw.get("description", ""),
            state=raw.get("state", ""),
            source_branch=from_ref.get("displayId", ""),
            target_branch=to_ref.get("displayId", ""),
            author=author_data.get("displayName", ""),
            author_email=author_data.get("emailAddress", ""),
            reviewers=reviewers,
            raw=raw,
        )

    async def get_diff(self, project: str, repo: str, pr_id: int) -> str:
        """Get the full diff for a pull request."""
        result = await self._call_tool("get_diff", {
            "projectKey": project,
            "repositorySlug": repo,
            "pullRequestId": pr_id,
        })
        if isinstance(result, dict):
            # Some MCP servers return structured diff data
            return result.get("diff", str(result))
        return str(result)

    async def get_file_content(
        self, project: str, repo: str, file_path: str, ref: str | None = None
    ) -> str:
        """Get the content of a file at a specific ref."""
        args: dict[str, Any] = {
            "projectKey": project,
            "repositorySlug": repo,
            "path": file_path,
        }
        if ref:
            args["at"] = ref
        result = await self._call_tool("get_file_content", args)
        if isinstance(result, dict):
            # Handle paginated content
            lines = result.get("lines", [])
            if lines:
                return "\n".join(
                    line.get("text", "") if isinstance(line, dict) else str(line)
                    for line in lines
                )
            return result.get("content", str(result))
        return str(result)

    async def browse_repository(
        self, project: str, repo: str, path: str = "", ref: str | None = None
    ) -> list[dict]:
        """Browse repository directory structure."""
        args: dict[str, Any] = {
            "projectKey": project,
            "repositorySlug": repo,
        }
        if path:
            args["path"] = path
        if ref:
            args["at"] = ref
        result = await self._call_tool("browse_repository", args)
        if isinstance(result, dict):
            return result.get("children", {}).get("values", [])
        if isinstance(result, list):
            return result
        return []

    async def add_comment(self, project: str, repo: str, pr_id: int, text: str) -> dict:
        """Post a comment on a pull request."""
        return await self._call_tool("add_comment", {
            "projectKey": project,
            "repositorySlug": repo,
            "pullRequestId": pr_id,
            "text": text,
        })

    async def get_comments(self, project: str, repo: str, pr_id: int) -> list[dict]:
        """Get all comments on a pull request."""
        result = await self._call_tool("get_comments", {
            "projectKey": project,
            "repositorySlug": repo,
            "pullRequestId": pr_id,
        })
        if isinstance(result, dict):
            return result.get("values", [])
        if isinstance(result, list):
            return result
        return []

    # ── Jira tools ───────────────────────────────────────────────────

    async def get_jira_issue(self, issue_key: str) -> dict:
        """Get a Jira issue by key (e.g., 'AIP-123')."""
        try:
            result = await self._call_tool("get_issue", {"issueKey": issue_key})
            return result if isinstance(result, dict) else {}
        except MCPToolError:
            logger.warning("Failed to fetch Jira issue %s", issue_key)
            return {}

    # ── Helpers ──────────────────────────────────────────────────────

    async def list_available_tools(self) -> list[str]:
        """List all tools available on the MCP server (for debugging)."""
        if not self._session:
            raise RuntimeError("MCP client not connected.")
        result = await self._session.list_tools()
        return [t.name for t in result.tools]


class MCPToolError(Exception):
    """Error from an MCP tool call."""

    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"MCP tool '{tool_name}' failed: {message}")
