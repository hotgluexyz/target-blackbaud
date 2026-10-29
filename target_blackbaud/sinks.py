"""Blackbaud target sink class, which handles writing streams."""
from collections.abc import Sequence
from datetime import datetime, timezone

from target_blackbaud.client import BlackbaudBatchSink, BlackbaudNativeSink, BlackbaudSink
from hotglue_models_crm.crm import Contact, Deal
from target_blackbaud.constants import countries


class ContactsSink(BlackbaudSink):
    """Blackbaud target sink class."""
    endpoint = "constituent/v1/constituents"
    unified_schema = Contact
    name = Contact.Stream.name
    campaigns = None
    contact_type = "Contact"


    def search_constituent_by_email(self, email: str):
        endpoint = f"constituent/v1/constituents/search?search_text={email}"

        response = self.request_api("GET", endpoint=endpoint)
        resp_json = response.json()
        if len(resp_json["value"]):
            return resp_json["value"][0]
        return None
    
    def get_constituent_by_id(self, id: str):
        endpoint = f"constituent/v1/constituents/{id}"
        response = self.request_api("GET", endpoint=endpoint)
        resp_json = response.json()
        return resp_json

    def preprocess_record(self, record: dict, context: dict) -> dict:
        
        record = self.validate_input(record)

        mapping = {
            "email": {
                "address": record.get("email"),
                "type": "Email"
            },
            "first": record.get("first_name"),
            "inactive": not record.get("active"),
            "last": record.get("last_name"),
            "lookup_id": record.get("id"),
            "middle": record.get("middle_name"),
            "name": record.get("company_name"),
            "title": record.get("salutation"),
            "type": "Individual",
            "industry":record.get("industry"),
            "num_employees": record.get("number_of_employees"),
        }

        email = record.get("email")
        id = None
        existing_contact = None
        if email:
            email_search_result = self.search_constituent_by_email(email)
            if email_search_result:
                id = email_search_result["id"]
                existing_contact = self.get_constituent_by_id(id)
        elif record.get("contact_id"):
            id = record.get("contact_id")
            existing_contact = self.get_constituent_by_id(id)

        if id:
            mapping["id"] = id
        
        website = record.get("website")
        if website:
            mapping["online_presence"] = {
                "address": website,
                "type": "Web address"
            }
        
        if record.get("birthdate"):
            birthdate = str(record.get("birthdate")).split("-")
            if len(birthdate) == 3:
                mapping["birthdate"] = {"d": birthdate[2], "m": birthdate[1], "y": birthdate[0]}

        addresses = record.get("addresses")
        if addresses and isinstance(addresses, Sequence):
            main_address: dict = record.get("addresses")[0]
            country = main_address.get("country")
            line1 = main_address.get("line1")
            line2 = main_address.get("line2")
            line3 = main_address.get("line3")
            address_lines = " ".join(l.strip() for l in [line1, line2, line3] if l and isinstance(l, str) and l.strip())
            mapping["address"] = {
                "type": "Business",
                "address_lines": address_lines,
                "city": main_address.get("city"),
                "postal_code": main_address.get("postal_code"),
            }
            if country:
                if country in countries.keys():
                    mapping["address"]["country"] = countries[country].capitalize()
                elif country.upper() in countries.values():
                    mapping["address"]["country"] = country.capitalize()
                else:
                    mapping["address"]["country"] = country

        created_at = record.get("created_at")
        if created_at:
            if isinstance(created_at, str):
                mapping["created_at"] = created_at
            else:
                mapping["created_at"] = created_at.isoformat()
        
        phone_numbers = record.get("phone_numbers")
        if phone_numbers and isinstance(phone_numbers, Sequence):
            mapping["phone"] = phone_numbers[0]

        external_ids = record.get("external_id")
        if external_ids and isinstance(external_ids, Sequence):
            for id in external_ids:
                if id["name"] == "blackbaud":
                    mapping["import_id"] = id["value"]


        mapping = self.validate_output(mapping)
        return mapping

    def upsert_record(self, record, context) -> None:
        """Process the record."""
        if record.get("id"):
            id = record.get("id")
            update_record = record.copy()
            if update_record:
                url = "/".join([self.endpoint, id])
                response = self.request_api(
                    "PATCH", endpoint=url, request_data=update_record
                )
            self.logger.info(f"{self.name} updated with id: {id}")
            return id, True, {"is_updated": True}
        else:
            url = self.endpoint
            response = self.request_api("POST", endpoint=url, request_data=record)
            id = response.json().get("id")
            self.logger.info(f"{self.contact_type} created with id: {id}")
            return id, True, {}

class OpportunitiesSink(BlackbaudSink):
    """Blackbaud target sink class."""
    endpoint = "opportunity/v1/opportunities"
    unified_schema = Deal
    name = Deal.Stream.name
    campaigns = None

    def preprocess_record(self, record: dict, context: dict) -> dict:

        record = self.validate_input(record)

        email = record.get("contact_email")

        if email:
            endpoint = f"constituent/v1/constituents/search?search_text={email}"
            response = self.request_api("GET", endpoint=endpoint)
            resp_json = response.json()

            if len(resp_json["value"]):
                contact_id = resp_json[0]["id"]
        else:
            contact_id = record.get("contact_id")

        mapping = {
            "ask_amount": {
                "value":record.get("monetary_amount")
            },
            "expected_amount": {
                "value":record.get("monetary_amount")
            },
            "fund_id": record.get("company_id"),
            "inactive": not record.get("active"),
            "name": record.get("title"),
            "purpose": "Multiple",
            "constituent_id": contact_id
        }

        opportunity_status = [
            "Closed",
            "Cultivation",
            "Follow-Up",
            "Identification",
            "On hold",
            "Proposal",
            "Rejected",
            "Research",
            "Solicitation",
            "Solicitation Visit",
            "Stewardship",
            "Strategy"
        ]

        if record.get("status") in opportunity_status:
            mapping["status"] = record.get("status"),
        else: 
            mapping["status"] = "Proposal"

        if record.get("deadline"):
            deadline = str(record.get("close_date")).split("-")
            if len(deadline) == 3:
                mapping["deadline"] = {"d": deadline[2], "m": deadline[1], "y": deadline[0]}

        mapping = self.validate_output(mapping)
        return mapping

    def upsert_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        if record.get("id"):
            id = record.get("id")
            update_record = record.copy()
            if update_record:
                url = "/".join([self.endpoint, id])
                response = self.request_api(
                    "PATCH", endpoint=url, request_data=update_record
                )
                id = response.json().get("id")
                self.logger.info(f"{self.name} updated with id: {id}")
                return id, True, {"is_updated": True}
        else:
            url = self.endpoint
            response = self.request_api("POST", endpoint=url, request_data=record)
            id = response.json().get("id")
            self.logger.info(f"{self.name} created with id: {id}")
            return id, True, {}


class ConstituentsSink(BlackbaudNativeSink):
    """Create native constituents without overwriting linked donor identities."""

    name = "constituents"
    endpoint = "constituent/v1/constituents"

    def search_constituent_by_email(self, email: str):
        response = self.request_api(
            "GET",
            f"{self.endpoint}/search",
            params={"search_text": email, "strict_search": "true", "limit": 5},
        )
        matches = response.json().get("value", [])
        if len(matches) > 1:
            raise ValueError(
                "Multiple constituents match the email; supply an explicit Blackbaud id"
            )
        return matches[0] if matches else None

    def preprocess_record(self, record: dict, context: dict) -> dict:
        payload = dict(record)
        if not payload.get("id"):
            email_obj = payload.get("email")
            email_address = (
                email_obj.get("address") if isinstance(email_obj, dict) else None
            )
            if email_address:
                match = self.search_constituent_by_email(email_address)
                if match:
                    payload["id"] = match["id"]
        if not payload.get("id"):
            payload.setdefault("type", "Individual")
        return payload

    def upsert_record(self, record, context) -> None:
        if record.get("id"):
            protected = {
                "first",
                "middle",
                "last",
                "name",
                "type",
                "email",
                "phone",
                "address",
            }
            payload = {
                k: v for k, v in self.api_payload(record).items() if k not in protected
            }
            if not payload:
                return str(record["id"]), True, {"existing": True}
            self.request_api(
                "PATCH", f"{self.endpoint}/{record['id']}", request_data=payload
            )
            return str(record["id"]), True, {"is_updated": True}
        return super().upsert_record(record, context)


class AddressesSink(BlackbaudNativeSink):
    """Append an address only if that address is not already on the donor."""

    name = "addresses"
    endpoint = "constituent/v1/addresses"
    relation_fields = [{"field": "constituent_id", "objectName": "constituents"}]
    match_fields = ("address_lines", "city", "state", "postal_code", "country")

    def identity(self, record):
        return tuple(
            " ".join(str(record.get(k) or "").casefold().split())
            for k in self.match_fields
        )

    def upsert_record(self, record, context):
        if record.get("id"):
            return str(record["id"]), True, {"existing": True}
        constituent_id = record.get("constituent_id")
        if not constituent_id:
            raise ValueError("constituent_id is required")
        entries = self.list_records(
            f"constituent/v1/constituents/{constituent_id}/{self.name}"
        )
        matches = [
            entry for entry in entries if self.identity(entry) == self.identity(record)
        ]
        if matches:
            return str(matches[0]["id"]), True, {"existing": True}
        payload = self.api_payload(record)
        if self.name == "addresses":
            payload["preferred"] = not entries
        response = self.request_api("POST", self.endpoint, request_data=payload)
        return self.created_id(response), True, {}


class PhonesSink(AddressesSink):
    """Append phone numbers without editing existing phone records."""

    name = "phones"
    endpoint = "constituent/v1/phones"

    def identity(self, record):
        return "".join(c for c in str(record.get("number") or "") if c.isdigit())


class ConstituentCustomFieldsSink(BlackbaudNativeSink):
    """Upsert a typed custom-field value, not its parent constituent."""

    name = "constituent_custom_fields"
    endpoint = "constituent/v1/constituents/customfields"
    parent_endpoint = "constituent/v1/constituents"
    relation_fields = [{"field": "parent_id", "objectName": "constituents"}]

    def upsert_record(self, record, context):
        remote_id = record.get("id")
        if "value" not in record:
            raise ValueError(
                "Custom field value is required (false and zero are valid)"
            )
        if not remote_id:
            if not record.get("parent_id") or not record.get("category"):
                raise ValueError(
                    "parent_id and category are required to create or match a custom field"
                )
            fields = self.list_records(
                f"{self.parent_endpoint}/{record['parent_id']}/customfields"
            )
            matches = [f for f in fields if f.get("category") == record["category"]]
            if len(matches) > 1:
                raise ValueError(
                    "Multiple custom fields match the category; supply the custom-field id"
                )
            if matches:
                remote_id = matches[0]["id"]
        if remote_id:
            self.request_api(
                "PATCH",
                f"{self.endpoint}/{remote_id}",
                request_data={"value": record["value"]},
            )
            return str(remote_id), True, {"is_updated": True}
        return super().upsert_record(record, context)


class GiftCustomFieldsSink(ConstituentCustomFieldsSink):
    """Upsert a gift's typed custom-field value."""

    name = "gift_custom_fields"
    endpoint = "gift/v1/gifts/customfields"
    parent_endpoint = "gift/v1/gifts"
    relation_fields = [{"field": "parent_id", "objectName": "gifts"}]


class DirectGiftsSink(BlackbaudNativeSink):
    """Create and update gifts directly, including recurring schedules/payments."""

    name = "gifts"
    endpoint = "gift/v1/gifts"
    relation_fields = [{"field": "constituent_id", "objectName": "constituents"}]
    edit_fields = {
        "lookup_id",
        "date",
        "is_anonymous",
        "reference",
        "gift_status",
        "recurring_gift_status_date",
        "gift_code",
        "subtype",
        "constituency",
        "marketing_details",
    }

    def preprocess_record(self, record, context):
        payload = dict(record)
        if payload.get("linked_gifts"):
            payload["linked_gifts"] = [
                str(
                    self._target.get_record_id(
                        self.name,
                        {"gift_id": gift_id},
                        [{"field": "gift_id", "objectName": self.name}],
                    )["gift_id"]
                )
                for gift_id in payload["linked_gifts"]
            ]
        if (
            not payload.get("id")
            and not payload.get("gift_splits")
            and self.config.get("default_fund_id")
        ):
            payload["gift_splits"] = [
                {
                    "fund_id": str(self.config["default_fund_id"]),
                    "amount": payload.get("amount"),
                }
            ]
        return payload

    def upsert_record(self, record, context):
        if record.get("id"):
            unsupported = set(self.api_payload(record)) - self.edit_fields
            if unsupported:
                raise ValueError(
                    "Gift PATCH does not support these fields: "
                    + ", ".join(sorted(unsupported))
                )
        return super().upsert_record(record, context)


class RecurringGiftStatusesSink(BlackbaudNativeSink):
    """Explicit recurring-gift status operation; scheduling belongs upstream."""

    name = "recurring_gift_statuses"
    endpoint = "gift/v1/recurringgifts"
    relation_fields = [{"field": "gift_id", "objectName": "gifts"}]

    def upsert_record(self, record, context):
        gift_id = record.get("gift_id")
        if not gift_id:
            raise ValueError("gift_id is required")
        status = record.get("gift_status")
        if status not in ("Active", "Held", "Cancelled"):
            raise ValueError("gift_status must be Active, Held, or Cancelled")
        self.request_api(
            "PUT",
            f"{self.endpoint}/{gift_id}/status",
            request_data={"gift_status": status},
        )
        return str(gift_id), True, {"is_updated": True}


class GiftBatchesSink(BlackbaudNativeSink):
    """Create an explicitly described batch; daily grouping belongs upstream."""

    name = "gift_batches"
    endpoint = "gift-batch/v1/giftbatches"

    def upsert_record(self, record, context):
        if record.get("id"):
            return str(record["id"]), True, {"existing": True}
        response = self.request_api(
            "POST", self.endpoint, request_data=self.api_payload(record)
        )
        return self.created_id(response, "batch_id"), True, {}


class GiftsSink(BlackbaudBatchSink):
    """Write gifts via AddGiftsToBatch to reduce per-gift API calls.

    Docs: https://developer.sky.blackbaud.com/api#api=58bdd5edd7dcde06046081d6&operation=AddGiftsToBatch
    """

    name = "gifts"
    endpoint = "gift/v1/giftbatches"
    max_size = 50
    relation_fields = [
        {"field": "constituent_id", "objectName": "constituents"},
        {"field": "batch_id", "objectName": "gift_batches"},
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._gift_batch_id = None

    def _ensure_gift_batch_id(self) -> str:
        """Return configured batch id, or create one gift batch for this job."""
        configured = self.config.get("gift_batch_id")
        if configured:
            return str(configured)
        if self._gift_batch_id:
            return self._gift_batch_id

        batch_number = f"HG-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        response = self.request_api(
            "POST",
            endpoint="gift-batch/v1/giftbatches",
            request_data={
                "batch_description": self.config.get(
                    "gift_batch_description", "hotglue gifts import"
                ),
                "batch_number": batch_number,
            },
        )
        body = response.json()
        batch_id = body.get("batch_id") or body.get("id")
        if not batch_id:
            raise ValueError(f"CreateGiftBatch did not return a batch id: {body}")
        self._gift_batch_id = str(batch_id)
        self.logger.info(
            "Created gift batch id=%s batch_number=%s",
            self._gift_batch_id,
            batch_number,
        )
        return self._gift_batch_id

    def _prepare_gift_payload(self, record: dict) -> dict:
        payload = dict(record)
        existing_id = payload.pop("id", None)

        external_id = payload.pop("externalId", None)
        if external_id is None:
            external_id = payload.pop("external_id", None)
        if external_id and not payload.get("lookup_id"):
            payload["lookup_id"] = str(external_id)
        if not payload.get("reference") and payload.get("lookup_id"):
            payload["reference"] = payload["lookup_id"]

        batch_id = payload.pop("batch_id", None)
        if existing_id:
            return {
                "_externalId": external_id,
                "_existing_id": str(existing_id),
                "_payload": {},
                "_batch_id": batch_id,
            }

        if payload.get("type") == "RecurringGiftPayment":
            payload["payments"] = [
                (
                    {**payment, "payment_method": "Other"}
                    if payment.get("payment_method") == "CreditCard"
                    else dict(payment)
                )
                for payment in payload.get("payments", [])
            ]

        if not payload.get("gift_splits"):
            fund_id = self.config.get("default_fund_id")
            if not fund_id:
                raise ValueError(
                    "default_fund_id is required in target config to create gifts"
                )
            payload["gift_splits"] = [
                {
                    "amount": payload.get("amount"),
                    "fund_id": str(fund_id),
                }
            ]

        if "post_status" not in payload:
            payload["post_status"] = "DoNotPost"
            payload["post_date"] = None

        return {
            "_externalId": str(external_id) if external_id is not None else None,
            "_existing_id": str(existing_id) if existing_id is not None else None,
            "_payload": BlackbaudNativeSink.api_payload(payload),
            "_batch_id": batch_id,
        }

    def process_batch_record(self, record: dict, _index: int) -> dict:
        return self._prepare_gift_payload(record)

    def make_batch_request(self, records: list[dict]):
        existing = [r for r in records if r.get("_existing_id")]
        to_create = [r for r in records if not r.get("_existing_id")]
        groups = {}
        for record in to_create:
            batch_id = record.get("_batch_id") or self._ensure_gift_batch_id()
            groups.setdefault(str(batch_id), []).append(record)
        results = []
        for batch_id, group in groups.items():
            gifts = [r["_payload"] for r in group]
            result = {"records": group, "batch_id": batch_id}
            try:
                lookup_ids = [g.get("lookup_id") for g in gifts]
                if not all(lookup_ids) or len(set(lookup_ids)) != len(lookup_ids):
                    raise ValueError(
                        "Batch gifts require distinct lookup_id values or externalId values for safe result correlation"
                    )
                response = self.request_api(
                    "POST",
                    endpoint=f"gift/v1/giftbatches/{batch_id}/gifts",
                    request_data={"gifts": gifts},
                )
                result["body"] = response.json()
            except Exception as error:
                # Preserve earlier groups' returned IDs if a later group fails.
                result["error"] = str(error)
            results.append(result)
        return {"existing": existing, "results": results}

    def update_state(self, state, **kwargs):
        return super().update_state(
            state, is_duplicate=state.pop("existing", False), **kwargs
        )

    def handle_batch_response(self, result: dict) -> dict:
        state_updates = []

        for record in result.get("existing") or []:
            state_updates.append(
                {
                    "externalId": record.get("_externalId"),
                    "id": record.get("_existing_id"),
                    "success": True,
                    "existing": True,
                }
            )

        for group in result.get("results", []):
            body = group.get("body", {})
            errors = body.get("errors") or []
            submitted = {str(r["_payload"].get("lookup_id")) for r in group["records"]}
            uncorrelated = any(str(e.get("lookup_id")) not in submitted for e in errors)
            for record in group["records"]:
                lookup_id = record["_payload"].get("lookup_id")
                matches = [
                    g
                    for g in body.get("gifts", [])
                    if lookup_id and str(g.get("lookup_id")) == str(lookup_id)
                ]
                # SKY may replace the submitted lookup_id with a generated UUID.
                # Only use an echoed reference if it identifies one submitted gift.
                reference = record["_payload"].get("reference")
                if (
                    not matches
                    and reference
                    and sum(
                        r["_payload"].get("reference") == reference
                        for r in group["records"]
                    )
                    == 1
                ):
                    matches = [
                        g
                        for g in body.get("gifts", [])
                        if g.get("reference") == reference
                        and str(g.get("lookup_id")) not in submitted
                    ]
                failures = [
                    e
                    for e in errors
                    if lookup_id and str(e.get("lookup_id")) == str(lookup_id)
                ]
                state = {
                    "externalId": record.get("_externalId"),
                    "success": False,
                    "batch_id": group["batch_id"],
                }
                if len(matches) == 1:
                    gift = matches[0]
                    failures.extend(gift.get("errors") or [])
                    if gift.get("id"):
                        state["id"] = str(gift["id"])
                    if gift.get("approved_gift_id"):
                        state["approved_gift_id"] = str(gift["approved_gift_id"])
                if group.get("error"):
                    state["error"] = group["error"]
                elif uncorrelated or failures:
                    state["error"] = failures or errors
                elif len(matches) != 1 or not state.get("id"):
                    state["error"] = (
                        "Missing or ambiguous batch gift result; reconcile before retrying"
                    )
                else:
                    state["success"] = True
                state_updates.append(state)

        return {"state_updates": state_updates}


class ActionsSink(BlackbaudSink):
    """Blackbaud target sink class."""

    name = "actions"
    endpoint = "constituent/v1/actions"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        return record
    
    def upsert_record(self, record: dict, context: dict) -> None:
        state = {}
        id = record.get("id")
        action = "POST"
        endpoint = self.endpoint
        if id:
            state["is_updated"] = True
            action = "PATCH"
            id = record.get("id")
            endpoint = "/".join([self.endpoint, id])
        # send data
        response = self.request_api(action, endpoint=endpoint, request_data=record)
        if action == "POST":
            id = response.json().get("id")
        return id, True, state


class UnsubscribesSink(BlackbaudSink):
    """Blackbaud target sink class."""

    name = "unsubscribes"
    endpoint = "constituent/v1/constituent"

    def preprocess_record(self, record: dict, context: dict) -> dict:
        return record
    
    def upsert_record(self, record: dict, context: dict) -> None:
        state = {}
        id = record.get("id")
        if not id:
            raise ValueError("ID is required")
        endpoint = "/".join([self.endpoint, id])
        response = self.request_api("PATCH", endpoint=endpoint, request_data=record)
        return id, True, state
