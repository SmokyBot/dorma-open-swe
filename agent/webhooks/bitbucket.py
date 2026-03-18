"""Bitbucket Data Center webhook handler.

Handles PR comment events and triggers code review when @dkai is mentioned.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from typing import Any

from fastapi import BackgroundTasks, HTTPException, Request

logger = logging.getLogger(__name__)

BITBUCKET_WEBHOOK_SECRET = os.environ.get("BITBUCKET_WEBHOOK_SECRET", "")
DKAI_TAG = "@dkai"

# Regex to extract Jira-like ticket keys from branch names or PR titles
JIRA_TICKET_PATTERN = re.compile(r"[A-Z][A-Z0-9]+-\d+")

# Bot signature to avoid responding to our own comments
BOT_SIGNATURE = "Generated with DK AI Platform"


def verify_bitbucket_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify Bitbucket DC webhook HMAC-SHA256 signature."""
    if not secret:
        logger.warning("BITBUCKET_WEBHOOK_SECRET not set, skipping signature verification")
        return True

    if not signature:
        return False

    # Bitbucket DC sends: X-Hub-Signature: sha256=<hex>
    prefix = "sha256="
    if signature.startswith(prefix):
        signature = signature[len(prefix):]

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_pr_context(payload: dict) -> dict[str, Any] | None:
    """Extract PR context from a Bitbucket webhook payload.

    Returns None if the payload is not a valid PR comment event with @dkai mention.
    """
    # Get the comment
    comment = payload.get("comment", {})
    comment_text = comment.get("text", "")

    # Check for @dkai mention (case-insensitive)
    if DKAI_TAG not in comment_text.lower():
        return None

    # Skip our own bot comments
    if BOT_SIGNATURE in comment_text:
        return None

    # Extract PR details
    pr = payload.get("pullRequest", {})
    if not pr:
        return None

    from_ref = pr.get("fromRef", {})
    to_ref = pr.get("toRef", {})
    repo = from_ref.get("repository", {}) or pr.get("toRef", {}).get("repository", {})
    project = repo.get("project", {})

    # Extract comment author
    author = comment.get("author", {})

    # Try to extract Jira ticket from branch name or PR title
    source_branch = from_ref.get("displayId", "")
    pr_title = pr.get("title", "")
    jira_tickets = JIRA_TICKET_PATTERN.findall(f"{source_branch} {pr_title}")

    return {
        "pr_id": pr.get("id"),
        "pr_title": pr_title,
        "pr_description": pr.get("description", ""),
        "source_branch": source_branch,
        "target_branch": to_ref.get("displayId", ""),
        "project_key": project.get("key", ""),
        "repo_slug": repo.get("slug", ""),
        "comment_text": comment_text,
        "comment_id": comment.get("id"),
        "comment_author": author.get("displayName", ""),
        "comment_author_email": author.get("emailAddress", ""),
        "pr_author": pr.get("author", {}).get("user", {}).get("displayName", ""),
        "jira_tickets": jira_tickets,
        "reviewers": [
            r.get("user", {}).get("displayName", "")
            for r in pr.get("reviewers", [])
        ],
    }


async def handle_bitbucket_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Handle incoming Bitbucket DC webhook requests.

    Registered as POST /webhooks/bitbucket in webapp.py.
    """
    body = await request.body()

    # Verify signature
    signature = request.headers.get("X-Hub-Signature", "")
    if not verify_bitbucket_signature(body, signature, BITBUCKET_WEBHOOK_SECRET):
        logger.warning("Invalid Bitbucket webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Check event type
    event_key = request.headers.get("X-Event-Key", "")
    if event_key != "pr:comment:added":
        logger.debug("Ignoring Bitbucket event: %s", event_key)
        return {"status": "ignored", "reason": f"Unsupported event: {event_key}"}

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse Bitbucket webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    # Extract PR context and check for @dkai mention
    pr_context = extract_pr_context(payload)
    if not pr_context:
        logger.debug("Ignoring Bitbucket webhook: no @dkai mention or invalid PR context")
        return {"status": "ignored", "reason": "No @dkai mention or invalid context"}

    project_key = pr_context["project_key"]
    repo_slug = pr_context["repo_slug"]
    pr_id = pr_context["pr_id"]

    logger.info(
        "Accepted Bitbucket webhook: @dkai in PR #%s (%s/%s), scheduling review",
        pr_id,
        project_key,
        repo_slug,
    )

    # Schedule review in background
    background_tasks.add_task(process_bitbucket_review, pr_context)

    return {
        "status": "accepted",
        "message": f"Review scheduled for PR #{pr_id} in {project_key}/{repo_slug}",
    }


async def process_bitbucket_review(pr_context: dict[str, Any]) -> None:
    """Process a code review request from Bitbucket.

    This runs in the background after the webhook response is sent.
    """
    from ..config import load_team_config, resolve_team
    from ..review.orchestrator import run_review

    project_key = pr_context["project_key"]
    repo_slug = pr_context["repo_slug"]
    pr_id = pr_context["pr_id"]

    logger.info("Starting review for PR #%s in %s/%s", pr_id, project_key, repo_slug)

    try:
        # Resolve team configuration
        config = load_team_config()
        team = resolve_team(project_key, repo_slug, config)

        if not team:
            logger.error("No team config for %s/%s", project_key, repo_slug)
            return

        if not team.mcp_token:
            logger.error("No MCP token for team %s", team.name)
            return

        # Run the review
        await run_review(
            project_key=project_key,
            repo_slug=repo_slug,
            pr_id=pr_id,
            pr_context=pr_context,
            team_config=team,
        )

        logger.info("Review completed for PR #%s in %s/%s", pr_id, project_key, repo_slug)

    except Exception:
        logger.exception(
            "Review failed for PR #%s in %s/%s", pr_id, project_key, repo_slug
        )
