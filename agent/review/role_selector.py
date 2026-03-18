"""Select review agent roles based on tech stack and change analysis.

Dynamically picks 2-6 review roles, always including the Architecture &
Integration Guardian. Roles are combined for smaller diffs and split for larger ones.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .tech_detector import TechProfile

logger = logging.getLogger(__name__)


@dataclass
class ReviewRole:
    """A review agent role specification."""

    name: str
    focus_areas: list[str]
    instructions: str
    relevance_score: float = 0.0  # 0.0–1.0, used for selection


# ── Role definitions ─────────────────────────────────────────────────

ARCHITECTURE_GUARDIAN = ReviewRole(
    name="Architecture & Integration Guardian",
    focus_areas=[
        "holistic solution quality",
        "integration with existing codebase",
        "no hacky workarounds",
        "best practices (web-researched)",
        "long-term maintainability",
    ],
    instructions=(
        "You are the Architecture & Integration Guardian. Your job is to assess whether "
        "this PR is a good holistic solution. Check how the changes integrate with the "
        "existing codebase. Look for hacky workarounds vs. proper long-term solutions. "
        "Research current best practices via web search if needed. Consider the overall "
        "design, separation of concerns, and whether the approach will scale. "
        "This is the MOST IMPORTANT review role."
    ),
)


def _build_role_catalog() -> list[ReviewRole]:
    """Build the full catalog of available review roles."""
    return [
        # Security
        ReviewRole(
            name="Security & Access Control Auditor",
            focus_areas=[
                "OWASP top 10", "injection attacks", "XSS/CSRF",
                "permission enforcement", "secrets exposure", "input validation",
            ],
            instructions=(
                "You are the Security & Access Control Auditor. Check for OWASP top 10 "
                "vulnerabilities: injection, XSS, CSRF, broken auth, security misconfig, "
                "sensitive data exposure. Verify ACL/permission checks, secrets handling, "
                "and input validation at system boundaries."
            ),
        ),
        # Performance
        ReviewRole(
            name="Performance & Scalability Analyst",
            focus_areas=[
                "N+1 queries", "unnecessary re-renders", "caching opportunities",
                "memory leaks", "bundle size", "algorithmic complexity",
            ],
            instructions=(
                "You are the Performance & Scalability Analyst. Look for N+1 queries, "
                "missing pagination, unnecessary re-renders, memory leaks, missing caching, "
                "and O(n^2) algorithms. Check bundle size impact for frontend changes. "
                "Consider database query efficiency and connection pooling."
            ),
        ),
        # Test quality
        ReviewRole(
            name="Test Quality Strategist",
            focus_areas=[
                "behavior-driven coverage", "edge cases", "mock anti-patterns",
                "test maintainability", "missing test scenarios",
            ],
            instructions=(
                "You are the Test Quality Strategist. Assess test coverage for the changes: "
                "are edge cases covered? Are there mock-to-pass anti-patterns? Check if tests "
                "verify behavior (not implementation). Identify missing test scenarios. "
                "Evaluate test naming and readability."
            ),
        ),
        # Error handling
        ReviewRole(
            name="Error Handling & Resilience Engineer",
            focus_areas=[
                "exception strategy", "graceful degradation", "logging quality",
                "retry logic", "circuit breakers", "timeout handling",
            ],
            instructions=(
                "You are the Error Handling & Resilience Engineer. Check exception handling "
                "strategy: are errors caught at the right level? Is there graceful degradation? "
                "Are errors logged with sufficient context? Check for swallowed exceptions, "
                "missing timeouts, and retry logic."
            ),
        ),
        # Backend framework
        ReviewRole(
            name="Backend Framework Expert",
            focus_areas=[
                "framework-native patterns", "anti-patterns", "dependency injection",
                "transaction management", "configuration best practices",
            ],
            instructions=(
                "You are the Backend Framework Expert. Check that the code follows "
                "framework-native patterns and conventions. Look for anti-patterns, "
                "misuse of framework features, and missed opportunities to use built-in "
                "capabilities. Verify DI patterns, transaction boundaries, and config."
            ),
        ),
        # Database
        ReviewRole(
            name="Database & Migration Specialist",
            focus_areas=[
                "schema changes", "migration safety", "indexing", "query efficiency",
                "data integrity constraints",
            ],
            instructions=(
                "You are the Database & Migration Specialist. Review schema changes for "
                "safety (backwards compatible?), indexing strategy, query efficiency, "
                "and data integrity. Check migration scripts for rollback support. "
                "Verify foreign key constraints and cascade behavior."
            ),
        ),
        # API design
        ReviewRole(
            name="API Design & Contract Reviewer",
            focus_areas=[
                "REST conventions", "DTOs/contracts", "breaking changes",
                "versioning", "documentation", "error responses",
            ],
            instructions=(
                "You are the API Design & Contract Reviewer. Check REST conventions, "
                "proper HTTP status codes, consistent naming, and clean DTOs. "
                "Flag breaking changes. Verify error response formats and API documentation."
            ),
        ),
        # Frontend framework
        ReviewRole(
            name="Frontend Framework Expert",
            focus_areas=[
                "component patterns", "state management", "hooks/signals",
                "rendering optimization", "SSR considerations",
            ],
            instructions=(
                "You are the Frontend Framework Expert. Check component patterns, "
                "state management (hooks, signals, stores), rendering optimization, "
                "and SSR considerations. Look for memory leaks in subscriptions, "
                "missing cleanup, and proper lifecycle management."
            ),
        ),
        # UI/UX
        ReviewRole(
            name="UI/UX & Accessibility Auditor",
            focus_areas=[
                "WCAG compliance", "keyboard navigation", "responsive design",
                "design system adherence", "semantic HTML",
            ],
            instructions=(
                "You are the UI/UX & Accessibility Auditor. Check WCAG compliance, "
                "keyboard navigation, ARIA attributes, semantic HTML, and responsive design. "
                "Verify design system adherence and consistent UX patterns."
            ),
        ),
        # Code quality
        ReviewRole(
            name="Code Quality & Maintainability Analyst",
            focus_areas=[
                "naming", "DRY principle", "dead code", "typing",
                "code complexity", "documentation",
            ],
            instructions=(
                "You are the Code Quality & Maintainability Analyst. Check naming conventions, "
                "DRY principle adherence, dead code, type safety, and cyclomatic complexity. "
                "Flag unclear logic that needs comments. Check for code smells."
            ),
        ),
        # Auth specialist
        ReviewRole(
            name="Authentication & Authorization Specialist",
            focus_areas=[
                "OAuth/OIDC flows", "token handling", "session management",
                "RBAC/ABAC", "SSO integration",
            ],
            instructions=(
                "You are the Authentication & Authorization Specialist. Review OAuth/OIDC "
                "flows, token handling, session management, and RBAC enforcement. Check for "
                "token leaks, improper storage, missing validation, and authorization bypasses."
            ),
        ),
        # DevOps
        ReviewRole(
            name="DevOps & Infrastructure Reviewer",
            focus_areas=[
                "Docker configuration", "CI/CD pipeline", "env vars",
                "deployment safety", "infrastructure as code",
            ],
            instructions=(
                "You are the DevOps & Infrastructure Reviewer. Check Docker config, "
                "CI/CD pipelines, environment variable handling, and deployment safety. "
                "Verify no secrets in code, no 0.0.0.0 bindings, and proper health checks."
            ),
        ),
        # Concurrency
        ReviewRole(
            name="Concurrency & Async Patterns Reviewer",
            focus_areas=[
                "thread safety", "race conditions", "async boundaries",
                "deadlocks", "connection pool management",
            ],
            instructions=(
                "You are the Concurrency & Async Patterns Reviewer. Check for thread safety "
                "issues, race conditions, improper async usage, deadlock potential, and "
                "connection/resource pool management. Verify proper synchronization."
            ),
        ),
        # AI/LLM
        ReviewRole(
            name="AI & LLM Integration Specialist",
            focus_areas=[
                "prompt engineering", "LLM API usage", "RAG patterns",
                "token management", "agent architecture",
            ],
            instructions=(
                "You are the AI & LLM Integration Specialist. Review LLM API usage, "
                "prompt engineering quality, RAG patterns, token/cost management, "
                "and agent architecture. Check for prompt injection vulnerabilities."
            ),
        ),
    ]


def select_roles(
    tech_profile: TechProfile,
    diff_line_count: int,
    custom_instructions: str = "",
    min_agents: int = 2,
    max_agents: int = 6,
) -> list[ReviewRole]:
    """Select review roles based on tech profile and change characteristics.

    Always includes Architecture & Integration Guardian.
    Returns 2-6 roles with combined roles for smaller changes.
    """
    catalog = _build_role_catalog()

    # Score each role by relevance
    for role in catalog:
        role.relevance_score = _score_role(role, tech_profile)

    # Sort by relevance (highest first) and filter zero-relevance
    relevant = [r for r in catalog if r.relevance_score > 0]
    relevant.sort(key=lambda r: r.relevance_score, reverse=True)

    # Determine target count based on diff size
    if diff_line_count < 50:
        target = min_agents
    elif diff_line_count < 200:
        target = min(3, max_agents)
    elif diff_line_count < 500:
        target = min(4, max_agents)
    elif diff_line_count < 1000:
        target = min(5, max_agents)
    else:
        target = max_agents

    # Always start with Architecture Guardian
    selected = [ARCHITECTURE_GUARDIAN]

    # For small diffs, combine top roles into fewer agents
    remaining_slots = target - 1  # -1 for architecture guardian
    if diff_line_count < 100 and len(relevant) > remaining_slots:
        # Combine top roles into fewer, multi-role agents
        combined = _combine_roles(relevant[:remaining_slots * 2], remaining_slots)
        selected.extend(combined)
    else:
        selected.extend(relevant[:remaining_slots])

    # Append custom instructions to all roles if provided
    if custom_instructions:
        for role in selected:
            role.instructions += f"\n\nAdditional team instructions: {custom_instructions}"

    logger.info(
        "Selected %d review roles for %d-line diff: %s",
        len(selected),
        diff_line_count,
        [r.name for r in selected],
    )

    return selected


def _score_role(role: ReviewRole, profile: TechProfile) -> float:
    """Score a role's relevance to the tech profile (0.0–1.0)."""
    score = 0.0

    # Role-specific scoring
    name = role.name.lower()

    if "security" in name:
        score += 0.3  # Always somewhat relevant
        if "auth/security" in profile.change_categories:
            score += 0.5
        if profile.has_api_definitions:
            score += 0.2

    elif "performance" in name:
        score += 0.2
        if profile.has_backend:
            score += 0.3
        if profile.has_frontend:
            score += 0.2
        if profile.has_database_migrations:
            score += 0.3

    elif "test" in name:
        if profile.has_tests:
            score += 0.8
        else:
            score += 0.3  # Missing tests is worth flagging

    elif "error" in name:
        if profile.has_backend:
            score += 0.4
        if profile.has_api_definitions:
            score += 0.3

    elif "backend framework" in name:
        if profile.has_backend:
            score += 0.7
        if any(f in profile.frameworks for f in ["Spring Boot", "Django", "FastAPI", "NestJS", "Express"]):
            score += 0.3

    elif "database" in name:
        if profile.has_database_migrations:
            score += 0.9
        if "database" in profile.change_categories:
            score += 0.3

    elif "api design" in name:
        if profile.has_api_definitions:
            score += 0.7
        if "API" in profile.change_categories:
            score += 0.3

    elif "frontend framework" in name:
        if profile.has_frontend:
            score += 0.7
        if any(f in profile.frameworks for f in ["Angular", "React", "Vue", "Svelte", "Next.js"]):
            score += 0.3

    elif "ui/ux" in name or "accessibility" in name:
        if profile.has_frontend:
            score += 0.5
        if "frontend" in profile.change_categories:
            score += 0.3

    elif "code quality" in name:
        score += 0.3  # Always somewhat relevant

    elif "authentication" in name:
        if "auth/security" in profile.change_categories:
            score += 0.9
        else:
            score += 0.1

    elif "devops" in name:
        if profile.has_ci_cd or profile.has_docker:
            score += 0.8
        if "CI/CD" in profile.change_categories:
            score += 0.3

    elif "concurrency" in name:
        if profile.has_backend:
            score += 0.2
        if any(f in profile.frameworks for f in ["Spring Boot", "FastAPI"]):
            score += 0.2

    elif "ai" in name or "llm" in name:
        if any(f in profile.frameworks for f in ["LangChain", "Mastra"]):
            score += 0.9

    return min(score, 1.0)


def _combine_roles(roles: list[ReviewRole], target_count: int) -> list[ReviewRole]:
    """Combine multiple roles into fewer multi-role agents."""
    if len(roles) <= target_count:
        return roles

    combined = []
    chunk_size = max(1, len(roles) // target_count)

    for i in range(0, len(roles), chunk_size):
        chunk = roles[i:i + chunk_size]
        if len(combined) >= target_count:
            # Merge remaining into last agent
            combined[-1] = _merge_roles(combined[-1], chunk[0])
            continue

        if len(chunk) == 1:
            combined.append(chunk[0])
        else:
            combined.append(_merge_roles(*chunk))

    return combined[:target_count]


def _merge_roles(role_a: ReviewRole, role_b: ReviewRole) -> ReviewRole:
    """Merge two roles into one combined role."""
    return ReviewRole(
        name=f"{role_a.name} + {role_b.name}",
        focus_areas=role_a.focus_areas + role_b.focus_areas,
        instructions=f"{role_a.instructions}\n\nADDITIONALLY: {role_b.instructions}",
        relevance_score=max(role_a.relevance_score, role_b.relevance_score),
    )
