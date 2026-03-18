"""Post review results to Bitbucket via MCP.

Posts a single summary comment on the PR. No inline comments for the POC.
"""

from __future__ import annotations

import logging

from ..utils.bitbucket_mcp import BitbucketMCPClient, MCPToolError

logger = logging.getLogger(__name__)


async def post_review_comment(
    mcp_client: BitbucketMCPClient,
    project: str,
    repo: str,
    pr_id: int,
    comment_text: str,
) -> bool:
    """Post the review summary comment to the PR.

    Returns True if successful, False otherwise.
    """
    try:
        await mcp_client.add_comment(project, repo, pr_id, comment_text)
        logger.info("Posted review comment on PR #%s (%s/%s)", pr_id, project, repo)
        return True
    except MCPToolError as e:
        logger.error("Failed to post review comment: %s", e)
        # Retry once
        try:
            await mcp_client.add_comment(project, repo, pr_id, comment_text)
            logger.info("Retry succeeded: posted review comment on PR #%s", pr_id)
            return True
        except MCPToolError as retry_err:
            logger.error("Retry also failed: %s", retry_err)
            return False


async def post_error_comment(
    mcp_client: BitbucketMCPClient,
    project: str,
    repo: str,
    pr_id: int,
    error_message: str,
) -> None:
    """Post an error message as a comment when the review fails."""
    text = (
        "## Review: Error\n\n"
        f"The code review could not be completed: {error_message}\n\n"
        "Please try again by commenting `@dkai` on this PR.\n\n"
        "---\n\n"
        "*Generated with DK AI Platform (ai-platform.dormakaba.net)*"
    )
    try:
        await mcp_client.add_comment(project, repo, pr_id, text)
    except MCPToolError:
        logger.exception("Failed to post error comment on PR #%s", pr_id)
