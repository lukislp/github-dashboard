from app.domain.hygiene import HYGIENE_KEYS, HygieneFacts, assess_hygiene


def _facts(**overrides) -> HygieneFacts:
    defaults = dict(
        has_readme=True,
        has_license=True,
        workflow_file_count=2,
        has_dependabot_config=True,
        has_renovate_config=False,
        branch_protection_rule_count=1,
        ruleset_count=0,
        vulnerability_alerts_enabled=True,
        delete_branch_on_merge=True,
        has_security_policy=True,
        has_codeowners=True,
        collaborator_count=2,
        is_archived=False,
        is_fork=False,
    )
    defaults.update(overrides)
    return HygieneFacts(**defaults)


def test_all_checks_pass():
    hygiene = assess_hygiene(_facts())
    assert hygiene.total == 9
    assert hygiene.passed == 9
    assert hygiene.score == 100
    assert hygiene.failing_keys == ()
    assert hygiene.applicable is True
    assert [c.key for c in hygiene.checks] == list(HYGIENE_KEYS)
    assert all(c.detail is None for c in hygiene.checks)


def test_all_checks_fail():
    hygiene = assess_hygiene(
        _facts(
            has_readme=False,
            has_license=False,
            workflow_file_count=0,
            has_dependabot_config=False,
            has_renovate_config=False,
            branch_protection_rule_count=0,
            ruleset_count=0,
            vulnerability_alerts_enabled=False,
            delete_branch_on_merge=False,
            has_security_policy=False,
            has_codeowners=False,
        )
    )
    assert hygiene.passed == 0
    assert hygiene.total == 9
    assert hygiene.score == 0
    assert hygiene.failing_keys == HYGIENE_KEYS
    assert all(c.detail is not None for c in hygiene.checks)


def test_partial_failure_rounds_score():
    # 8 of 9 pass -> 88.88..% rounds to 89.
    hygiene = assess_hygiene(_facts(has_codeowners=False))
    assert hygiene.passed == 8
    assert hygiene.score == 89
    assert hygiene.failing_keys == ("codeowners",)


def test_codeowners_check_is_absent_without_other_contributors():
    """A one-person repository is not asked for a CODEOWNERS file it cannot use."""
    hygiene = assess_hygiene(_facts(collaborator_count=1, has_codeowners=False))
    assert hygiene.total == 8
    assert "codeowners" not in [c.key for c in hygiene.checks]
    assert hygiene.failing_keys == ()
    assert hygiene.score == 100


def test_codeowners_check_applies_with_a_second_contributor():
    hygiene = assess_hygiene(_facts(collaborator_count=2, has_codeowners=False))
    assert hygiene.total == 9
    assert hygiene.failing_keys == ("codeowners",)


def test_codeowners_check_passes_with_a_second_contributor_and_a_file():
    hygiene = assess_hygiene(_facts(collaborator_count=2, has_codeowners=True))
    assert hygiene.total == 9
    assert hygiene.score == 100


def test_unknown_collaborator_count_omits_the_codeowners_check():
    """GitHub nulls `collaborators` without push access; never demand the file on a guess."""
    hygiene = assess_hygiene(_facts(collaborator_count=None, has_codeowners=False))
    assert hygiene.total == 8
    assert "codeowners" not in [c.key for c in hygiene.checks]


def test_dropping_codeowners_does_not_inflate_a_failing_repository():
    """The other eight checks keep their meaning: all failing is still a score of 0."""
    hygiene = assess_hygiene(
        _facts(
            collaborator_count=1,
            has_readme=False,
            has_license=False,
            workflow_file_count=0,
            has_dependabot_config=False,
            has_renovate_config=False,
            branch_protection_rule_count=0,
            ruleset_count=0,
            vulnerability_alerts_enabled=False,
            delete_branch_on_merge=False,
            has_security_policy=False,
            has_codeowners=False,
        )
    )
    assert hygiene.total == 8
    assert hygiene.passed == 0
    assert hygiene.score == 0
    assert hygiene.failing_keys == HYGIENE_KEYS[:-1]


def test_archived_repo_is_not_applicable_but_score_is_100():
    hygiene = assess_hygiene(_facts(has_readme=False, is_archived=True))
    assert hygiene.applicable is False
    assert hygiene.score == 100
    # Checks are still computed so the raw data is available if ever needed.
    assert hygiene.passed == 8
    assert "readme" in hygiene.failing_keys


def test_fork_is_not_applicable_but_score_is_100():
    hygiene = assess_hygiene(_facts(has_license=False, is_fork=True))
    assert hygiene.applicable is False
    assert hygiene.score == 100


def test_dependency_updates_ok_with_dependabot_only():
    hygiene = assess_hygiene(_facts(has_dependabot_config=True, has_renovate_config=False))
    assert "dependency_updates" not in hygiene.failing_keys


def test_dependency_updates_ok_with_renovate_only():
    hygiene = assess_hygiene(_facts(has_dependabot_config=False, has_renovate_config=True))
    assert "dependency_updates" not in hygiene.failing_keys


def test_dependency_updates_fails_with_neither():
    hygiene = assess_hygiene(_facts(has_dependabot_config=False, has_renovate_config=False))
    assert "dependency_updates" in hygiene.failing_keys


def test_branch_protection_ok_with_ruleset_only():
    hygiene = assess_hygiene(_facts(branch_protection_rule_count=0, ruleset_count=1))
    assert "branch_protection" not in hygiene.failing_keys


def test_branch_protection_fails_with_neither():
    hygiene = assess_hygiene(_facts(branch_protection_rule_count=0, ruleset_count=0))
    assert "branch_protection" in hygiene.failing_keys
