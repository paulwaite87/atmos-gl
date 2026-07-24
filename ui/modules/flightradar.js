// ui/modules/flightradar.js
// Client-side dead-reckoning + bounded-freeze helpers behind the Flight Radar layer
// (issue #203, docs/adr/0009). The backend pushes aircraft state on its own
// hot/gentle cadence (routes/flightradar.py, 2-20s); between pushes, the frontend
// extrapolates each aircraft's position from its last-known lat/lon + ground speed +
// track via requestAnimationFrame, so movement reads as smooth rather than snapping
// every few seconds. If updates stop arriving (disconnect, region churn),
// boundedElapsedSeconds caps how far that extrapolation is allowed to run --
// past MAX_EXTRAPOLATION_S the aircraft freezes in place rather than flying off along
// a stale heading indefinitely.

const NM_PER_DEGREE_LAT = 60.0;
const MAX_EXTRAPOLATION_S = 30.0;

export function boundedElapsedSeconds(lastSeenMs, nowMs, maxExtrapolationS = MAX_EXTRAPOLATION_S) {
    const elapsed = (nowMs - lastSeenMs) / 1000.0;
    return Math.max(0, Math.min(elapsed, maxExtrapolationS));
}

// Kept as its own predicate (not derived from boundedElapsedSeconds' clamped output)
// so the render loop can show a "signal lost" cue exactly when extrapolation has
// been capped, without the two concerns folded into one function -- see issue #203's
// Testing Decisions ("should we freeze" kept separate from the interpolation itself).
export function isFrozen(lastSeenMs, nowMs, maxExtrapolationS = MAX_EXTRAPOLATION_S) {
    return (nowMs - lastSeenMs) / 1000.0 >= maxExtrapolationS;
}

// adsb.lol's baro_rate (ft/min, barometric vertical rate) is noisy even in level
// flight -- a deadband avoids the popup flickering between Climbing/Descending/Level
// on sensor jitter alone. 150ft/min is a conservative starting guess (real level-flight
// noise is usually well under 100ft/min), tunable like every other numeric constant in
// this feature.
export function flightStatus(baroRateFpm, deadbandFpm = 150) {
    if (typeof baroRateFpm !== 'number') return 'Level flight';
    if (baroRateFpm > deadbandFpm) return 'Climbing';
    if (baroRateFpm < -deadbandFpm) return 'Descending';
    return 'Level flight';
}

// nav_altitude_mcp (the autopilot/MCP-selected target altitude) rarely matches
// alt_baro (the actual sensed altitude) exactly even once an aircraft has settled at
// cruise -- a real adsb.lol record: alt_baro=37000, nav_altitude_mcp=36992. A small
// tolerance (rather than bit-exact equality) is what makes "Reached" actually fire in
// practice.
export function targetAltitudeLabel(navAltitudeMcpFt, altBaroFt, toleranceFt = 50) {
    if (typeof navAltitudeMcpFt !== 'number') return null;
    if (typeof altBaroFt === 'number' && Math.abs(navAltitudeMcpFt - altBaroFt) <= toleranceFt) {
        return 'Reached';
    }
    return `${Math.round(navAltitudeMcpFt).toLocaleString()} ft`;
}

// Flat-earth dead reckoning -- accurate enough over the few-second gaps this bridges
// (see MAX_EXTRAPOLATION_S), not meant for long-range navigation. track is degrees
// true, clockwise from north (adsb.lol's `track`/`true_heading` field); gs is ground
// speed in knots (adsb.lol's `gs`). Longitude degrees shrink toward the poles
// (1 / cos(lat)), same convergence correction as everywhere else in this codebase that
// converts a distance to a longitude delta.
export function interpolatedPosition({ lat, lon, gs, track }, elapsedSeconds) {
    if (!gs || !elapsedSeconds || track == null) return { lat, lon };
    const distanceNm = gs * (elapsedSeconds / 3600.0);
    const trackRad = (track * Math.PI) / 180.0;
    const deltaLat = (distanceNm / NM_PER_DEGREE_LAT) * Math.cos(trackRad);
    const cosLat = Math.cos((lat * Math.PI) / 180.0);
    const deltaLon = cosLat !== 0 ? ((distanceNm / NM_PER_DEGREE_LAT) * Math.sin(trackRad)) / cosLat : 0;
    return { lat: lat + deltaLat, lon: lon + deltaLon };
}

// ICAO aircraft type designator (adsb.lol's `t` field, e.g. "B77W") -> a broad,
// human-friendly class for the hover popup. Locally maintained and deliberately not
// exhaustive -- covers designators likely to actually show up in real ADS-B traffic
// (airliners, regional/business jets, turboprops, light GA, rotorcraft, a handful of
// military types). An unregistered designator (including any adsb.lol variant/typo
// this list doesn't happen to cover) falls back to a vague default rather than
// guessing -- see aircraftClass()'s default.
const AIRCRAFT_CLASS_REGISTER = {
    // Widebody jets
    A332: 'Widebody Jet', A333: 'Widebody Jet', A338: 'Widebody Jet', A339: 'Widebody Jet',
    A342: 'Widebody Jet', A343: 'Widebody Jet', A345: 'Widebody Jet', A346: 'Widebody Jet',
    A359: 'Widebody Jet', A35K: 'Widebody Jet', A388: 'Widebody Jet',
    B744: 'Widebody Jet', B748: 'Widebody Jet', B772: 'Widebody Jet', B773: 'Widebody Jet',
    B77L: 'Widebody Jet', B77W: 'Widebody Jet', B788: 'Widebody Jet', B789: 'Widebody Jet',
    B78X: 'Widebody Jet', MD11: 'Widebody Jet',

    // Narrowbody jets
    A318: 'Narrowbody Jet', A319: 'Narrowbody Jet', A320: 'Narrowbody Jet', A321: 'Narrowbody Jet',
    A19N: 'Narrowbody Jet', A20N: 'Narrowbody Jet', A21N: 'Narrowbody Jet',
    B712: 'Narrowbody Jet', B737: 'Narrowbody Jet', B738: 'Narrowbody Jet', B739: 'Narrowbody Jet',
    B37M: 'Narrowbody Jet', B38M: 'Narrowbody Jet', B39M: 'Narrowbody Jet', B3XM: 'Narrowbody Jet',
    B752: 'Narrowbody Jet', B753: 'Narrowbody Jet',
    MD82: 'Narrowbody Jet', MD83: 'Narrowbody Jet', MD90: 'Narrowbody Jet',

    // Regional jets
    CRJ1: 'Regional Jet', CRJ2: 'Regional Jet', CRJ7: 'Regional Jet', CRJ9: 'Regional Jet',
    CRJX: 'Regional Jet',
    E135: 'Regional Jet', E145: 'Regional Jet', E170: 'Regional Jet', E175: 'Regional Jet',
    E190: 'Regional Jet', E195: 'Regional Jet', E290: 'Regional Jet', E295: 'Regional Jet',
    SU95: 'Regional Jet', ARJ21: 'Regional Jet', F70: 'Regional Jet', F100: 'Regional Jet',

    // Turboprops
    AT43: 'Turboprop', AT45: 'Turboprop', AT72: 'Turboprop', AT75: 'Turboprop', AT76: 'Turboprop',
    DH8A: 'Turboprop', DH8B: 'Turboprop', DH8C: 'Turboprop', DH8D: 'Turboprop',
    SF34: 'Turboprop', B190: 'Turboprop', C208: 'Turboprop', C208B: 'Turboprop',
    PC12: 'Turboprop', TBM7: 'Turboprop', TBM8: 'Turboprop', TBM9: 'Turboprop',
    D328: 'Turboprop', SW4: 'Turboprop', BE20: 'Turboprop',

    // Business jets
    GLF4: 'Business Jet', GLF5: 'Business Jet', GLF6: 'Business Jet',
    CL30: 'Business Jet', CL35: 'Business Jet', CL60: 'Business Jet',
    GLEX: 'Business Jet', GL5T: 'Business Jet', GL6T: 'Business Jet',
    C525: 'Business Jet', C550: 'Business Jet', C560: 'Business Jet', C56X: 'Business Jet',
    C650: 'Business Jet', C680: 'Business Jet', C68A: 'Business Jet', C700: 'Business Jet',
    C750: 'Business Jet',
    FA7X: 'Business Jet', FA8X: 'Business Jet', FA50: 'Business Jet', FA6X: 'Business Jet',
    FA20: 'Business Jet',
    LJ35: 'Business Jet', LJ45: 'Business Jet', LJ60: 'Business Jet', LJ75: 'Business Jet',
    PC24: 'Business Jet', E50P: 'Business Jet', E55P: 'Business Jet', H25B: 'Business Jet',

    // Light aircraft (piston GA)
    C152: 'Light Aircraft', C172: 'Light Aircraft', C182: 'Light Aircraft', C206: 'Light Aircraft',
    P28A: 'Light Aircraft', PA31: 'Light Aircraft', PA34: 'Light Aircraft', PA44: 'Light Aircraft',
    BE33: 'Light Aircraft', BE35: 'Light Aircraft', BE36: 'Light Aircraft', BE58: 'Light Aircraft',
    M20P: 'Light Aircraft', M20T: 'Light Aircraft', SR20: 'Light Aircraft', SR22: 'Light Aircraft',
    DA40: 'Light Aircraft', DA42: 'Light Aircraft',

    // Helicopters
    R22: 'Helicopter', R44: 'Helicopter', R66: 'Helicopter',
    EC30: 'Helicopter', EC35: 'Helicopter', EC45: 'Helicopter', EC55: 'Helicopter',
    AS50: 'Helicopter', AS55: 'Helicopter', AS65: 'Helicopter',
    A109: 'Helicopter', A119: 'Helicopter', A139: 'Helicopter', A169: 'Helicopter', A189: 'Helicopter',
    B06: 'Helicopter', B47: 'Helicopter', B407: 'Helicopter', B412: 'Helicopter',
    B429: 'Helicopter', B430: 'Helicopter',
    S76: 'Helicopter', S92: 'Helicopter',
    H60: 'Helicopter', AH64: 'Helicopter', UH1: 'Helicopter',

    // Military (fixed-wing) -- a small representative set; ADS-B military traffic is
    // rare and often squawks without a populated `t` at all.
    F15: 'Military Aircraft', F16: 'Military Aircraft', F18: 'Military Aircraft',
    F22: 'Military Aircraft', F35: 'Military Aircraft',
    C130: 'Military Aircraft', C17: 'Military Aircraft',
    B52: 'Military Aircraft', A10: 'Military Aircraft',
};

const DEFAULT_AIRCRAFT_CLASS = 'Aircraft (unclassified)';

export function aircraftClass(typeCode) {
    if (!typeCode) return DEFAULT_AIRCRAFT_CLASS;
    return AIRCRAFT_CLASS_REGISTER[typeCode.toUpperCase()] || DEFAULT_AIRCRAFT_CLASS;
}

// Coarser than aircraftClass() -- 4 icon-color groups, roughly by size/prominence
// (a widebody is more globally significant than a light aircraft, same reasoning
// docs/adr/0008 uses for the altitude zoom filter). 'other' is the catch-all for
// anything that isn't a fixed-wing airliner/GA type -- helicopters, military, and
// anything aircraftClass() couldn't identify at all.
export function aircraftGroup(typeCode) {
    const cls = aircraftClass(typeCode);
    if (cls === 'Widebody Jet') return 'widebody';
    if (cls === 'Narrowbody Jet' || cls === 'Regional Jet') return 'airliner';
    if (cls === 'Turboprop' || cls === 'Business Jet' || cls === 'Light Aircraft') return 'light';
    return 'other';
}

// Brightness/warmth roughly tracks group size: near-white for the largest widebodies,
// down to light blue for the smallest/other-shaped craft. Multiplied onto the SDF
// icon at render time (icon-color paint property) rather than baked into the PNG, so
// one shape asset per icon (aircraft/glider) covers every group.
const AIRCRAFT_GROUP_COLORS = {
    widebody: '#f2f2f2',
    airliner: '#ffd166',
    light: '#ff8c42',
    other: '#8ec9ff',
};

export function aircraftGroupColor(typeCode) {
    return AIRCRAFT_GROUP_COLORS[aircraftGroup(typeCode)];
}

// ---------------------------------------------------------------------------------
// Layer wiring: WebSocket client, requestAnimationFrame render loop, MapLibre
// filters/icons, hover popup. Not unit-tested (same boundary every other layer module
// in this codebase draws: pure math/config-mapping gets tests -- buildLUT/
// speedFromConfig in jetstream.js, interpolatedPosition/boundedElapsedSeconds above --
// the DOM/network/map glue below is verified live instead, same as mount/refresh/
// unmount in shipping.js/markers.js/satellites.js).
// ---------------------------------------------------------------------------------
import { liveDataSync } from './_datasync.js';
import { hoverPopup } from './_hoverpopup.js';
import { preloadIcons } from './_feedhelpers.js';

// An aircraft with no push for this long is assumed to have left every region this
// connection is subscribed to (backend never sends an explicit "removed" message --
// see docs/adr/0009) and is dropped from the map rather than frozen in place forever.
// 3x the gentle-tier cadence (routes/flightradar.py's GENTLE_CADENCE_S=20s) tolerates
// a couple of missed slow-tier updates before pruning.
const STALE_PRUNE_MS = 60000;

// docs/adr/0008: alt_baro (not category) drives the zoom-density filter -- high-
// altitude traffic is visible zoomed out, low-altitude/ground traffic only reveals
// once zoomed in close, mirroring shipping.js's length-based step filter. Feet.
const ALT_ZOOM_STEP = ['step', ['zoom'], 30000, 4, 20000, 5, 10000, 6, 3000, 7, 500, 8, 0];

// Nose points north (track=0), matching icon-rotate's rotation-from-north semantics.
// docs/adr/0008: B* categories (gliders/balloons/drones) get distinct treatment
// (aircraft_light.png) rather than being folded into the generic aircraft icon.
// Both are plain white silhouettes registered as SDF (sdf: true) so the layer can tint
// them per aircraftGroupColor() at render time -- one shape asset per icon covers
// every color group, rather than needing a separately-baked PNG per group per shape.
const FLIGHTRADAR_ICONS = [
    { id: 'flightradar-aircraft', url: '/images/aircraft_generic.png', sdf: true },
    { id: 'flightradar-glider', url: '/images/aircraft_light.png', sdf: true },
];

// docs/adr/0008: category C* (ground vehicles/obstacles) is filtered out entirely --
// done here at feature-build time rather than as a MapLibre style filter, so the style
// itself only needs the altitude density step. Position is dead-reckoned from each
// record's last known state -- see interpolatedPosition/boundedElapsedSeconds above.
function buildFeatureCollection(aircraftByHex, now) {
    const features = [];
    for (const rec of aircraftByHex.values()) {
        if (typeof rec.lat !== 'number' || typeof rec.lon !== 'number') continue;
        const category = (rec.category || '').toUpperCase();
        if (category.startsWith('C')) continue;
        const elapsed = boundedElapsedSeconds(rec.receivedAt, now);
        const pos = interpolatedPosition({ lat: rec.lat, lon: rec.lon, gs: rec.gs, track: rec.track }, elapsed);
        features.push({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [pos.lon, pos.lat] },
            properties: {
                hex: rec.hex,
                flight: (rec.flight || '').trim() || rec.hex,
                registration: rec.r || '',
                aircraft_type: rec.t || '',
                alt_baro_ft: typeof rec.alt_baro === 'number' ? rec.alt_baro : 0,
                baro_rate_fpm: typeof rec.baro_rate === 'number' ? rec.baro_rate : null,
                nav_altitude_mcp_ft: typeof rec.nav_altitude_mcp === 'number' ? rec.nav_altitude_mcp : null,
                gs: rec.gs ?? 0,
                track: rec.track ?? 0,
                icon: category.startsWith('B') ? 'flightradar-glider' : 'flightradar-aircraft',
                color: aircraftGroupColor(rec.t),
                frozen: isFrozen(rec.receivedAt, now),
            },
        });
    }
    return { type: 'FeatureCollection', features };
}

function pruneStale(aircraftByHex, now) {
    for (const [hex, rec] of aircraftByHex) {
        if (now - rec.receivedAt > STALE_PRUNE_MS) aircraftByHex.delete(hex);
    }
}

function popupHtml(f) {
    const p = f.properties;
    const alt = p.alt_baro_ft ? `${Math.round(p.alt_baro_ft).toLocaleString()} ft` : 'ground';
    const target = targetAltitudeLabel(p.nav_altitude_mcp_ft, p.alt_baro_ft);
    const cls = aircraftClass(p.aircraft_type);
    const staleNote = p.frozen
        ? '<div style="color:#c0392b;font-size:11px;margin-top:4px;">&#9888; Signal lost -- position frozen</div>' : '';
    return `<div style="font-family:sans-serif;font-size:12px;color:#000;padding:5px;">
            <strong style="color:#007bff;font-size:14px;">${p.flight}</strong><br>
            ${p.aircraft_type ? `<span style="color:#666;">Type:</span> ${p.aircraft_type}<br>` : ''}
            <span style="color:#666;">Class:</span> ${cls}<br>
            ${p.registration ? `<span style="color:#666;">Registration:</span> ${p.registration}<br>` : ''}
            <span style="color:#666;">Status:</span> ${flightStatus(p.baro_rate_fpm)}<br>
            <span style="color:#666;">Altitude:</span> ${alt}<br>
            ${target ? `<span style="color:#666;">Target altitude:</span> ${target}<br>` : ''}
            <span style="color:#666;">Speed:</span> ${Math.round(p.gs)} kts<br>
            <span style="color:#666;">Heading:</span> ${Math.round(p.track)}&deg;<br>
            <span style="color:#666;">ICAO:</span> ${p.hex}${staleNote}
        </div>`;
}

// ws:// (or wss:// over https) sibling of window.MAP_UI, the same origin every other
// layer's fetch() calls already target.
function wsUrl() {
    return `${window.MAP_UI.replace(/^http/, 'ws')}/api/ws/flightradar`;
}

export function loadLayer(map, config) {
    const sourceId = 'flightradar-source';
    const layerId = 'flightradar-layer';

    const aircraftByHex = new Map();   // hex -> {...adsb.lol record fields, receivedAt}
    let ws = null;
    let closedByUs = false;
    let reconnectTimer = null;
    let rafId = null;
    let stopPopup = null;

    const sendViewport = () => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const b = map.getBounds();
        ws.send(JSON.stringify({
            type: 'viewport',
            west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth(),
        }));
    };

    const onMessage = (evt) => {
        let msg;
        try { msg = JSON.parse(evt.data); } catch { return; }
        if (msg.type !== 'aircraft_update') return;
        const now = Date.now();
        // One message per region key this connection subscribes to (routes/
        // flightradar.py's poll_due_regions -- not deduped server-side); an aircraft
        // seen from two overlapping regions just gets upserted twice, latest wins.
        for (const rec of msg.aircraft || []) {
            if (!rec.hex) continue;
            aircraftByHex.set(rec.hex, { ...rec, receivedAt: now });
        }
    };

    // docs/adr/0009: reconnection needs no special handling -- polling state is keyed
    // by region server-side, not connection identity, so a fresh connection + a fresh
    // viewport message on open is the entire recovery path.
    const scheduleReconnect = () => {
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(connect, 3000);
    };

    const connect = () => {
        ws = new WebSocket(wsUrl());
        ws.onopen = sendViewport;
        ws.onmessage = onMessage;
        ws.onclose = () => { if (!closedByUs) scheduleReconnect(); };
        ws.onerror = () => ws.close();
    };

    const renderFrame = () => {
        const now = Date.now();
        pruneStale(aircraftByHex, now);
        map.getSource(sourceId)?.setData(buildFeatureCollection(aircraftByHex, now));
        rafId = requestAnimationFrame(renderFrame);
    };

    const mount = async (cfg) => {
        if (map.getSource(sourceId)) return;
        await preloadIcons(map, FLIGHTRADAR_ICONS);

        map.addSource(sourceId, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer({
            id: layerId, type: 'symbol', source: sourceId,
            filter: ['>=', ['get', 'alt_baro_ft'], ALT_ZOOM_STEP],
            layout: {
                'icon-image': ['get', 'icon'],
                'icon-size': 0.5 * (cfg.icon_zoom ?? 1.0),
                'icon-rotate': ['get', 'track'],
                'icon-rotation-alignment': 'map',
                'icon-allow-overlap': true, 'icon-ignore-placement': true,
            },
            // Tints the SDF icon per aircraftGroupColor() -- see FLIGHTRADAR_ICONS.
            paint: {
                'icon-color': ['get', 'color'],
            },
        });

        stopPopup = hoverPopup(map, layerId, { offset: 10, html: popupHtml });
        map.on('moveend', sendViewport);

        closedByUs = false;
        connect();
        rafId = requestAnimationFrame(renderFrame);
    };

    const refresh = async (cfg) => {
        if (map.getLayer(layerId)) {
            map.setLayoutProperty(layerId, 'icon-size', 0.5 * (cfg.icon_zoom ?? 1.0));
        }
    };

    const unmount = () => {
        closedByUs = true;
        clearTimeout(reconnectTimer);
        if (rafId != null) cancelAnimationFrame(rafId);
        rafId = null;
        if (ws) { try { ws.close(); } catch { /* already closed */ } ws = null; }
        map.off('moveend', sendViewport);
        stopPopup?.();
        aircraftByHex.clear();
        if (map.getLayer(layerId)) map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    };

    return liveDataSync(map, {
        sectionKey: 'flightradar', initialConfig: config,
        mount, refresh, unmount,
        refreshMs: 3600000,   // data arrives via WS push, not a periodic fetch -- see mount()
    });
}
