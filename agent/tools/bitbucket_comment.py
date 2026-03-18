"""Tool for posting review comments to Bitbucket PRs via MCP.

The agent calls this tool to post its review summary. The tool
uses the MCP gateway (add_comment) — the only write operation
enabled for the POC.
"""

import asyncio
from typing import Any

from langgraph.config import get_config

from ..utils.bitbucket_mcp import add_comment


def bitbucket_comment(comment: str) -> dict[str, Any]:
    """Post a review comment to the current Bitbucket pull request.

    Args:
        comment: The review comment in Bitbucket-compatible CommonMark markdown.
            Do NOT use HTML tags, checkboxes, collapsible details, or emojis in
            headers. Supported: headings, bold, italic, fenced code blocks,
            tables, blockquotes, lists, links, horizontal rules.
    """
    config = get_config()
    configurable = config.get("configurable", {})

    bb_pr = configurable.get("bitbucket_pr", {})
    project_key = bb_pr.get("project_key", "")
    repo_slug = bb_pr.get("repo_slug", "")
    pr_id = bb_pr.get("pr_id")

    if not project_key or not repo_slug or not pr_id:
        return {
            "success": False,
            "error": "Missing bitbucket_pr context (project_key, repo_slug, pr_id) in config",
        }

    if not comment.strip():
        return {"success": False, "error": "Comment cannot be empty"}

    mcp_endpoint = configurable.get("mcp_endpoint")
    mcp_token = configurable.get("mcp_token")

    try:
        result = asyncio.run(
            add_comment(
                project_key=project_key,
                repo_slug=repo_slug,
                pr_id=pr_id,
                comment_text=comment,
                mcp_endpoint=mcp_endpoint,
                mcp_token=mcp_token,
            )
        )
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
