"""Format review findings into Bitbucket-compatible markdown.

Produces a single summary comment in CommonMark format.
Bitbucket Server supports: headings, bold, italic, strikethrough, fenced code blocks,
tables, blockquotes, links, lists, horizontal rules.
NOT supported: checkboxes, collapsible details, HTML tags, emojis.
"""

from __future__ import annotations

import logging

from .executor import Finding

logger = logging.getLogger(__name__)


def determine_verdict(findings: list[Finding]) -> str:
    """Determine the review verdict based on findings."""
    critical_count = sum(1 for f in findings if f.severity == "critical")
    major_count = sum(1 for f in findings if f.severity == "major")

    if critical_count > 0:
        return "REQUEST CHANGES"
    if major_count > 0:
        return "APPROVE WITH COMMENTS"
    return "APPROVE"


def format_summary_comment(
    findings: list[Finding],
    positives: list[str],
    pr_context: dict,
    file_count: int,
) -> str:
    """Format all findings into a single summary comment.

    Uses the Bitbucket-compatible markdown format from the review skill spec.
    Since we're not posting inline comments, Critical/Major findings include
    code blocks directly in the summary.
    """
    # Assign finding IDs
    critical_findings = [f for f in findings if f.severity == "critical"]
    major_findings = [f for f in findings if f.severity == "major"]
    minor_findings = [f for f in findings if f.severity == "minor"]

    verdict = determine_verdict(findings)

    # Extract ticket from Jira tickets list
    jira_tickets = pr_context.get("jira_tickets", [])
    ticket = jira_tickets[0] if jira_tickets else ""

    source = pr_context.get("source_branch", "")
    target = pr_context.get("target_branch", "")
    pr_title = pr_context.get("pr_title", "")

    # Header
    title_part = f"{ticket} --- " if ticket else ""
    lines = [
        f"## Review: {title_part}{pr_title}",
        f"`{source}` > `{target}` | {file_count} files | **{verdict}**",
        "",
    ]

    # Summary sentence
    if not findings:
        lines.append("Clean PR --- no issues found. Well done!")
    elif critical_findings:
        lines.append(
            f"Found {len(critical_findings)} critical and {len(major_findings)} major "
            f"issue(s) that should be addressed before merging."
        )
    elif major_findings:
        lines.append(
            f"Found {len(major_findings)} major issue(s) worth addressing. "
            f"Overall solid implementation."
        )
    else:
        lines.append(
            f"Found {len(minor_findings)} minor suggestion(s). Good to merge."
        )

    lines.append("")
    lines.append("---")

    # Critical findings (with code blocks)
    if critical_findings:
        lines.append("")
        lines.append(f"### Critical ({len(critical_findings)})")
        lines.append("")
        for i, f in enumerate(critical_findings, 1):
            lines.extend(_format_finding_detail(f"C{i}", f))

    # Major findings (with code blocks)
    if major_findings:
        lines.append("")
        lines.append(f"### Major ({len(major_findings)})")
        lines.append("")
        for i, f in enumerate(major_findings, 1):
            lines.extend(_format_finding_detail(f"M{i}", f))

    # Minor findings (table only, no code)
    if minor_findings:
        lines.append("")
        lines.append(f"### Minor ({len(minor_findings)})")
        lines.append("")
        lines.append("| # | File | Finding |")
        lines.append("|---|------|---------|")
        for i, f in enumerate(minor_findings, 1):
            file_ref = f"`{f.file_path}:{f.line}`" if f.line else f"`{f.file_path}`"
            lines.append(f"| m{i} | {file_ref} | {f.title} --- {f.description} |")

    # Positives (always included)
    if positives:
        lines.append("")
        lines.append("### Good")
        lines.append("")
        for p in positives[:5]:  # Cap at 5
            lines.append(f"- {p}")

    # Actions line
    actions = []
    for i, f in enumerate(critical_findings, 1):
        actions.append(f"C{i}: {_action_verb(f)}")
    for i, f in enumerate(major_findings, 1):
        actions.append(f"M{i}: {_action_verb(f)}")

    if actions:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"**Actions:** {' | '.join(actions)}")

    # Signature
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated with DK AI Platform (ai-platform.dormakaba.net)*")

    return "\n".join(lines)


def _format_finding_detail(finding_id: str, finding: Finding) -> list[str]:
    """Format a single Critical or Major finding with code blocks."""
    lines = []
    file_ref = f"`{finding.file_path}:{finding.line}`" if finding.line else f"`{finding.file_path}`"

    lines.append(f"**{finding_id}. {finding.title}** {file_ref} _{finding.agent_role}_")
    lines.append("")
    lines.append(finding.description)

    # Detect language for syntax highlighting
    lang = _detect_lang(finding.file_path)

    if finding.current_code:
        lines.append("")
        lines.append(f"```{lang}")
        lines.append("// current")
        lines.append(finding.current_code)
        lines.append("```")

    if finding.suggested_code:
        lines.append(f"```{lang}")
        lines.append("// suggested")
        lines.append(finding.suggested_code)
        lines.append("```")

    lines.append("")
    lines.append("---")
    lines.append("")

    return lines


def _action_verb(finding: Finding) -> str:
    """Generate a short action verb for a finding."""
    title = finding.title.lower()
    if any(w in title for w in ["missing", "add", "lacks"]):
        return f"add {_shorten(finding.title)}"
    if any(w in title for w in ["remove", "unused", "dead"]):
        return f"remove {_shorten(finding.title)}"
    if any(w in title for w in ["fix", "bug", "error", "incorrect"]):
        return f"fix {_shorten(finding.title)}"
    return f"address {_shorten(finding.title)}"


def _shorten(text: str, max_len: int = 40) -> str:
    """Shorten text for action line."""
    if len(text) <= max_len:
        return text.lower()
    return text[:max_len].rsplit(" ", 1)[0].lower() + "..."


def _detect_lang(file_path: str) -> str:
    """Detect syntax highlighting language from file extension."""
    ext_map = {
        ".java": "java",
        ".kt": "kotlin",
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".sql": "sql",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".vue": "vue",
        ".svelte": "svelte",
    }
    for ext, lang in ext_map.items():
        if file_path.endswith(ext):
            return lang
    return ""
