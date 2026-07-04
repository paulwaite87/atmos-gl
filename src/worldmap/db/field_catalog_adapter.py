import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from worldmap.db.engine import Session
from worldmap.db.models import BackfillRequest, FieldCatalog

logger = logging.getLogger(__name__)

_CATALOG_KEY_COLUMNS = (
    FieldCatalog.run_date,
    FieldCatalog.run_id,
    FieldCatalog.fhour,
    FieldCatalog.product,
)

_BACKFILL_KEY_COLUMNS = (
    BackfillRequest.run_date,
    BackfillRequest.run_id,
    BackfillRequest.fhour,
    BackfillRequest.product,
)


def _row_to_dict(row):
    return {
        "run_date": row.run_date,
        "run_id": row.run_id,
        "fhour": row.fhour,
        "product": row.product,
        "storage_uri": row.storage_uri,
    }


class FieldCatalogAdapter:
    """Real adapter for field_catalog + backfill_requests, backed by SQLAlchemy."""

    def products_with_data(self, candidates):
        """Of `candidates`, return the subset that actually have catalogued field rows for
        the freshest (run_date, run_id) among those candidates."""
        if not candidates:
            return []
        candidates = list(candidates)
        with Session() as session:
            latest = session.execute(
                select(FieldCatalog.run_date, FieldCatalog.run_id)
                .where(FieldCatalog.product.in_(candidates))
                .order_by(FieldCatalog.run_date.desc(), FieldCatalog.run_id.desc())
                .limit(1)
            ).first()
            if not latest:
                return []
            run_date, run_id = latest
            rows = session.execute(
                select(FieldCatalog.product)
                .distinct()
                .where(
                    FieldCatalog.run_date == run_date,
                    FieldCatalog.run_id == run_id,
                    FieldCatalog.product.in_(candidates),
                )
            ).all()
            return [r[0] for r in rows]

    def get_latest_run_hours(self, products=None):
        """Return availability summary for the freshest (date, run) in the catalog,
        optionally scoped to `products` (an hour counts only when ALL are present, and
        the freshest run is resolved within those products only)."""
        with Session() as session:
            run_stmt = select(FieldCatalog.run_date, FieldCatalog.run_id)
            if products:
                run_stmt = run_stmt.where(FieldCatalog.product.in_(list(products)))
            run_stmt = run_stmt.order_by(
                FieldCatalog.run_date.desc(), FieldCatalog.run_id.desc()
            ).limit(1)
            run = session.execute(run_stmt).first()
            if not run:
                return None
            run_date, run_id = run

            if products:
                products = list(products)
                hours_stmt = (
                    select(FieldCatalog.fhour)
                    .where(
                        FieldCatalog.run_date == run_date,
                        FieldCatalog.run_id == run_id,
                        FieldCatalog.product.in_(products),
                    )
                    .group_by(FieldCatalog.fhour)
                    .having(func.count(func.distinct(FieldCatalog.product)) == len(products))
                    .order_by(FieldCatalog.fhour)
                )
            else:
                hours_stmt = (
                    select(FieldCatalog.fhour)
                    .distinct()
                    .where(FieldCatalog.run_date == run_date, FieldCatalog.run_id == run_id)
                    .order_by(FieldCatalog.fhour)
                )
            hours = [r[0] for r in session.execute(hours_stmt).all()]

        if not hours:
            return {
                "run_date": run_date,
                "run_id": run_id,
                "fmin": None,
                "fmax": None,
                "hours": [],
                "n_products": 0,
            }

        return {
            "run_date": run_date,
            "run_id": run_id,
            "fmin": hours[0],
            "fmax": hours[-1],
            "hours": hours,
        }

    def upsert_field_catalog(
        self,
        run_date,
        run_id,
        fhour,
        product,
        nlat,
        nlon,
        valid_time=None,
        storage_uri=None,
    ):
        """Upsert a field catalog row (metadata only, no arrays)."""
        stmt = pg_insert(FieldCatalog).values(
            run_date=run_date,
            run_id=run_id,
            fhour=int(fhour),
            product=product,
            nlat=nlat,
            nlon=nlon,
            valid_time=valid_time,
            storage_uri=storage_uri,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=list(_CATALOG_KEY_COLUMNS),
            set_={
                "nlat": stmt.excluded.nlat,
                "nlon": stmt.excluded.nlon,
                "valid_time": stmt.excluded.valid_time,
                "storage_uri": stmt.excluded.storage_uri,
                "updated_at": func.now(),
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error upserting field catalog: {e}")
            raise

    def get_field_catalog(self, run_date, run_id, fhour, product):
        """Fetch a catalog row (metadata only)."""
        stmt = select(FieldCatalog).where(
            FieldCatalog.run_date == run_date,
            FieldCatalog.run_id == run_id,
            FieldCatalog.fhour == int(fhour),
            FieldCatalog.product == product,
        )
        with Session() as session:
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            return {
                "run_date": row.run_date,
                "run_id": row.run_id,
                "fhour": row.fhour,
                "product": row.product,
                "nlat": row.nlat,
                "nlon": row.nlon,
                "valid_time": row.valid_time,
                "updated_at": row.updated_at,
                "storage_uri": row.storage_uri,
            }

    def get_product_hours(self, run_date, run_id, product):
        """Return the sorted list of forecast hours present for one product in a run."""
        stmt = (
            select(FieldCatalog.fhour)
            .where(
                FieldCatalog.run_date == run_date,
                FieldCatalog.run_id == run_id,
                FieldCatalog.product == product,
            )
            .order_by(FieldCatalog.fhour)
        )
        with Session() as session:
            return [r[0] for r in session.execute(stmt).all()]

    def get_live_product_hours(self):
        """Return the set of all (product, fhour) pairs present anywhere in the catalog,
        across every run/date."""
        stmt = select(FieldCatalog.product, FieldCatalog.fhour).distinct()
        with Session() as session:
            return {(product, int(fhour)) for product, fhour in session.execute(stmt).all()}

    def field_catalog_exists(self, run_date, run_id, fhour, product):
        """Check if a catalog row exists (fast, indexed)."""
        stmt = (
            select(FieldCatalog.product)
            .where(
                FieldCatalog.run_date == run_date,
                FieldCatalog.run_id == run_id,
                FieldCatalog.fhour == int(fhour),
                FieldCatalog.product == product,
            )
            .limit(1)
        )
        with Session() as session:
            return session.execute(stmt).first() is not None

    def delete_field_catalog(self, run_date, run_id, fhour, product):
        """Delete a catalog row."""
        stmt = delete(FieldCatalog).where(
            FieldCatalog.run_date == run_date,
            FieldCatalog.run_id == run_id,
            FieldCatalog.fhour == int(fhour),
            FieldCatalog.product == product,
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def get_orphan_field_rows(self, workdir_path) -> list:
        """Find catalog rows whose files are missing (for reconciliation)."""
        stmt = select(FieldCatalog).order_by(FieldCatalog.updated_at.desc())
        with Session() as session:
            rows = session.execute(stmt).scalars().all()
            orphans = []
            for row in rows:
                path = workdir_path / row.storage_uri
                if not path.exists():
                    orphans.append(_row_to_dict(row))
            return orphans

    def get_field_rows_except(self, run_date, run_id, products=None):
        """Return catalog rows NOT belonging to (run_date, run_id), optionally scoped to
        `products` so pruning stays within one model family."""
        stmt = select(FieldCatalog).where(
            ~((FieldCatalog.run_date == run_date) & (FieldCatalog.run_id == run_id))
        )
        if products:
            stmt = stmt.where(FieldCatalog.product.in_(list(products)))
        with Session() as session:
            rows = session.execute(stmt).scalars().all()
            return [_row_to_dict(row) for row in rows]

    def get_expired_field_rows(self, expiry_hours=48):
        """Return catalog rows older than expiry_hours."""
        cutoff = func.now() - timedelta(hours=expiry_hours)
        stmt = select(FieldCatalog).where(FieldCatalog.updated_at < cutoff)
        with Session() as session:
            rows = session.execute(stmt).scalars().all()
            return [_row_to_dict(row) for row in rows]

    def prune_field_catalog(self, expiry_hours: int = 48):
        """Delete catalog rows older than threshold. Returns the number of rows removed."""
        cutoff = func.now() - timedelta(hours=expiry_hours)
        stmt = delete(FieldCatalog).where(FieldCatalog.updated_at < cutoff)
        with Session() as session:
            result = session.execute(stmt)
            session.commit()
            return result.rowcount

    def enqueue_backfill(self, run_date, run_id, fhour, product):
        """Record a missing-data request. Idempotent: a key already in the queue is left
        as-is, EXCEPT a previously 'failed' row is reset to 'requested'."""
        stmt = pg_insert(BackfillRequest).values(
            run_date=run_date,
            run_id=run_id,
            fhour=int(fhour),
            product=product,
            status="requested",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                BackfillRequest.run_date,
                BackfillRequest.run_id,
                BackfillRequest.fhour,
                BackfillRequest.product,
            ],
            set_={
                "status": case(
                    (BackfillRequest.status == "failed", "requested"),
                    else_=BackfillRequest.status,
                ),
                "updated_at": func.now(),
            },
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error enqueuing backfill: {e}")
            raise

    def claim_backfill_requests(self, limit=20):
        """Atomically claim up to `limit` pending requests, flipping them to 'fetching'.
        Uses SKIP LOCKED for safe concurrent draining."""
        pending = (
            select(*_BACKFILL_KEY_COLUMNS)
            .where(BackfillRequest.status == "requested")
            .order_by(BackfillRequest.requested_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .subquery()
        )
        stmt = (
            update(BackfillRequest)
            .where(tuple_(*_BACKFILL_KEY_COLUMNS).in_(select(pending)))
            .values(
                status="fetching",
                attempts=BackfillRequest.attempts + 1,
                updated_at=func.now(),
            )
            .returning(
                BackfillRequest.run_date,
                BackfillRequest.run_id,
                BackfillRequest.fhour,
                BackfillRequest.product,
                BackfillRequest.attempts,
            )
        )
        try:
            with Session() as session:
                rows = session.execute(stmt).all()
                session.commit()
                return [
                    {
                        "run_date": r.run_date,
                        "run_id": r.run_id,
                        "fhour": r.fhour,
                        "product": r.product,
                        "attempts": r.attempts,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Error claiming backfill requests: {e}")
            return []

    def mark_backfill(self, run_date, run_id, fhour, product, status):
        """Set the terminal/intermediate status of a request ('done' | 'failed' |
        'requested')."""
        stmt = (
            update(BackfillRequest)
            .where(
                BackfillRequest.run_date == run_date,
                BackfillRequest.run_id == run_id,
                BackfillRequest.fhour == int(fhour),
                BackfillRequest.product == product,
            )
            .values(status=status, updated_at=func.now())
        )
        try:
            with Session() as session:
                session.execute(stmt)
                session.commit()
        except Exception as e:
            logger.error(f"Error marking backfill {status}: {e}")


class FakeFieldCatalogAdapter:
    """In-memory fake for field_catalog + backfill_requests, matching
    FieldCatalogAdapter's method contracts."""

    def __init__(self):
        self._catalog: dict[tuple, dict] = {}
        self._backfill: dict[tuple, dict] = {}

    def products_with_data(self, candidates):
        if not candidates:
            return []
        candidates = set(candidates)
        matches = [r for r in self._catalog.values() if r["product"] in candidates]
        if not matches:
            return []
        latest = max(matches, key=lambda r: (r["run_date"], r["run_id"]))
        run_date, run_id = latest["run_date"], latest["run_id"]
        return [
            r["product"]
            for r in matches
            if r["run_date"] == run_date and r["run_id"] == run_id
        ]

    def get_latest_run_hours(self, products=None):
        rows = list(self._catalog.values())
        if products:
            products = set(products)
            rows = [r for r in rows if r["product"] in products]
        if not rows:
            return None
        latest = max(rows, key=lambda r: (r["run_date"], r["run_id"]))
        run_date, run_id = latest["run_date"], latest["run_id"]
        run_rows = [r for r in rows if r["run_date"] == run_date and r["run_id"] == run_id]

        if products:
            by_hour: dict[int, set] = {}
            for r in run_rows:
                by_hour.setdefault(r["fhour"], set()).add(r["product"])
            hours = sorted(h for h, prods in by_hour.items() if len(prods) == len(products))
        else:
            hours = sorted({r["fhour"] for r in run_rows})

        if not hours:
            return {
                "run_date": run_date,
                "run_id": run_id,
                "fmin": None,
                "fmax": None,
                "hours": [],
                "n_products": 0,
            }
        return {
            "run_date": run_date,
            "run_id": run_id,
            "fmin": hours[0],
            "fmax": hours[-1],
            "hours": hours,
        }

    def upsert_field_catalog(
        self,
        run_date,
        run_id,
        fhour,
        product,
        nlat,
        nlon,
        valid_time=None,
        storage_uri=None,
    ):
        key = (run_date, run_id, int(fhour), product)
        self._catalog[key] = {
            "run_date": run_date,
            "run_id": run_id,
            "fhour": int(fhour),
            "product": product,
            "nlat": nlat,
            "nlon": nlon,
            "valid_time": valid_time,
            "storage_uri": storage_uri,
            "updated_at": datetime.now(timezone.utc),
        }

    def get_field_catalog(self, run_date, run_id, fhour, product):
        row = self._catalog.get((run_date, run_id, int(fhour), product))
        return dict(row) if row else None

    def get_product_hours(self, run_date, run_id, product):
        return sorted(
            r["fhour"]
            for r in self._catalog.values()
            if r["run_date"] == run_date and r["run_id"] == run_id and r["product"] == product
        )

    def get_live_product_hours(self):
        return {(r["product"], int(r["fhour"])) for r in self._catalog.values()}

    def field_catalog_exists(self, run_date, run_id, fhour, product):
        return (run_date, run_id, int(fhour), product) in self._catalog

    def delete_field_catalog(self, run_date, run_id, fhour, product):
        self._catalog.pop((run_date, run_id, int(fhour), product), None)

    def get_orphan_field_rows(self, workdir_path) -> list:
        rows = sorted(self._catalog.values(), key=lambda r: r["updated_at"], reverse=True)
        orphans = []
        for row in rows:
            path = workdir_path / row["storage_uri"]
            if not path.exists():
                orphans.append(dict(row))
        return orphans

    def get_field_rows_except(self, run_date, run_id, products=None):
        rows = [
            dict(r)
            for r in self._catalog.values()
            if not (r["run_date"] == run_date and r["run_id"] == run_id)
        ]
        if products:
            products = set(products)
            rows = [r for r in rows if r["product"] in products]
        return rows

    def get_expired_field_rows(self, expiry_hours=48):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        return [dict(r) for r in self._catalog.values() if r["updated_at"] < cutoff]

    def prune_field_catalog(self, expiry_hours: int = 48):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        before = len(self._catalog)
        self._catalog = {k: v for k, v in self._catalog.items() if v["updated_at"] >= cutoff}
        return before - len(self._catalog)

    def enqueue_backfill(self, run_date, run_id, fhour, product):
        key = (run_date, run_id, int(fhour), product)
        existing = self._backfill.get(key)
        now = datetime.now(timezone.utc)
        if existing is None:
            self._backfill[key] = {
                "run_date": run_date,
                "run_id": run_id,
                "fhour": int(fhour),
                "product": product,
                "status": "requested",
                "attempts": 0,
                "requested_at": now,
                "updated_at": now,
            }
        elif existing["status"] == "failed":
            existing["status"] = "requested"
            existing["updated_at"] = now

    def claim_backfill_requests(self, limit=20):
        pending = sorted(
            (r for r in self._backfill.values() if r["status"] == "requested"),
            key=lambda r: r["requested_at"],
        )[:limit]
        claimed = []
        for r in pending:
            r["status"] = "fetching"
            r["attempts"] += 1
            r["updated_at"] = datetime.now(timezone.utc)
            claimed.append(
                {
                    "run_date": r["run_date"],
                    "run_id": r["run_id"],
                    "fhour": r["fhour"],
                    "product": r["product"],
                    "attempts": r["attempts"],
                }
            )
        return claimed

    def mark_backfill(self, run_date, run_id, fhour, product, status):
        row = self._backfill.get((run_date, run_id, int(fhour), product))
        if row:
            row["status"] = status
            row["updated_at"] = datetime.now(timezone.utc)
