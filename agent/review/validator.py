"""Validate and deduplicate findings from multiple review agents.

Merges findings from parallel agents, removes duplicates, and assigns
final severity and finding IDs.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from .executor import AgentResult, Finding

logger = logging.getLogger(__name__)

# Similarity threshold for deduplication (0.0–1.0)
SIMILARITY_THRESHOLD = 0.6


def validate_and_deduplicate(
    agent_results: list[AgentResult],
) -> tuple[list[Finding], list[str]]:
    """Merge, deduplicate, and validate findings from all agents.

    Returns:
        Tuple of (deduplicated findings sorted by severity, merged positives).
    """
    all_findings: list[Finding] = []
    all_positives: list[str] = []

    for result in agent_results:
        if result.error:
            logger.warning("Skipping errored agent '%s': %s", result.role, result.error)
            continue
        all_findings.extend(result.findings)
        all_positives.extend(result.positives)

    logger.info("Total raw findings: %d from %d agents", len(all_findings), len(agent_results))

    # Deduplicate findings
    deduped = _deduplicate(all_findings)
    logger.info("After dedup: %d findings", len(deduped))

    # Sort: critical first, then major, then minor; within same severity by file
    severity_order = {"critical": 0, "major": 1, "minor": 2}
    deduped.sort(key=lambda f: (severity_order.get(f.severity, 3), f.file_path, f.line or 0))

    # Deduplicate positives
    unique_positives = _deduplicate_strings(all_positives)

    return deduped, unique_positives


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    """Remove duplicate or near-duplicate findings."""
    if not findings:
        return []

    kept: list[Finding] = []

    for finding in findings:
        is_duplicate = False
        for existing in kept:
            if _is_duplicate(finding, existing):
                is_duplicate = True
                # If the new finding has higher severity, replace
                severity_rank = {"critical": 0, "major": 1, "minor": 2}
                if severity_rank.get(finding.severity, 3) < severity_rank.get(existing.severity, 3):
                    kept.remove(existing)
                    kept.append(finding)
                break

        if not is_duplicate:
            kept.append(finding)

    return kept


def _is_duplicate(a: Finding, b: Finding) -> bool:
    """Check if two findings are duplicates or near-duplicates."""
    # Same file and close line numbers
    if a.file_path != b.file_path:
        return False

    # Check line proximity (within 5 lines)
    if a.line is not None and b.line is not None:
        if abs(a.line - b.line) > 5:
            return False

    # Check title/description similarity
    title_sim = _similarity(a.title, b.title)
    desc_sim = _similarity(a.description, b.description)

    return title_sim > SIMILARITY_THRESHOLD or desc_sim > SIMILARITY_THRESHOLD


def _similarity(a: str, b: str) -> float:
    """Calculate string similarity ratio (0.0–1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _deduplicate_strings(items: list[str]) -> list[str]:
    """Deduplicate a list of strings, keeping unique ones."""
    seen: list[str] = []
    for item in items:
        if not any(_similarity(item, s) > 0.7 for s in seen):
            seen.append(item)
    return seen
