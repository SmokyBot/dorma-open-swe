"""Load and resolve team configuration from JSON."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "teams.json"


@dataclass
class ReviewConfig:
    """Review-specific settings for a team."""

    custom_review_instructions: str = ""
    excluded_paths: list[str] = field(default_factory=list)
    max_diff_lines: int = 5000
    max_review_agents: int = 6
    min_review_agents: int = 2


@dataclass
class TeamConfig:
    """Resolved configuration for a single team."""

    name: str
    bitbucket_projects: list[str]
    bitbucket_repos: list[str]
    mcp_token: str
    llm_provider: str
    llm_model: str
    review: ReviewConfig


def load_team_config(config_path: str | None = None) -> dict:
    """Load the raw team configuration JSON."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    env_path = os.environ.get("TEAM_CONFIG_PATH")
    if env_path:
        path = Path(env_path)

    if not path.exists():
        logger.warning("Team config not found at %s, using empty config", path)
        return {"defaults": {}, "teams": {}}

    with open(path) as f:
        return json.load(f)


def resolve_team(project_key: str, repo_slug: str, config: dict | None = None) -> TeamConfig | None:
    """Resolve a team config from a Bitbucket project key and repo slug.

    Matching priority:
    1. Exact repo match: "PROJECT/repo-slug" in bitbucket_repos
    2. Project match: "PROJECT" in bitbucket_projects
    """
    if config is None:
        config = load_team_config()

    defaults = config.get("defaults", {})
    teams = config.get("teams", {})
    repo_key = f"{project_key}/{repo_slug}"

    for team_id, team_data in teams.items():
        bb_repos = team_data.get("bitbucket_repos", [])
        bb_projects = team_data.get("bitbucket_projects", [])

        if repo_key in bb_repos or project_key in bb_projects:
            review_raw = {**defaults, **team_data.get("review_config", {})}
            review = ReviewConfig(
                custom_review_instructions=review_raw.get("custom_review_instructions", ""),
                excluded_paths=review_raw.get(
                    "excluded_paths", defaults.get("excluded_paths", [])
                ),
                max_diff_lines=review_raw.get("max_diff_lines", defaults.get("max_diff_lines", 5000)),
                max_review_agents=review_raw.get(
                    "max_review_agents", defaults.get("max_review_agents", 6)
                ),
                min_review_agents=review_raw.get(
                    "min_review_agents", defaults.get("min_review_agents", 2)
                ),
            )

            # Resolve MCP token from env var
            token_env = team_data.get("mcp_token_env", "")
            mcp_token = os.environ.get(token_env, "") if token_env else ""
            if not mcp_token:
                mcp_token = os.environ.get("DK_MCP_TOKEN", "")

            return TeamConfig(
                name=team_data.get("name", team_id),
                bitbucket_projects=bb_projects,
                bitbucket_repos=bb_repos,
                mcp_token=mcp_token,
                llm_provider=team_data.get("llm_provider", defaults.get("llm_provider", "anthropic")),
                llm_model=team_data.get("llm_model", defaults.get("llm_model", "claude-sonnet-4-20250514")),
                review=review,
            )

    logger.warning("No team config found for %s/%s, using defaults", project_key, repo_slug)
    # Return a default config so the agent can still function
    mcp_token = os.environ.get("DK_MCP_TOKEN", "")
    return TeamConfig(
        name="default",
        bitbucket_projects=[project_key],
        bitbucket_repos=[repo_key],
        mcp_token=mcp_token,
        llm_provider=defaults.get("llm_provider", "anthropic"),
        llm_model=defaults.get("llm_model", "claude-sonnet-4-20250514"),
        review=ReviewConfig(
            excluded_paths=defaults.get("excluded_paths", []),
            max_diff_lines=defaults.get("max_diff_lines", 5000),
            max_review_agents=defaults.get("max_review_agents", 6),
            min_review_agents=defaults.get("min_review_agents", 2),
        ),
    )
