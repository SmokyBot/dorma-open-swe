"""Tests for the Bitbucket webhook handler and related components."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient

from agent import webapp
from agent.config.loader import ReviewConfig, resolve_team_config
from agent.prompt import construct_review_system_prompt
from agent.webhooks.bitbucket import (
    DKAI_MENTION_PATTERN,
    extract_pr_context,
    generate_thread_id_from_pr,
    verify_bitbucket_signature,
)

_TEST_WEBHOOK_SECRET = "test-bitbucket-secret"


def _make_bb_payload(
    comment_text: str = "@dkai please review",
    pr_id: int = 42,
    project_key: str = "PROJ",
    repo_slug: str = "my-repo",
    source_branch: str = "feature/my-feature",
    target_branch: str = "main",
    pr_title: str = "Add new feature",
) -> dict:
    return {
        "comment": {
            "text": comment_text,
            "author": {
                "name": "jsmith",
                "displayName": "John Smith",
            },
        },
        "pullRequest": {
            "id": pr_id,
            "title": pr_title,
            "author": {
                "user": {
                    "name": "jsmith",
                    "displayName": "John Smith",
                    "emailAddress": "john.smith@example.com",
                }
            },
            "fromRef": {
                "id": f"refs/heads/{source_branch}",
                "displayId": source_branch,
                "repository": {
                    "slug": repo_slug,
                    "project": {"key": project_key},
                },
            },
            "toRef": {
                "id": f"refs/heads/{target_branch}",
                "displayId": target_branch,
                "repository": {
                    "slug": repo_slug,
                    "project": {"key": project_key},
                },
            },
        },
    }


def _sign_body(body: bytes, secret: str = _TEST_WEBHOOK_SECRET) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _post_bb_webhook(
    client: TestClient,
    payload: dict,
    event_key: str = "pr:comment:added",
    secret: str = _TEST_WEBHOOK_SECRET,
) -> object:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/webhooks/bitbucket",
        content=body,
        headers={
            "X-Event-Key": event_key,
            "X-Hub-Signature": _sign_body(body, secret),
            "Content-Type": "application/json",
        },
    )


# ── Signature verification ──────────────────────────────────────────


class TestSignatureVerification:
    def test_valid_signature(self, monkeypatch):
        monkeypatch.setattr(
            "agent.webhooks.bitbucket.BITBUCKET_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET
        )
        body = b'{"test": true}'
        sig = hmac.new(_TEST_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert verify_bitbucket_signature(body, f"sha256={sig}") is True

    def test_invalid_signature(self, monkeypatch):
        monkeypatch.setattr(
            "agent.webhooks.bitbucket.BITBUCKET_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET
        )
        assert verify_bitbucket_signature(b'{"test": true}', "sha256=bad") is False

    def test_no_secret_configured_allows_all(self, monkeypatch):
        monkeypatch.setattr("agent.webhooks.bitbucket.BITBUCKET_WEBHOOK_SECRET", "")
        assert verify_bitbucket_signature(b"anything", "any-sig") is True


# ── @dkai mention detection ─────────────────────────────────────────


class TestDkaiMentionDetection:
    def test_detects_lowercase(self):
        assert DKAI_MENTION_PATTERN.search("@dkai please review") is not None

    def test_detects_uppercase(self):
        assert DKAI_MENTION_PATTERN.search("@DKAI review this") is not None

    def test_detects_mixed_case(self):
        assert DKAI_MENTION_PATTERN.search("Hey @DkAi can you look?") is not None

    def test_ignores_partial_match(self):
        assert DKAI_MENTION_PATTERN.search("@dkaistuff") is None

    def test_no_mention(self):
        assert DKAI_MENTION_PATTERN.search("Just a normal comment") is None


# ── PR context extraction ───────────────────────────────────────────


class TestExtractPrContext:
    def test_extracts_full_context(self):
        payload = _make_bb_payload()
        ctx = extract_pr_context(payload)

        assert ctx is not None
        assert ctx["project_key"] == "PROJ"
        assert ctx["repo_slug"] == "my-repo"
        assert ctx["pr_id"] == 42
        assert ctx["pr_title"] == "Add new feature"
        assert ctx["source_branch"] == "feature/my-feature"
        assert ctx["target_branch"] == "main"
        assert ctx["author_name"] == "John Smith"
        assert ctx["author_email"] == "john.smith@example.com"
        assert "@dkai" in ctx["comment_text"]

    def test_returns_none_without_dkai_mention(self):
        payload = _make_bb_payload(comment_text="Looks good to me!")
        assert extract_pr_context(payload) is None

    def test_returns_none_without_pr_id(self):
        payload = _make_bb_payload()
        del payload["pullRequest"]["id"]
        assert extract_pr_context(payload) is None

    def test_returns_none_without_project_key(self):
        payload = _make_bb_payload()
        del payload["pullRequest"]["toRef"]["repository"]["project"]["key"]
        payload["pullRequest"]["fromRef"]["repository"]["project"]["key"] = ""
        assert extract_pr_context(payload) is None


# ── Thread ID generation ────────────────────────────────────────────


class TestGenerateThreadId:
    def test_deterministic(self):
        id1 = generate_thread_id_from_pr("PROJ", "repo", 42)
        id2 = generate_thread_id_from_pr("PROJ", "repo", 42)
        assert id1 == id2

    def test_different_for_different_prs(self):
        id1 = generate_thread_id_from_pr("PROJ", "repo", 42)
        id2 = generate_thread_id_from_pr("PROJ", "repo", 43)
        assert id1 != id2

    def test_uuid_format(self):
        thread_id = generate_thread_id_from_pr("PROJ", "repo", 42)
        assert len(thread_id) == 36
        parts = thread_id.split("-")
        assert len(parts) == 5


# ── Webhook endpoint ────────────────────────────────────────────────


class TestBitbucketWebhookEndpoint:
    def test_accepts_valid_webhook(self, monkeypatch):
        captured = {}

        async def fake_process(pr_context):
            captured["pr_context"] = pr_context

        monkeypatch.setattr(
            "agent.webhooks.bitbucket.BITBUCKET_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET
        )
        monkeypatch.setattr(
            "agent.webhooks.bitbucket.process_bitbucket_review", fake_process
        )

        client = TestClient(webapp.app)
        payload = _make_bb_payload()
        response = _post_bb_webhook(client, payload)

        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

    def test_rejects_invalid_signature(self, monkeypatch):
        monkeypatch.setattr(
            "agent.webhooks.bitbucket.BITBUCKET_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET
        )

        client = TestClient(webapp.app)
        payload = _make_bb_payload()
        body = json.dumps(payload).encode()

        response = client.post(
            "/webhooks/bitbucket",
            content=body,
            headers={
                "X-Event-Key": "pr:comment:added",
                "X-Hub-Signature": "sha256=invalid",
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 401

    def test_ignores_non_comment_events(self, monkeypatch):
        monkeypatch.setattr(
            "agent.webhooks.bitbucket.BITBUCKET_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET
        )

        client = TestClient(webapp.app)
        payload = _make_bb_payload()
        response = _post_bb_webhook(client, payload, event_key="pr:opened")

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_ignores_comments_without_dkai(self, monkeypatch):
        monkeypatch.setattr(
            "agent.webhooks.bitbucket.BITBUCKET_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET
        )

        client = TestClient(webapp.app)
        payload = _make_bb_payload(comment_text="LGTM!")
        response = _post_bb_webhook(client, payload)

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_get_endpoint_returns_ok(self):
        client = TestClient(webapp.app)
        response = client.get("/webhooks/bitbucket")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ── process_bitbucket_review ────────────────────────────────────────


class TestProcessBitbucketReview:
    def test_creates_langgraph_run(self, monkeypatch):
        captured = {}

        class _FakeRunsClient:
            async def create(self, thread_id, assistant_id, **kwargs):
                captured["thread_id"] = thread_id
                captured["assistant_id"] = assistant_id
                captured["input"] = kwargs.get("input")
                captured["config"] = kwargs.get("config")

        class _FakeLangGraphClient:
            runs = _FakeRunsClient()

        monkeypatch.setattr(
            "agent.webhooks.bitbucket.get_client",
            lambda url: _FakeLangGraphClient(),
        )
        monkeypatch.setenv("DK_BITBUCKET_TOKEN", "test-bb-token")
        monkeypatch.setenv("DK_MCP_TOKEN", "test-mcp-token")

        from agent.webhooks.bitbucket import process_bitbucket_review

        pr_context = {
            "project_key": "PROJ",
            "repo_slug": "my-repo",
            "pr_id": 42,
            "pr_title": "Add feature",
            "source_branch": "feature/x",
            "target_branch": "main",
            "author_name": "John Smith",
            "author_email": "john@example.com",
            "comment_text": "@dkai please review",
            "comment_author": "Jane Doe",
        }

        asyncio.run(process_bitbucket_review(pr_context))

        assert captured["assistant_id"] == "agent"
        assert captured["thread_id"] is not None

        configurable = captured["config"]["configurable"]
        assert configurable["source"] == "bitbucket"
        assert configurable["bitbucket_pr"]["project_key"] == "PROJ"
        assert configurable["bitbucket_pr"]["pr_id"] == 42
        assert configurable["bitbucket_token"] == "test-bb-token"
        assert configurable["mcp_token"] == "test-mcp-token"

        prompt = captured["input"]["messages"][0]["content"]
        assert "PR #42" in prompt
        assert "Add feature" in prompt
        assert "bitbucket_comment" in prompt


# ── Team config ─────────────────────────────────────────────────────


class TestTeamConfig:
    def test_resolve_defaults(self):
        config = resolve_team_config("UNKNOWN", "unknown-repo")
        assert config.team_id == "default"
        assert config.bitbucket_host == "bitbucket.dormakaba.net"

    def test_resolve_by_project(self):
        config = resolve_team_config("PROJ", "unknown-repo")
        assert config.team_id == "example-team"

    def test_resolve_by_repo(self):
        config = resolve_team_config("PROJ", "my-repo")
        assert config.team_id == "example-team"

    def test_review_config_has_defaults(self):
        config = resolve_team_config("UNKNOWN", "unknown-repo")
        assert config.review_config.min_agents == 2
        assert config.review_config.max_agents == 6
        assert config.review_config.max_diff_lines == 5000


# ── Review system prompt ────────────────────────────────────────────


class TestReviewSystemPrompt:
    def test_includes_pr_context(self):
        prompt = construct_review_system_prompt(
            "/workspace/repo",
            pr_title="Add feature",
            pr_id=42,
            source_branch="feature/x",
            target_branch="main",
            author="John",
            project_key="PROJ",
            repo_slug="my-repo",
        )

        assert "PR #42" in prompt
        assert "Add feature" in prompt
        assert "feature/x" in prompt
        assert "PROJ/my-repo" in prompt
        assert "John" in prompt

    def test_includes_workflow_instructions(self):
        prompt = construct_review_system_prompt("/workspace/repo")
        assert "git diff" in prompt
        assert "task" in prompt
        assert "bitbucket_comment" in prompt

    def test_includes_role_catalog(self):
        prompt = construct_review_system_prompt("/workspace/repo")
        assert "Architecture Guardian" in prompt
        assert "Security Auditor" in prompt
        assert "Performance Analyst" in prompt

    def test_includes_output_format(self):
        prompt = construct_review_system_prompt("/workspace/repo")
        assert "APPROVE" in prompt
        assert "REQUEST CHANGES" in prompt
        assert "DK AI Platform" in prompt

    def test_includes_agents_md(self):
        prompt = construct_review_system_prompt(
            "/workspace/repo",
            agents_md="Always run mypy before review.",
        )
        assert "Always run mypy before review." in prompt
        assert "<agents_md>" in prompt

    def test_includes_claude_md(self):
        prompt = construct_review_system_prompt(
            "/workspace/repo",
            claude_md="Use pytest for all tests.",
        )
        assert "Use pytest for all tests." in prompt
        assert "<claude_md>" in prompt

    def test_includes_custom_instructions(self):
        prompt = construct_review_system_prompt(
            "/workspace/repo",
            custom_instructions="Focus on SQL injection vulnerabilities.",
        )
        assert "Focus on SQL injection vulnerabilities." in prompt

    def test_agent_count_limits(self):
        prompt = construct_review_system_prompt(
            "/workspace/repo",
            min_agents=3,
            max_agents=5,
        )
        assert "at least 3" in prompt
        assert "at most 5" in prompt

    def test_read_only_mode(self):
        prompt = construct_review_system_prompt("/workspace/repo")
        assert "read-only review mode" in prompt
