"""Blackbaud target sink class, which handles writing streams."""
from collections.abc import Sequence
from target_blackbaud.client import BlackbaudSink
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