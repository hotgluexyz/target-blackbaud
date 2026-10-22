"""Blackbaud target class."""
from typing import Type
from singer_sdk.sinks import Sink
from hotglue_singer_sdk.target_sdk.target import TargetHotglue
from singer_sdk import typing as th

from target_blackbaud.sinks import (
    ContactsSink,
    ConstituentsSink,
    OpportunitiesSink,
    ActionsSink,
    UnsubscribesSink
)

SINK_TYPES = [
    ContactsSink,
    ConstituentsSink,
    OpportunitiesSink,
    ActionsSink,
    UnsubscribesSink,
]

class TargetBlackbaud(TargetHotglue):
    """Sample target for Blackbaud."""

    name = "target-blackbaud"
    SINK_TYPES = SINK_TYPES

    def __init__(
        self,
        config=None,
        parse_env_config=False,
        validate_config=True,
        state=None,
    ) -> None:
        self.config_file = config[0]
        super().__init__(config, parse_env_config, validate_config)

    config_jsonschema = th.PropertiesList(
        th.Property(
            "filepath",
            th.StringType,
            description="The path to the target output file"
        )
    ).to_dict()
    
    def get_sink_class(self, stream_name: str) -> Type[Sink]:
        """Get sink for a stream."""
        return next(
            (
                sink_class
                for sink_class in SINK_TYPES
                if sink_class.name.lower() == stream_name.lower()
            ),
            None,
        )


if __name__ == "__main__":
    TargetBlackbaud.cli()