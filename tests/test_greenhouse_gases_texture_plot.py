#!/usr/bin/env python3
"""Unit tests for GhgUpdater.plot()'s conversion to a raw client-LUT data texture
(issue #312) -- palette/scale settings must NOT affect the encoded texture's fixed
per-species physical domain, only the live display range written to ghg_meta.json
for the client-side LUT to remap onto. See ui/modules/greenhouse_gases.js's
buildScaledLUT.
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import numpy as np

from atmos_gl.tasks.greenhouse_gases import GhgUpdater, _ABS_ENCODE_DOMAIN, _ANOMALY_ENCODE_DOMAIN


def make_bare_updater(settings=None, output_path="/data/greenhouse_gases.png"):
    u = GhgUpdater.__new__(GhgUpdater)
    u.settings = settings or {}
    u.common = {}
    u.output_path = output_path
    u.section = "greenhouse_gases"
    u._write_meta_sidecar = MagicMock()
    return u


_NEW_LATS = np.array([0.0, 5.0, 10.0])
_NEW_LONS = np.array([0.0, 5.0, 10.0])
_DISPLAY_DATA = np.array([[400.0, 405.0, 410.0], [410.0, 415.0, 420.0], [420.0, 425.0, 430.0]])


def _patched(mock_land=None):
    stack = ExitStack()
    mocks = {
        "load_field": stack.enter_context(
            patch("atmos_gl.tasks.greenhouse_gases.load_field",
                  return_value=(_DISPLAY_DATA.copy(), _NEW_LATS, _NEW_LONS))
        ),
        "compute_anomaly": stack.enter_context(
            patch("atmos_gl.tasks.greenhouse_gases.compute_anomaly", return_value=_DISPLAY_DATA.copy())
        ),
        "coastline_land_mask": stack.enter_context(
            patch("atmos_gl.tasks.greenhouse_gases.coastline_land_mask", return_value=mock_land)
        ),
        "encode_frames": stack.enter_context(
            patch("atmos_gl.tasks.greenhouse_gases.encode_frames", return_value=True)
        ),
    }
    return stack, mocks


def test_plot_absolute_encodes_with_each_species_fixed_physical_domain():
    for species, domain in _ABS_ENCODE_DOMAIN.items():
        u = make_bare_updater(settings={"co2_min_ppm": 400, "co2_max_ppm": 410, "ch4_min_ppb": 1800, "ch4_max_ppb": 1900})
        stack, mocks = _patched()
        with stack:
            u.regrid_for_lod = MagicMock(return_value=(_NEW_LATS, _NEW_LONS, _DISPLAY_DATA.copy()))
            u.plot(species, "absolute", "/tmp/current.nc", None, f"/data/greenhouse_gases_{species}_absolute.png")

            args = mocks["encode_frames"].call_args.args
            assert args[2] == domain[0]
            assert args[3] == domain[1]


def test_plot_anomaly_encodes_with_each_species_fixed_physical_domain():
    for species, domain in _ANOMALY_ENCODE_DOMAIN.items():
        u = make_bare_updater(settings={})
        stack, mocks = _patched()
        with stack:
            u.regrid_for_lod = MagicMock(return_value=(_NEW_LATS, _NEW_LONS, _DISPLAY_DATA.copy()))
            u.plot(species, "anomaly", "/tmp/current.nc", "/tmp/egg4.nc",
                   f"/data/greenhouse_gases_{species}_anomaly.png")

            args = mocks["encode_frames"].call_args.args
            assert args[2] == domain[0]
            assert args[3] == domain[1]
            sidecar_entry_key, sidecar_value = u._write_meta_sidecar.call_args.args[1:]
            assert sidecar_entry_key == species
            assert sidecar_value["anomaly"]["vmin"] == -sidecar_value["anomaly"]["vmax"]


def test_plot_masks_land_cells_as_nan_before_encoding():
    land = np.array([[False, False, True], [False, False, True], [False, False, True]])
    u = make_bare_updater(settings={})
    stack, mocks = _patched(mock_land=land)
    with stack:
        u.regrid_for_lod = MagicMock(return_value=(_NEW_LATS, _NEW_LONS, _DISPLAY_DATA.copy()))
        u.plot("co2", "absolute", "/tmp/current.nc", None, "/data/greenhouse_gases_co2_absolute.png")

        encoded_frame = mocks["encode_frames"].call_args.args[0][0]
        assert np.isnan(encoded_frame[:, 2]).all()
        assert not np.isnan(encoded_frame[:, :2]).any()


def test_mode_settings_signature_absolute_is_empty_since_nothing_affects_the_encoded_texture():
    u = make_bare_updater(settings={"co2_min_ppm": 400, "co2_max_ppm": 420, "co2_palette": "vivid", "opacity": 90})
    assert u._mode_settings_signature("co2", "absolute") == u._settings_signature({})


def test_mode_settings_signature_anomaly_still_tracks_baseline_year():
    u = make_bare_updater(settings={"baseline_year": 2015})
    assert u._mode_settings_signature("co2", "anomaly") == u._settings_signature({"baseline_year": 2015})
