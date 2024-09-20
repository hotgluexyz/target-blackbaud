from singer_sdk.sinks import RecordSink
from typing import Any, Dict, List, Optional, Callable, cast

from target_blackbaud.rest import Rest
from singer_sdk.plugin_base import PluginBase
from target_blackbaud.auth import BlackbaudAuthenticator
from target_blackbaud.utils import clean_null

class BlackbaudSink(RecordSink, Rest):
    """BlackbaudSink target sink class."""

    def __init__(
        self,
        target: PluginBase,
        stream_name: str,
        schema: Dict,
        key_properties: Optional[List[str]],
    ) -> None:
        """Initialize target sink."""
        self._target = target
        super().__init__(target, stream_name, schema, key_properties)


    @property
    def authenticator(self) -> BlackbaudAuthenticator:
        """Return a new authenticator object."""
        return BlackbaudAuthenticator(
            self._target, "https://oauth2.sky.blackbaud.com/token"
        )

    @property
    def name(self):
        raise NotImplementedError

    @property
    def endpoint(self):
        raise NotImplementedError

    @property
    def unified_schema(self):
        raise NotImplementedError

    @property
    def base_url(self):
        site_url = "https://api.sky.blackbaud.com/"
        return site_url

    def url(self, endpoint=None):
        if not endpoint:
            endpoint = self.endpoint
        return f"{self.base_url}{endpoint}"

    def validate_input(self, record: dict):
        return self.unified_schema(**record).dict()

    def validate_output(self, mapping):
        payload = clean_null(mapping)
        return payload

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

    def init_state(self):
        self.latest_state = self.latest_state or {"bookmarks": {}}
        if self.name not in self.latest_state["bookmarks"]:
            if not self.latest_state["bookmarks"].get(self.name):
                self.latest_state["bookmarks"][self.name] = []
