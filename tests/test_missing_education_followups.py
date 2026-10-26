"""Follow-up rounds preserve prior research without inheriting approvals."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.review import missing_education as module
from apemap.review.evidence import EvidenceRecord, append_evidence, evidence_revision
from apemap.review.research_batches import ResearchBatches
from apemap.review.store import StaleReviewError
from tests.test_review_service import review_service as review_service
from tests.test_missing_education_batches import (
    missing_batches as missing_batches,
    result,
)


@pytest.fixture
def followup_parent(
    missing_batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> ResearchBatches:
    path = missing_batches.path("missing")
    manifest = json.loads((path / "manifest.json").read_bytes())
    manifest["parliament"] = 48
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    proposal = missing_batches.import_results("missing", [result(positive=False)])[
        "proposals"
    ][0]
    missing_batches.record_review(
        "missing",
        proposal["proposal_id"],
        "research",
        {item["evidence_id"]: item["stance"] for item in proposal["evidence"]},
        reviewer="Human reviewer",
        notes="Research this missing attendance further.",
    )
    monkeypatch.setattr(
        module,
        "project_missing_education_scope",
        lambda *args: [
            {
                "member_id": "aph-test",
                "aph_id": "TEST",
                "display_name": "Test Person",
                "status": "needs_research",
                "services": [],
                "secondary_education": [],
            },
            {
                "member_id": "aph-home",
                "aph_id": "HOME",
                "display_name": "Home Person",
                "status": "resolved_skip",
                "reason": "Reviewed home education.",
                "services": [],
                "secondary_education": [],
            },
        ],
    )
    return missing_batches


def prepare(batches: ResearchBatches, count: int = 1) -> dict[str, Any]:
    return module.prepare_missing_education_followup(
        batches, "round-two", "missing", expected_count=count
    )


def test_followup_freezes_lineage_history_and_budget_without_authority(
    followup_parent: ResearchBatches,
) -> None:
    parent_path = followup_parent.path("missing")
    before = {path.name: path.read_bytes() for path in parent_path.glob("*.json*")}
    decisions = (
        followup_parent.service.log_path.read_bytes()
        if followup_parent.service.log_path.exists()
        else None
    )
    prepared = prepare(followup_parent)
    child = followup_parent.show("round-two")
    assert child["manifest"]["case_count"] == 1
    assert child["manifest"]["skipped_count"] == 1
    assert child["manifest"]["parent"]["batch_id"] == "missing"
    assert (
        child["tasks"][0]["prior_research"]["proposal"]["research_log"]["searches"] == 1
    )
    assert child["tasks"][0]["research_budget"]["searches"] == 5
    assert child["tasks"][0]["research_budget"]["source_opens"] == 10
    assert not child["reviews"] and not child["applications"]
    assert (
        prepared["manifest"]["decision_revision"]
        == followup_parent.service.review_revision()
    )
    assert before == {
        path.name: path.read_bytes() for path in parent_path.glob("*.json*")
    }
    assert not followup_parent.service.evidence_path.exists()
    assert decisions == (
        followup_parent.service.log_path.read_bytes()
        if followup_parent.service.log_path.exists()
        else None
    )


def test_followup_includes_immutable_retained_claims_without_rewriting_them(
    followup_parent: ResearchBatches,
) -> None:
    record = EvidenceRecord.from_dict(result()["evidence"][0])
    path = followup_parent.service.evidence_path
    append_evidence(path, [record], expected_revision=evidence_revision(path))
    before = path.read_bytes()
    prepared = prepare(followup_parent)
    assert prepared["tasks"][0]["context"]["retained_evidence"] == [record.to_dict()]
    assert path.read_bytes() == before


@pytest.mark.parametrize("action", ["accept", "reject", None])
def test_followup_excludes_cases_not_explicitly_marked_for_research(
    followup_parent: ResearchBatches,
    monkeypatch: pytest.MonkeyPatch,
    action: str | None,
) -> None:
    state = followup_parent.show("missing")
    proposal_id = state["proposals"][0]["proposal_id"]
    if action is None:
        state["reviews"] = {}
    else:
        state["reviews"][proposal_id]["action"] = action
    monkeypatch.setattr(followup_parent, "show", lambda batch_id: state)
    with pytest.raises(ValueError, match="Expected 1 research cases, found 0"):
        prepare(followup_parent)
    assert not followup_parent.path("round-two").exists()


@pytest.mark.parametrize("change", ["applied", "named", "home", "wrong-purpose"])
def test_followup_excludes_resolved_cases_and_requires_the_correct_purpose(
    followup_parent: ResearchBatches, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    state = followup_parent.show("missing")
    if change == "applied":
        state["applied_proposals"] = [state["proposals"][0]["proposal_id"]]
    elif change == "wrong-purpose":
        state["manifest"]["purpose"] = "survey-evidence-reconciliation"
    else:
        monkeypatch.setattr(
            module,
            "project_missing_education_scope",
            lambda *args: (
                []
                if change == "named"
                else [{"aph_id": "TEST", "status": "resolved_skip"}]
            ),
        )
    monkeypatch.setattr(followup_parent, "show", lambda batch_id: state)
    with pytest.raises(ValueError, match="Expected|purpose"):
        prepare(followup_parent)
    assert not followup_parent.path("round-two").exists()


def test_followup_rejects_count_source_and_destination_drift(
    followup_parent: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="Expected 30 research cases, found 1"):
        prepare(followup_parent, 30)
    with monkeypatch.context() as changed:
        changed.setattr(
            followup_parent.service, "source_revision", lambda **kwargs: "changed"
        )
        with pytest.raises(StaleReviewError, match="source"):
            prepare(followup_parent)
    assert not followup_parent.path("round-two").exists()
    prepare(followup_parent)
    with pytest.raises(ValueError, match="already exists"):
        prepare(followup_parent)


@pytest.mark.parametrize("change", ["source", "decisions", "evidence", "parent"])
def test_followup_refuses_concurrent_input_changes(
    followup_parent: ResearchBatches, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    original = module.project_missing_education_scope
    original_show = followup_parent.show

    def project(*args: Any) -> list[dict[str, Any]]:
        rows = original(*args)
        if change == "source":
            monkeypatch.setattr(
                followup_parent.service, "source_revision", lambda **kwargs: "changed"
            )
        elif change == "decisions":
            monkeypatch.setattr(
                followup_parent.service, "review_revision", lambda: "changed"
            )
        elif change == "evidence":
            monkeypatch.setattr(module, "evidence_revision", lambda path: "changed")
        else:

            def changed_parent(batch_id: str) -> dict[str, Any]:
                state = original_show(batch_id)
                state["review_revision"] = "changed"
                return state

            monkeypatch.setattr(followup_parent, "show", changed_parent)
        return rows

    monkeypatch.setattr(module, "project_missing_education_scope", project)
    with pytest.raises(StaleReviewError, match="changed"):
        prepare(followup_parent)
    assert not followup_parent.path("round-two").exists()


def test_followup_cli_creates_a_normal_batch(
    followup_parent: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import research_batch_cli

    monkeypatch.setattr(research_batch_cli, "_batches", lambda ctx: followup_parent)
    outcome = CliRunner().invoke(
        app,
        [
            "review",
            "research-batch",
            "follow-up-missing-education",
            "round-two",
            "--from-batch",
            "missing",
            "--expected-count",
            "1",
        ],
    )
    assert outcome.exit_code == 0, outcome.output
    assert json.loads(outcome.output)["manifest"]["case_count"] == 1
    assert not followup_parent.show("round-two")["reviews"]


@pytest.mark.parametrize("field,total", [("searches", 6), ("source_opens", 11)])
def test_followup_import_enforces_total_new_round_budget(
    followup_parent: ResearchBatches, field: str, total: int
) -> None:
    prepare(followup_parent)
    raw = deepcopy(result(positive=False))
    entries = "search_queries" if field == "searches" else "opened_sources"
    raw["research_log"][entries] *= total
    raw["research_log"][field] = total
    with pytest.raises(ValueError, match="budget"):
        followup_parent.import_results("round-two", [raw])
    assert not list(followup_parent.path("round-two").glob("proposals-*.json"))


def test_followup_positive_findings_require_fresh_approval_and_apply_once(
    followup_parent: ResearchBatches, tmp_path: Path
) -> None:
    from tests.test_research_batches import accept

    prepare(followup_parent)
    proposal = followup_parent.import_results("round-two", [result(two_schools=True)])[
        "proposals"
    ][0]
    assert not followup_parent.show("round-two")["reviews"]
    with pytest.raises(ValueError, match="No approved"):
        followup_parent.preview(
            "round-two", tmp_path / "unapproved.json", reviewer="Human reviewer"
        )
    accept(followup_parent, "round-two", proposal)
    followup_parent.retain("round-two")
    preview = followup_parent.preview(
        "round-two", tmp_path / "approved.json", reviewer="Human reviewer"
    )
    assert len(preview["preview"]["events"]) == 2
    applied = followup_parent.apply(
        "round-two", Path(preview["preview_path"]), preview["sha256"]
    )
    assert applied["new_events"] == 2
    assert (
        followup_parent.apply(
            "round-two", Path(preview["preview_path"]), preview["sha256"]
        )["new_events"]
        == 0
    )
    assert (
        followup_parent.show("missing")["reviews"][
            followup_parent.show("missing")["proposals"][0]["proposal_id"]
        ]["action"]
        == "research"
    )
