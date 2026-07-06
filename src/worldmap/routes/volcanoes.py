from fastapi import APIRouter, Response, Query, Depends
from worldmap.db.volcano_adapter import VolcanoAdapter

router = APIRouter(prefix="/api", tags=["Geology"])


def get_volcano_adapter() -> VolcanoAdapter:
    return VolcanoAdapter()


@router.get("/volcanoes/geojson")
async def get_volcanoes(
    vei_min: int = Query(0),
    significant: bool = Query(False),
    codes: str = Query(...),
    volcano_adapter: VolcanoAdapter = Depends(get_volcano_adapter),
):
    # 1. Force it into a Python list (e.g. "D1,D2" -> ['D1', 'D2'])
    # We strip whitespace just in case the URL has spaces like "D1, D2"
    codes_list = [c.strip() for c in codes.split(",")]

    # Debug print to terminal
    print(
        f"API DEBUG: VEI >= {vei_min} | Significant: {significant} | Codes List: {codes_list}"
    )

    geojson_string = volcano_adapter.get_volcanoes_as_geojson(vei_min, significant, codes_list)

    return Response(content=geojson_string, media_type="application/json")
