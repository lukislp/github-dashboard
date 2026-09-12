import json

from app.domain.codec import overview_from_dict, overview_to_dict
from app.domain.models import RateLimit, RunStatus
from app.domain.overview import build_overview, classify_ci
from tests.fakes import NOW, make_repo, make_run


def test_overview_roundtrip_through_json():
    repos = [make_repo("a", prs=1, issues=2), make_repo("b", archived=True)]
    ci = {
        "octocat/a": classify_ci(
            [make_run(RunStatus.FAILURE), make_run(RunStatus.QUEUED, run_id=2)]
        )
    }
    original = build_overview(
        viewer_login="octocat",
        repositories=repos,
        ci_by_repo=ci,
        rate_limit=RateLimit(10, 5000, NOW),
        now=NOW,
    )

    restored = overview_from_dict(json.loads(json.dumps(overview_to_dict(original))))

    assert restored == original


def test_run_dict_exposes_derived_flags():
    payload = overview_to_dict(
        build_overview(
            viewer_login="o",
            repositories=[make_repo("a")],
            ci_by_repo={"octocat/a": classify_ci([make_run(RunStatus.TIMED_OUT)])},
            rate_limit=None,
            now=NOW,
        )
    )
    run = payload["repos"][0]["ci"]["runs"][0]
    assert run["failed"] is True
    assert run["active"] is False
    assert payload["rate_limit"] is None
