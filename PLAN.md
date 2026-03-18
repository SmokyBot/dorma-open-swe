# DK AI Code Review Agent — Implementation Plan

> **CRITICAL:** This project is a fork of the Open SWE Agent framework. All implementation MUST follow the framework's established patterns as documented in `CUSTOMIZATION.md`. Read `CUSTOMIZATION.md` and the existing code in `agent/server.py`, `agent/webapp.py`, `agent/prompt.py`, and `agent/tools/` before writing any new code. Do NOT build custom pipelines — use `create_deep_agent()`, the `task` tool for subagents, and the framework's middleware system.

---

## Summary

Extend the Open SWE Agent framework to support **Bitbucket Data Center** code reviews triggered by `@dkai` mentions in PR comments. The review agent runs in a sandbox with a cloned repo, uses Deep Agent architecture with subagents for parallel multi-role review, and posts results back as a Bitbucket summary comment via MCP.

---

## Roadmap

### Phase 1 — POC: Full Framework-Aligned Review Agent (THIS PLAN)
- Bitbucket DC webhook → LangGraph run (same pattern as Slack/Linear triggers)
- Full sandbox with cloned repo (HTTPS + per-repo access token)
- Deep Agent with review-focused system prompt and Bitbucket/Jira tools
- Agent-driven subagent orchestration via `task` tool for parallel multi-role review
- MCP for Bitbucket write operations (posting review comment)
- Claude (Anthropic API) as LLM backend
- JSON config file for team settings
- Deploy to Azure

### Phase 2 — Auto-Review on PR Creation
- Webhook for `pr:opened` events (automatic review without `@dkai`)
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

## Access Control Model

Three layers of control ensure the agent can never exceed its intended permissions:

### Layer 1: System-Managed Git Credentials
- Per-repo HTTPS access tokens are stored as environment variables, referenced by name in team config
- The **system** (in `get_agent()`) uses these tokens to clone the repo into the sandbox
- Credentials are written via `sandbox_backend.write()` (never in shell history) and **removed after clone**
- The agent never sees, handles, or has access to git credentials
- This follows the exact pattern in `agent/utils/github.py`: `setup_git_credentials()` → clone → `cleanup_git_credentials()`

### Layer 2: MCP Gateway (AI Platform)
- The agent receives a **bearer token** for the dormakaba AI Platform MCP server
- The MCP gateway controls **which tools** the agent can call
- POC: only `add_comment` for write operations (posting review results)
- Read operations: `get_pull_request`, `get_diff`, `get_file_content`, `browse_repository`, `get_comments`, `get_issue` (Jira)
- The agent cannot perform any Bitbucket operation not whitelisted on the MCP server

### Layer 3: Sandbox Isolation
- Agent code runs in a sandboxed environment (configurable via `SANDBOX_TYPE`)
- No network access to Bitbucket directly — only through MCP gateway
- No persistent credentials in the sandbox after clone

### Future (Phase 3): PR Creation
- `commit_and_open_bb_pr` tool follows the same pattern as the framework's `commit_and_open_pr`
- The tool internally accesses system-stored encrypted credentials (like `get_github_token()` does today)
- Agent just calls `commit_and_open_bb_pr(title, body)` — never handles credentials
- System controls what the tool can do (which branch, which repo)

---

## Architecture: Request Flow

```
Bitbucket DC PR Comment ("@dkai please review")
        │
        ▼  Webhook (pr:comment:added)
┌────────────────────────────────────┐
│  POST /webhooks/bitbucket          │
│  (agent/webhooks/bitbucket.py)     │
│  1. Verify HMAC-SHA256 signature   │
│  2. Detect @dkai mention           │
│  3. Extract PR context             │
│  4. Resolve team config            │
│  5. langgraph_client.runs.create() │ ◄── MUST create LangGraph run
└────────────┬───────────────────────┘     (not call review code directly)
             │
             ▼
┌────────────────────────────────────┐
│  get_agent(config)                 │
│  (agent/server.py)                 │
│  source == "bitbucket" branch:     │
│  1. Resolve BB token from config   │ ◄── system-level, agent never sees it
│  2. Create sandbox                 │
│  3. Clone repo via HTTPS           │
│  4. Remove credentials from sandbox│
│  5. Read AGENTS.md + CLAUDE.md     │ ◄── repo-specific instructions
│  6. Build review system prompt     │
│  7. Return create_deep_agent()     │
│     with Bitbucket tools           │
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│  Deep Agent (review mode)          │
│  Agent autonomously:               │
│  1. Reads the PR diff (local git   │
│     or MCP)                        │
│  2. Explores codebase in sandbox   │
│     (grep, read_file, execute)     │
│  3. Detects tech stack from files  │
│  4. Decides which review roles     │
│     to spawn (2-6 subagents)       │
│  5. Spawns subagents via `task`    │ ◄── framework's built-in tool
│     tool in parallel               │
│  6. Collects + validates findings  │
│  7. Formats summary comment        │
│  8. Posts via bitbucket_comment    │ ◄── MCP-backed tool
└────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Webhook Handler Must Create LangGraph Runs

The current POC code calls `run_review()` directly from the webhook handler. This is **wrong**. It must follow the Slack/Linear pattern: create a LangGraph run via `langgraph_client.runs.create()` with the appropriate config. This ensures proper thread management, state persistence, and sandbox lifecycle.

The webhook handler passes PR context in `config.configurable`:
- `repo.owner` → Bitbucket project key
- `repo.name` → Bitbucket repo slug
- `source` → `"bitbucket"`
- `bitbucket_pr` → PR metadata (id, title, branches, comment text, etc.)
- `team_config_id` → team identifier for config lookup
- `mcp_token` → encrypted MCP bearer token

### 2. Agent-Driven Review Orchestration

The agent (not Python code) decides which review roles to spawn. The system prompt includes:
- The role catalog with descriptions (Architecture Guardian, Security Auditor, Performance Analyst, etc.)
- Guidelines on when each role is relevant (e.g., "spawn Security Auditor if there are API changes")
- Min/max agent count from team config
- Custom review instructions from team config

The agent uses the `task` tool (Deep Agent's built-in subagent spawning) to create review subagents in parallel. Each subagent gets:
- A role-specific prompt with focus areas
- Access to the sandbox (can grep, read files, run commands)
- The PR diff as context

This replaces the current `executor.py` which calls `init_chat_model().ainvoke()` directly.

### 3. Clone via HTTPS with Credential Helper

Bitbucket DC supports HTTPS clone with access tokens. The URL format differs from GitHub:
- GitHub: `https://github.com/{owner}/{repo}.git`
- Bitbucket DC: `https://{BITBUCKET_HOST}/scm/{project_key}/{repo_slug}.git`

The credential handling follows the framework's existing pattern exactly:
1. Write token to `/tmp/.git-credentials` via `sandbox_backend.write()` (not shell)
2. Clone with `git -c credential.helper='store --file=/tmp/.git-credentials' clone <url>`
3. Remove `/tmp/.git-credentials` immediately after clone

### 4. MCP for Bitbucket Writes, Sandbox for Code Reads

The agent has two ways to access code:
- **Sandbox (primary):** Local clone of the repo — grep, read_file, execute, glob all work locally. This is fast and gives full codebase access.
- **MCP (secondary):** For Bitbucket-specific operations the sandbox can't do — PR metadata, diff, posting comments, Jira tickets.

For the POC, the only MCP **write** operation is `add_comment`. All code exploration is local.

### 5. No `commit_and_open_pr` in POC

The review agent does not create commits or PRs. The tools list does NOT include `commit_and_open_pr`. The `open_pr_if_needed` middleware is also excluded. The agent's only write action is posting the review comment.

### 6. Agent Uses Existing Utilities as Guidance

The following existing modules contain useful logic that should inform the **system prompt**, not be called as a custom pipeline:
- `agent/review/tech_detector.py` — Tech detection heuristics. The system prompt should guide the agent on how to detect tech stacks. Alternatively, the agent can call this as a utility from within the sandbox.
- `agent/review/role_selector.py` — Role catalog and selection logic. Encode the role descriptions and relevance criteria directly in the system prompt.
- `agent/review/output.py` — Comment formatting rules (Bitbucket-compatible markdown). Encode the output format in the system prompt.

### 7. Respect AGENTS.md and CLAUDE.md from the Reviewed Repository

The framework already reads `AGENTS.md` from the cloned repo and injects it into the system prompt (see `agent/utils/agents_md.py` and `agent/server.py:368`). Extend this to also read `CLAUDE.md` — both are standard convention files that repositories use to provide project-specific instructions to AI agents.

These files may contain:
- Coding conventions and style rules
- Architecture constraints ("never modify X", "always use Y pattern")
- Test requirements ("run `make test` before submitting")
- Review-specific guidance ("pay special attention to SQL injection in the data layer")

The review agent must follow these instructions just like a human reviewer would follow a team's review guidelines. Both files are injected into the system prompt via `<agents_md>` / `<claude_md>` tags so the agent treats them as authoritative project context.

The `read_agents_md_in_sandbox()` utility should be extended (or a parallel `read_claude_md_in_sandbox()` added) to also look for `CLAUDE.md`. Both are passed to `construct_review_system_prompt()`.

### 8. Checkout Source Branch in Sandbox

After cloning, the agent should checkout the PR's source branch to review the actual code:
```
git checkout {source_branch}
```
This can be done by the agent via `execute` tool, or by the system in `get_agent()` before returning the agent. The diff can then be computed locally: `git diff {target_branch}...{source_branch}`.

---

## Components to Build / Modify

### New Files

| File | Purpose |
|---|---|
| `agent/tools/bitbucket_comment.py` | Tool for posting review comments via MCP `add_comment`. Replaces `github_comment` for Bitbucket source. |
| `agent/utils/bitbucket_auth.py` | Resolve Bitbucket HTTPS access token from team config env var. Replaces `resolve_github_token()` for Bitbucket source. |

### Files to Rewrite

| File | What Changes |
|---|---|
| `agent/webhooks/bitbucket.py` | `process_bitbucket_review()` must create a LangGraph run via `langgraph_client.runs.create()` instead of calling `run_review()` directly. Keep the webhook verification and PR context extraction — that part is good. |

### Files to Adapt

| File | What Changes |
|---|---|
| `agent/server.py` | Add `source == "bitbucket"` branch in `get_agent()`. Calls `resolve_bitbucket_token()`, creates sandbox, clones repo (Bitbucket URL format), returns `create_deep_agent()` with review-specific tools and prompt. Adapt `_clone_or_pull_repo_in_sandbox()` to support Bitbucket DC URL format. |
| `agent/prompt.py` | Add review-focused system prompt section with role catalog, review workflow instructions, output format rules, and PR context template. Add `construct_review_system_prompt()` function. |
| `agent/config/loader.py` | Add `bitbucket_token_env` field to `TeamConfig`. Add `bitbucket_host` to defaults. |
| `agent/config/teams.json` | Add `bitbucket_token_env` per team. Add `bitbucket_host` to defaults. |
| `agent/webapp.py` | Register the `/webhooks/bitbucket` route (may already be done). |

### Files to Delete

| File | Why |
|---|---|
| `agent/review/orchestrator.py` | Replaced by Deep Agent orchestration via system prompt + `task` tool. |
| `agent/review/executor.py` | Replaced by `task` tool subagents. Direct `init_chat_model().ainvoke()` calls are wrong — must use framework's agent architecture. Keep the `Finding` and `AgentResult` dataclasses (move to `validator.py` or a types module). |
| `agent/review/poster.py` | Replaced by `bitbucket_comment` tool. |

### Files to Keep As-Is (Utilities)

| File | Why |
|---|---|
| `agent/review/tech_detector.py` | Useful heuristics for tech stack detection. Agent can use this logic or the system prompt can encode it. |
| `agent/review/role_selector.py` | Role catalog is valuable. Encode in system prompt. |
| `agent/review/output.py` | Bitbucket-compatible markdown formatting. Used by system prompt or `bitbucket_comment` tool. |
| `agent/review/validator.py` | Finding validation and deduplication logic. |
| `agent/utils/bitbucket_mcp.py` | MCP client. Used internally by `bitbucket_comment` tool. |

---

## Team Configuration

Teams configure their repos via `agent/config/teams.json`:

- `bitbucket_projects` — Bitbucket project keys this team owns
- `bitbucket_repos` — Specific `PROJECT/repo-slug` entries
- `mcp_token_env` — Environment variable name holding the MCP bearer token
- `bitbucket_token_env` — Environment variable name holding the Bitbucket HTTPS access token for cloning
- `llm_provider` / `llm_model` — LLM configuration
- `review_config` — Review-specific settings (custom instructions, excluded paths, diff limits, agent count limits)

Matching priority: exact repo match first, then project match, then defaults.

---

## Environment Variables

| Variable | Purpose | Required |
|---|---|---|
| `BITBUCKET_WEBHOOK_SECRET` | HMAC secret for webhook signature verification | Yes |
| `ANTHROPIC_API_KEY` | Claude API key | Yes |
| `BITBUCKET_HOST` | Bitbucket DC hostname (default: `bitbucket.dormakaba.net`) | Yes |
| `LANGGRAPH_URL` | LangGraph server URL for run creation | Yes |
| `SANDBOX_TYPE` | Sandbox provider: `langsmith`, `daytona`, `local`, etc. | Yes |
| `TEAM_CONFIG_PATH` | Path to teams.json (default: `agent/config/teams.json`) | No |
| `DK_MCP_ENDPOINT` | AI Platform MCP URL (default: `https://dk-ai-platform-apim.azure-api.net/mcp`) | No |
| `DK_MCP_TOKEN` | Default/fallback MCP bearer token | No |
| `DK_BITBUCKET_TOKEN` | Default/fallback Bitbucket HTTPS access token | No |
| `TEAM_*_MCP_TOKEN` | Per-team MCP bearer tokens | Per team |
| `TEAM_*_BB_TOKEN` | Per-team Bitbucket access tokens | Per team |

---

## MCP Tools (AI Platform Gateway)

All MCP operations go through the dormakaba AI Platform MCP server via Streamable HTTP transport.

### Enabled for POC

| Tool | Direction | Purpose |
|---|---|---|
| `get_pull_request` | Read | PR metadata (title, branches, author, reviewers) |
| `get_diff` | Read | Full unified diff for the PR |
| `get_file_content` | Read | File content at a specific ref |
| `browse_repository` | Read | Directory listing |
| `get_comments` | Read | Existing PR comments |
| `add_comment` | **Write** | Post review summary comment |
| `get_issue` (Jira) | Read | Linked Jira ticket details |

### Not Enabled (POC)

`add_comment_inline`, `create_pull_request`, `merge_pull_request`, `decline_pull_request`, `approve_pull_request`, `delete_branch` — no destructive or PR-modifying actions.

---

## Review Output Format

Single summary comment in Bitbucket-compatible CommonMark markdown:
- **No HTML tags** (Bitbucket Server doesn't support them)
- **No checkboxes** (not supported)
- **No collapsible details** (not supported)
- **No emojis** in headers
- Supported: headings, bold, italic, fenced code blocks, tables, blockquotes, lists, links, horizontal rules

Structure:
1. Header: `## Review: {ticket} --- {PR title}` with branch info and verdict
2. Summary sentence
3. Critical findings (with `// current` and `// suggested` code blocks)
4. Major findings (with code blocks)
5. Minor findings (table format: # | File | Finding)
6. Positives section
7. Actions line
8. Signature: `*Generated with DK AI Platform (ai-platform.dormakaba.net)*`

Verdicts: **APPROVE** | **APPROVE WITH COMMENTS** | **REQUEST CHANGES**

---

## Deployment (Azure)

- Azure Container Instances or App Service
- Docker image based on existing Dockerfile
- Webhook URL: `https://<app>.azurewebsites.net/webhooks/bitbucket`
- Configure Bitbucket DC webhook → this URL, event: `pr:comment:added`
- All secrets as Azure App Service environment variables
- `SANDBOX_TYPE=local` for development, cloud provider for production

---

*This plan targets Phase 1 (POC) for implementation. Phases 2-5 are directional and will be planned when we get there.*
