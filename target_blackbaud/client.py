from functools import cached_property
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from hotglue_singer_sdk.plugin_base import PluginBase
from hotglue_singer_sdk.target_sdk.client import HotglueBatchSink
from hotglue_singer_sdk.target_sdk.sinks import HotglueSink

from target_blackbaud.auth import BlackbaudAuthenticator
from target_blackbaud.rest import Rest
from target_blackbaud.utils import clean_null


class _BlackbaudRestMixin(Rest):
    """Shared REST/auth helpers for record and batch Blackbaud sinks."""

    def __init__(
        self,
        target: PluginBase,
        stream_name: str,
        schema: Dict,
        key_properties: Optional[List[str]],
    ) -> None:
        self._target = target
        self.processed_hashes = []
        super().__init__(target, stream_name, schema, key_properties)

    @cached_property
    def authenticator(self) -> BlackbaudAuthenticator:
        return BlackbaudAuthenticator(
            self._target, {}, "https://oauth2.sky.blackbaud.com/token"
        )

    @property
    def base_url(self):
        return "https://api.sky.blackbaud.com/"

    def url(self, endpoint=None):
        if not endpoint:
            endpoint = self.endpoint
        return f"{self.base_url}{endpoint}"

    def validate_output(self, mapping):
        return clean_null(mapping)


class BlackbaudSink(_BlackbaudRestMixin, HotglueSink):
    """Blackbaud record sink class."""

    unified_schema = None

    @property
    def name(self):
        raise NotImplementedError

    @property
    def endpoint(self):
        raise NotImplementedError

    def validate_input(self, record: dict):
        return self.unified_schema(**record).dict()

    def get_reference_data(self, stream, fields=None, filter={}):
        page = 1
        data = []
        params = {"per_page": 100, "order": "asc", "page": page}
        params.update(filter)
        while True:
            resp = self.request_api("GET", stream, params)
            total_pages = resp.headers.get("X-WP-TotalPages")
            resp = resp.json()
            if fields:
                resp = [{k: v for k, v in r.items() if k in fields} for r in resp]
            data += resp

            if resp and int(total_pages) > page:
                page += 1
                params.update({"page": page})
            else:
                break
        return data


class BlackbaudBatchSink(_BlackbaudRestMixin, HotglueBatchSink):
    """Blackbaud batch sink class."""

    unified_schema = None

    @property
    def name(self):
        raise NotImplementedError

    @property
    def endpoint(self):
        raise NotImplementedError


class BlackbaudNativeSink(BlackbaudSink):
    """Native SKY records; keep SDK identifiers out of copied HTTP payloads."""

    def preprocess_record(self, record, context):
        return dict(record)

    @staticmethod
    def api_payload(record):
        payload = {k: v for k, v in record.items() if k not in ("id", "externalId")}
        for key in ("constituent_id", "parent_id"):
            if payload.get(key) is not None:
                payload[key] = str(payload[key])
        if payload.get("gift_splits"):
            payload["gift_splits"] = [
                {
                    k: (
                        str(v)
                        if k in ("fund_id", "appeal_id", "package_id", "campaign_id")
                        and v is not None
                        else v
                    )
                    for k, v in split.items()
                }
                for split in payload["gift_splits"]
            ]
        return payload

    def build_record_hash(self, record):
        # Snapshot resolution adds the remote ID on a repeat import. It is not
        # a content change and must not defeat the SDK's unchanged-record skip.
        if record.get("externalId"):
            record = {k: v for k, v in record.items() if k != "id"}
        return super().build_record_hash(record)

    @staticmethod
    def created_id(response, key="id"):
        remote_id = response.json().get(key)
        if remote_id is None or remote_id == "":
            raise ValueError(
                "Blackbaud create response did not contain a remote ID; reconcile before retrying"
            )
        return str(remote_id)

    def list_records(self, endpoint, params=None):
        """Follow every SKY next_link, without forwarding credentials elsewhere."""
        records = []
        while endpoint:
            body = self.request_api("GET", endpoint, params=params).json()
            records.extend(body.get("value", []))
            next_link = body.get("next_link")
            if not next_link:
                break
            parsed = urlsplit(next_link)
            if parsed.netloc and (
                parsed.scheme != "https" or parsed.netloc != "api.sky.blackbaud.com"
            ):
                raise ValueError("Unexpected host in Blackbaud pagination link")
            endpoint = parsed.path.lstrip("/")
            if parsed.query:
                endpoint += "?" + parsed.query
            params = None
        return records

    def upsert_record(self, record, context):
        remote_id = record.get("id")
        payload = self.api_payload(record)
        if remote_id:
            self.request_api(
                "PATCH", f"{self.endpoint}/{remote_id}", request_data=payload
            )
            return str(remote_id), True, {"is_updated": True}
        response = self.request_api("POST", self.endpoint, request_data=payload)
        return self.created_id(response), True, {}
