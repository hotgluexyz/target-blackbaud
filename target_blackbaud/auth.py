"""Blackbaud OAuth using Hotglue-managed refresh and shared token state."""

from hotglue_singer_sdk.helpers._util import utc_now
from hotglue_singer_sdk.target_sdk.auth import OAuthAuthenticator


class BlackbaudAuthenticator(OAuthAuthenticator):
    """Use the SDK's refresh implementation without logging credentials."""

    def __init__(self, target, state, auth_endpoint=None):
        super().__init__(target, state, auth_endpoint=auth_endpoint)
        # A supplied absolute expiry is valid across process restarts. The SDK
        # otherwise refreshes on the first request even if the token is fresh.
        if self._config.get("access_token") and self._config.get("expires_in"):
            self.last_refreshed = utc_now()

    @property
    def auth_headers(self):
        return {
            **super().auth_headers,
            "Bb-Api-Subscription-Key": self._config["subscription_key"],
        }
