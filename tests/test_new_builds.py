import igprofiles
from devtools import check_new_builds

LISTING = "440.1.0.46.86, 446.0.0.49.77, 447.0.0.12.3, 447.0.0.40.1,\n448.0.0.1.2 garbage 39.0.0.0.0"


def test_only_majors_above_the_newest_validated_build_count() -> None:
    assert check_new_builds.newer_builds(LISTING, "446.0.0.49.77") == {
        447: "447.0.0.40.1",
        448: "448.0.0.1.2",
    }
    assert check_new_builds.newer_builds(LISTING, "448.0.0.1.2") == {}


def test_issue_body_lists_each_major_and_how_to_baseline_it() -> None:
    body: str = check_new_builds.issue_body({447: "447.0.0.40.1"}, "446.0.0.49.77")
    assert f"| 447 | `447.0.0.40.1` | `{igprofiles.covering(447)}` |" in body
    assert "new-profile baseline 447.0.0.40.1" in body
    assert check_new_builds.issue_body({}, "446.0.0.49.77") == ""
