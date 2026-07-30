from fastapi import APIRouter, Response, Depends
from atmos_gl.db.volcanic_activity_adapter import VolcanicActivityAdapter

router = APIRouter(prefix="/api", tags=["Geology"])


def get_volcano_adapter() -> VolcanicActivityAdapter:
    return VolcanicActivityAdapter()


@router.get("/volcanoes/geojson")
async def get_volcanoes(
    volcano_adapter: VolcanicActivityAdapter = Depends(get_volcano_adapter),
):
    geojson_string = volcano_adapter.get_activity_as_geojson()
    return Response(content=geojson_string, media_type="application/json")
