# DK AI Code Review Agent — Roadmap & POC Plan

## Summary

Extend the Open SWE Agent framework to support **Bitbucket Data Center** code reviews, triggered by `@dkai` mentions in PR comments. The agent reads the PR diff via dormakaba AI Platform MCP tools, spawns multiple independent review sub-agents with auto-detected roles, validates findings, and posts results back as Bitbucket inline + summary comments.

---

## High-Level Roadmap (Long-Term)

### Phase 1 — POC: Comment-Only Review via @dkai (THIS PLAN)
- Bitbucket DC webhook handler for PR comment events
- `@dkai` mention detection
- MCP-based Bitbucket access (read diff, file contents, post comments)
- Multi-agent review with auto-detected roles (no sandbox, no git clone)
- Claude (Anthropic API) as LLM backend
- JSON config file for team settings
- Deploy to Azure

### Phase 2 — Sandbox + Deep Code Analysis
- Add sandbox support (clone repo, full codebase exploration)
- Git checkout of source branch for surrounding code context
- Enhanced review depth (cross-file analysis, dependency graphs)
- AGENTS.md support for repo-specific review rules

### Phase 3 — Auto-Review on PR Creation
- Webhook for `pr:opened` events (automatic review without @dkai mention)
- Configurable per-team: opt-in auto-review vs. mention-only
- Review scope filters (file patterns, diff size thresholds)

### Phase 4 — Review + Auto-Fix
- Agent can push fix commits to the PR branch
- "Fix on request" mode: `@dkai fix C1` to auto-fix a specific finding
- Lint/format auto-fixes

### Phase 5 — Multi-LLM + AI Experience Hub Integration
- Azure OpenAI (Codex Mini, GPT-4o) as alternative backend
- Pluggable LLM per team via config
- DB/API config backend (replace JSON) managed from AI Experience Hub
- Team self-service configuration UI

### Phase 6 — Multi-Provider
- GitLab support
- Azure DevOps support
- Unified provider abstraction layer

---

## POC Detailed Plan

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Bitbucket DC                                               │
│  PR Comment: "@dkai please review"                          │
└──────────────────────────┬──────────────────────────────────┘
                           │ Webhook (pr:comment:added)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI Webhook Handler (new: /webhooks/bitbucket)         │
│  ─────────────────────────────────────────────────────────  │
│  1. Verify webhook signature (HMAC)                         │
│  2. Detect @dkai mention in comment text                    │
│  3. Extract: project, repo, PR number, comment author       │
│  4. Look up team config from JSON                           │
│  5. Create LangGraph run                                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Review Orchestrator (new graph node / agent)               │
│  ─────────────────────────────────────────────────────────  │
│  1. Fetch PR metadata + diff via MCP                        │
│  2. Auto-detect tech stack from changed files               │
│  3. Read linked Jira ticket via MCP (if available)          │
│  4. Select 2-6 review roles based on changes + tech stack   │
│  5. Spawn independent review sub-agents (parallel)          │
│     Each agent gets MCP tools to freely explore the repo    │
│     (get_file_content, browse_repository, get_diff)         │
│  6. Collect findings, deduplicate, validate                 │
│  7. Post single summary comment (table format, no inlines)  │
└─────────────────────────────────────────────────────────────┘
```

### Components to Build

#### 1. Team Configuration (JSON)

**File:** `agent/config/teams.json`

```json
{
  "teams": {
    "team-alpha": {
      "name": "Team Alpha",
      "bitbucket_projects": ["WAD", "CORE"],
      "bitbucket_repos": ["vaude", "trinity-cloud"],
      "mcp_token_env": "TEAM_ALPHA_MCP_TOKEN",
      "llm_provider": "anthropic",
      "llm_model": "claude-sonnet-4-20250514",
      "review_config": {
        "auto_review_on_pr_create": false,
        "max_diff_lines": 5000,
        "custom_review_instructions": "Focus on Spring Boot best practices",
        "excluded_paths": ["**/generated/**", "**/test/fixtures/**"]
      }
    }
  }
}
```

**File:** `agent/config/loader.py` — Config loader with env var interpolation and validation.

#### 2. Bitbucket Webhook Handler

**File:** `agent/webhooks/bitbucket.py`

New FastAPI route `POST /webhooks/bitbucket`:
- Verify webhook signature (Bitbucket DC uses HMAC-SHA256 on `X-Hub-Signature` header)
- Parse event type from `X-Event-Key` header (`pr:comment:added`)
- Extract PR details: project key, repo slug, PR ID, comment body, author
- Check for `@dkai` mention (case-insensitive)
- Resolve team from project/repo mapping in config
- Generate thread ID: `bitbucket:{project}:{repo}:{pr_id}`
- Acknowledge with reaction/reply (if Bitbucket API supports it)
- Create LangGraph run with PR context

Register in `agent/webapp.py` alongside existing webhook routes.

#### 3. Bitbucket MCP Integration Layer

**File:** `agent/utils/bitbucket_mcp.py`

Wrapper around MCP tool calls. Both Bitbucket and Jira tools are served from the **same** dormakaba AI Platform MCP server:

- **MCP endpoint:** `https://ai-platform.dormakaba.net/api/mcp` (via APIM: `https://dk-ai-platform-apim.azure-api.net/mcp`)
- **Auth:** Bearer token in `Authorization` header
- **Protocol:** Standard MCP over HTTP (Streamable HTTP transport)

**Bitbucket MCP tools (6 enabled):**
- `get_pull_request` — PR metadata (source/target branch, title, description, author)
- `get_diff` — Full diff content for the PR
- `get_file_content` — Fetch any file at specific ref (agents use this freely to explore surrounding code)
- `browse_repository` — Explore repo directory structure (tech stack detection, project understanding)
- `add_comment` — Post summary review comment on the PR
- `get_comments` — Fetch existing PR comments (context on re-review)

**Jira MCP tools (read-only):**
- `get_issue` — Read the linked Jira ticket (summary, description, acceptance criteria, status)

**Not enabled (POC):** `add_comment_inline` (no inline comments to avoid PR clutter), `create_pull_request`, `merge_pull_request`, `decline_pull_request`, `approve_pull_request`, `delete_branch` (no write/destructive actions).

**Key design:** Review sub-agents get direct access to `get_file_content` and `browse_repository` so they can autonomously explore the codebase as deep as needed — not limited to pre-fetched files.

#### 4. Tech Stack Auto-Detection

**File:** `agent/review/tech_detector.py`

Analyze changed files to detect:
- **Languages:** Java, TypeScript, Python, Go, etc. (by file extension)
- **Frameworks:** Spring Boot (pom.xml, @SpringBootApplication), Angular (angular.json, @Component), React (package.json with react), etc.
- **Build tools:** Maven, Gradle, npm, yarn, etc.
- **Patterns:** REST APIs, GraphQL, WebSocket, database migrations, CI/CD configs

Returns a `TechProfile` dataclass used by the role selector.

#### 5. Review Role Selector

**File:** `agent/review/role_selector.py`

Given the `TechProfile` + diff summary + team config:
1. Always include **Architecture & Integration Guardian** (Role #1)
2. Score remaining roles by relevance to the actual changes
3. Combine roles for smaller diffs (e.g., "Security + Performance" in one agent)
4. Split into focused agents for larger diffs
5. Cap at 2-6 agents total
6. Respect team config overrides (custom roles, excluded roles)

Returns list of `ReviewAgent` specs with role name, focus areas, and specific instructions.

#### 6. Review Sub-Agent Execution

**File:** `agent/review/executor.py`

For each review agent:
1. Build a role-specific system prompt with:
   - Role description and focus areas
   - The PR diff
   - Key file contents (for context around changes)
   - Tech stack info
   - Team-specific review instructions
2. Call Claude API (or configured LLM) independently
3. Parse structured findings (severity, file, line, description, suggestion)
4. Return findings list

All agents run in **parallel** for speed.

#### 7. Finding Validation & Deduplication

**File:** `agent/review/validator.py`

After all sub-agents complete:
1. Merge all findings
2. Deduplicate (same file + line + similar description)
3. Validate ambiguous findings by:
   - Cross-referencing with actual file content via MCP
   - Checking if the "issue" is intentional (e.g., TODO comments, known patterns)
4. Assign final severity (Critical/Major/Minor)
5. Sort by severity, then by file

#### 8. Comment Formatter & Poster

**File:** `agent/review/output.py`

Format findings into a **single summary comment** in Bitbucket-compatible markdown:
- Table format per the skill spec (CommonMark, no HTML, no checkboxes)
- Each Critical/Major finding includes `// current` and `// suggested` code blocks inline in the summary
- **Verdict:** APPROVE / APPROVE WITH COMMENTS / REQUEST CHANGES
- **No inline comments** on the diff (avoids cluttering the PR)

**File:** `agent/review/poster.py`

Post to Bitbucket via MCP:
1. Post single summary comment via `add_comment`
2. Handle errors gracefully (retry once, then log)

#### 9. LLM Configuration

**File:** `agent/utils/llm.py` (extend existing `model.py`)

- Default: `anthropic:claude-sonnet-4-20250514` for review agents (fast + capable)
- Configurable per team in JSON config
- API key via environment variable (subscription-based Anthropic key)
- Later: Azure OpenAI support

### File Structure (New Files)

```
agent/
├── config/
│   ├── __init__.py
│   ├── teams.json              # Team configuration
│   └── loader.py               # Config loader + validation
├── webhooks/
│   ├── __init__.py
│   └── bitbucket.py            # Bitbucket DC webhook handler
├── review/
│   ├── __init__.py
│   ├── orchestrator.py         # Main review flow coordinator
│   ├── tech_detector.py        # Auto-detect tech stack
│   ├── role_selector.py        # Pick review roles
│   ├── executor.py             # Run review sub-agents
│   ├── validator.py            # Validate + deduplicate findings
│   ├── output.py               # Format findings as markdown
│   └── poster.py               # Post to Bitbucket via MCP
├── utils/
│   └── bitbucket_mcp.py        # MCP tool call wrappers
└── webapp.py                   # (modified) Register /webhooks/bitbucket
```

### Dependencies to Add

- `mcp` — MCP Python SDK (standard MCP client for connecting to AI Platform MCP server)
- No other new major dependencies (uses existing FastAPI, LangChain, LangGraph)

### Configuration / Environment Variables

New env vars:
- `BITBUCKET_WEBHOOK_SECRET` — HMAC secret for webhook verification
- `ANTHROPIC_API_KEY` — Claude subscription API key
- `TEAM_CONFIG_PATH` — Path to teams.json (default: `agent/config/teams.json`)
- `DK_MCP_ENDPOINT` — AI Platform MCP URL (default: `https://dk-ai-platform-apim.azure-api.net/mcp`)
- `DK_MCP_TOKEN` — Bearer token for AI Platform MCP (or per-team tokens via config)

### Deployment (Azure)

- Azure App Service or Azure Container Instances
- Docker image based on existing Dockerfile
- Webhook URL: `https://<app>.azurewebsites.net/webhooks/bitbucket`
- Configure Bitbucket DC webhook to point to this URL
- Env vars in Azure App Service Configuration

### Resolved Decisions

1. **MCP Protocol:** Standard MCP protocol — agent connects as MCP client to dormakaba AI Platform MCP server using `mcp` Python SDK.
2. **Review trigger:** `@dkai` in PR **comments only** (not PR description).
3. **Re-review:** Full review every time `@dkai` is mentioned (no incremental logic).
4. **Output:** Single summary comment only — no inline comments on the diff (avoid PR clutter).
5. **Webhooks:** Bitbucket DC natively supports `Pull request > Comment added` event. No plugin needed.
6. **Rate limits:** No concerns for POC.
7. **Context depth:** Agent-driven exploration — review agents get direct MCP tool access to freely explore the repo, not just pre-fetched files. Reviews should assess the full feature context, project structure, and broader impact — not just changed lines.
8. **Jira:** Yes — add read-only Jira MCP access so agents can read the linked ticket for intent and acceptance criteria.

---

*This plan targets the POC (Phase 1) for implementation. Phases 2-6 are directional and will be detailed when we get there.*
