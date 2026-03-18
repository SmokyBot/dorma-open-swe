"""Execute review sub-agents in parallel.

Each sub-agent gets a role-specific system prompt, the PR diff, and MCP tools
to autonomously explore the codebase for deeper context.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from langchain.chat_models import init_chat_model

from .role_selector import ReviewRole

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    """A single review finding from a sub-agent."""

    severity: str  # "critical", "major", "minor"
    file_path: str
    line: int | None
    title: str
    description: str
    current_code: str = ""
    suggested_code: str = ""
    agent_role: str = ""

    @property
    def finding_id(self) -> str:
        """Generate a finding ID like C1, M1, m1."""
        prefix = {"critical": "C", "major": "M", "minor": "m"}.get(self.severity, "?")
        return f"{prefix}?"  # Actual numbering happens in output.py


@dataclass
class AgentResult:
    """Result from a single review sub-agent."""

    role: str
    findings: list[Finding] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    error: str | None = None


# System prompt template for review agents
REVIEW_AGENT_SYSTEM_PROMPT = """You are a senior code reviewer acting as: {role_name}

## Your Focus Areas
{focus_areas}

## Instructions
{instructions}

## Context
- **Project:** {project_key}/{repo_slug}
- **PR:** #{pr_id} — {pr_title}
- **Branch:** {source_branch} → {target_branch}
- **Tech Stack:** {tech_summary}
{jira_context}

## PR Description
{pr_description}

## The Diff
```diff
{diff_content}
```

## Your Task
Review the diff above as your designated role. For EACH finding:
1. Assess its severity: critical (bugs, security holes, data loss), major (logic errors, performance, bad patterns), or minor (style, naming, small improvements)
2. Reference the exact file and line number
3. Show the current problematic code and suggest a fix

Also note 2-3 specific positive things about the code.

## IMPORTANT: Exploring the Codebase
The diff alone may not be sufficient. You have access to the Bitbucket repository to fetch file contents and browse the directory structure. USE THESE to understand:
- How the changed code integrates with the rest of the codebase
- What functions/classes the changes depend on
- Whether there are patterns elsewhere that should be followed
- Whether the changes break any existing contracts or conventions

## Output Format
Respond with a structured JSON object (and nothing else) in this exact format:
```json
{{
  "findings": [
    {{
      "severity": "critical|major|minor",
      "file": "path/to/file.ext",
      "line": 42,
      "title": "Short title of the issue",
      "description": "One sentence explaining what's wrong and its impact.",
      "current_code": "// 3-5 lines of problematic code",
      "suggested_code": "// 3-5 lines of suggested fix"
    }}
  ],
  "positives": [
    "Specific positive observation about the code",
    "Another positive observation"
  ]
}}
```

If you find no issues, return an empty findings array with positives only.
Be precise, actionable, and honest. Do not invent issues that don't exist.
"""


async def execute_review_agents(
    roles: list[ReviewRole],
    diff_content: str,
    pr_context: dict,
    tech_summary: str,
    llm_provider: str,
    llm_model: str,
    jira_context: str = "",
) -> list[AgentResult]:
    """Execute all review sub-agents in parallel.

    Each agent gets the diff and a role-specific prompt. They independently
    analyze the code and return structured findings.
    """
    model_id = f"{llm_provider}:{llm_model}"
    logger.info("Executing %d review agents with model %s", len(roles), model_id)

    tasks = [
        _run_single_agent(
            role=role,
            diff_content=diff_content,
            pr_context=pr_context,
            tech_summary=tech_summary,
            model_id=model_id,
            jira_context=jira_context,
        )
        for role in roles
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    agent_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("Review agent '%s' failed: %s", roles[i].name, result)
            agent_results.append(AgentResult(
                role=roles[i].name,
                error=str(result),
            ))
        else:
            agent_results.append(result)

    return agent_results


async def _run_single_agent(
    role: ReviewRole,
    diff_content: str,
    pr_context: dict,
    tech_summary: str,
    model_id: str,
    jira_context: str = "",
) -> AgentResult:
    """Run a single review agent and parse its findings."""
    logger.info("Starting review agent: %s", role.name)

    # Build the prompt
    prompt = REVIEW_AGENT_SYSTEM_PROMPT.format(
        role_name=role.name,
        focus_areas="\n".join(f"- {area}" for area in role.focus_areas),
        instructions=role.instructions,
        project_key=pr_context.get("project_key", ""),
        repo_slug=pr_context.get("repo_slug", ""),
        pr_id=pr_context.get("pr_id", ""),
        pr_title=pr_context.get("pr_title", ""),
        source_branch=pr_context.get("source_branch", ""),
        target_branch=pr_context.get("target_branch", ""),
        tech_summary=tech_summary,
        pr_description=pr_context.get("pr_description", "No description provided."),
        diff_content=_truncate_diff(diff_content, max_lines=3000),
        jira_context=f"\n## Jira Ticket\n{jira_context}" if jira_context else "",
    )

    # Call the LLM
    model = init_chat_model(model=model_id, temperature=0, max_tokens=8000)
    response = await model.ainvoke([{"role": "user", "content": prompt}])

    # Parse the response
    return _parse_agent_response(response.content, role.name)


def _parse_agent_response(content: str, role_name: str) -> AgentResult:
    """Parse the structured JSON response from a review agent."""
    import json
    import re

    # Extract JSON from the response (may be wrapped in markdown code blocks)
    json_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON
        json_match = re.search(r"\{[\s\S]*\"findings\"[\s\S]*\}", content)
        if json_match:
            json_str = json_match.group(0)
        else:
            logger.warning("Could not parse JSON from agent '%s', raw content: %s", role_name, content[:500])
            return AgentResult(role=role_name, error="Failed to parse structured response")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON from agent '%s': %s", role_name, e)
        return AgentResult(role=role_name, error=f"Invalid JSON: {e}")

    findings = []
    for f in data.get("findings", []):
        findings.append(Finding(
            severity=f.get("severity", "minor").lower(),
            file_path=f.get("file", ""),
            line=f.get("line"),
            title=f.get("title", ""),
            description=f.get("description", ""),
            current_code=f.get("current_code", ""),
            suggested_code=f.get("suggested_code", ""),
            agent_role=role_name,
        ))

    positives = data.get("positives", [])

    logger.info(
        "Agent '%s' found %d findings (%d critical, %d major, %d minor) and %d positives",
        role_name,
        len(findings),
        sum(1 for f in findings if f.severity == "critical"),
        sum(1 for f in findings if f.severity == "major"),
        sum(1 for f in findings if f.severity == "minor"),
        len(positives),
    )

    return AgentResult(role=role_name, findings=findings, positives=positives)


def _truncate_diff(diff_content: str, max_lines: int = 3000) -> str:
    """Truncate diff to a maximum number of lines."""
    lines = diff_content.split("\n")
    if len(lines) <= max_lines:
        return diff_content
    truncated = "\n".join(lines[:max_lines])
    return f"{truncated}\n\n... (diff truncated at {max_lines} lines, {len(lines) - max_lines} lines omitted)"
