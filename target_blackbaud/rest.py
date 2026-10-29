"""Blackbaud transport: bounded throttling and no ambiguous write retries."""

import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from hotglue_singer_sdk.target_sdk.common import HGJSONEncoder


class Rest:
    timeout = 60

    @property
    def http_headers(self):
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.authenticator.auth_headers,
        }

    def request_api(
        self, http_method, endpoint=None, params=None, request_data=None, headers=None
    ):
        method = http_method.upper()
        for attempt in range(5):
            try:
                response = requests.request(
                    method,
                    self.url(endpoint),
                    params=params,
                    headers={**self.http_headers, **(headers or {})},
                    data=(
                        json.dumps(request_data, cls=HGJSONEncoder)
                        if request_data is not None
                        else None
                    ),
                    timeout=self.timeout,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                # A timed-out write may already have succeeded. Never replay it.
                if method != "GET" or attempt == 4:
                    raise
                time.sleep(2**attempt)
                continue

            retryable = response.status_code == 429 or (
                method == "GET" and response.status_code >= 500
            )
            if retryable and attempt < 4:
                delay = 2**attempt
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = (
                            parsedate_to_datetime(retry_after)
                            - datetime.now(timezone.utc)
                        ).total_seconds()
                # Do not retry before a long provider-requested cooldown.
                if delay > 300:
                    self.validate_response(response)
                time.sleep(max(0, delay))
                continue
            self.validate_response(response)
            return response

    def validate_response(self, response):
        if response.status_code in (401, 403):
            raise InvalidCredentialsError(
                f"Blackbaud authorization failed (HTTP {response.status_code})"
            )
        if not 200 <= response.status_code < 300:
            raise InvalidPayloadError(
                f"Blackbaud HTTP {response.status_code}: {response.text}"
            )
