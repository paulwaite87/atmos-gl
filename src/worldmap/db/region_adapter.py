import logging

from sqlalchemy import case, func, select

from worldmap.db.engine import Session
from worldmap.db.models import MapRegion

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

    def is_in_region(self, lat, lon, region_label):
        """Quick boolean check if a point is inside a specific region."""
        point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
        stmt = select(1).where(
            MapRegion.label == region_label, func.ST_Contains(MapRegion.boundary, point)
        )
        with Session() as session:
            return session.execute(stmt).first() is not None

    def get_priority_region_list(self, primary_region_label):
        """Returns all regions from the database, ordered so the primary_region_label
        is first. Includes bounding box coordinates."""
        stmt = (
            select(
                MapRegion.label,
                func.ST_XMin(MapRegion.boundary).label("lon_min"),
                func.ST_YMin(MapRegion.boundary).label("lat_min"),
                func.ST_XMax(MapRegion.boundary).label("lon_max"),
                func.ST_YMax(MapRegion.boundary).label("lat_max"),
            )
            .order_by(
                case((MapRegion.label == primary_region_label, 0), else_=1),
                MapRegion.label.asc(),
            )
        )
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
            logger.error(f"Error fetching priority region list: {e}")
            return []


class FakeRegionAdapter:
    """In-memory fake for map_region, matching RegionAdapter's method contracts."""

    def __init__(self):
        self._regions: dict[str, dict] = {}

    def get_region_definition(self, label):
        region = self._regions.get(label)
        return dict(region) if region else None

    def is_in_region(self, lat, lon, region_label):
        region = self._regions.get(region_label)
        if region is None:
            return False
        return (
            region["lon_min"] <= lon <= region["lon_max"]
            and region["lat_min"] <= lat <= region["lat_max"]
        )

    def get_priority_region_list(self, primary_region_label):
        labels = sorted(self._regions.keys())
        if primary_region_label in labels:
            labels.remove(primary_region_label)
            labels.insert(0, primary_region_label)
        return [{"label": label, **self._regions[label]} for label in labels]
