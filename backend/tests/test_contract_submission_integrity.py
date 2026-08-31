from app.services.execution import contract_submission_drop_issues


def test_contract_submission_drop_is_reported_when_no_outcome_exists() -> None:
    issues = contract_submission_drop_issues(
        ["data/daily.csv", "data/monthly.csv"],
        ["data/daily.csv"],
        [],
    )

    assert issues == [
        {
            "code": "CONTRACT_SUBMISSION_DROPPED",
            "artifact_path": "data/monthly.csv",
            "message": (
                "submitted artifact contract produced neither an acceptance nor a validation "
                "issue"
            ),
        }
    ]


def test_contract_submission_with_rejection_is_not_marked_dropped() -> None:
    issues = contract_submission_drop_issues(
        ["data/daily.csv"],
        [],
        [{"code": "ARTIFACT_CONTRACT_INVALID", "artifact_path": "data/daily.csv"}],
    )

    assert issues == []

