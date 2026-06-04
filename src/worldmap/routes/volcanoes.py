from fastapi import APIRouter, Response, Query
from worldmap.lib.db import Database

router = APIRouter(prefix="/api")

@router.get("/volcanoes/geojson")
async def get_volcanoes(
    vei_min: int = 5,
    significant: bool = False,
    codes: str = Query(...) # e.g. "D1,D2"
):
    db = Database()
    codes_list = codes.split(',')
    return Response(content=db.get_volcanoes_as_geojson(vei_min, significant, codes_list), media_type="application/json")