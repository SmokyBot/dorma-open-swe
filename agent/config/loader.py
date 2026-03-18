"""Team configuration loader for Bitbucket review agent."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).parent / "teams.json"


@dataclass
class ReviewConfig:
    """Review-specific settings for a team."""

    max_diff_lines: int = 5000
    min_agents: int = 2
    max_agents: int = 6
    excluded_paths: list[str] = field(default_factory=list)
    custom_instructions: str = ""


@dataclass
class TeamConfig:
    """Resolved configuration for a team."""

    team_id: str
    team_name: str
    bitbucket_host: str
    mcp_endpoint: str
    mcp_token_env: str
    bitbucket_token_env: str
    llm_provider: str
    llm_model: str
    review_config: ReviewConfig


def _load_config_file() -> dict:
    config_path = os.environ.get("TEAM_CONFIG_PATH", str(DEFAULT_CONFIG_PATH))
    with open(config_path) as f:
        return json.load(f)


def _merge_review_config(defaults: dict, team: dict) -> ReviewConfig:
    base = defaults.get("review_config", {})
    override = team.get("review_config", {})
    merged = {**base, **override}
    return ReviewConfig(
        max_diff_lines=merged.get("max_diff_lines", 5000),
        min_agents=merged.get("min_agents", 2),
        max_agents=merged.get("max_agents", 6),
        excluded_paths=merged.get("excluded_paths", []),
        custom_instructions=merged.get("custom_instructions", ""),
    )


def resolve_team_config(project_key: str, repo_slug: str) -> TeamConfig:
    """Resolve team config by matching project key and repo slug.

    Priority: exact repo match > project match > defaults.
    """
    config = _load_config_file()
    defaults = config.get("defaults", {})
    teams = config.get("teams", [])

    full_repo = f"{project_key}/{repo_slug}"

    matched_team: dict | None = None

    for team in teams:
        if full_repo in team.get("bitbucket_repos", []):
            matched_team = team
            break

    if not matched_team:
        for team in teams:
            if project_key in team.get("bitbucket_projects", []):
                matched_team = team
                break

    if not matched_team:
        matched_team = {}

    return TeamConfig(
        team_id=matched_team.get("id", "default"),
        team_name=matched_team.get("name", "Default"),
        bitbucket_host=matched_team.get("bitbucket_host", defaults.get("bitbucket_host", "bitbucket.dormakaba.net")),
        mcp_endpoint=matched_team.get("mcp_endpoint", defaults.get("mcp_endpoint", "https://dk-ai-platform-apim.azure-api.net/mcp")),
        mcp_token_env=matched_team.get("mcp_token_env", "DK_MCP_TOKEN"),
        bitbucket_token_env=matched_team.get("bitbucket_token_env", "DK_BITBUCKET_TOKEN"),
        llm_provider=matched_team.get("llm_provider", defaults.get("llm_provider", "anthropic")),
        llm_model=matched_team.get("llm_model", defaults.get("llm_model", "claude-sonnet-4-6")),
        review_config=_merge_review_config(defaults, matched_team),
    )


def load_team_config(team_config_id: str | None = None) -> TeamConfig:
    """Load team config by ID, falling back to defaults."""
    config = _load_config_file()
    defaults = config.get("defaults", {})
    teams = config.get("teams", [])

    matched_team: dict = {}
    if team_config_id:
        for team in teams:
            if team.get("id") == team_config_id:
                matched_team = team
                break

    return TeamConfig(
        team_id=matched_team.get("id", "default"),
        team_name=matched_team.get("name", "Default"),
        bitbucket_host=matched_team.get("bitbucket_host", defaults.get("bitbucket_host", "bitbucket.dormakaba.net")),
        mcp_endpoint=matched_team.get("mcp_endpoint", defaults.get("mcp_endpoint", "https://dk-ai-platform-apim.azure-api.net/mcp")),
        mcp_token_env=matched_team.get("mcp_token_env", "DK_MCP_TOKEN"),
        bitbucket_token_env=matched_team.get("bitbucket_token_env", "DK_BITBUCKET_TOKEN"),
        llm_provider=matched_team.get("llm_provider", defaults.get("llm_provider", "anthropic")),
        llm_model=matched_team.get("llm_model", defaults.get("llm_model", "claude-sonnet-4-6")),
        review_config=_merge_review_config(defaults, matched_team),
    )
