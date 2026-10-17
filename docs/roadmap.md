# Roadmap: Terrascope and candidate layers

Captured 2026-10-16 so the suggestions aren't lost. The name decision is made (Terrascope);
the layer list is a brainstorm, not a commitment.
Verify every external feed's real response shape with a raw request before building on it.

## Scope as it stands

A live 3D globe (MapLibre GL, FastAPI, PostGIS) with five groups of layers:

- **Weather:** clouds, precipitation, precipitable water, isobars, wind, jet stream, waves,
  storm tracks, lightning.
- **Climate:** SST, air temperature, ocean currents, ozone, CAPE/CIN storm watch, CO₂/CH₄.
- **Hazards and events:** earthquakes, volcanoes and smoke plumes, wildfires, air quality,
  flood risk, world events, the Ukraine frontline.
- **Human activity:** AIS shipping, flight radar, satellites, place markers.
- **World data:** Country Statistics (41 indicators, three groups, picker) and population density.

It is no longer mainly a weather project, and "atmos" covers only part of it. Its sources are
many (NOAA, USGS, NASA, Copernicus, AIS, ADS-B and others).

## a) Name: decided — Terrascope

Alternatives considered:

| Name | Notes |
|---|---|
| **Terrascope** (chosen) | An instrument for looking at the Earth. Neutral across weather, events and data. |
| Worldglass | "Globe" plus "glass". Simple but generic. |
| Orrery | A model of a moving system. Elegant, but suggests the solar system. |
| Meridian | Short and globe-flavoured, but very common. |
| Planetary Pulse | Says "live" clearly, but long. |

Before committing: check name collisions, domains and GitHub.

**Status: renamed.** The package, config (`config/terrascope.json`), `terrascope.sh`, Docker
image names, installer URL and docs now use Terrascope. Still to do by hand: rename the GitHub
repo to `terrascope` (GitHub redirects the old URL) and re-publish the images. `gfs_atmos` and
other atmosphere-meaning names were deliberately left alone.

## Done

GDACS alerts, tsunami warnings, internet outages and UV index are built. Night lights was
built and then dropped: the GIBS Black Marble daily product has swath and wedge artifacts
(for example a wedge across Chad) that neither a noise floor nor an ocean mask removes.

## Next: chosen layers (in order)

1. Aurora and space weather (OVATION aurora oval; built, a Kp readout is left out)
2. Sea ice (NOAA OISST ice concentration; built)
3. Dust and aerosols
4. Soil moisture and drought
5. WHO disease outbreaks

The remaining candidates below stay as the backlog.

## b) Candidate layers

Grouped by the existing layer shape each would reuse (see `docs/conventions/layers.md`).

### Animated or static fill (gridded fields)

- **Sea ice concentration and extent** (NSIDC or OSI SAF). Very visual at the poles; pairs with SST.
- **Snow cover and depth** (NOAA SNODAS, or GIBS MODIS).
- **Soil moisture and drought** (NASA SMAP via the Earthdata token, or Copernicus CDS).
  Complements flood risk.
- **Dust and aerosols** (CAMS via the CDS key). Saharan dust plumes; sits next to air quality
  and smoke.
- **UV index** (CAMS, or derived from the ozone layer).
- **Coral bleaching and marine heatwaves** (NOAA Coral Reef Watch). Reuses the SST pipeline.
- **Chlorophyll and ocean colour** (GIBS).
- **Night lights** (VIIRS). Counterpart to the terminator and population density.

### Particle overlay

- **Swell direction and period.** Wave data already comes from GFS-wave; this adds a flow-style
  layer on top.
- **Sea ice drift.**

### Point feeds and markers

- **Aurora oval and space weather** (NOAA SWPC OVATION). Cheap, global, live, and looks great
  on a globe.
- **Tsunami warnings** (NOAA PTWC). Links to the earthquakes layer.
- **GDACS alerts.** One feed for floods, cyclones, droughts and earthquakes; could cross-check
  existing layers.
- **Buoys and weather stations** (NDBC buoys, METAR). Ground truth against the model fields.
- **Internet outages** (Cloudflare Radar, or IODA). Fits next to world events.
- **Disease outbreaks** (WHO Disease Outbreak News).
- **Fireballs and meteors** (NASA CNEOS).
- **Launch schedule and re-entries**, as an extra for the satellites layer.

### Suggested priority

1. Aurora and space weather (best return on effort).
2. Sea ice.
3. Dust and aerosols.
4. Soil moisture and drought.
5. GDACS alerts.
6. Tsunami warnings.

The first four use free feeds, and several reuse API keys already configured (CDS, Earthdata).
