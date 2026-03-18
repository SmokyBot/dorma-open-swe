"""Bitbucket DC authentication utilities.

Resolves HTTPS access tokens for repo cloning from team config env vars.
Follows the same credential handling pattern as agent/utils/github.py.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def resolve_bitbucket_token(bitbucket_token_env: str) -> str:
    """Resolve Bitbucket HTTPS access token from the team config env var.

    Args:
        bitbucket_token_env: Name of the environment variable holding the token
            (e.g. "TEAM_EXAMPLE_BB_TOKEN").

    Returns:
        The access token string.

    Raises:
        ValueError: If the token cannot be resolved.
    """
    token = os.environ.get(bitbucket_token_env, "")
    if token:
        logger.info("Resolved Bitbucket token from env var %s", bitbucket_token_env)
        return token

    fallback = os.environ.get("DK_BITBUCKET_TOKEN", "")
    if fallback:
        logger.info("Using fallback DK_BITBUCKET_TOKEN")
        return fallback

    msg = f"No Bitbucket token found in {bitbucket_token_env} or DK_BITBUCKET_TOKEN"
    raise ValueError(msg)


def resolve_mcp_token(mcp_token_env: str) -> str:
    """Resolve MCP bearer token from the team config env var.

    Args:
        mcp_token_env: Name of the environment variable holding the token.

    Returns:
        The MCP bearer token string.

    Raises:
        ValueError: If the token cannot be resolved.
    """
    token = os.environ.get(mcp_token_env, "")
    if token:
        logger.info("Resolved MCP token from env var %s", mcp_token_env)
        return token

    fallback = os.environ.get("DK_MCP_TOKEN", "")
    if fallback:
        logger.info("Using fallback DK_MCP_TOKEN")
        return fallback

    msg = f"No MCP token found in {mcp_token_env} or DK_MCP_TOKEN"
    raise ValueError(msg)
