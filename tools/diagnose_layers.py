#!/usr/bin/env python3
"""
diagnose_layers.py — Find why a layer's output is absent.

Checks, for each product, the three places the pipeline can silently stall:
  1. Is the field in the catalog (collector stored it)?
  2. Does the .npz field file exist on disk (fieldstore wrote it)?
  3. Does the rendered _data.png exist (task plotted it)?

Run inside the container (same env as layer_builder), e.g.:
  python3 diagnose_layers.py --config /path/to/atmos-gl.json
"""

import os
import argparse
import glob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--products", default="isobars,precipitation,wind,temperature,ozone,stormwatch")
    args = ap.parse_args()

    from atmos_gl.lib.config import AtmosGLConfig
    from atmos_gl.db.engine import Session
    from atmos_gl.db.models import FieldCatalog
    from sqlalchemy import func, select

    config = AtmosGLConfig(args.config)
    config.load()
    workdir = config.get_setting("common", "workdir", ".")
    data_dir = os.path.join(workdir, "data")
    products = [p.strip() for p in args.products.split(",")]

    print(f"workdir   = {workdir}")
    print(f"data_dir  = {data_dir}")
    print("=" * 70)

    # 1. Catalog contents
    print("\n[1] field_catalog rows per product:")
    try:
        stmt = (
            select(
                FieldCatalog.product,
                FieldCatalog.run_date,
                FieldCatalog.run_id,
                func.count().label("n"),
                func.min(FieldCatalog.fhour).label("fmin"),
                func.max(FieldCatalog.fhour).label("fmax"),
                func.max(FieldCatalog.updated_at).label("latest"),
            )
            .group_by(FieldCatalog.product, FieldCatalog.run_date, FieldCatalog.run_id)
            .order_by(
                FieldCatalog.product, FieldCatalog.run_date.desc(), FieldCatalog.run_id.desc()
            )
        )
        with Session() as session:
            rows = session.execute(stmt).all()
        if not rows:
            print("    (catalog EMPTY — collector hasn't stored anything)")
        for r in rows:
            print(f"    {r.product:13} {r.run_date} {r.run_id}Z  "
                  f"hours f{r.fmin:03d}..f{r.fmax:03d} ({r.n} rows)  latest={r.latest}")
    except Exception as e:
        print(f"    catalog query FAILED: {e}")

    # 2. Field files on disk
    print("\n[2] .npz field files on disk (under data/fields):")
    fields_root = os.path.join(data_dir, "fields")
    for prod in products:
        hits = glob.glob(os.path.join(fields_root, "*", "*", f"{prod}_f*.npz"))
        if hits:
            sizes = sum(os.path.getsize(h) for h in hits) / 1e6
            print(f"    {prod:13} {len(hits)} file(s), {sizes:.1f} MB total")
        else:
            print(f"    {prod:13} NONE")

    # 3. Rendered outputs
    print("\n[3] rendered outputs in data/ (per-hour PNG + _data.png texture):")
    for prod in products:
        static = sorted(glob.glob(os.path.join(data_dir, f"{prod}_f*.png")))
        static = [s for s in static if not s.endswith("_data.png")]
        texture = sorted(glob.glob(os.path.join(data_dir, f"{prod}_f*_data.png")))
        print(f"    {prod:13} static={len(static)}  data_texture={len(texture)}")
        for t in texture[:3]:
            print(f"        texture: {os.path.basename(t)} ({os.path.getsize(t)/1e3:.0f} KB)")

    print("\n" + "=" * 70)
    print("Interpretation:")
    print("  catalog row present + .npz present + texture MISSING -> plot() is failing")
    print("       (check layer_builder logs for \"Task '<product>' execution failed\")")
    print("  catalog row present + .npz MISSING  -> fieldstore write / path mismatch")
    print("  catalog row MISSING                 -> collector didn't store (unpack threw,")
    print("       or field_exists() short-circuited the download)")


if __name__ == "__main__":
    main()
