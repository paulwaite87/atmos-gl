#!/usr/bin/env python3
"""Tests for lib/texture.py's GPU texture encoders (architecture review candidate
"lock down the numeric core with tests"). Both functions are fully pure (array in, PNG
out) but had zero coverage. Round-trips through a tmp_path PNG rather than asserting on
an intermediate array -- PNG is lossless for RGBA, so this tests exactly the bytes that
ship to disk and that the shaders then read.
"""
import numpy as np
import pytest
from PIL import Image

from worldmap.lib.texture import encode_uv, encode_frames


def _decode_uv_channel(byte, vmax):
    return (float(byte) / 255.0) * 2.0 * vmax - vmax


# ---- encode_uv ----------------------------------------------------------------

def test_encode_uv_round_trips_valid_values(tmp_path):
    u = np.array([[10.0, -5.0]], dtype=np.float32)
    v = np.array([[0.0, 20.0]], dtype=np.float32)
    vmax = 40.0
    path = tmp_path / "uv.png"

    encode_uv(u, v, str(path), vmax)
    px = np.asarray(Image.open(path))

    assert px.shape == (1, 2, 4)
    assert _decode_uv_channel(px[0, 0, 0], vmax) == pytest.approx(10.0, abs=0.2)
    assert _decode_uv_channel(px[0, 1, 1], vmax) == pytest.approx(20.0, abs=0.2)


def test_encode_uv_masks_nan_to_alpha_zero(tmp_path):
    u = np.array([[1.0, np.nan]], dtype=np.float32)
    v = np.array([[0.0, np.nan]], dtype=np.float32)
    path = tmp_path / "uv.png"

    encode_uv(u, v, str(path), vmax=40.0)
    px = np.asarray(Image.open(path))

    assert px[0, 0, 3] == 255  # valid cell -> opaque
    assert px[0, 1, 3] == 0    # NaN cell -> transparent (no-data)


def test_encode_uv_flips_rows_when_lat_is_ascending(tmp_path):
    # Two rows: row0 has u=10 at lat=-45 (south), row1 has u=-10 at lat=45 (north).
    # Ascending lat means south-first input; encode_uv must flip so north ends up row0.
    u = np.array([[10.0], [-10.0]], dtype=np.float32)
    v = np.array([[0.0], [0.0]], dtype=np.float32)
    lat = np.array([-45.0, 45.0])  # ascending -> south-first
    path = tmp_path / "uv.png"

    encode_uv(u, v, str(path), vmax=40.0, lat=lat)
    px = np.asarray(Image.open(path))

    # After the flip, row 0 (north-at-top) should hold the original row1's value (-10).
    assert _decode_uv_channel(px[0, 0, 0], 40.0) == pytest.approx(-10.0, abs=0.2)
    assert _decode_uv_channel(px[1, 0, 0], 40.0) == pytest.approx(10.0, abs=0.2)


def test_encode_uv_does_not_flip_when_lat_is_already_north_first(tmp_path):
    u = np.array([[10.0], [-10.0]], dtype=np.float32)
    v = np.array([[0.0], [0.0]], dtype=np.float32)
    lat = np.array([45.0, -45.0])  # descending -> already north-first
    path = tmp_path / "uv.png"

    encode_uv(u, v, str(path), vmax=40.0, lat=lat)
    px = np.asarray(Image.open(path))

    assert _decode_uv_channel(px[0, 0, 0], 40.0) == pytest.approx(10.0, abs=0.2)


def test_encode_uv_rejects_mismatched_shapes():
    u = np.zeros((2, 2), dtype=np.float32)
    v = np.zeros((3, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        encode_uv(u, v, "/dev/null", vmax=40.0)


# ---- encode_frames -------------------------------------------------------------

def test_encode_frames_16bit_round_trips(tmp_path):
    frame = np.array([[0.0, 50.0], [100.0, 25.0]], dtype=np.float32)
    path = tmp_path / "frames.png"

    encode_frames([frame], str(path), vmin=0.0, vmax=100.0, bits=16)
    px = np.asarray(Image.open(path))

    norm = (px[..., 0].astype(np.float64) * 256 + px[..., 1]) / 65535.0
    decoded = norm * 100.0  # linear, vmin=0 vmax=100
    assert np.allclose(decoded, frame, atol=0.01)


def test_encode_frames_8bit_round_trips(tmp_path):
    frame = np.array([[0.0, 50.0, 100.0]], dtype=np.float32)
    path = tmp_path / "frames8.png"

    encode_frames([frame], str(path), vmin=0.0, vmax=100.0, bits=8)
    px = np.asarray(Image.open(path))

    decoded = (px[..., 0].astype(np.float64) / 255.0) * 100.0
    assert np.allclose(decoded, frame, atol=0.5)


def test_encode_frames_sqrt_transform_gives_low_end_more_precision(tmp_path):
    # A small value near vmin should map to a LARGER normalized fraction under sqrt
    # than under linear -- that's the whole point of the transform.
    frame = np.array([[1.0]], dtype=np.float32)  # small value in a 0..100 range
    linear_path = tmp_path / "linear.png"
    sqrt_path = tmp_path / "sqrt.png"

    encode_frames([frame], str(linear_path), vmin=0.0, vmax=100.0, bits=16)
    encode_frames([frame], str(sqrt_path), vmin=0.0, vmax=100.0, bits=16, transform="sqrt")

    def norm16(path):
        px = np.asarray(Image.open(path))
        return (px[0, 0, 0].astype(np.float64) * 256 + px[0, 0, 1]) / 65535.0

    assert norm16(sqrt_path) > norm16(linear_path)
    # And it really is the square root of the linear norm.
    assert norm16(sqrt_path) == pytest.approx(np.sqrt(norm16(linear_path)), abs=0.01)


def test_encode_frames_masks_nan_to_alpha_zero(tmp_path):
    frame = np.array([[1.0, np.nan]], dtype=np.float32)
    path = tmp_path / "frames.png"

    encode_frames([frame], str(path), vmin=0.0, vmax=10.0, bits=16)
    px = np.asarray(Image.open(path))

    assert px[0, 0, 3] == 255
    assert px[0, 1, 3] == 0


def test_encode_frames_stacks_multiple_frames_vertically(tmp_path):
    frame_a = np.zeros((2, 3), dtype=np.float32)
    frame_b = np.ones((2, 3), dtype=np.float32) * 10.0
    path = tmp_path / "stacked.png"

    encode_frames([frame_a, frame_b], str(path), vmin=0.0, vmax=10.0, bits=16)
    px = np.asarray(Image.open(path))

    assert px.shape == (4, 3, 4)  # 2 frames x 2 rows each, stacked
    # frame 0 (top slab) should decode near 0; frame 1 (bottom slab) near max.
    top_norm = (px[0, 0, 0].astype(np.float64) * 256 + px[0, 0, 1]) / 65535.0
    bottom_norm = (px[2, 0, 0].astype(np.float64) * 256 + px[2, 0, 1]) / 65535.0
    assert top_norm == pytest.approx(0.0, abs=0.01)
    assert bottom_norm == pytest.approx(1.0, abs=0.01)


def test_encode_frames_rejects_mismatched_frame_shapes():
    frames = [np.zeros((2, 2), dtype=np.float32), np.zeros((3, 3), dtype=np.float32)]
    with pytest.raises(ValueError):
        encode_frames(frames, "/dev/null", vmin=0.0, vmax=1.0)
