"""Auto-detect tech stack from PR diff and repository structure.

Analyzes changed file extensions, config files, and code patterns to build
a TechProfile used by the role selector.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# File extension → language mapping
EXTENSION_MAP: dict[str, str] = {
    ".java": "Java",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".groovy": "Groovy",
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".less": "Less",
    ".sql": "SQL",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".tf": "Terraform",
    ".hcl": "HCL",
    ".proto": "Protobuf",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
}

# Config file → framework/tool detection
CONFIG_FILE_PATTERNS: dict[str, list[str]] = {
    # Java / JVM
    "pom.xml": ["Maven"],
    "build.gradle": ["Gradle"],
    "build.gradle.kts": ["Gradle"],
    # JavaScript / TypeScript
    "package.json": ["Node.js"],
    "angular.json": ["Angular"],
    "next.config": ["Next.js"],
    "nuxt.config": ["Nuxt"],
    "vite.config": ["Vite"],
    "webpack.config": ["Webpack"],
    "tsconfig.json": ["TypeScript"],
    ".eslintrc": ["ESLint"],
    # Python
    "pyproject.toml": ["Python"],
    "setup.py": ["Python"],
    "requirements.txt": ["Python"],
    "Pipfile": ["Pipenv"],
    # Go
    "go.mod": ["Go"],
    # Rust
    "Cargo.toml": ["Rust"],
    # .NET
    ".csproj": [".NET"],
    ".sln": [".NET"],
    # DevOps / CI
    "Dockerfile": ["Docker"],
    "docker-compose": ["Docker Compose"],
    "Jenkinsfile": ["Jenkins"],
    ".github/workflows": ["GitHub Actions"],
    "azure-pipelines": ["Azure DevOps"],
    "bitbucket-pipelines": ["Bitbucket Pipelines"],
    # Database
    "liquibase": ["Liquibase"],
    "flyway": ["Flyway"],
    "prisma/schema.prisma": ["Prisma"],
    # IaC
    "terraform": ["Terraform"],
    "helm": ["Helm"],
}

# Code pattern → framework detection (applied to diff content)
CODE_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "Java": [
        (r"@SpringBootApplication|@RestController|@Service|@Repository", "Spring Boot"),
        (r"@Entity|@Table|@Column|@ManyToOne", "JPA/Hibernate"),
        (r"@GetMapping|@PostMapping|@RequestMapping", "Spring MVC"),
        (r"@Autowired|@Inject|@Bean", "Spring DI"),
        (r"import\s+de\.hybris|import\s+com\.sap", "SAP Hybris"),
        (r"@WebServlet|HttpServlet", "Java Servlet"),
    ],
    "TypeScript": [
        (r"@Component|@Injectable|@NgModule|@Pipe", "Angular"),
        (r"from\s+['\"]react['\"]|useState|useEffect", "React"),
        (r"from\s+['\"]@ngrx", "NgRx"),
        (r"from\s+['\"]@spartacus", "Spartacus"),
        (r"from\s+['\"]next", "Next.js"),
        (r"from\s+['\"]express['\"]", "Express"),
        (r"from\s+['\"]@nestjs", "NestJS"),
        (r"tRPC|createTRPCRouter", "tRPC"),
    ],
    "Python": [
        (r"from\s+fastapi|from\s+starlette", "FastAPI"),
        (r"from\s+django", "Django"),
        (r"from\s+flask", "Flask"),
        (r"from\s+langchain|from\s+langgraph", "LangChain"),
        (r"from\s+mastra", "Mastra"),
    ],
}


@dataclass
class TechProfile:
    """Detected technology profile for a project."""

    languages: dict[str, int] = field(default_factory=dict)  # language → file count
    frameworks: list[str] = field(default_factory=list)
    build_tools: list[str] = field(default_factory=list)
    has_tests: bool = False
    has_ci_cd: bool = False
    has_docker: bool = False
    has_database_migrations: bool = False
    has_api_definitions: bool = False
    has_frontend: bool = False
    has_backend: bool = False
    change_categories: list[str] = field(default_factory=list)  # e.g., "API", "UI", "config"

    @property
    def primary_language(self) -> str:
        """Most common language in the changes."""
        if not self.languages:
            return "Unknown"
        return max(self.languages, key=self.languages.get)

    @property
    def summary(self) -> str:
        """Human-readable tech stack summary."""
        parts = []
        if self.primary_language != "Unknown":
            parts.append(self.primary_language)
        if self.frameworks:
            parts.append(" + ".join(self.frameworks[:3]))
        if self.build_tools:
            parts.append(f"built with {', '.join(self.build_tools[:2])}")
        return " | ".join(parts) if parts else "Unknown tech stack"


def detect_tech_stack(
    changed_files: list[str],
    diff_content: str,
    repo_files: list[str] | None = None,
) -> TechProfile:
    """Detect the tech stack from changed files and diff content.

    Args:
        changed_files: List of file paths changed in the PR.
        diff_content: The full diff text.
        repo_files: Optional list of root-level files in the repo (from browse_repository).
    """
    profile = TechProfile()

    # 1. Detect languages from file extensions
    for file_path in changed_files:
        ext = _get_extension(file_path)
        lang = EXTENSION_MAP.get(ext)
        if lang:
            profile.languages[lang] = profile.languages.get(lang, 0) + 1

        # Categorize changes
        _categorize_file(file_path, profile)

    # 2. Detect frameworks from config files
    all_files = set(changed_files)
    if repo_files:
        all_files.update(repo_files)

    for file_path in all_files:
        basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
        for pattern, tools in CONFIG_FILE_PATTERNS.items():
            if pattern in file_path or basename.startswith(pattern):
                for tool in tools:
                    if tool not in profile.build_tools and tool not in profile.frameworks:
                        profile.build_tools.append(tool)

    # 3. Detect frameworks from code patterns in the diff
    for lang, patterns in CODE_PATTERNS.items():
        if lang in profile.languages:
            for regex, framework in patterns:
                if re.search(regex, diff_content) and framework not in profile.frameworks:
                    profile.frameworks.append(framework)

    # 4. Detect cross-cutting concerns
    _detect_cross_cutting(changed_files, diff_content, profile)

    return profile


def extract_changed_files_from_diff(diff_content: str) -> list[str]:
    """Extract list of changed file paths from a unified diff."""
    files = []
    for line in diff_content.split("\n"):
        # Match "diff --git a/path b/path" or "+++ b/path" or "--- a/path"
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                files.append(match.group(1))
        elif line.startswith("+++ b/"):
            path = line[6:]
            if path and path not in files:
                files.append(path)
    return files


def _get_extension(file_path: str) -> str:
    """Get the file extension (lowercase)."""
    dot_pos = file_path.rfind(".")
    if dot_pos == -1:
        return ""
    return file_path[dot_pos:].lower()


def _categorize_file(file_path: str, profile: TechProfile) -> None:
    """Categorize a file into change categories and set flags."""
    lower = file_path.lower()

    if any(p in lower for p in ["test/", "tests/", "spec/", "__tests__", ".test.", ".spec."]):
        profile.has_tests = True
        if "test" not in profile.change_categories:
            profile.change_categories.append("test")

    if any(p in lower for p in [".github/", "jenkinsfile", "pipeline", "ci/", ".gitlab-ci"]):
        profile.has_ci_cd = True
        if "CI/CD" not in profile.change_categories:
            profile.change_categories.append("CI/CD")

    if any(p in lower for p in ["dockerfile", "docker-compose", ".dockerignore"]):
        profile.has_docker = True

    if any(p in lower for p in ["migration", "liquibase", "flyway", "impex", "prisma"]):
        profile.has_database_migrations = True
        if "database" not in profile.change_categories:
            profile.change_categories.append("database")

    if any(p in lower for p in [
        "openapi", "swagger", ".graphql", ".proto", "api/", "controller", "resource",
    ]):
        profile.has_api_definitions = True
        if "API" not in profile.change_categories:
            profile.change_categories.append("API")

    if any(ext in lower for ext in [".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss"]):
        profile.has_frontend = True
        if "frontend" not in profile.change_categories:
            profile.change_categories.append("frontend")

    if any(p in lower for p in [
        "service", "repository", "controller", "handler", "server", "api/",
    ]) and any(ext in lower for ext in [".java", ".py", ".go", ".ts", ".cs"]):
        profile.has_backend = True
        if "backend" not in profile.change_categories:
            profile.change_categories.append("backend")


def _detect_cross_cutting(
    changed_files: list[str], diff_content: str, profile: TechProfile
) -> None:
    """Detect cross-cutting concerns from the diff."""
    # Auth-related changes
    auth_patterns = [
        r"auth", r"oauth", r"jwt", r"token", r"login", r"session",
        r"permission", r"role", r"acl", r"security",
    ]
    for pattern in auth_patterns:
        if re.search(pattern, diff_content, re.IGNORECASE):
            if "auth/security" not in profile.change_categories:
                profile.change_categories.append("auth/security")
            break

    # Config/env changes
    if any(f.endswith((".env", ".env.example", ".properties", "application.yml"))
           for f in changed_files):
        if "config" not in profile.change_categories:
            profile.change_categories.append("config")
