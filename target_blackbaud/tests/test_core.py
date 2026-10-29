"""Native write contracts exercised through Singer input and emitted state."""

import io
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests
import responses
from responses import matchers

from target_blackbaud.target import TargetBlackbaud

BASE = "https://api.sky.blackbaud.com/"


@pytest.fixture
def run_target(tmp_path, monkeypatch, capsys):
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / ".secrets").mkdir()
    monkeypatch.chdir(work)

    def run(rows, config=None, state=None):
        settings = {
            "subscription_key": "test-subscription",
            "access_token": "test-access",
            "expires_in": int(time.time()) + 3600,
            "_refresh_token_via_hg_api": False,
            "gift_write_mode": "direct",
            **(config or {}),
        }
        schemas = {}
        for stream, record in rows:
            schemas.setdefault(stream, {}).update({k: {} for k in record})
        messages = [
            {
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"type": "object", "properties": properties},
                "key_properties": [],
            }
            for stream, properties in schemas.items()
        ]
        messages.extend(
            {"type": "RECORD", "stream": stream, "record": record}
            for stream, record in rows
        )
        target = TargetBlackbaud(config=settings, state=state)
        target.listen(io.StringIO("\n".join(json.dumps(m) for m in messages)))
        emitted = [
            json.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("{")
        ]
        return emitted[-1]

    return run


@pytest.fixture
def api():
    with responses.RequestsMock() as mock:
        yield mock


def add_json(api, method, endpoint, payload, response=None, status=200):
    api.add(
        method,
        BASE + endpoint,
        json=response or {},
        status=status,
        match=[matchers.json_params_matcher(payload)],
    )


def gift(external_id="charge-1", **extra):
    return {
        "externalId": external_id,
        "reference": "BW-90001",
        "constituent_id": "12345",
        "date": "2026-10-27T14:32:05-04:00",
        "amount": {"value": 25.75},
        "type": "Donation",
        "gift_status": "Active",
        "gift_splits": [
            {
                "fund_id": "280",
                "amount": {"value": 25.75},
                "appeal_id": "101",
                "package_id": "2001",
            }
        ],
        "payments": [{"payment_method": "CreditCard"}],
        **extra,
    }


def test_constituent_create_preserves_native_payload_and_source_id(api, run_target):
    record = {
        "externalId": "donor-1",
        "first": "Jane",
        "middle": None,
        "last": "Donor",
        "type": "individual",
        "email": {"address": "jane+giving@example.com", "type": "Email"},
        "address": {"country": "United States", "type": "Home", "city": "Boston"},
    }
    api.get(
        BASE + "constituent/v1/constituents/search",
        json={"value": []},
        match=[
            matchers.query_param_matcher(
                {
                    "search_text": "jane+giving@example.com",
                    "strict_search": "true",
                    "limit": "5",
                }
            )
        ],
    )
    add_json(
        api,
        "POST",
        "constituent/v1/constituents",
        {k: v for k, v in record.items() if k != "externalId"},
        {"id": "12345"},
    )
    state = run_target([("constituents", record)])
    bookmark = state["bookmarks"]["constituents"][0]
    assert (bookmark["externalId"], bookmark["id"], bookmark["success"]) == (
        "donor-1",
        "12345",
        True,
    )
    assert record["externalId"] == "donor-1"


def test_matched_donor_keeps_identity_but_updates_mapped_fields(api, run_target):
    api.get(
        BASE + "constituent/v1/constituents/search", json={"value": [{"id": "12345"}]}
    )
    add_json(
        api, "PATCH", "constituent/v1/constituents/12345", {"preferred_name": "JJ"}
    )
    state = run_target(
        [
            (
                "constituents",
                {
                    "externalId": "donor-2",
                    "first": "Replacement",
                    "last": "Name",
                    "email": {"address": "existing@example.com"},
                    "phone": {"number": "555"},
                    "address": {"city": "Changed"},
                    "preferred_name": "JJ",
                },
            )
        ]
    )
    assert state["summary"]["constituents"]["updated"] == 1
    assert state["bookmarks"]["constituents"][0]["id"] == "12345"


def test_ambiguous_email_does_not_create_or_update(api, run_target):
    api.get(
        BASE + "constituent/v1/constituents/search",
        json={"value": [{"id": "1"}, {"id": "2"}]},
    )
    state = run_target(
        [
            (
                "constituents",
                {"externalId": "ambiguous", "email": {"address": "shared@example.com"}},
            )
        ]
    )
    assert state["summary"]["constituents"]["fail"] == 1
    assert "Multiple constituents" in state["bookmarks"]["constituents"][0]["error"]


def test_address_duplicate_on_later_page_and_preferred_rule(api, run_target):
    endpoint = "constituent/v1/constituents/12345/addresses"
    api.get(
        BASE + endpoint,
        json={
            "value": [{"id": "old", "city": "Other"}],
            "next_link": BASE + endpoint + "?offset=1",
        },
        match=[matchers.query_param_matcher({})],
    )
    api.get(
        BASE + endpoint + "?offset=1",
        json={
            "value": [
                {"id": "address-1", "city": "Boston", "address_lines": "123 Main St"}
            ]
        },
        match=[matchers.query_param_matcher({"offset": "1"})],
    )
    state = run_target(
        [
            (
                "addresses",
                {
                    "externalId": "addr-1",
                    "constituent_id": "12345",
                    "city": " BOSTON ",
                    "address_lines": "123  Main St",
                },
            )
        ]
    )
    assert state["bookmarks"]["addresses"][0]["id"] == "address-1"
    assert state["summary"]["addresses"]["existing"] == 1


@pytest.mark.parametrize(
    "existing,preferred", [([], True), ([{"id": "old", "city": "Other"}], False)]
)
def test_new_address_preferred_only_when_first(api, run_target, existing, preferred):
    api.get(
        BASE + "constituent/v1/constituents/12345/addresses", json={"value": existing}
    )
    add_json(
        api,
        "POST",
        "constituent/v1/addresses",
        {"constituent_id": "12345", "city": "Boston", "preferred": preferred},
        {"id": "new"},
    )
    state = run_target(
        [
            (
                "addresses",
                {
                    "externalId": "addr",
                    "constituent_id": "12345",
                    "city": "Boston",
                    "preferred": not preferred,
                },
            )
        ]
    )
    assert state["bookmarks"]["addresses"][0]["success"] is True


def test_phone_formatting_does_not_duplicate(api, run_target):
    api.get(
        BASE + "constituent/v1/constituents/12345/phones",
        json={"value": [{"id": "phone-1", "number": "(555) 010-0123"}]},
    )
    state = run_target(
        [
            (
                "phones",
                {
                    "externalId": "phone",
                    "constituent_id": "12345",
                    "number": "5550100123",
                    "type": "Home",
                },
            )
        ]
    )
    assert state["bookmarks"]["phones"][0]["id"] == "phone-1"
    assert state["summary"]["phones"]["existing"] == 1


@pytest.mark.parametrize(
    "stream,parent_endpoint",
    [
        ("constituent_custom_fields", "constituent/v1/constituents"),
        ("gift_custom_fields", "gift/v1/gifts"),
    ],
)
@pytest.mark.parametrize(
    "value", [False, 0, 12.34, "2026-10-27", {"d": 27, "m": 10, "y": 2026}]
)
def test_custom_fields_preserve_typed_values_and_update_value_id(
    api, run_target, stream, parent_endpoint, value
):
    api.get(
        BASE + parent_endpoint + "/12345/customfields",
        json={"value": [{"id": "field-987", "category": "BW Category"}]},
    )
    add_json(
        api, "PATCH", parent_endpoint + "/customfields/field-987", {"value": value}
    )
    state = run_target(
        [
            (
                stream,
                {
                    "externalId": "field-1",
                    "parent_id": "12345",
                    "category": "BW Category",
                    "value": value,
                },
            )
        ]
    )
    assert state["bookmarks"][stream][0]["id"] == "field-987"
    assert state["summary"][stream]["updated"] == 1


def test_custom_field_create_then_explicit_update(api, run_target):
    api.get(BASE + "gift/v1/gifts/70001/customfields", json={"value": []})
    add_json(
        api,
        "POST",
        "gift/v1/gifts/customfields",
        {"parent_id": "70001", "category": "Campaign", "value": "Fall"},
        {"id": "field-1"},
    )
    add_json(api, "PATCH", "gift/v1/gifts/customfields/field-1", {"value": "Winter"})
    state = run_target(
        [
            (
                "gift_custom_fields",
                {
                    "externalId": "cf",
                    "parent_id": "70001",
                    "category": "Campaign",
                    "value": "Fall",
                },
            ),
            (
                "gift_custom_fields",
                {"externalId": "cf", "id": "field-1", "value": "Winter"},
            ),
        ]
    )
    assert state["summary"]["gift_custom_fields"]["success"] == 1
    assert state["summary"]["gift_custom_fields"]["updated"] == 1


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {
            "type": "RecurringGift",
            "is_manual": True,
            "post_status": "DoNotPost",
            "recurring_gift_schedule": {
                "frequency": "MONTHLY",
                "start_date": "2026-09-27T10:05:00-04:00",
            },
        },
        {"type": "RecurringGiftPayment", "is_manual": True, "linked_gifts": ["70002"]},
        {
            "type": "RecurringGiftPayment",
            "gift_status": "Terminated",
            "is_manual": True,
            "linked_gifts": ["70002"],
        },
    ],
)
def test_direct_gift_variants_keep_native_payload(api, run_target, extra):
    record = gift(**extra)
    add_json(
        api,
        "POST",
        "gift/v1/gifts",
        {k: v for k, v in record.items() if k != "externalId"},
        {"id": "70001"},
    )
    state = run_target([("gifts", record)])
    assert state["bookmarks"]["gifts"][0]["id"] == "70001"
    assert state["bookmarks"]["gifts"][0]["externalId"] == "charge-1"


def test_gift_patch_uses_explicit_id_and_does_not_expect_response_id(api, run_target):
    add_json(
        api,
        "PATCH",
        "gift/v1/gifts/70001",
        {"reference": "Corrected", "is_anonymous": False},
    )
    state = run_target(
        [
            (
                "gifts",
                {
                    "externalId": "charge-1",
                    "id": "70001",
                    "reference": "Corrected",
                    "is_anonymous": False,
                },
            )
        ]
    )
    assert state["summary"]["gifts"]["updated"] == 1
    assert state["bookmarks"]["gifts"][0]["id"] == "70001"


def test_unsupported_financial_edit_fails_instead_of_silent_skip(api, run_target):
    state = run_target([("gifts", gift(id="70001", amount={"value": 0}))])
    assert state["summary"]["gifts"]["fail"] == 1
    assert "amount" in state["bookmarks"]["gifts"][0]["error"]


def test_same_run_donor_schedule_payment_custom_field_and_status_relationships(
    api, run_target
):
    add_json(
        api,
        "POST",
        "constituent/v1/constituents",
        {"first": "Jane", "type": "Individual"},
        {"id": "12345"},
    )
    schedule = gift("subscription-1", constituent_id="donor-1", type="RecurringGift")
    payment = gift(
        "payment-1",
        constituent_id="donor-1",
        type="RecurringGiftPayment",
        linked_gifts=["subscription-1"],
    )
    add_json(
        api,
        "POST",
        "gift/v1/gifts",
        {
            **{k: v for k, v in schedule.items() if k != "externalId"},
            "constituent_id": "12345",
        },
        {"id": "70002"},
    )
    add_json(
        api,
        "POST",
        "gift/v1/gifts",
        {
            **{k: v for k, v in payment.items() if k != "externalId"},
            "constituent_id": "12345",
            "linked_gifts": ["70002"],
        },
        {"id": "70003"},
    )
    api.get(BASE + "gift/v1/gifts/70003/customfields", json={"value": []})
    add_json(
        api,
        "POST",
        "gift/v1/gifts/customfields",
        {"parent_id": "70003", "category": "Campaign", "value": "Fall"},
        {"id": "cf-1"},
    )
    add_json(api, "PUT", "gift/v1/recurringgifts/70002/status", {"gift_status": "Held"})
    state = run_target(
        [
            ("constituents", {"externalId": "donor-1", "first": "Jane"}),
            ("gifts", schedule),
            ("gifts", payment),
            (
                "gift_custom_fields",
                {
                    "externalId": "cf",
                    "parent_id": "payment-1",
                    "category": "Campaign",
                    "value": "Fall",
                },
            ),
            (
                "recurring_gift_statuses",
                {
                    "externalId": "status-1",
                    "gift_id": "subscription-1",
                    "gift_status": "Held",
                },
            ),
        ]
    )
    assert [b["id"] for b in state["bookmarks"]["gifts"]] == ["70002", "70003"]
    assert state["bookmarks"]["recurring_gift_statuses"][0]["id"] == "70002"


def test_http_404_update_is_failure(api, run_target):
    api.patch(BASE + "gift/v1/gifts/missing", status=404, json={"message": "not found"})
    state = run_target(
        [("gifts", {"externalId": "missing", "id": "missing", "reference": "Updated"})]
    )
    assert state["summary"]["gifts"]["fail"] == 1
    assert "404" in state["bookmarks"]["gifts"][0]["error"]


@pytest.mark.parametrize("failure", [requests.exceptions.ReadTimeout("ambiguous"), 503])
def test_ambiguous_create_is_not_retried(api, run_target, failure):
    if isinstance(failure, int):
        api.post(BASE + "gift/v1/gifts", status=failure)
    else:
        api.post(BASE + "gift/v1/gifts", body=failure)
    state = run_target([("gifts", gift())])
    assert state["summary"]["gifts"]["fail"] == 1
    assert len(api.calls) == 1


def test_oauth_refresh_shared_across_writes_without_secret_logging(
    api, run_target, caplog
):
    api.post(
        "https://oauth2.sky.blackbaud.com/token",
        json={
            "access_token": "new-secret-token",
            "refresh_token": "rotated-secret-token",
            "expires_in": 3600,
        },
        match=[
            matchers.urlencoded_params_matcher(
                {
                    "grant_type": "refresh_token",
                    "client_id": "test-client",
                    "client_secret": "secret-client",
                    "refresh_token": "old-secret-token",
                }
            )
        ],
    )
    api.post(
        BASE + "gift/v1/gifts",
        json={"id": "70001"},
        match=[
            matchers.header_matcher(
                {
                    "Authorization": "Bearer new-secret-token",
                    "Bb-Api-Subscription-Key": "test-subscription",
                }
            )
        ],
    )
    add_json(api, "PATCH", "gift/v1/gifts/70001", {"reference": "New"})
    state = run_target(
        [
            ("gifts", gift()),
            ("gifts", {"externalId": "charge-1", "id": "70001", "reference": "New"}),
        ],
        config={
            "expires_in": 1,
            "client_id": "test-client",
            "client_secret": "secret-client",
            "refresh_token": "old-secret-token",
        },
    )
    assert state["summary"]["gifts"]["fail"] == 0
    assert (
        sum(
            c.request.url == "https://oauth2.sky.blackbaud.com/token" for c in api.calls
        )
        == 1
    )
    assert all(
        secret not in caplog.text
        for secret in (
            "new-secret-token",
            "rotated-secret-token",
            "secret-client",
            "old-secret-token",
        )
    )


def test_batch_contract_reordered_results_nested_errors_and_missing_ids(
    api, run_target
):
    api.post(BASE + "gift-batch/v1/giftbatches", json={"batch_id": "batch-1"})

    def batch_response(request):
        payload = json.loads(request.body)
        assert set(payload) == {"gifts"}
        assert payload["gifts"][1]["payments"] == [{"payment_method": "Other"}]
        assert "receipts" not in payload["gifts"][0]
        return (
            200,
            {},
            json.dumps(
                {
                    "gifts": [
                        {
                            "id": "g-2",
                            "lookup_id": "generated-two",
                            "reference": "BW-two",
                            "errors": [{"exception_error_message": "Invalid fund"}],
                        },
                        {
                            "id": "g-1",
                            "lookup_id": "generated-one",
                            "reference": "BW-one",
                            "approved_gift_id": "approved-1",
                        },
                    ],
                    "errors": [],
                }
            ),
        )

    api.add_callback(
        "POST", BASE + "gift/v1/giftbatches/batch-1/gifts", callback=batch_response
    )
    state = run_target(
        [
            ("gifts", gift("one", reference="BW-one")),
            ("gifts", gift("two", reference="BW-two", type="RecurringGiftPayment")),
            ("gifts", gift("three", reference="BW-three")),
        ],
        config={"gift_write_mode": "batch"},
    )
    bookmarks = {b["externalId"]: b for b in state["bookmarks"]["gifts"]}
    assert bookmarks["one"]["id"] == "g-1" and bookmarks["one"]["success"] is True
    assert bookmarks["one"]["approved_gift_id"] == "approved-1"
    assert bookmarks["two"]["success"] is False
    assert bookmarks["three"]["success"] is False and "id" not in bookmarks["three"]


def test_rewritten_batch_lookup_with_shared_reference_is_ambiguous(api, run_target):
    api.post(
        BASE + "gift/v1/giftbatches/batch-a/gifts",
        json={
            "gifts": [{"id": "g-1", "lookup_id": "generated", "reference": "shared"}]
        },
    )
    state = run_target(
        [
            ("gifts", gift("one", reference="shared", batch_id="batch-a")),
            ("gifts", gift("two", reference="shared", batch_id="batch-a")),
        ],
        config={"gift_write_mode": "batch"},
    )
    assert state["summary"]["gifts"]["fail"] == 2
    assert all("id" not in b for b in state["bookmarks"]["gifts"])


def test_explicit_batch_creation_and_source_relationship(api, run_target):
    add_json(
        api,
        "POST",
        "gift-batch/v1/giftbatches",
        {"batch_description": "BetterWorld Batch - Demo - Donation - 2026-10-27"},
        {"batch_id": "batch-123"},
    )
    api.post(
        BASE + "gift/v1/giftbatches/batch-123/gifts",
        json={"gifts": [{"id": "batch-gift", "lookup_id": "one"}]},
    )
    state = run_target(
        [
            (
                "gift_batches",
                {
                    "externalId": "daily-batch",
                    "batch_description": "BetterWorld Batch - Demo - Donation - 2026-10-27",
                },
            ),
            ("gifts", gift("one", batch_id="daily-batch")),
        ],
        config={"gift_write_mode": "batch"},
    )
    assert state["bookmarks"]["gifts"][0]["batch_id"] == "batch-123"


def test_repeat_import_with_snapshot_skips_unchanged_and_updates_changed(
    api, run_target, tmp_path, monkeypatch
):
    import hotglue_singer_sdk.target_sdk.target as sdk_target

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr(sdk_target, "SNAPSHOT_DIR", str(snapshots))
    monkeypatch.setattr(sdk_target, "flow_id", "test-flow")
    record = gift()
    add_json(
        api,
        "POST",
        "gift/v1/gifts",
        {k: v for k, v in record.items() if k != "externalId"},
        {"id": "70001"},
    )
    original = run_target([("gifts", record)])
    saved_state = tmp_path / "state.json"
    saved_state.write_text(json.dumps(original))
    (snapshots / "gifts_test-flow.snapshot.csv").write_text(
        "InputId,RemoteId\ncharge-1,70001\n"
    )
    repeated = run_target([("gifts", record)], state=saved_state)
    assert repeated["summary"]["gifts"]["existing"] == 1
    assert repeated["summary"]["gifts"]["fail"] == 0
    add_json(api, "PATCH", "gift/v1/gifts/70001", {"reference": "Changed"})
    changed = run_target(
        [("gifts", {"externalId": "charge-1", "reference": "Changed"})],
        state=saved_state,
    )
    assert changed["bookmarks"]["gifts"][-1]["id"] == "70001"
    assert changed["summary"]["gifts"]["updated"] == 1


def test_snapshot_only_parent_relationships(api, run_target, tmp_path, monkeypatch):
    import hotglue_singer_sdk.target_sdk.target as sdk_target

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr(sdk_target, "SNAPSHOT_DIR", str(snapshots))
    monkeypatch.setattr(sdk_target, "flow_id", "test-flow")
    (snapshots / "constituents_test-flow.snapshot.csv").write_text(
        "InputId,RemoteId\n48213,12345\n"
    )
    (snapshots / "gifts_test-flow.snapshot.csv").write_text(
        "InputId,RemoteId\nsubscription-old,70002\n"
    )
    record = gift(
        type="RecurringGiftPayment",
        constituent_id="48213",
        linked_gifts=["subscription-old"],
    )
    expected = {k: v for k, v in record.items() if k != "externalId"}
    expected.update(constituent_id="12345", linked_gifts=["70002"])
    add_json(api, "POST", "gift/v1/gifts", expected, {"id": "70003"})
    state = run_target([("gifts", record)])
    assert state["bookmarks"]["gifts"][0]["success"] is True


def test_failed_parent_cannot_route_child_write(api, run_target):
    api.post(BASE + "gift/v1/gifts", json={"id": "later-gift"})
    state = run_target(
        [
            ("gifts", gift("failed-parent", id="70001", amount={"value": 0})),
            (
                "gift_custom_fields",
                {
                    "externalId": "child",
                    "parent_id": "failed-parent",
                    "category": "Campaign",
                    "value": "Fall",
                },
            ),
            ("gifts", gift("later")),
        ]
    )
    child = state["bookmarks"]["gift_custom_fields"][0]
    assert child["externalId"] == "child" and child["success"] is False
    assert "parent record failed" in child["error"]
    assert state["summary"]["gift_custom_fields"]["fail"] == 1
    assert state["bookmarks"]["gifts"][-1]["id"] == "later-gift"
    assert state["bookmarks"]["gifts"][-1]["success"] is True
    assert len(api.calls) == 1


def test_failed_record_remains_retryable(api, run_target, tmp_path):
    api.post(BASE + "gift/v1/gifts", status=400, json={"message": "Invalid fund"})
    first = run_target([("gifts", gift())])
    saved = tmp_path / "failed.json"
    saved.write_text(json.dumps(first))
    api.post(BASE + "gift/v1/gifts", json={"id": "70001"})
    retried = run_target([("gifts", gift())], state=saved)
    assert retried["bookmarks"]["gifts"][-1]["success"] is True
    assert retried["bookmarks"]["gifts"][-1]["id"] == "70001"


def test_partial_batch_group_failure_keeps_successful_ids(api, run_target):
    api.post(
        BASE + "gift/v1/giftbatches/batch-a/gifts",
        json={"gifts": [{"lookup_id": "one", "id": "gift-a"}]},
    )
    api.post(BASE + "gift/v1/giftbatches/batch-b/gifts", status=503)
    state = run_target(
        [
            ("gifts", gift("one", batch_id="batch-a")),
            ("gifts", gift("two", batch_id="batch-b")),
        ],
        config={"gift_write_mode": "batch"},
    )
    bookmarks = {b["externalId"]: b for b in state["bookmarks"]["gifts"]}
    assert bookmarks["one"]["success"] is True and bookmarks["one"]["id"] == "gift-a"
    assert bookmarks["two"]["success"] is False


def test_unknown_batch_error_never_reports_success(api, run_target):
    api.post(
        BASE + "gift/v1/giftbatches/batch-a/gifts",
        json={
            "gifts": [{"lookup_id": "one", "id": "gift-a"}],
            "errors": [{"exception_error_message": "Uncorrelated failure"}],
        },
    )
    state = run_target(
        [("gifts", gift("one", batch_id="batch-a"))],
        config={"gift_write_mode": "batch"},
    )
    assert state["summary"]["gifts"]["fail"] == 1


@pytest.mark.parametrize("approved_id", [None, "approved-70001"])
def test_batch_child_requires_approved_gift_id(api, run_target, approved_id):
    result = {"lookup_id": "one", "id": "batch-gift-1"}
    if approved_id:
        result["approved_gift_id"] = approved_id
        api.get(BASE + f"gift/v1/gifts/{approved_id}/customfields", json={"value": []})
        add_json(
            api,
            "POST",
            "gift/v1/gifts/customfields",
            {"parent_id": approved_id, "category": "Campaign", "value": "Fall"},
            {"id": "cf-1"},
        )
    api.post(BASE + "gift/v1/giftbatches/batch-a/gifts", json={"gifts": [result]})
    rows = [
        ("gifts", gift("one", batch_id="batch-a")),
        (
            "gift_custom_fields",
            {
                "externalId": "cf",
                "parent_id": "one",
                "category": "Campaign",
                "value": "Fall",
            },
        ),
    ]
    api.get(BASE + "gift/v1/gifts/70002/customfields", json={"value": []})
    add_json(
        api,
        "POST",
        "gift/v1/gifts/customfields",
        {"parent_id": "70002", "category": "Campaign", "value": "Winter"},
        {"id": "cf-later"},
    )
    rows.append(
        (
            "gift_custom_fields",
            {
                "externalId": "later",
                "parent_id": "70002",
                "category": "Campaign",
                "value": "Winter",
            },
        )
    )
    state = run_target(rows, config={"gift_write_mode": "batch"})
    children = {b["externalId"]: b for b in state["bookmarks"]["gift_custom_fields"]}
    assert (
        children["later"]["id"] == "cf-later" and children["later"]["success"] is True
    )
    parent = state["bookmarks"]["gifts"][0]
    assert parent["id"] == "batch-gift-1" and parent["success"] is True
    if approved_id:
        assert children["cf"]["id"] == "cf-1"
    else:
        assert children["cf"]["success"] is False
        assert "Batch gift is not approved" in children["cf"]["error"]
    assert sum(c.request.url.endswith("/batch-a/gifts") for c in api.calls) == 1


@pytest.mark.parametrize("existing_parent", [False, True])
def test_batch_snapshot_cannot_route_direct_gift_write(
    api, run_target, tmp_path, monkeypatch, existing_parent
):
    import hotglue_singer_sdk.target_sdk.target as sdk_target

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr(sdk_target, "SNAPSHOT_DIR", str(snapshots))
    monkeypatch.setattr(sdk_target, "flow_id", "test-flow")
    (snapshots / "gifts_test-flow.snapshot.csv").write_text(
        "InputId,RemoteId\nsubscription-old,70002\n"
    )
    rows = []
    if existing_parent:
        rows.append(("gifts", {"externalId": "subscription-old", "id": "70002"}))
    state = run_target(
        rows
        + [
            (
                "recurring_gift_statuses",
                {
                    "externalId": "status",
                    "gift_id": "subscription-old",
                    "gift_status": "Held",
                },
            )
        ],
        config={"gift_write_mode": "batch"},
    )
    assert state["bookmarks"]["recurring_gift_statuses"][0]["success"] is False
    assert "Batch gift" in state["bookmarks"]["recurring_gift_statuses"][0]["error"]
    assert not api.calls


@pytest.mark.parametrize(
    "retry_after,expected_delay",
    [
        ("3", 3),
        ("not-a-date", 1),
        ("Thu, 01 Jan 2026 00:00:05 -0000", 5),
        ("Thu, 01 Jan 2026 00:00:05 GMT", 5),
    ],
)
def test_rate_limit_honors_retry_after(
    api, run_target, monkeypatch, retry_after, expected_delay
):
    waits = []
    monkeypatch.setattr(
        "target_blackbaud.rest.datetime",
        SimpleNamespace(now=lambda _: datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    monkeypatch.setattr(
        "target_blackbaud.rest.time", SimpleNamespace(sleep=waits.append)
    )
    api.post(BASE + "gift/v1/gifts", status=429, headers={"Retry-After": retry_after})
    api.post(BASE + "gift/v1/gifts", json={"id": "70001"})
    state = run_target([("gifts", gift())])
    assert state["summary"]["gifts"]["success"] == 1
    assert waits == [expected_delay]


def test_managed_refresh_uses_hotglue_not_local_client_secrets(
    api, run_target, monkeypatch
):
    for key, value in {
        "ENV_ID": "test-env",
        "FLOW": "test-flow",
        "TENANT": "default",
        "TARGET": "blackbaud",
        "API_KEY": "test-hg-key",
        "API_URL": "https://api.hotglue.com",
    }.items():
        monkeypatch.setenv(key, value)
    api.get(
        "https://api.hotglue.com/test-env/test-flow/default/connectors/blackbaud/accesstoken?include_properties=expires_in",
        json={
            "success": True,
            "access_token": "managed-token",
            "expires_in": int(time.time()) + 3600,
        },
    )
    api.post(
        BASE + "gift/v1/gifts",
        json={"id": "70001"},
        match=[matchers.header_matcher({"Authorization": "Bearer managed-token"})],
    )
    state = run_target(
        [("gifts", gift())], config={"expires_in": 1, "_refresh_token_via_hg_api": True}
    )
    assert state["summary"]["gifts"]["success"] == 1
