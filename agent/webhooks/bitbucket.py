"""Bitbucket Data Center webhook handler.

Handles pr:comment:added events with @dkai mentions.
Creates LangGraph runs following the same pattern as Linear/Slack/GitHub triggers.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any

from langgraph_sdk import get_client

from ..config.loader import resolve_team_config
from ..utils.bitbucket_auth import resolve_bitbucket_token, resolve_mcp_token

logger = logging.getLogger(__name__)

BITBUCKET_WEBHOOK_SECRET = os.environ.get("BITBUCKET_WEBHOOK_SECRET", "")
LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

_AGENT_VERSION_METADATA: dict[str, str] = (
    {"LANGSMITH_AGENT_VERSION": os.environ["LANGCHAIN_REVISION_ID"]}
    if os.environ.get("LANGCHAIN_REVISION_ID")
    else {}
)

DKAI_MENTION_PATTERN = re.compile(r"@dkai\b", re.IGNORECASE)


def verify_bitbucket_signature(payload_body: bytes, signature: str) -> bool:
    """Verify Bitbucket webhook HMAC-SHA256 signature."""
    if not BITBUCKET_WEBHOOK_SECRET:
        logger.warning("BITBUCKET_WEBHOOK_SECRET not set, skipping signature verification")
        return True

    expected = hmac.new(
        BITBUCKET_WEBHOOK_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    provided = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def generate_thread_id_from_pr(project_key: str, repo_slug: str, pr_id: int) -> str:
    """Generate a deterministic thread ID from Bitbucket PR identifiers."""
    composite = f"bitbucket-pr:{project_key}/{repo_slug}/{pr_id}"
    hash_bytes = hashlib.sha256(composite.encode()).hexdigest()
    return (
        f"{hash_bytes[:8]}-{hash_bytes[8:12]}-{hash_bytes[12:16]}-"
        f"{hash_bytes[16:20]}-{hash_bytes[20:32]}"
    )


def extract_pr_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract PR context from a Bitbucket webhook payload.

    Returns None if the payload doesn't contain a valid @dkai mention.
    """
    comment = payload.get("comment", {})
    comment_text = comment.get("text", "")

    if not DKAI_MENTION_PATTERN.search(comment_text):
        return None

    pr = payload.get("pullRequest", {})
    pr_id = pr.get("id")
    if not pr_id:
        return None

    from_ref = pr.get("fromRef", {})
    to_ref = pr.get("toRef", {})
    repo = to_ref.get("repository", {}) or from_ref.get("repository", {})
    project = repo.get("project", {})

    project_key = project.get("key", "")
    repo_slug = repo.get("slug", "")

    if not project_key or not repo_slug:
        return None

    author = pr.get("author", {}).get("user", {})

    return {
        "project_key": project_key,
        "repo_slug": repo_slug,
        "pr_id": pr_id,
        "pr_title": pr.get("title", ""),
        "source_branch": from_ref.get("displayId", from_ref.get("id", "")),
        "target_branch": to_ref.get("displayId", to_ref.get("id", "")),
        "author_name": author.get("displayName", author.get("name", "")),
        "author_email": author.get("emailAddress", ""),
        "comment_text": comment_text,
        "comment_author": comment.get("author", {}).get("displayName", ""),
    }


async def process_bitbucket_review(pr_context: dict[str, Any]) -> None:
    """Process a Bitbucket PR review request by creating a LangGraph run.

    This follows the same pattern as process_linear_issue() and process_slack_mention():
    creates a LangGraph run via langgraph_client.runs.create() with PR context in config.
    """
    project_key = pr_context["project_key"]
    repo_slug = pr_context["repo_slug"]
    pr_id = pr_context["pr_id"]

    logger.info(
        "Processing Bitbucket review for %s/%s PR #%d",
        project_key,
        repo_slug,
        pr_id,
    )

    team_config = resolve_team_config(project_key, repo_slug)
    logger.info("Resolved team config: %s (%s)", team_config.team_id, team_config.team_name)

    try:
        bb_token = resolve_bitbucket_token(team_config.bitbucket_token_env)
    except ValueError:
        logger.exception("Failed to resolve Bitbucket token for team %s", team_config.team_id)
        return

    try:
        mcp_token = resolve_mcp_token(team_config.mcp_token_env)
    except ValueError:
        logger.exception("Failed to resolve MCP token for team %s", team_config.team_id)
        return

    thread_id = generate_thread_id_from_pr(project_key, repo_slug, pr_id)

    prompt = (
        f"Please review the following pull request:\n\n"
        f"**PR #{pr_id}:** {pr_context['pr_title']}\n"
        f"**Repository:** {project_key}/{repo_slug}\n"
        f"**Branch:** `{pr_context['source_branch']}` -> `{pr_context['target_branch']}`\n"
        f"**Author:** {pr_context['author_name']}\n\n"
    )
    if pr_context.get("comment_text"):
        prompt += f"**Review request comment:** {pr_context['comment_text']}\n\n"

    prompt += (
        "Follow the review workflow in your system prompt. "
        "Explore the codebase, spawn review subagents, collect findings, "
        "and post the review summary using the `bitbucket_comment` tool."
    )

    configurable: dict[str, Any] = {
        "repo": {
            "owner": project_key,
            "name": repo_slug,
        },
        "source": "bitbucket",
        "bitbucket_pr": {
            "project_key": project_key,
            "repo_slug": repo_slug,
            "pr_id": pr_id,
            "pr_title": pr_context["pr_title"],
            "source_branch": pr_context["source_branch"],
            "target_branch": pr_context["target_branch"],
            "author_name": pr_context["author_name"],
            "author_email": pr_context["author_email"],
            "comment_text": pr_context["comment_text"],
        },
        "team_config_id": team_config.team_id,
        "bitbucket_host": team_config.bitbucket_host,
        "bitbucket_token": bb_token,
        "mcp_endpoint": team_config.mcp_endpoint,
        "mcp_token": mcp_token,
        "llm_provider": team_config.llm_provider,
        "llm_model": team_config.llm_model,
    }

    langgraph_client = get_client(url=LANGGRAPH_URL)

    logger.info("Creating LangGraph run for Bitbucket review, thread %s", thread_id)
    await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable, "metadata": _AGENT_VERSION_METADATA},
        if_not_exists="create",
    )
    logger.info("LangGraph run created for Bitbucket review, thread %s", thread_id)
