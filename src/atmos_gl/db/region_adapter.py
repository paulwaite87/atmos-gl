import logging

from sqlalchemy import func, select

from atmos_gl.db.engine import Session
from atmos_gl.db.models import MapRegion

logger = logging.getLogger(__name__)


class RegionAdapter:
    """Real adapter for map_region, backed by SQLAlchemy."""

    def get_region_definition(self, label):
        """Fetches the bounding box for a specific region label."""
        stmt = select(
            func.ST_XMin(MapRegion.boundary).label("lon_min"),
            func.ST_YMin(MapRegion.boundary).label("lat_min"),
            func.ST_XMax(MapRegion.boundary).label("lon_max"),
            func.ST_YMax(MapRegion.boundary).label("lat_max"),
        ).where(MapRegion.label == label)
        with Session() as session:
            row = session.execute(stmt).first()
            if row is None:
                return None
            return {
                "lon_min": row.lon_min,
                "lat_min": row.lat_min,
                "lon_max": row.lon_max,
                "lat_max": row.lat_max,
            }

    def get_all_regions(self):
        """Returns all regions from the database, alphabetical by label, with bounding
        box coordinates. Used by LightningCollector's grid-point queue, which
        prioritizes by live viewport proximity rather than a static "primary region"."""
        stmt = select(
            MapRegion.label,
            func.ST_XMin(MapRegion.boundary).label("lon_min"),
            func.ST_YMin(MapRegion.boundary).label("lat_min"),
            func.ST_XMax(MapRegion.boundary).label("lon_max"),
            func.ST_YMax(MapRegion.boundary).label("lat_max"),
        ).order_by(MapRegion.label.asc())
        try:
            with Session() as session:
                rows = session.execute(stmt).all()
                return [
                    {
                        "label": r.label,
                        "lon_min": r.lon_min,
                        "lat_min": r.lat_min,
                        "lon_max": r.lon_max,
                        "lat_max": r.lat_max,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Error fetching region list: {e}")
            return []


class FakeRegionAdapter:
    """In-memory fake for map_region, matching RegionAdapter's method contracts."""

    def __init__(self):
        self._regions: dict[str, dict] = {}

    def get_region_definition(self, label):
        region = self._regions.get(label)
        return dict(region) if region else None

    def get_all_regions(self):
        labels = sorted(self._regions.keys())
        return [{"label": label, **self._regions[label]} for label in labels]
