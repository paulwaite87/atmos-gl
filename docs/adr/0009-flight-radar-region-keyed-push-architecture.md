---
status: supersedes 0007
---

# Flight Radar: region-keyed backend polling, pushed to browsers over WebSocket, hosted inside map_api

Supersedes `docs/adr/0007-flight-radar-has-no-collector-or-db-table.md`. That ADR's
"no DB, no Postgres table" conclusion still holds — this design keeps aircraft state
entirely in memory — but its "no collector, no persistence, no background process at
all" conclusion does not. `0007`'s own "Revisit if" section anticipated exactly this:
"if adsb.lol's rate limits... turn out to make live, per-session polling impractical
at scale." They do: `0007`'s design had each connected browser session independently
polling adsb.lol with its own viewport, so N sessions looking at the map multiplied
adsb.lol's request load by N — even two tabs on the same self-hosted instance would
double it. That doesn't scale down gracefully even for this app's single-operator
framing.

The fix is a backend-proxy-and-push architecture: a persistent process polls adsb.lol
on its own schedule, decoupled from how many browsers are watching, and pushes results
out rather than waiting to be asked.

## Decisions

- **Region-keyed polling, not per-connection or per-request.** A coarse grid buckets
  viewport centers into region keys. Polling loops are keyed by grid cell; multiple
  browser sessions whose viewports land in the same cell share one underlying poll.
  This — not WebSocket vs. REST — is the part that actually solves the N-sessions
  problem; without it, pushing over a WebSocket to N independently-polling loops would
  have the identical scaling flaw `0007` already had.
- **Two cadence tiers, both grid-bucketed the same way.** The grid cell(s) nearest a
  connection's viewport center get a fast poll cadence (one circle, sized as large as
  a single well-behaved adsb.lol query reasonably allows); the remaining cells
  covering the rest of the viewport get a slower cadence. Bucketing *both* tiers onto
  the same grid (not just the outer one) means two sessions looking at roughly the
  same place share both tiers' poll loops, not just the coarse one.
- **WebSocket, not SSE.** The client needs to send its current viewport to the server
  (on connect, and again on every pan/zoom) as well as receive the push stream — SSE
  is server-to-client only and would need a second channel for viewport updates.
  WebSocket carries both directions on one connection, and mirrors a shape this
  codebase already uses (`ShippingCollector` already does "connect, send a
  subscription payload, receive a stream" — as a client to AIS, not a server to
  browsers, but the same shape).
- **Lives entirely inside `map_api`**, as background `asyncio` tasks sharing
  in-process state directly with WebSocket connection handling — not a new Docker
  service, not `AsyncCollectorBase`/`CollectorService`. That pattern exists for
  decoupled collector→DB→other-services data flow; nothing else needs this data, and
  splitting the polling into a separate process would only add an inter-process
  channel to get results back to whichever process holds the WebSocket connections,
  for no benefit.
- **Region lifecycle has a 30s grace period** after a region's last subscriber leaves
  before its poll loop actually stops, so briefly glancing away and back doesn't cause
  a cold-start stale/empty moment. Bounded, small cost per region — much smaller than
  the problem this whole redesign solves.
- **Reconnection needs no special handling.** Because polling state is keyed by
  region, not by connection identity, a dropped connection is just "one fewer
  subscriber" (the same grace-period path as any other unsubscribe). Reconnecting is
  just a fresh connection with a fresh viewport message — no session-resumption
  handshake.
- **Client-side dead-reckoning interpolation** (heading + ground speed +
  `requestAnimationFrame`) smooths normal gaps between pushes, bounded — after a short
  overrun past the expected next update (covers a dropped connection or an unusually
  slow poll), aircraft freeze in place rather than keep extrapolating indefinitely and
  visibly drifting from their true position.

## Considered Options

- **Per-connection polling, pushed over WebSocket** — rejected: doesn't actually fix
  the scaling problem. Pushing instead of polling changes the transport, not the
  N-sessions-multiply-load flaw, unless polling is also keyed by region.
- **Global sweep of the whole planet every 1-2s** (the generic "Flightradar24-style"
  advice this redesign started from) — rejected: adsb.lol has no bounding-box query,
  only point+radius, so "global bounding boxes" doesn't map onto this specific API;
  covering the whole globe with circles and hitting all of them every 1-2s is a much
  more aggressive load pattern than this design needs, and risks getting a
  self-hosted instance rate-limited by a community-run service sized for that. This
  app only needs to track what's actually being looked at.
- **Region-keyed polling, push over WebSocket, on-demand (only actively-watched
  regions), hosted in `map_api`** — chosen.

## Revisit if

adsb.lol's real-world rate limits (still undocumented/"dynamic") turn out not to
tolerate even this reduced, on-demand load — at that point the fast-tier cadence or
hot-circle radius are the first things to relax, before reconsidering the
architecture itself. Also revisit if `map_api`'s resource usage from hosting these
background tasks becomes a real problem alongside its existing request/response
traffic — at that point, splitting the polling into its own process (accepting the
inter-process-channel cost this ADR rejected) becomes worth it.
