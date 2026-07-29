#!/usr/bin/env python3
"""Updater._is_render_fresh / _settings_signature / _write_render_signature: the
shared freshness-check helper backing SSTUpdater.run() and GhgUpdater.run().

Root cause fixed here: both updaters' run() loops used to decide whether to
re-render purely by comparing the output PNG's mtime against its SOURCE netCDF
cache's mtime -- a palette/scale/opacity-only config change never touches that
cache file, so it was silently never picked up until the source data itself next
refreshed (which can be hours away). The fix adds a persisted settings-signature
sidecar ('<out>.sig') that must ALSO match the current render-relevant settings for
an output to count as fresh.

Also covers Updater._publish_variant, lifted here after the same "copy the
currently-configured variant's render to the stable, run-agnostic base filename"
logic appeared verbatim in SSTUpdater, GhgUpdater, and AirQualityUpdater -- the
orchestration-level tests for those three (test_sst_mode_switch.py,
test_greenhouse_gases_updater.py, test_air_quality_updater.py) mock this method out
entirely, so its actual file-copy behaviour is only exercised here.
"""
import os

from atmos_gl.tasks.common import Updater


def make_bare_updater():
    return Updater.__new__(Updater)


def _touch(path, mtime_offset=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")
    if mtime_offset:
        t = os.path.getmtime(path) + mtime_offset
        os.utime(path, (t, t))


def test_settings_signature_is_stable_regardless_of_dict_key_order():
    u = make_bare_updater()
    sig_a = u._settings_signature({"palette": "thermal", "min": 0, "max": 32})
    sig_b = u._settings_signature({"max": 32, "min": 0, "palette": "thermal"})
    assert sig_a == sig_b


def test_settings_signature_differs_for_different_values():
    u = make_bare_updater()
    sig_a = u._settings_signature({"palette": "thermal"})
    sig_b = u._settings_signature({"palette": "vivid"})
    assert sig_a != sig_b


def test_is_render_fresh_false_when_output_missing(tmp_path):
    u = make_bare_updater()
    out = str(tmp_path / "out.png")
    assert u._is_render_fresh(out, [], "sig") is False


def test_is_render_fresh_false_when_source_is_newer_than_output(tmp_path):
    u = make_bare_updater()
    out = str(tmp_path / "out.png")
    src = str(tmp_path / "src.nc")
    _touch(out)
    _touch(src, mtime_offset=10)
    sig = "sig"
    u._write_render_signature(out, sig)
    assert u._is_render_fresh(out, [src], sig) is False


def test_is_render_fresh_false_when_no_signature_was_ever_written(tmp_path):
    """A pre-existing output with no .sig sidecar (e.g. rendered before this
    freshness check existed) must be treated as needing a re-render, not silently
    trusted -- otherwise the fix would never take effect for anything already on
    disk until its source data happens to change."""
    u = make_bare_updater()
    out = str(tmp_path / "out.png")
    src = str(tmp_path / "src.nc")
    _touch(src)
    _touch(out, mtime_offset=10)
    assert u._is_render_fresh(out, [src], "sig") is False


def test_is_render_fresh_false_when_settings_signature_changed(tmp_path):
    """The actual bug: source data unchanged, but the config-derived signature
    (e.g. palette) differs from what was last rendered -- must NOT be treated as
    fresh."""
    u = make_bare_updater()
    out = str(tmp_path / "out.png")
    src = str(tmp_path / "src.nc")
    _touch(src)
    _touch(out, mtime_offset=10)
    u._write_render_signature(out, "old-sig")
    assert u._is_render_fresh(out, [src], "new-sig") is False


def test_is_render_fresh_true_when_data_and_signature_both_match(tmp_path):
    u = make_bare_updater()
    out = str(tmp_path / "out.png")
    src = str(tmp_path / "src.nc")
    _touch(src)
    _touch(out, mtime_offset=10)
    u._write_render_signature(out, "sig")
    assert u._is_render_fresh(out, [src], "sig") is True


def test_publish_variant_copies_the_variant_render_to_the_stable_base_filename(tmp_path):
    u = make_bare_updater()
    u.output_path = str(tmp_path / "data" / "greenhouse_gases.png")
    variant_path = str(tmp_path / "data" / "greenhouse_gases_co2_absolute.png")
    _touch(variant_path)

    u._publish_variant(variant_path)

    assert open(u.output_path, "rb").read() == open(variant_path, "rb").read()


def test_publish_variant_also_copies_the_key_companion_image_when_present(tmp_path):
    u = make_bare_updater()
    u.output_path = str(tmp_path / "data" / "greenhouse_gases.png")
    variant_path = str(tmp_path / "data" / "greenhouse_gases_co2_absolute.png")
    _touch(variant_path)
    _touch(str(tmp_path / "data" / "greenhouse_gases_co2_absolute_key.png"))

    u._publish_variant(variant_path)

    assert os.path.exists(str(tmp_path / "data" / "greenhouse_gases_key.png"))


def test_publish_variant_skips_the_key_companion_when_it_does_not_exist(tmp_path):
    u = make_bare_updater()
    u.output_path = str(tmp_path / "data" / "greenhouse_gases.png")
    variant_path = str(tmp_path / "data" / "greenhouse_gases_co2_absolute.png")
    _touch(variant_path)

    u._publish_variant(variant_path)

    assert not os.path.exists(str(tmp_path / "data" / "greenhouse_gases_key.png"))
