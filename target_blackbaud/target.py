"""Blackbaud target class."""

from typing import Type

import hotglue_singer_sdk.target_sdk.target as target_sdk
from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.sinks import Sink
from hotglue_singer_sdk.target_sdk.target import TargetHotglue

from target_blackbaud.auth import BlackbaudAuthenticator
from target_blackbaud.sinks import (
    ActionsSink,
    AddressesSink,
    ConstituentCustomFieldsSink,
    ConstituentsSink,
    ContactsSink,
    DirectGiftsSink,
    GiftBatchesSink,
    GiftCustomFieldsSink,
    GiftsSink,
    OpportunitiesSink,
    PhonesSink,
    RecurringGiftStatusesSink,
    UnsubscribesSink,
)

SINK_TYPES = [
    ContactsSink,
    ConstituentsSink,
    GiftsSink,
    OpportunitiesSink,
    ActionsSink,
    UnsubscribesSink,
    AddressesSink,
    PhonesSink,
    ConstituentCustomFieldsSink,
    GiftCustomFieldsSink,
    RecurringGiftStatusesSink,
    GiftBatchesSink,
]


class RelationResolutionError(ValueError):
    """A rejected child relationship, with its mapped record and sink."""

    def __init__(self, message, sink_name, record):
        super().__init__(message)
        self.sink_name = sink_name
        self.record = record


class TargetBlackbaud(TargetHotglue):
    """Sample target for Blackbaud."""

    name = "target-blackbaud"
    SINK_TYPES = SINK_TYPES

    @classmethod
    def access_token_support(cls, connector=None):
        return BlackbaudAuthenticator, "https://oauth2.sky.blackbaud.com/token"

    config_jsonschema = th.PropertiesList(
        th.Property("subscription_key", th.StringType, required=True),
        th.Property("access_token", th.StringType),
        th.Property("expires_in", th.IntegerType),
        th.Property("client_id", th.StringType),
        th.Property("client_secret", th.StringType),
        th.Property("refresh_token", th.StringType),
        th.Property("_refresh_token_via_hg_api", th.BooleanType, default=True),
        th.Property(
            "gift_write_mode",
            th.CustomType({"type": "string", "enum": ["batch", "direct"]}),
            default="direct",
        ),
        th.Property(
            "gift_batch_description", th.StringType, default="hotglue gifts import"
        ),
        th.Property(
            "filepath", th.StringType, description="The path to the target output file"
        ),
        th.Property(
            "default_fund_id",
            th.StringType,
            description=(
                "Blackbaud fund system record id used for gift_splits when the "
                "ETL payload does not include gift_splits"
            ),
        ),
        th.Property(
            "gift_batch_id",
            th.StringType,
            description=(
                "Optional Blackbaud gift batch system record id for AddGiftsToBatch. "
                "If omitted, the target creates a new unapproved gift batch per job."
            ),
        ),
    ).to_dict()

    def get_sink_class(self, stream_name: str) -> Type[Sink]:
        """Get sink for a stream."""
        if (
            stream_name.lower() == "gifts"
            and self.config.get("gift_write_mode") == "direct"
        ):
            return DirectGiftsSink
        return next(
            (
                sink_class
                for sink_class in SINK_TYPES
                if sink_class.name.lower() == stream_name.lower()
            ),
            None,
        )

    def read_snapshot(self, object_name):
        snapshot = super().read_snapshot(object_name)
        if snapshot is not None:
            snapshot["InputId"] = snapshot["InputId"].astype(str)
        return snapshot

    def _process_record_message(self, message_dict):
        """Report rejected relationships per record without losing drained parents."""
        try:
            super()._process_record_message(message_dict)
        except RelationResolutionError as error:
            sink = self.get_sink(error.sink_name, record=error.record)
            # SDK sink initialization may load previous-job state. Keep the
            # current state, including any parents drained during resolution.
            current_state = self._latest_state
            if not sink.latest_state:
                sink.init_state()
            self._latest_state = current_state
            sink.update_state(
                sink._build_record_error_state(error, record=error.record),
                record=error.record,
            )
            for key in ("bookmarks", "summary"):
                self._latest_state.setdefault(key, {})[sink.name] = sink.latest_state[
                    key
                ][sink.name]

    def get_record_id(self, sink_name, record, relation_fields=None):
        external_id = record.get(self.EXTERNAL_ID_KEY)
        own_bookmarks = self._latest_state.get("bookmarks", {}).get(sink_name, [])
        if external_id is not None and not record.get("id"):
            successful = [
                b
                for b in own_bookmarks
                if b.get("success")
                and str(b.get("externalId")) == str(external_id)
                and b.get("id")
            ]
            if successful:
                record["id"] = successful[-1]["id"]
        # Do not allow a failed parent with a known id to route a child write.
        unresolved = []
        for relation in relation_fields or []:
            parent_sink = self._sinks_active.get(relation["objectName"])
            if isinstance(parent_sink, GiftsSink):
                self.drain_one(parent_sink)
                if parent_sink.latest_state:
                    for key in ("bookmarks", "summary"):
                        self._latest_state.setdefault(key, {})[parent_sink.name] = (
                            parent_sink.latest_state[key][parent_sink.name]
                        )
            value = record.get(relation["field"])
            bookmarks = self._latest_state.get("bookmarks", {}).get(
                relation["objectName"], []
            )
            matching = (
                [b for b in bookmarks if str(b.get("externalId")) == str(value)]
                if value is not None
                else []
            )
            if matching and not matching[-1].get("success"):
                raise RelationResolutionError(
                    f"Cannot resolve {relation['field']}: parent record failed",
                    sink_name,
                    record,
                )
            if matching and matching[-1].get("id"):
                parent = matching[-1]
                if relation["objectName"] == "gifts" and (
                    parent.get("batch_id")
                    or self.config.get("gift_write_mode") != "direct"
                ):
                    if not parent.get("approved_gift_id"):
                        raise RelationResolutionError(
                            "Batch gift is not approved; a direct gift ID is required",
                            sink_name,
                            record,
                        )
                    record[relation["field"]] = parent["approved_gift_id"]
                else:
                    record[relation["field"]] = parent["id"]
            else:
                if (
                    value is not None
                    and relation["objectName"] == "gifts"
                    and self.config.get("gift_write_mode") != "direct"
                ):
                    snapshot = self.read_snapshot(f"gifts_{target_sdk.flow_id}")
                    if (
                        snapshot is not None
                        and (snapshot["InputId"] == str(value)).any()
                    ):
                        raise RelationResolutionError(
                            "Batch gift snapshot cannot establish an approved direct gift ID",
                            sink_name,
                            record,
                        )
                if value is not None:
                    record[relation["field"]] = str(value)
                unresolved.append(relation)
        return super().get_record_id(sink_name, record, unresolved)


if __name__ == "__main__":
    TargetBlackbaud.cli()
