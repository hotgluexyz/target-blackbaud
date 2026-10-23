"""Blackbaud target sink class, which handles writing streams."""
from collections.abc import Sequence
from datetime import datetime, timezone

from target_blackbaud.client import BlackbaudBatchSink, BlackbaudSink
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


class ConstituentsSink(BlackbaudSink):
    """Passthrough sink for Blackbaud-native constituent payloads (catalog mapping)."""

    name = "constituents"
    endpoint = "constituent/v1/constituents"

    def search_constituent_by_email(self, email: str):
        endpoint = f"constituent/v1/constituents/search?search_text={email}"
        response = self.request_api("GET", endpoint=endpoint)
        resp_json = response.json()
        if len(resp_json["value"]):
            return resp_json["value"][0]
        return None

    def preprocess_record(self, record: dict, context: dict) -> dict:
        payload = dict(record)

        # hotglue snapshot key — not a Blackbaud API field
        external_id = payload.pop("externalId", None)
        if external_id is None:
            external_id = payload.pop("external_id", None)

        if external_id and not payload.get("lookup_id"):
            payload["lookup_id"] = str(external_id)

        if not payload.get("type"):
            payload["type"] = "Individual"

        email = payload.get("email")
        if isinstance(email, dict) and email.get("address") and not email.get("type"):
            payload["email"] = {**email, "type": "Email"}

        address = payload.get("address")
        if isinstance(address, dict):
            address = dict(address)
            if any(
                address.get(k)
                for k in ("address_lines", "city", "postal_code", "state", "country")
            ) and not address.get("type"):
                address["type"] = "Home"
            country = address.get("country")
            if country:
                if country in countries:
                    address["country"] = countries[country].capitalize()
                elif country.upper() in countries.values():
                    address["country"] = country.capitalize()
            payload["address"] = address

        # Prefer snapshot remote id; otherwise match existing by email
        if not payload.get("id"):
            email_obj = payload.get("email")
            email_address = email_obj.get("address") if isinstance(email_obj, dict) else None
            if email_address:
                match = self.search_constituent_by_email(email_address)
                if match:
                    payload["id"] = match["id"]

        return self.validate_output(payload)

    def upsert_record(self, record, context) -> None:
        if record.get("id"):
            id = record.get("id")
            update_record = {k: v for k, v in record.items() if k != "id"}
            url = "/".join([self.endpoint, id])
            self.request_api("PATCH", endpoint=url, request_data=update_record)
            self.logger.info(f"{self.name} updated with id: {id}")
            return id, True, {"is_updated": True}

        response = self.request_api("POST", endpoint=self.endpoint, request_data=record)
        id = response.json().get("id")
        self.logger.info(f"{self.name} created with id: {id}")
        return id, True, {}


class GiftsSink(BlackbaudBatchSink):
    """Write gifts via AddGiftsToBatch to reduce per-gift API calls.

    Docs: https://developer.sky.blackbaud.com/api#api=58bdd5edd7dcde06046081d6&operation=AddGiftsToBatch
    """

    name = "gifts"
    endpoint = "gift/v1/giftbatches"
    max_size = 50
    relation_fields = [
        {"field": "constituent_id", "objectName": "constituents"},
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
            endpoint="gift/v1/giftbatches",
            request_data={
                "batch_description": "hotglue Springboard gifts import",
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

        # AddGiftsToBatch commonly expects a receipt entry.
        if not payload.get("receipts") and payload.get("amount"):
            payload["receipts"] = [
                {
                    "status": "NeedsReceipt",
                    "amount": payload.get("amount"),
                }
            ]

        return {
            "_externalId": str(external_id) if external_id is not None else None,
            "_existing_id": str(existing_id) if existing_id is not None else None,
            "_payload": self.validate_output(payload),
        }

    def process_batch_record(self, record: dict, _index: int) -> dict:
        return self._prepare_gift_payload(record)

    def make_batch_request(self, records: list[dict]):
        existing = [r for r in records if r.get("_existing_id")]
        to_create = [r for r in records if not r.get("_existing_id")]
        response = None
        batch_id = None

        if to_create:
            batch_id = self._ensure_gift_batch_id()
            gifts = [r["_payload"] for r in to_create]
            response = self.request_api(
                "POST",
                endpoint=f"gift/v1/giftbatches/{batch_id}/gifts",
                request_data={"batchGifts": gifts},
            )
            self.logger.info(
                "Added %s gifts to batch %s",
                len(gifts),
                batch_id,
            )

        return {
            "existing": existing,
            "created": to_create,
            "response": response,
            "batch_id": batch_id,
        }

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

        response = result.get("response")
        created = result.get("created") or []
        if not response:
            return {"state_updates": state_updates}

        body = response.json() if response.content else {}
        gifts_by_lookup = {
            str(g.get("lookup_id")): g
            for g in (body.get("gifts") or [])
            if g.get("lookup_id") is not None
        }
        errors_by_lookup = {
            str(e.get("lookup_id")): e
            for e in (body.get("errors") or [])
            if e.get("lookup_id") is not None
        }

        for index, record in enumerate(created):
            lookup_id = record.get("_payload", {}).get("lookup_id")
            lookup_key = str(lookup_id) if lookup_id is not None else None
            state = {
                "externalId": record.get("_externalId"),
                "success": response.ok and lookup_key not in errors_by_lookup,
            }

            if lookup_key and lookup_key in errors_by_lookup:
                err = errors_by_lookup[lookup_key]
                state["error"] = (
                    err.get("exception_error_message")
                    or err.get("exception_error_name")
                    or err
                )
            elif lookup_key and lookup_key in gifts_by_lookup:
                state["id"] = gifts_by_lookup[lookup_key].get("id")
            elif response.ok and index < len(body.get("gifts") or []):
                # Fallback when lookup_id is missing from the response ordering.
                state["id"] = body["gifts"][index].get("id")
            elif not response.ok:
                state["error"] = body or response.text

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