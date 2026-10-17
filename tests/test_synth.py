from io import BytesIO

import pytest

from dvm.isobmff import extract_mask_samples, extract_tracks, parse_file
from dvm.mask import RasterRectangleValue, Sample
from dvm.synth import generate_test_mp4


def test_generate_and_parse_single_sample_mp4():
    sample = Sample(
        start=0,
        duration=2,
        value=RasterRectangleValue(1920, 1080, 420, 1080, 0, 1080),
    )
    mp4_bytes = generate_test_mp4([sample], canvas_width=1920, canvas_height=1080)
    assert len(mp4_bytes) > 0

    bio = BytesIO(mp4_bytes)
    boxes = parse_file(bio)
    fourccs = [b.fourcc for b in boxes]
    assert fourccs == ["ftyp", "moov", "mdat"]

    tracks = extract_tracks(boxes)
    assert len(tracks) == 2

    v_track = tracks[0]
    assert v_track.track_id == 1
    assert v_track.handler_type == "vide"
    assert v_track.sample_entry_type == "avc1"

    m_track = tracks[1]
    assert m_track.track_id == 2
    assert m_track.handler_type == "meta"
    assert m_track.sample_entry_type == "mebx"
    assert m_track.track_references.get("rndr") == [1]

    mask_samples = extract_mask_samples(m_track, bio)
    assert len(mask_samples) == 1
    s0 = mask_samples[0]
    assert s0.sample_index == 0
    assert s0.error is None
    assert s0.value == RasterRectangleValue(1920, 1080, 420, 1080, 0, 1080)


def test_generate_multi_sample_mp4_with_varying_rects():
    s1 = Sample(start=0, duration=1, value=RasterRectangleValue(1920, 1080, 0, 1080, 0, 1080))
    s2 = Sample(start=1, duration=1, value=RasterRectangleValue(1920, 1080, 200, 1080, 0, 1080))
    s3 = Sample(start=2, duration=2, value=RasterRectangleValue(1920, 1080, 420, 1080, 0, 1080))

    mp4_bytes = generate_test_mp4([s1, s2, s3], canvas_width=1920, canvas_height=1080, meta_timescale=1000)
    bio = BytesIO(mp4_bytes)
    boxes = parse_file(bio)
    tracks = extract_tracks(boxes)
    m_track = tracks[1]

    mask_samples = extract_mask_samples(m_track, bio)
    assert len(mask_samples) == 3

    assert mask_samples[0].dts == 0
    assert mask_samples[0].duration == 1000
    assert mask_samples[0].value == s1.value

    assert mask_samples[1].dts == 1000
    assert mask_samples[1].duration == 1000
    assert mask_samples[1].value == s2.value

    assert mask_samples[2].dts == 2000
    assert mask_samples[2].duration == 2000
    assert mask_samples[2].value == s3.value
