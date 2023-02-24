"""Blackbaud target class."""
from typing import Type
from singer_sdk.sinks import Sink
from singer_sdk.target_base import Target
from singer_sdk import typing as th

from target_blackbaud.sinks import (
    ContactsSink
)

SINK_TYPES = [ContactsSink]

class TargetBlackbaud(Target):
    """Sample target for Blackbaud."""

    name = "target-blackbaud"

    def __init__(
        self,
        config=None,
        parse_env_config=False,
        validate_config=True,
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