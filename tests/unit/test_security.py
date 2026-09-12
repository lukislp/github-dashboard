from app.domain.models import RepoSecurity, SeverityCounts


def test_severity_counts_total_sums_all_levels():
    counts = SeverityCounts(critical=1, high=2, moderate=3, low=4)
    assert counts.total == 10


def test_repo_security_total_is_none_for_everything_unavailable():
    security = RepoSecurity(None, None, None, None)
    assert security.total == 0
    assert security.has_critical is False


def test_repo_security_total_sums_available_parts_only():
    security = RepoSecurity(
        dependabot=SeverityCounts(1, 1, 0, 0),
        dependabot_total=2,
        code_scanning=SeverityCounts(0, 2, 1, 0),
        secret_scanning=3,
    )
    # 2 (dependabot severities) + 3 (code scanning severities) + 3 (secrets) = 8
    assert security.total == 8


def test_repo_security_total_ignores_unavailable_parts():
    security = RepoSecurity(
        dependabot=SeverityCounts(0, 1, 0, 0),
        dependabot_total=1,
        code_scanning=None,
        secret_scanning=None,
    )
    assert security.total == 1


def test_has_critical_true_for_dependabot_critical():
    security = RepoSecurity(SeverityCounts(1, 0, 0, 0), 1, None, None)
    assert security.has_critical is True


def test_has_critical_true_for_code_scanning_critical():
    security = RepoSecurity(None, None, SeverityCounts(1, 0, 0, 0), None)
    assert security.has_critical is True


def test_has_critical_true_for_any_secret_regardless_of_severity():
    security = RepoSecurity(None, None, None, 1)
    assert security.has_critical is True


def test_has_critical_false_without_critical_or_secrets():
    security = RepoSecurity(SeverityCounts(0, 5, 5, 5), 15, SeverityCounts(0, 1, 0, 0), 0)
    assert security.has_critical is False
