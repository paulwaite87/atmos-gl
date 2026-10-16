import json
import logging

logger = logging.getLogger(__name__)


class GdacsAdapter:
    """Real adapter for GDACS events and footprints, backed by SQLAlchemy.

    Driven by tests/test_gdacs_adapter_real_vs_fake.py -- methods are filled in there,
    one contract scenario at a time, against the same scenarios FakeGdacsAdapter runs.
    """

    def upsert_event(self, event: dict) -> None:
        raise NotImplementedError

    def get_events_as_geojson(self) -> str:
        raise NotImplementedError


class FakeGdacsAdapter:
    """In-memory fake for GDACS events, matching GdacsAdapter's method contracts."""

    def __init__(self):
        self._events: dict[tuple[str, int], dict] = {}

    def upsert_event(self, event: dict) -> None:
        self._events[(event["event_type"], event["event_id"])] = dict(event)

    def get_events_as_geojson(self) -> str:
        features = []
        for e in self._events.values():
            props = {k: v for k, v in e.items() if k not in ("lat", "lon")}
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                    "properties": props,
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})
