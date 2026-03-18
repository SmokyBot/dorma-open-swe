# DK AI Code Review Agent — Implementation Plan

## Summary

Extend the Open SWE Agent framework to support **Bitbucket Data Center** code reviews, triggered by `@dkai` mentions in PR comments. Following the framework's customization patterns (`CUSTOMIZATION.md`), the agent runs in a sandbox with a cloned repo, uses the Deep Agent architecture with subagents for parallel review, and posts results via MCP tools.

---

## Roadmap

### Phase 1 — POC: Full Framework-Aligned Review Agent (THIS PLAN)
- Bitbucket DC webhook → LangGraph run (same pattern as Slack/Linear triggers)
- Full sandbox: clone repo via HTTPS + per-repo access token (system-managed)
- Deep Agent with review-focused system prompt and Bitbucket/Jira tools
- Subagents via `task` tool for parallel multi-role review
- MCP for Bitbucket write operations (add_comment)
- Deploy to Azure

### Phase 2 — Auto-Review on PR Creation
- Webhook for `pr:opened` events (auto-review without `@dkai`)
- Configurable per-team opt-in

### Phase 3 — Review + Auto-Fix
- Agent pushes fix commits via system-managed `commit_and_open_bb_pr` tool
- `@dkai fix C1` to auto-fix specific findings

### Phase 4 — Multi-LLM + AI Experience Hub
- Azure OpenAI as alternative backend
- DB/API config backend (replace JSON)
- Team self-service configuration UI

### Phase 5 — Multi-Provider
- GitLab, Azure DevOps support

---

## Architecture

### Access Control Model

```
┌─────────────────────────────────────────────────────────┐
│  SYSTEM LEVEL (agent never sees these credentials)      │
│  ─────────────────────────────────────────────────────  │
│  • Per-repo HTTPS access token (from team config)       │
│  • System clones repo into sandbox at startup           │
│  • Credentials written via sandbox_backend.write() —    │
│    never in shell history                               │
│  • Credentials removed after clone (cleanup_git_creds)  │
│  • Future: system-managed PR creation (Phase 3)         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  AGENT LEVEL (what the agent has access to)             │
│  ─────────────────────────────────────────────────────  │
│  • Local file access in sandbox (read, grep, execute)   │
│  • MCP bearer token for AI Platform gateway             │
│    → Controls WHICH tools agent can call                │
│    → POC: add_comment, get_file_content,                │
│      browse_repository, get_diff, get_pull_request,     │
│      get_comments, get_issue (Jira)                     │
│  • NO git push credentials                              │
│  • NO direct Bitbucket REST API access                  │
└─────────────────────────────────────────────────────────┘
```

### Request Flow

```
Bitbucket DC PR Comment (@dkai)
        │
        ▼  Webhook (pr:comment:added)
┌────────────────────────────────────┐
│  POST /webhooks/bitbucket          │
│  (agent/webhooks/bitbucket.py)     │
│  1. Verify HMAC signature          │
│  2. Detect @dkai mention           │
│  3. Extract PR context             │
│  4. Resolve team config            │
│  5. langgraph_client.runs.create() │ ◄── KEY: creates LangGraph run
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│  get_agent(config)                 │
│  (agent/server.py)                 │
│  source == "bitbucket" branch:     │
│  1. resolve_bitbucket_token()      │ ◄── system-level credential
│  2. Create sandbox                 │
│  3. Clone repo via HTTPS           │
│  4. Cleanup credentials            │
│  5. Read AGENTS.md from sandbox    │
│  6. Build review system prompt     │
│  7. create_deep_agent() with       │
│     Bitbucket tools + MCP tools    │
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│  Deep Agent (review mode)          │
│  System prompt instructs:          │
│  1. Fetch PR metadata + diff       │
│     (MCP or local files)           │
│  2. Auto-detect tech stack         │
│     (local files in sandbox)       │
│  3. Fetch Jira ticket (MCP)        │
│  4. Select 2-6 review roles        │
│  5. Spawn subagents via `task`     │ ◄── framework's built-in tool
│     Each subagent:                 │
│     - Gets role-specific prompt    │
│     - Has sandbox access (grep,    │
│       read_file, execute)          │
│     - Can explore codebase deeply  │
│  6. Collect + validate findings    │
│  7. Format summary comment         │
│  8. Post via bitbucket_comment     │ ◄── MCP-backed tool
└────────────────────────────────────┘
```

---

## Implementation Details

### 1. Webhook Handler (REWRITE)

**File:** `agent/webhooks/bitbucket.py`

Current state: Calls `run_review()` directly in background task.
Required: Must create LangGraph runs like Slack/Linear handlers do.

```python
async def process_bitbucket_review(pr_context: dict, team_config: TeamConfig):
    thread_id = f"bitbucket:{project_key}:{repo_slug}:{pr_id}"
    langgraph_client = get_client(url=LANGGRAPH_URL)

    prompt = _build_review_prompt(pr_context)

    await langgraph_client.runs.create(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": {
            "repo": {
                "owner": project_key,     # maps to Bitbucket project
                "name": repo_slug,        # maps to Bitbucket repo
            },
            "source": "bitbucket",
            "bitbucket_pr": pr_context,   # PR metadata for tools/prompt
            "team_config_id": team_config.name,
            "mcp_token": team_config.mcp_token,  # encrypted in transit
        }},
        if_not_exists="create",
    )
```

### 2. Server Integration (ADAPT `get_agent()`)

**File:** `agent/server.py`

Following CUSTOMIZATION.md section 4 (Adding a new trigger), add a `source == "bitbucket"` branch in `get_agent()`:

```python
async def get_agent(config: RunnableConfig) -> Pregel:
    source = config["configurable"].get("source")

    if source == "bitbucket":
        return await _get_bitbucket_review_agent(config)

    # ... existing GitHub/Linear/Slack flow ...
```

The `_get_bitbucket_review_agent()` function:
1. Resolves Bitbucket HTTPS access token from team config (system-level)
2. Creates sandbox (same factory as existing — `SANDBOX_TYPE` env var)
3. Clones repo via `_clone_or_pull_repo_in_sandbox()` (adapted for Bitbucket DC URL format: `https://{bitbucket_host}/scm/{project}/{repo}.git`)
4. Cleans up git credentials after clone
5. Reads AGENTS.md from sandbox
6. Constructs review-focused system prompt
7. Returns `create_deep_agent()` with Bitbucket-specific tools

```python
return create_deep_agent(
    model=make_model(team_config.llm_model, temperature=0, max_tokens=16_000),
    system_prompt=construct_review_system_prompt(repo_dir, pr_context, agents_md),
    tools=[
        bitbucket_comment,       # Post review via MCP add_comment
        fetch_url,               # Web research (best practices lookup)
        http_request,            # General HTTP
    ],
    backend=sandbox_backend,
    middleware=[
        ToolErrorMiddleware(),
        check_message_queue_before_model,
        ensure_no_empty_msg,
        # NO open_pr_if_needed — review agent doesn't create PRs
    ],
).with_config(config)
```

Note: The agent also gets Deep Agent built-in tools (read_file, write_file, execute, grep, glob, task, todo) from the sandbox backend — these are not listed in `tools=[]` but are always available.

### 3. Clone URL Adaptation

**File:** `agent/server.py` (in `_clone_or_pull_repo_in_sandbox`)

The framework constructs: `https://github.com/{owner}/{repo}.git`
For Bitbucket DC: `https://{BITBUCKET_HOST}/scm/{project_key}/{repo_slug}.git`

We adapt the URL construction based on source:

```python
if source == "bitbucket":
    bitbucket_host = os.environ.get("BITBUCKET_HOST", "bitbucket.dormakaba.net")
    clean_url = f"https://{bitbucket_host}/scm/{owner}/{repo}.git"
else:
    clean_url = f"https://github.com/{owner}/{repo}.git"
```

Credential flow stays identical:
1. `setup_git_credentials(sandbox_backend, token)` — writes to `/tmp/.git-credentials`
2. `git clone` with credential helper
3. `cleanup_git_credentials(sandbox_backend)` — removes file

### 4. Auth Resolution (NEW)

**File:** `agent/utils/bitbucket_auth.py`

Replaces `resolve_github_token()` for Bitbucket source:

```python
async def resolve_bitbucket_token(config: RunnableConfig, thread_id: str) -> tuple[str, str]:
    """Resolve a Bitbucket HTTPS access token from team config.

    For Bitbucket, tokens come from per-repo access tokens stored as env vars
    in the team config (e.g., TEAM_ALPHA_BB_TOKEN), not from OAuth.
    """
    team_config_id = config["configurable"].get("team_config_id")
    team_config = load_team_config_by_id(team_config_id)

    # Token stored as env var name in team config
    token_env = team_config.get("bitbucket_token_env", "")
    token = os.environ.get(token_env, "")

    if not token:
        # Fallback to default
        token = os.environ.get("DK_BITBUCKET_TOKEN", "")

    if not token:
        raise RuntimeError(f"No Bitbucket token for team {team_config_id}")

    encrypted = encrypt_token(token)
    await client.threads.update(thread_id=thread_id, metadata={"bb_token_encrypted": encrypted})
    return token, encrypted
```

### 5. Team Configuration (ADAPT)

**File:** `agent/config/teams.json`

Add `bitbucket_token_env` field:

```json
{
  "defaults": {
    "llm_provider": "anthropic",
    "llm_model": "claude-sonnet-4-20250514",
    "bitbucket_host": "bitbucket.dormakaba.net"
  },
  "teams": {
    "team-alpha": {
      "name": "Team Alpha",
      "bitbucket_projects": ["WAD", "CORE"],
      "bitbucket_repos": ["WAD/my-repo"],
      "mcp_token_env": "TEAM_ALPHA_MCP_TOKEN",
      "bitbucket_token_env": "TEAM_ALPHA_BB_TOKEN",
      "llm_provider": "anthropic",
      "llm_model": "claude-sonnet-4-20250514",
      "review_config": {
        "custom_review_instructions": "Focus on Spring Boot best practices",
        "excluded_paths": ["**/generated/**"],
        "max_diff_lines": 5000,
        "min_review_agents": 2,
        "max_review_agents": 6
      }
    }
  }
}
```

### 6. Bitbucket Comment Tool (NEW)

**File:** `agent/tools/bitbucket_comment.py`

Replaces `github_comment`. Uses MCP via the `BitbucketMCPClient`:

```python
def bitbucket_comment(text: str) -> dict:
    """Post a comment on the current Bitbucket pull request.

    Use this to post your review summary after completing the code review.

    Args:
        text: The comment text in markdown format.

    Returns:
        Dictionary with success status and any error.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    pr_context = configurable.get("bitbucket_pr", {})
    mcp_token = configurable.get("mcp_token", "")

    # Uses BitbucketMCPClient to call add_comment via MCP
    result = asyncio.run(_post_comment(mcp_token, pr_context, text))
    return result
```

### 7. Review System Prompt (NEW)

**File:** `agent/prompt.py` — add `REVIEW_SYSTEM_PROMPT_SECTION`

Following CUSTOMIZATION.md section 5, add a new prompt section for reviews:

```python
REVIEW_SYSTEM_PROMPT_SECTION = """---

### Review Task

You are performing a code review on a Bitbucket Pull Request.

**PR Context:**
- Project: {project_key}/{repo_slug}
- PR #{pr_id}: {pr_title}
- Branch: {source_branch} → {target_branch}
- Author: {pr_author}
{jira_context}

**Your workflow:**
1. Fetch the PR diff (use `execute` to run `git diff {target_branch}...{source_branch}` in your local repo)
2. Explore the local codebase in your sandbox for deeper context (grep, read files, understand architecture)
3. Detect the tech stack from local files
4. Use the `task` tool to spawn 2-6 review subagents in parallel, each with a specialized role
5. Collect findings from all subagents
6. Validate, deduplicate, and format findings
7. Post a single summary comment using the `bitbucket_comment` tool

**Review roles to consider:** Architecture Guardian (always), Security Auditor, Performance Analyst, Test Strategist, Error Handling Engineer, Backend Framework Expert, Database Specialist, API Reviewer, Frontend Expert, Code Quality Analyst — select based on tech stack and change type.

**Output format:** Use the standard review comment format (CommonMark markdown, no HTML):
- Header with verdict (APPROVE / APPROVE WITH COMMENTS / REQUEST CHANGES)
- Critical findings with code blocks (current + suggested)
- Major findings with code blocks
- Minor findings in table format
- Positives section
- Actions line
- Signature: *Generated with DK AI Platform (ai-platform.dormakaba.net)*

{custom_review_instructions}
"""
```

The `construct_review_system_prompt()` function assembles:
- `WORKING_ENV_SECTION` (from existing prompt.py)
- `REVIEW_SYSTEM_PROMPT_SECTION` (new)
- AGENTS.md content (from repo)
- Team custom instructions (from config)

### 8. MCP Client (KEEP)

**File:** `agent/utils/bitbucket_mcp.py`

Keep the existing `BitbucketMCPClient` as-is. It's used by the `bitbucket_comment` tool internally. The agent doesn't need direct MCP access because it has local sandbox access for file exploration.

For POC, the only MCP write operation is `add_comment`. All code exploration happens locally in the sandbox (grep, read_file, etc.).

### 9. Utilities (KEEP as utilities)

**Files that stay as utility modules:**
- `agent/review/tech_detector.py` — Used by system prompt builder or agent can detect locally
- `agent/review/role_selector.py` — Used by system prompt builder to suggest roles
- `agent/review/output.py` — Used by `bitbucket_comment` tool or the agent formats it in-prompt
- `agent/review/validator.py` — Used as post-processing utility

### 10. Files to DELETE

These are replaced by the framework's Deep Agent + subagent architecture:
- `agent/review/orchestrator.py` — Agent handles orchestration via system prompt
- `agent/review/executor.py` — Subagents run via `task` tool, not direct `init_chat_model().ainvoke()`
- `agent/review/poster.py` — Replaced by `bitbucket_comment` tool

Keep the data classes (`Finding`, `AgentResult`) — move them to a shared types module or into `validator.py`.

---

## File Changes Summary

```
KEEP AS-IS:
  agent/config/loader.py           ← team config loader (add bitbucket_token_env support)
  agent/config/teams.json          ← team config (add bitbucket_token_env field)
  agent/utils/bitbucket_mcp.py     ← MCP client (used by bitbucket_comment tool)
  agent/review/tech_detector.py    ← utility
  agent/review/role_selector.py    ← utility
  agent/review/output.py           ← utility
  agent/review/validator.py        ← utility

REWRITE:
  agent/webhooks/bitbucket.py      ← must create LangGraph runs, not call run_review()

ADAPT:
  agent/server.py                  ← add source=="bitbucket" branch in get_agent()
  agent/prompt.py                  ← add review system prompt section
  agent/config/loader.py           ← add bitbucket_token_env to TeamConfig

NEW:
  agent/tools/bitbucket_comment.py ← MCP-backed tool for posting review comments
  agent/utils/bitbucket_auth.py    ← resolve Bitbucket token from team config

DELETE:
  agent/review/orchestrator.py     ← replaced by Deep Agent orchestration
  agent/review/executor.py         ← replaced by `task` tool subagents
  agent/review/poster.py           ← replaced by bitbucket_comment tool
```

---

## Environment Variables

```bash
# Existing (keep)
BITBUCKET_WEBHOOK_SECRET        # HMAC secret for webhook verification
ANTHROPIC_API_KEY               # Claude API key
TEAM_CONFIG_PATH                # Path to teams.json
DK_MCP_ENDPOINT                 # AI Platform MCP URL
DK_MCP_TOKEN                    # Default MCP bearer token

# New
BITBUCKET_HOST                  # Bitbucket DC hostname (default: bitbucket.dormakaba.net)
DK_BITBUCKET_TOKEN              # Default Bitbucket HTTPS access token (fallback)
LANGGRAPH_URL                   # LangGraph server URL for run creation
SANDBOX_TYPE                    # Sandbox provider: langsmith, daytona, local, etc.

# Per-team (in Azure App Service config)
TEAM_ALPHA_MCP_TOKEN            # Team-specific MCP bearer token
TEAM_ALPHA_BB_TOKEN             # Team-specific Bitbucket HTTPS access token
```

---

## Deployment (Azure)

- Azure Container Instances or App Service
- Docker image based on existing Dockerfile
- Webhook URL: `https://<app>.azurewebsites.net/webhooks/bitbucket`
- Configure Bitbucket DC webhook → this URL, event: `pr:comment:added`
- All secrets as Azure App Service environment variables

---

## Resolved Decisions

1. **Framework alignment:** Full `get_agent()` + `create_deep_agent()` pattern per CUSTOMIZATION.md
2. **Sandbox:** Yes, from Phase 1. `SANDBOX_TYPE` determines provider (local for dev, cloud for prod)
3. **Clone:** HTTPS + per-repo access token, same credential helper pattern as framework's GitHub flow
4. **Credential isolation:** System manages clone credentials; agent never sees them. MCP bearer token is the only credential the agent uses, and the MCP gateway controls tool access.
5. **Subagents:** Via framework's built-in `task` tool, NOT direct `init_chat_model().ainvoke()`
6. **Orchestration:** Via Deep Agent system prompt, NOT custom Python orchestrator
7. **Review trigger:** `@dkai` in PR comments only (not PR description)
8. **Re-review:** Full review each time `@dkai` is mentioned
9. **Output:** Single summary comment, no inline comments (avoid PR clutter)
10. **PR creation (future):** System-managed `commit_and_open_bb_pr` tool (Phase 3), same pattern as framework's `commit_and_open_pr` — uses system-held credentials the agent can't access directly
11. **MCP scope (POC):** Only `add_comment` for writes. All code exploration is local in sandbox.
12. **Jira:** Read-only via MCP `get_issue` tool — agent can fetch linked ticket for context

---

*This plan targets Phase 1 for implementation. Phases 2-5 are directional.*
