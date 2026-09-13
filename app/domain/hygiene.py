"""Repository hygiene scoring: pure checks over plain facts, no GraphQL shapes here."""

from __future__ import annotations

from dataclasses import dataclass

HYGIENE_KEYS: tuple[str, ...] = (
    "readme",
    "license",
    "ci_workflow",
    "dependency_updates",
    "branch_protection",
    "vulnerability_alerts",
    "delete_branch_on_merge",
    "security_policy",
    "codeowners",
)


@dataclass(frozen=True, slots=True)
class HygieneFacts:
    """Plain booleans/counts for one repository, collected by the GraphQL adapter."""

    has_readme: bool
    has_license: bool
    workflow_file_count: int
    has_dependabot_config: bool
    has_renovate_config: bool
    branch_protection_rule_count: int
    ruleset_count: int
    vulnerability_alerts_enabled: bool
    delete_branch_on_merge: bool
    has_security_policy: bool
    has_codeowners: bool
    is_archived: bool = False
    is_fork: bool = False


@dataclass(frozen=True, slots=True)
class HygieneCheck:
    key: str
    ok: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RepoHygiene:
    checks: tuple[HygieneCheck, ...]
    applicable: bool = True

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def score(self) -> int:
        """0-100. Always 100 for a repository the checks don't apply to (archived/fork)."""
        if not self.applicable or not self.checks:
            return 100
        return round(100 * self.passed / self.total)

    @property
    def failing_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.checks if not c.ok)


def assess_hygiene(facts: HygieneFacts) -> RepoHygiene:
    """Derive the nine hygiene checks from plain facts.

    `applicable` is False for archived repositories and forks: the checks are still
    computed (so the data is there if ever needed) but the score is pinned to 100, letting
    the frontend grey the repository out instead of penalising it.
    """
    checks = (
        HygieneCheck("readme", facts.has_readme, None if facts.has_readme else "no README.md"),
        HygieneCheck(
            "license", facts.has_license, None if facts.has_license else "no license detected"
        ),
        HygieneCheck(
            "ci_workflow",
            facts.workflow_file_count > 0,
            None if facts.workflow_file_count > 0 else "no workflow files in .github/workflows",
        ),
        HygieneCheck(
            "dependency_updates",
            facts.has_dependabot_config or facts.has_renovate_config,
            None
            if (facts.has_dependabot_config or facts.has_renovate_config)
            else "no Dependabot or Renovate configuration found",
        ),
        HygieneCheck(
            "branch_protection",
            facts.branch_protection_rule_count > 0 or facts.ruleset_count > 0,
            None
            if (facts.branch_protection_rule_count > 0 or facts.ruleset_count > 0)
            else "no branch protection rules or rulesets configured",
        ),
        HygieneCheck(
            "vulnerability_alerts",
            facts.vulnerability_alerts_enabled,
            None if facts.vulnerability_alerts_enabled else "Dependabot alerts are disabled",
        ),
        HygieneCheck(
            "delete_branch_on_merge",
            facts.delete_branch_on_merge,
            None
            if facts.delete_branch_on_merge
            else "merged branches are not deleted automatically",
        ),
        HygieneCheck(
            "security_policy",
            facts.has_security_policy,
            None if facts.has_security_policy else "no SECURITY.md found",
        ),
        HygieneCheck(
            "codeowners",
            facts.has_codeowners,
            None if facts.has_codeowners else "no CODEOWNERS file found",
        ),
    )
    applicable = not (facts.is_archived or facts.is_fork)
    return RepoHygiene(checks, applicable=applicable)
