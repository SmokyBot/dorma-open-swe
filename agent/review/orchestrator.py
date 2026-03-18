"""Review orchestrator — coordinates the full review pipeline.

1. Fetch PR metadata + diff via MCP
2. Auto-detect tech stack
3. Read linked Jira ticket (if available)
4. Select review roles
5. Spawn parallel review sub-agents
6. Validate and deduplicate findings
7. Post summary comment to Bitbucket
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config.loader import TeamConfig
from ..utils.bitbucket_mcp import BitbucketMCPClient
from .executor import execute_review_agents
from .output import format_summary_comment
from .poster import post_error_comment, post_review_comment
from .role_selector import select_roles
from .tech_detector import detect_tech_stack, extract_changed_files_from_diff
from .validator import validate_and_deduplicate

logger = logging.getLogger(__name__)

# Regex for Jira ticket keys
JIRA_KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9]+-\d+")


async def run_review(
    project_key: str,
    repo_slug: str,
    pr_id: int,
    pr_context: dict[str, Any],
    team_config: TeamConfig,
) -> None:
    """Run the full code review pipeline for a Bitbucket PR.

    This is the main entry point called from the webhook handler.
    """
    async with BitbucketMCPClient(token=team_config.mcp_token) as mcp:
        try:
            await _run_review_pipeline(
                mcp=mcp,
                project_key=project_key,
                repo_slug=repo_slug,
                pr_id=pr_id,
                pr_context=pr_context,
                team_config=team_config,
            )
        except Exception as e:
            logger.exception("Review pipeline failed for PR #%s", pr_id)
            await post_error_comment(mcp, project_key, repo_slug, pr_id, str(e))


async def _run_review_pipeline(
    mcp: BitbucketMCPClient,
    project_key: str,
    repo_slug: str,
    pr_id: int,
    pr_context: dict[str, Any],
    team_config: TeamConfig,
) -> None:
    """Internal review pipeline implementation."""

    # ── Step 1: Fetch PR details and diff ────────────────────────────
    logger.info("Step 1: Fetching PR details and diff")

    pr_details = await mcp.get_pull_request(project_key, repo_slug, pr_id)
    diff_content = await mcp.get_diff(project_key, repo_slug, pr_id)

    if not diff_content or not diff_content.strip():
        logger.warning("Empty diff for PR #%s, skipping review", pr_id)
        await mcp.add_comment(
            project_key, repo_slug, pr_id,
            "## Review: No Changes\n\nThe diff appears to be empty. Nothing to review.\n\n"
            "---\n\n*Generated with DK AI Platform (ai-platform.dormakaba.net)*"
        )
        return

    # Update pr_context with fetched details
    pr_context["pr_title"] = pr_context.get("pr_title") or pr_details.title
    pr_context["pr_description"] = pr_context.get("pr_description") or pr_details.description
    pr_context["source_branch"] = pr_context.get("source_branch") or pr_details.source_branch
    pr_context["target_branch"] = pr_context.get("target_branch") or pr_details.target_branch

    # ── Step 2: Detect tech stack ────────────────────────────────────
    logger.info("Step 2: Detecting tech stack")

    changed_files = extract_changed_files_from_diff(diff_content)
    diff_line_count = len(diff_content.split("\n"))

    # Check diff size limit
    if diff_line_count > team_config.review.max_diff_lines:
        logger.warning(
            "Diff too large (%d lines, max %d), posting notice",
            diff_line_count,
            team_config.review.max_diff_lines,
        )
        await mcp.add_comment(
            project_key, repo_slug, pr_id,
            f"## Review: Diff Too Large\n\n"
            f"This PR has {diff_line_count} lines of diff (limit: {team_config.review.max_diff_lines}). "
            f"Please consider splitting it into smaller PRs for better review quality.\n\n"
            f"---\n\n*Generated with DK AI Platform (ai-platform.dormakaba.net)*"
        )
        return

    # Filter excluded paths
    changed_files = _filter_excluded(changed_files, team_config.review.excluded_paths)

    if not changed_files:
        logger.info("No reviewable files after filtering exclusions")
        return

    # Browse repo root for tech detection
    repo_root_files = []
    try:
        root_entries = await mcp.browse_repository(project_key, repo_slug)
        repo_root_files = [
            entry.get("path", {}).get("toString", "") if isinstance(entry, dict)
            else str(entry)
            for entry in root_entries
        ]
    except Exception:
        logger.warning("Could not browse repository root, continuing without it")

    tech_profile = detect_tech_stack(changed_files, diff_content, repo_root_files)
    logger.info("Detected tech stack: %s", tech_profile.summary)

    # ── Step 3: Fetch Jira ticket context ────────────────────────────
    logger.info("Step 3: Fetching Jira context")

    jira_context = ""
    jira_tickets = pr_context.get("jira_tickets", [])
    if jira_tickets:
        jira_context = await _fetch_jira_context(mcp, jira_tickets)

    # ── Step 4: Select review roles ──────────────────────────────────
    logger.info("Step 4: Selecting review roles")

    roles = select_roles(
        tech_profile=tech_profile,
        diff_line_count=diff_line_count,
        custom_instructions=team_config.review.custom_review_instructions,
        min_agents=team_config.review.min_review_agents,
        max_agents=team_config.review.max_review_agents,
    )

    # ── Step 5: Execute review agents ────────────────────────────────
    logger.info("Step 5: Executing %d review agents in parallel", len(roles))

    agent_results = await execute_review_agents(
        roles=roles,
        diff_content=diff_content,
        pr_context=pr_context,
        tech_summary=tech_profile.summary,
        llm_provider=team_config.llm_provider,
        llm_model=team_config.llm_model,
        jira_context=jira_context,
    )

    # ── Step 6: Validate and deduplicate ─────────────────────────────
    logger.info("Step 6: Validating and deduplicating findings")

    findings, positives = validate_and_deduplicate(agent_results)

    # ── Step 7: Format and post review ───────────────────────────────
    logger.info("Step 7: Formatting and posting review")

    comment_text = format_summary_comment(
        findings=findings,
        positives=positives,
        pr_context=pr_context,
        file_count=len(changed_files),
    )

    success = await post_review_comment(
        mcp_client=mcp,
        project=project_key,
        repo=repo_slug,
        pr_id=pr_id,
        comment_text=comment_text,
    )

    if success:
        logger.info(
            "Review posted: %d findings (%d critical, %d major, %d minor) on PR #%s",
            len(findings),
            sum(1 for f in findings if f.severity == "critical"),
            sum(1 for f in findings if f.severity == "major"),
            sum(1 for f in findings if f.severity == "minor"),
            pr_id,
        )
    else:
        logger.error("Failed to post review on PR #%s", pr_id)


async def _fetch_jira_context(mcp: BitbucketMCPClient, ticket_keys: list[str]) -> str:
    """Fetch Jira ticket details and format as context string."""
    parts = []
    for key in ticket_keys[:3]:  # Limit to 3 tickets
        issue = await mcp.get_jira_issue(key)
        if issue:
            fields = issue.get("fields", {})
            summary = fields.get("summary", "")
            description = fields.get("description", "")
            status = fields.get("status", {}).get("name", "")
            acceptance = ""

            # Try to find acceptance criteria in description or custom fields
            if description and "acceptance criteria" in description.lower():
                acceptance = description

            parts.append(
                f"**{key}** ({status}): {summary}"
                + (f"\n{description[:500]}" if description else "")
                + (f"\n\nAcceptance Criteria:\n{acceptance[:500]}" if acceptance else "")
            )

    return "\n\n".join(parts) if parts else ""


def _filter_excluded(files: list[str], patterns: list[str]) -> list[str]:
    """Filter out files matching exclusion patterns."""
    import fnmatch

    if not patterns:
        return files

    filtered = []
    for file_path in files:
        excluded = any(fnmatch.fnmatch(file_path, pattern) for pattern in patterns)
        if not excluded:
            filtered.append(file_path)

    if len(filtered) < len(files):
        logger.info(
            "Filtered %d files by exclusion patterns, %d remaining",
            len(files) - len(filtered),
            len(filtered),
        )

    return filtered
