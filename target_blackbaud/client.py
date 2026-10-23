from hotglue_singer_sdk.target_sdk.client import HotglueBatchSink
from hotglue_singer_sdk.target_sdk.sinks import HotglueSink
from typing import Dict, List, Optional

from target_blackbaud.rest import Rest
from singer_sdk.plugin_base import PluginBase
from target_blackbaud.auth import BlackbaudAuthenticator
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
        super().__init__(target, stream_name, schema, key_properties)

    @property
    def authenticator(self) -> BlackbaudAuthenticator:
        return BlackbaudAuthenticator(
            self._target, "https://oauth2.sky.blackbaud.com/token"
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

    @property
    def name(self):
        raise NotImplementedError

    @property
    def endpoint(self):
        raise NotImplementedError

    @property
    def unified_schema(self):
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
