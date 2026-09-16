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
"""Every check key that can appear, in display order.

`codeowners` is conditional and only present for repositories other people can contribute
to; see `assess_hygiene`. Every other key is evaluated for every repository.
"""


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
    # People with access to the repository, as reported by GitHub. `None` means GitHub did
    # not tell us: the field needs push access and comes back nulled out otherwise.
    collaborator_count: int | None = None
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
        """0-100, over the checks that apply to this repository.

        `total` is not a constant: a check that cannot mean anything for a repository is
        left out of `checks` entirely, so it counts in neither the numerator nor the
        denominator. Always 100 for a repository the checks don't apply to (archived/fork).
        """
        if not self.applicable or not self.checks:
            return 100
        return round(100 * self.passed / self.total)

    @property
    def failing_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.checks if not c.ok)


def _codeowners_check_applies(facts: HygieneFacts) -> bool:
    """True when a CODEOWNERS file could actually do anything in this repository.

    A CODEOWNERS file has exactly one job: when *someone else* opens a pull request,
    GitHub automatically requests a review from the listed owners. GitHub never requests a
    review from the author of a pull request, so in a repository that only one person can
    contribute to, the file is inert - it would sit there and change nothing.

    This is deliberately NOT a way to make a one-person repository look better by dropping a
    check it happens to fail. The check was never meaningful there: demanding the file
    reported a problem that could not be fixed by anything except adding a file with no
    effect. The other eight checks still apply to every repository without exception, and
    this one comes back on its own as soon as a second person has access.

    A `None` count means GitHub did not report it (the underlying field needs push access).
    We cannot tell, so we do not demand the file.
    """
    return facts.collaborator_count is not None and facts.collaborator_count > 1


def assess_hygiene(facts: HygieneFacts) -> RepoHygiene:
    """Derive the hygiene checks from plain facts.

    Eight checks apply to every repository. The ninth, `codeowners`, is only added for
    repositories more than one person can contribute to - see `_codeowners_check_applies`.

    `applicable` is False for archived repositories and forks: the checks are still
    computed (so the data is there if ever needed) but the score is pinned to 100, letting
    the frontend grey the repository out instead of penalising it.
    """
    checks: list[HygieneCheck] = [
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
    ]
    if _codeowners_check_applies(facts):
        checks.append(
            HygieneCheck(
                "codeowners",
                facts.has_codeowners,
                None if facts.has_codeowners else "no CODEOWNERS file found",
            )
        )
    applicable = not (facts.is_archived or facts.is_fork)
    return RepoHygiene(tuple(checks), applicable=applicable)
