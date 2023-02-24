"""Salesloft Authentication."""
import json
from datetime import datetime
from typing import Optional
from base64 import b64encode
from typing import Any, Dict, Optional

import logging
import requests


class BlackbaudAuthenticator:
    """API Authenticator for OAuth 2.0 flows."""

    def __init__(
        self,
        target,
        auth_endpoint: Optional[str] = None,
    ) -> None:
        """Init authenticator.

        Args:
            stream: A stream for a RESTful endpoint.
        """
        self.target_name: str = target.name
        self._config: Dict[str, Any] = dict(target.config)
        self._auth_headers: Dict[str, Any] = {}
        self._auth_params: Dict[str, Any] = {}
        self.logger: logging.Logger = target.logger
        self._auth_endpoint = auth_endpoint
        self._config_file = target.config_file
        self._target = target
        self.update_access_token()

    @property
    def auth_endpoint(self) -> str:
        """Get the authorization endpoint.

        Returns:
            The API authorization endpoint if it is set.

        Raises:
            ValueError: If the endpoint is not set.
        """
        if not self._auth_endpoint:
            raise ValueError("Authorization endpoint not set.")
        return self._auth_endpoint

    @property
    def auth_headers(self) -> dict:
        """Return a dictionary of auth headers to be applied.

        These will be merged with any `http_headers` specified in the stream.

        Returns:
            HTTP headers for authentication.
        """
        if not self.is_token_valid():
            self.update_access_token()
        result = {}
        result["Authorization"] = f"Bearer {self._config.get('access_token')}"
        result["Bb-Api-Subscription-Key"] = self._config.get('subscription_key')
        return result
    
    @property
    def auth_endpoint(self) -> str:
        """Get the authorization endpoint.

        Returns:
            The API authorization endpoint if it is set.

        Raises:
            ValueError: If the endpoint is not set.
        """
        if not self._auth_endpoint:
            raise ValueError("Authorization endpoint not set.")
        return self._auth_endpoint


    @property
    def oauth_request_body(self) -> dict:
        """Define the OAuth request body for the hubspot API."""
        return {
            'grant_type': "refresh_token",
            'refresh_token': self._config["refresh_token"]
        }

    def is_token_valid(self) -> bool:
        access_token = self._config.get("access_token")
        now = round(datetime.utcnow().timestamp())
        expires_in = self._config.get("expires_in")
        return not bool(
            (not access_token) or (not expires_in) or ((expires_in - now) < 60)
        )

    @property
    def oauth_request_payload(self) -> dict:
        """Get request body.

        Returns:
            A plain (OAuth) or encrypted (JWT) request body.
        """
        return self.oauth_request_body

    def token_authenticator(self):
        user = self._config.get("client_id")
        passwd = self._config.get("client_secret")
        token = b64encode(f"{user}:{passwd}".encode()).decode()
        return f"Basic {token}"

    # Authentication and refresh
    def update_access_token(self) -> None:
        """Update `access_token` along with: `last_refreshed` and `expires_in`.

        Raises:
            RuntimeError: When OAuth login fails.
        """
        request_time = round(datetime.utcnow().timestamp())
        auth_request_payload = self.oauth_request_payload
        headers = {}
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers.update({"Authorization": self.token_authenticator()})
        token_response = requests.post(self.auth_endpoint, data=auth_request_payload, headers=headers)
        try:
            token_response.raise_for_status()
            self.logger.info("OAuth authorization attempt was successful.")
        except Exception as ex:
            raise RuntimeError(
                f"Failed OAuth login, response was '{token_response.json()}'. {ex}"
            )
        token_json = token_response.json()
        self.access_token = token_json["access_token"]
        expires_in = request_time + token_json["expires_in"]

        self._config["access_token"] = token_json["access_token"]
        self._config["expires_in"] = expires_in
        self._config["refresh_token"] = token_json["refresh_token"]
        with open(self._target.config_file, "w") as outfile:
            json.dump(self._config, outfile, indent=4)

