import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, cast, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import Text as SqlText

from atmos_gl.db.engine import Session
from atmos_gl.db.geojson import as_feature_collection, EMPTY_FEATURE_COLLECTION
from atmos_gl.db.models import WorldEvent, WorldEventArticle, WorldEventExportFile

logger = logging.getLogger(__name__)

# Categories max_conflict_tone applies to. Diplomacy is exempt: its coverage tone is
# naturally either sign, whereas a "conflict" event whose coverage reads positively is
# almost always GDELT coding figurative "battle"/"fight" language (sitcom round-ups,
# business deals, awards) -- confirmed by sampling live rows, see get_events_as_geojson.
_TONE_FILTERED_CATEGORIES = ("explosion", "warfare", "targeted_violence")

# A "retry" article (429/5xx/network error) is re-attempted at most this many times in
# total, no sooner than _ARTICLE_RETRY_AFTER after its last attempt.
_ARTICLE_MAX_ATTEMPTS = 3
_ARTICLE_RETRY_AFTER = timedelta(hours=1)

# Same chunking rationale as FireAdapter/MarkerAdapter's bulk upserts: a backfill run
# can cover several days of curated-category GDELT events in one collect() cycle, well
# past a single-statement-per-row shape's comfortable size.
_UPSERT_CHUNK_SIZE = 5000


class WorldEventAdapter:
    """Real adapter for world_events (GDELT-sourced conflict/diplomacy point markers,
    see collectors/world_events.py), backed by SQLAlchemy."""

    def upsert_events(self, rows):
        """Bulk-UPSERTs classified GDELT events. Each row is a dict with keys: id
        (GLOBALEVENTID), category, event_code, actor1_name, actor2_name,
        action_geo_full_name, lat, lon, event_date (ISO str), num_mentions,
        num_sources, goldstein_scale, avg_tone, source_url.

        A re-ingested id (a backfill re-covering a window collect() already filled)
        just refreshes the same row -- GDELT's own fields for a given event don't
        change after the fact, so the ON CONFLICT SET list is the full column set.

        Returns True on success (including nothing to write), False if the write
        failed -- WorldEventsCollector only marks an export file processed on True, so
        a failed write is retried next cycle rather than silently leaving a gap."""
        if not rows:
            return True
        values = [
            {**r, "geom": f"SRID=4326;POINT({r['lon']} {r['lat']})"}
            for r in rows
            if r.get("lat") is not None and r.get("lon") is not None
        ]
        if not values:
            return True
        stmt = pg_insert(WorldEvent)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WorldEvent.id],
            set_={
                "category": stmt.excluded.category,
                "event_code": stmt.excluded.event_code,
                "actor1_name": stmt.excluded.actor1_name,
                "actor2_name": stmt.excluded.actor2_name,
                "action_geo_full_name": stmt.excluded.action_geo_full_name,
                "lat": stmt.excluded.lat,
                "lon": stmt.excluded.lon,
                "geom": stmt.excluded.geom,
                "event_date": stmt.excluded.event_date,
                "num_mentions": stmt.excluded.num_mentions,
                "num_sources": stmt.excluded.num_sources,
                "goldstein_scale": stmt.excluded.goldstein_scale,
                "avg_tone": stmt.excluded.avg_tone,
                "source_url": stmt.excluded.source_url,
            },
        )
        try:
            with Session() as session:
                for i in range(0, len(values), _UPSERT_CHUNK_SIZE):
                    session.execute(stmt, values[i : i + _UPSERT_CHUNK_SIZE])
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Error bulk-saving {len(values)} world events: {e}")
            return False

    def processed_export_slots(self, since):
        """Slot timestamps of every GDELT export file already processed (status ok or
        missing) at or after `since` -- WorldEventsCollector diffs the backfill
        window's expected slots against this to find gaps. Raises on a DB error (an
        empty set would wrongly mean "nothing covered" and trigger a full re-fetch)."""
        with Session() as session:
            return set(
                session.scalars(
                    select(WorldEventExportFile.slot).where(WorldEventExportFile.slot >= since)
                )
            )

    def mark_export_processed(self, slot, status, row_count=0):
        """Records one export file as processed ("ok" or "missing"); idempotent."""
        stmt = pg_insert(WorldEventExportFile).values(
            slot=slot, status=status, row_count=row_count
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[WorldEventExportFile.slot],
            set_={
                "status": stmt.excluded.status,
                "row_count": stmt.excluded.row_count,
                "processed_at": func.now(),
            },
        )
        with Session() as session:
            session.execute(stmt)
            session.commit()

    def urls_needing_preview(self, since, limit):
        """Distinct source URLs of events since `since` with no article preview yet, or
        a "retry" one that's due again -- most recent event first, so a backlog fills
        in what users are most likely looking at first. Raises on a DB error."""
        due_retry = and_(
            WorldEventArticle.status == "retry",
            WorldEventArticle.attempts < _ARTICLE_MAX_ATTEMPTS,
            WorldEventArticle.fetched_at < func.now() - _ARTICLE_RETRY_AFTER,
        )
        stmt = (
            select(WorldEvent.source_url)
            .outerjoin(WorldEventArticle, WorldEventArticle.url == WorldEvent.source_url)
            .where(
                WorldEvent.event_date >= since,
                WorldEvent.source_url.isnot(None),
                or_(WorldEventArticle.url.is_(None), due_retry),
            )
            .group_by(WorldEvent.source_url)
            .order_by(func.max(WorldEvent.event_date).desc())
            .limit(limit)
        )
        with Session() as session:
            return list(session.scalars(stmt))

    def save_article_previews(self, previews):
        """Upserts article previews: dicts with url, status, http_status, headline,
        summary. attempts counts every save for that URL."""
        if not previews:
            return
        stmt = pg_insert(WorldEventArticle)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WorldEventArticle.url],
            set_={
                "status": stmt.excluded.status,
                "http_status": stmt.excluded.http_status,
                "headline": stmt.excluded.headline,
                "summary": stmt.excluded.summary,
                "attempts": WorldEventArticle.attempts + 1,
                "fetched_at": func.now(),
            },
        )
        values = [{**p, "attempts": 1} for p in previews]
        with Session() as session:
            session.execute(stmt, values)
            session.commit()

    def prune_export_slots(self, before) -> int:
        """Deletes processed-file records older than `before` (the backfill window's
        start) -- they no longer affect coverage. Returns the number deleted."""
        stmt = delete(WorldEventExportFile).where(WorldEventExportFile.slot < before)
        try:
            with Session() as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"Error pruning world event export file records: {e}")
            return 0

    def get_events_as_geojson(self, expiry_days=7, max_conflict_tone=None):
        """Returns world events as GeoJSON, filtering by age. age_hours (like quakes'
        age_minutes) lets the frontend de-emphasize older events within the window.

        max_conflict_tone (None = off) is a read-time quality filter: a conflict-
        category event (_TONE_FILTERED_CATEGORIES) whose GDELT avg_tone is ABOVE it is
        dropped. An event with no avg_tone is kept -- there's nothing to judge it by.
        Read-time rather than at collection, like expiry_days, so every row is still
        stored and the threshold can be retuned against existing history.

        Duplicates are collapsed to one feature per STORY, after the tone filter:
        GDELT often codes one article into several events (22% of markers, measured),
        and syndicated copies of one story appear at different outlets' URLs. A story
        is the article's summary when it has a real one, else its URL (else the event
        itself); the most-mentioned event wins (then most recent), and the story's
        other URLs come back as also_reported_by. A summary one site reuses across
        articles with DIFFERENT headlines ("News in real-time", seen live) is site
        boilerplate: it's dropped and never used to merge. Retitled syndication -- same
        summary, different headlines, different sites -- still merges."""
        cutoff = func.now() - timedelta(days=expiry_days)
        article_domain = func.split_part(func.split_part(WorldEventArticle.url, "//", 2), "/", 1)
        boilerplate = (
            select(WorldEventArticle.summary)
            .where(WorldEventArticle.status == "ok", WorldEventArticle.summary.isnot(None))
            .group_by(WorldEventArticle.summary, article_domain)
            .having(func.count(func.lower(WorldEventArticle.headline).distinct()) > 1)
        )
        summary = case(
            (WorldEventArticle.summary.in_(boilerplate), None),
            else_=WorldEventArticle.summary,
        )
        story = func.coalesce(summary, WorldEvent.source_url, WorldEvent.id)
        events = (
            select(
                WorldEvent.id,
                WorldEvent.category,
                WorldEvent.event_code,
                WorldEvent.actor1_name,
                WorldEvent.actor2_name,
                WorldEvent.action_geo_full_name,
                WorldEvent.geom,
                WorldEvent.event_date,
                WorldEvent.num_mentions,
                WorldEvent.num_sources,
                WorldEvent.source_url,
                WorldEventArticle.headline,
                summary.label("summary"),
                story.label("story"),
                func.row_number()
                .over(
                    partition_by=story,
                    order_by=(
                        WorldEvent.num_mentions.desc().nulls_last(),
                        WorldEvent.event_date.desc(),
                        WorldEvent.id,
                    ),
                )
                .label("rank"),
            )
            .select_from(WorldEvent)
            .outerjoin(
                WorldEventArticle,
                and_(
                    WorldEventArticle.url == WorldEvent.source_url,
                    WorldEventArticle.status == "ok",
                ),
            )
            .where(WorldEvent.event_date >= cutoff)
        )
        if max_conflict_tone is not None:
            events = events.where(
                or_(
                    WorldEvent.category.notin_(_TONE_FILTERED_CATEGORIES),
                    WorldEvent.avg_tone.is_(None),
                    WorldEvent.avg_tone <= max_conflict_tone,
                )
            )
        events = events.cte("events")
        outlets = (
            select(events.c.story, func.array_agg(events.c.source_url.distinct()).label("urls"))
            .where(events.c.source_url.isnot(None))
            .group_by(events.c.story)
            .cte("outlets")
        )
        also_reported_by = func.coalesce(
            func.to_jsonb(func.array_remove(outlets.c.urls, events.c.source_url)),
            text("'[]'::jsonb"),
        )
        feature = func.jsonb_build_object(
            "type",
            "Feature",
            "geometry",
            cast(func.ST_AsGeoJSON(events.c.geom), JSONB),
            "properties",
            func.jsonb_build_object(
                "id",
                events.c.id,
                "category",
                events.c.category,
                "event_code",
                events.c.event_code,
                "actor1_name",
                events.c.actor1_name,
                "actor2_name",
                events.c.actor2_name,
                "place",
                events.c.action_geo_full_name,
                "event_date",
                events.c.event_date,
                "num_mentions",
                events.c.num_mentions,
                "num_sources",
                events.c.num_sources,
                "source_url",
                events.c.source_url,
                "headline",
                events.c.headline,
                "summary",
                events.c.summary,
                "also_reported_by",
                also_reported_by,
                "age_hours",
                func.extract("epoch", func.now() - events.c.event_date) / 3600.0,
            ),
        )
        stmt = (
            select(cast(as_feature_collection(feature), SqlText))
            .select_from(events)
            .outerjoin(outlets, outlets.c.story == events.c.story)
            .where(events.c.rank == 1)
        )
        try:
            with Session() as session:
                result = session.scalar(stmt)
                return result if result is not None else EMPTY_FEATURE_COLLECTION
        except Exception as e:
            logger.error(f"Error building world events GeoJSON: {e}")
            return EMPTY_FEATURE_COLLECTION

    def delete_expired(self, expiry_days=7) -> int:
        """Deletes world_event rows older than expiry_days. Returns the number deleted."""
        cutoff = func.now() - timedelta(days=expiry_days)
        stmt = delete(WorldEvent).where(WorldEvent.event_date < cutoff)
        try:
            with Session() as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"Error deleting expired world events: {e}")
            return 0

    def prune_orphaned_articles(self) -> int:
        """Deletes article previews no remaining event points at -- run right after
        delete_expired() so world_event_articles shrinks with world_events instead of
        growing forever. Returns the number deleted."""
        referenced = select(WorldEvent.id).where(WorldEvent.source_url == WorldEventArticle.url)
        stmt = delete(WorldEventArticle).where(~referenced.exists())
        try:
            with Session() as session:
                result = session.execute(stmt)
                session.commit()
                return result.rowcount
        except Exception as e:
            logger.error(f"Error pruning orphaned world event articles: {e}")
            return 0


class FakeWorldEventAdapter:
    """In-memory fake for world_events, matching WorldEventAdapter's method contracts."""

    def __init__(self):
        self._events: dict[str, dict] = {}
        self._export_files: dict[datetime, dict] = {}
        self._articles: dict[str, dict] = {}

    def upsert_events(self, rows):
        if not rows:
            return True
        for r in rows:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            self._events[r["id"]] = {
                "id": r["id"],
                "category": r["category"],
                "event_code": r.get("event_code"),
                "actor1_name": r.get("actor1_name"),
                "actor2_name": r.get("actor2_name"),
                "action_geo_full_name": r.get("action_geo_full_name"),
                "lat": r["lat"],
                "lon": r["lon"],
                "event_date": datetime.fromisoformat(r["event_date"]),
                "num_mentions": r.get("num_mentions"),
                "num_sources": r.get("num_sources"),
                "goldstein_scale": r.get("goldstein_scale"),
                "avg_tone": r.get("avg_tone"),
                "source_url": r.get("source_url"),
            }
        return True

    def processed_export_slots(self, since):
        return {slot for slot in self._export_files if slot >= since}

    def mark_export_processed(self, slot, status, row_count=0):
        self._export_files[slot] = {"status": status, "row_count": row_count}

    def urls_needing_preview(self, since, limit):
        now = datetime.now(timezone.utc)
        latest_by_url: dict[str, datetime] = {}
        for e in self._events.values():
            url = e["source_url"]
            if url is None or e["event_date"] < since:
                continue
            a = self._articles.get(url)
            due = a is None or (
                a["status"] == "retry"
                and a["attempts"] < _ARTICLE_MAX_ATTEMPTS
                and a["fetched_at"] < now - _ARTICLE_RETRY_AFTER
            )
            if due:
                latest_by_url[url] = max(e["event_date"], latest_by_url.get(url, e["event_date"]))
        ordered = sorted(latest_by_url, key=latest_by_url.get, reverse=True)
        return ordered[:limit]

    def save_article_previews(self, previews):
        now = datetime.now(timezone.utc)
        for p in previews:
            previous = self._articles.get(p["url"])
            self._articles[p["url"]] = {
                **p,
                "attempts": (previous["attempts"] + 1) if previous else 1,
                "fetched_at": now,
            }

    def prune_export_slots(self, before) -> int:
        stale = [slot for slot in self._export_files if slot < before]
        for slot in stale:
            del self._export_files[slot]
        return len(stale)

    def _boilerplate_summaries(self) -> set:
        """Summaries one site uses for articles with different headlines -- mirrors
        the real adapter's boilerplate subquery (domain = text between "//" and the
        next "/", exactly as its split_part() pair computes it)."""
        headlines_by_site: dict[tuple, set] = {}
        for url, a in self._articles.items():
            if a["status"] != "ok" or a.get("summary") is None:
                continue
            domain = url.split("//", 1)[1].split("/", 1)[0] if "//" in url else ""
            headlines_by_site.setdefault((a["summary"], domain), set()).add(
                (a.get("headline") or "").lower() if a.get("headline") is not None else None
            )
        return {
            summary for (summary, _), heads in headlines_by_site.items()
            if len(heads - {None}) > 1
        }

    def get_events_as_geojson(self, expiry_days=7, max_conflict_tone=None):
        import json

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=expiry_days)
        boilerplate = self._boilerplate_summaries()
        stories: dict[str, list] = {}
        for e in self._events.values():
            if e["event_date"] < cutoff:
                continue
            if (
                max_conflict_tone is not None
                and e["category"] in _TONE_FILTERED_CATEGORIES
                and e["avg_tone"] is not None
                and e["avg_tone"] > max_conflict_tone
            ):
                continue
            article = self._articles.get(e["source_url"]) or {}
            ok = article.get("status") == "ok"
            summary = article.get("summary") if ok else None
            if summary in boilerplate:
                summary = None
            story = summary or e["source_url"] or e["id"]
            stories.setdefault(story, []).append((e, article if ok else {}, summary))

        features = []
        for members in stories.values():
            members.sort(key=lambda m: (
                -(m[0]["num_mentions"] if m[0]["num_mentions"] is not None else -1),
                -m[0]["event_date"].timestamp(),
                m[0]["id"],
            ))
            e, article, summary = members[0]
            urls = {m[0]["source_url"] for m in members if m[0]["source_url"] is not None}
            age_hours = (now - e["event_date"]).total_seconds() / 3600.0
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                    "properties": {
                        "id": e["id"],
                        "category": e["category"],
                        "event_code": e["event_code"],
                        "actor1_name": e["actor1_name"],
                        "actor2_name": e["actor2_name"],
                        "place": e["action_geo_full_name"],
                        "event_date": e["event_date"].isoformat(),
                        "num_mentions": e["num_mentions"],
                        "num_sources": e["num_sources"],
                        "source_url": e["source_url"],
                        "headline": article.get("headline"),
                        "summary": summary,
                        "also_reported_by": sorted(urls - {e["source_url"]}),
                        "age_hours": age_hours,
                    },
                }
            )
        return json.dumps({"type": "FeatureCollection", "features": features})

    def delete_expired(self, expiry_days=7) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=expiry_days)
        expired = [eid for eid, e in self._events.items() if e["event_date"] < cutoff]
        for eid in expired:
            del self._events[eid]
        return len(expired)

    def prune_orphaned_articles(self) -> int:
        referenced = {e["source_url"] for e in self._events.values()}
        orphans = [url for url in self._articles if url not in referenced]
        for url in orphans:
            del self._articles[url]
        return len(orphans)
