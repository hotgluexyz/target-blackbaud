"""Blackbaud target sink class, which handles writing streams."""

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
            "online_presence": {
                "address": record.get("website"),
                "type": "Web address"
            },
            "title": record.get("salutation"),
            "type": "Individual",
            "industry":record.get("industry"),
            "num_employees": record.get("number_of_employees"),
        }

        if record.get("birthdate"):
            birthdate = str(record.get("birthdate")).split("-")
            mapping["birthdate"] = {"d": birthdate[2], "m": birthdate[1], "y": birthdate[0]}

        if len(record.get("addresses")):
            main_address = record.get("addresses")[0]
            mapping["address"] = {
                "type": "Business",
                "address_lines": main_address["line1"] + " " + main_address["line2"] + " " + main_address["line3"],
                "city": main_address["city"],
                "postal_code": main_address["postal_code"],
            }
            if main_address["country"] in countries.keys():
                country = countries[main_address["country"]]
                mapping["address"]["country"] = country.capitalize()
            elif main_address["country"].upper() in countries.values():
                country = main_address["country"]
                mapping["address"]["country"] = country.capitalize()
            else:
                country = main_address["country"]
                mapping["address"]["country"] = country

        if record.get("created_at"):
            mapping["created_at"] = record.get("created_at"),
        
        if len(record.get("phone_numbers")):
            main_phonenumber = record.get("phone_numbers")[0]
            mapping["phone"] = main_phonenumber

        if record.get("external_id") and len(record.get("external_id")):
            external_ids = record.get("external_id")
            for id in external_ids:
                if id["name"] == "blackbaud":
                    mapping["import_id"] = id["value"]

        mapping = self.validate_output(mapping)
        return mapping

    def process_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        if record.get("id"):
            id = record.get("id")
            try:
                update_record = record.copy()
                if update_record:
                    url = "/".join([self.endpoint, id])
                    response = self.request_api(
                        "PATCH", endpoint=url, request_data=update_record
                    )
                    id = response.json().get("id")
                self.logger.info(f"{self.name} updated with id: {id}")
            except:
                self.logger.info(f"{self.name} with id {id} does not exist.")
                raise "Invalid id"
        else:
            try:
                url = self.endpoint
                response = self.request_api("POST", endpoint=url, request_data=record)
                id = response.json().get("id")
                self.logger.info(f"{self.contact_type} created with id: {id}")
            except:
                raise

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
            mapping["deadline"] = {"d": deadline[2], "m": deadline[1], "y": deadline[0]}

        mapping = self.validate_output(mapping)
        return mapping

    def process_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        if record.get("id"):
            id = record.get("id")
            try:
                update_record = record.copy()
                if update_record:
                    url = "/".join([self.endpoint, id])
                    response = self.request_api(
                        "PATCH", endpoint=url, request_data=update_record
                    )
                    id = response.json().get("id")
                self.logger.info(f"{self.name} updated with id: {id}")
            except:
                self.logger.info(f"{self.name} with id {id} does not exist.")
                raise "Invalid id"
        else:
            try:
                url = self.endpoint
                response = self.request_api("POST", endpoint=url, request_data=record)
                id = response.json().get("id")
                self.logger.info(f"{self.name} created with id: {id}")
            except:
                raise