from fractions import Fraction as F

import pytest

from dvm import (MalformedPayload, MaskReader, RasterRectangleValue, Rect, Sample,
                 cfr_pts, check_samples, compile_timeline, parse_timeline, rect_at,
                 scale_to_buffer, to_ticks)


@pytest.fixture
def tl(psl_doc):
    return parse_timeline(psl_doc)


# ---- payload bytes --------------------------------------------------------

def test_payload_is_12_bytes_big_endian():
    v = RasterRectangleValue(1080, 1920, 0, 1080, 420, 1080)
    # 1080=0x0438 1920=0x0780 0 1080=0x0438 420=0x01A4 1080=0x0438
    assert v.pack() == bytes.fromhex("0438 0780 0000 0438 01a4 0438".replace(" ", ""))
    assert RasterRectangleValue.unpack(v.pack()) == v


def test_payload_wrong_length_and_overflow():
    with pytest.raises(MalformedPayload):
        RasterRectangleValue.unpack(b"\x00" * 11)
    with pytest.raises(MalformedPayload):
        RasterRectangleValue(70000, 1, 0, 1, 0, 1).pack()


# ---- scaling to the real buffer ------------------------------------------

def test_scale_identity():
    v = RasterRectangleValue(1920, 1920, 420, 1080, 0, 1920)
    assert scale_to_buffer(v, 1920, 1920) == Rect(420, 0, 1080, 1920)


def test_scale_half_resolution():
    v = RasterRectangleValue(1920, 1920, 420, 1080, 0, 1920)
    assert scale_to_buffer(v, 960, 960) == Rect(210, 0, 540, 960)


def test_scale_can_collapse_to_empty_which_is_valid():
    v = RasterRectangleValue(1080, 1080, 1, 1, 0, 10)
    r = scale_to_buffer(v, 720, 720)
    assert r.width == 0  # left=1, right=1 -> empty, not an error


def test_adjacent_rectangles_never_gap_or_overlap_after_scaling():
    for buf in (640, 721, 1000, 1919):
        for split in range(1, 1080, 37):
            a = scale_to_buffer(RasterRectangleValue(1080, 1080, 0, split, 0, 1080), buf, buf)
            b = scale_to_buffer(RasterRectangleValue(1080, 1080, split, 1080 - split, 0, 1080), buf, buf)
            assert a.right == b.x and b.right == buf


def test_scale_clips_to_buffer():
    v = RasterRectangleValue(1080, 1080, 1000, 500, 0, 1080)
    r = scale_to_buffer(v, 1080, 1080)
    assert r == Rect(1000, 0, 80, 1080)


def test_zero_reference_raster_is_malformed():
    with pytest.raises(MalformedPayload):
        scale_to_buffer(RasterRectangleValue(0, 1080, 0, 1, 0, 1), 100, 100)


# ---- compile --------------------------------------------------------------

def test_compile_matches_rect_at_for_every_frame(tl):
    fps = F(30)
    pts = cfr_pts(fps, 360)
    samples = compile_timeline(tl, pts, F(12))
    reader = MaskReader(samples)
    for p in pts:
        got, status = reader.rect_at(p, 1920, 1920)
        assert status == "mask"
        assert got == rect_at(tl, p)


def test_compile_run_structure_and_size(tl):
    samples = compile_timeline(tl, cfr_pts(F(30), 360), F(12))
    # hold(portrait incl. frame at t=3.0 where p=0), then 14 transition frames,
    # square hold, 14, landscape hold, 14, portrait hold  => 4 holds + 3*14
    assert len(samples) == 46
    assert samples[0].start == 0 and samples[0].duration == F(91, 30)
    assert sum(s.duration for s in samples) == 12
    assert all(a.end == b.start for a, b in zip(samples, samples[1:]))
    assert check_samples(samples) == []
    assert 12 * len(samples) == 552  # payload bytes


def test_cut_only_timeline_compiles_to_one_sample_per_event():
    import json
    from conftest import ROOT
    tl = parse_timeline(json.loads((ROOT / "examples" / "era-cuts.json").read_text()))
    samples = compile_timeline(tl, cfr_pts(F(24000, 1001), 192), F(192 * 1001, 24000))
    assert len(samples) == 4


def test_compile_handles_variable_frame_rate(tl):
    pts = [F(0), F(1, 10), F(3, 10), F(3), F("3.2"), F("3.45"), F("3.9"), F(6), F("6.1")]
    samples = compile_timeline(tl, pts, F(7))
    reader = MaskReader(samples)
    for p in pts:
        assert reader.rect_at(p, 1920, 1920)[0] == rect_at(tl, p)
    assert samples[-1].end == 7
    assert all(a.end == b.start for a, b in zip(samples, samples[1:]))


def test_compile_rejects_bad_pts(tl):
    with pytest.raises(ValueError):
        compile_timeline(tl, [F(1), F(1)], F(2))
    with pytest.raises(ValueError):
        compile_timeline(tl, [F(0), F(1)], F(1))


def test_ticks():
    assert to_ticks(F(1, 30), 30000) == 1000
    assert to_ticks(F(1001, 30000), 30000) == 1001
    with pytest.raises(ValueError):
        to_ticks(F(1, 7), 30)


# ---- reader: seeking, gaps, malformed ------------------------------------

def test_seek_directly_into_mid_transition(tl):
    samples = compile_timeline(tl, cfr_pts(F(30), 360), F(12))
    reader = MaskReader(samples)
    t = F("3.25")  # inside the frame whose pts is 3.2333..., a mid-transition sample
    got, status = reader.rect_at(t, 1920, 1920)
    frame_pts = F(97, 30)
    assert status == "mask" and got == rect_at(tl, frame_pts)
    assert got.x not in (0, 420)  # genuinely mid-transition


def test_reader_scales_to_other_resolution(tl):
    reader = MaskReader(compile_timeline(tl, cfr_pts(F(30), 360), F(12)))
    r, _ = reader.rect_at(F(1), 960, 960)
    assert r == Rect(210, 0, 540, 960)


def test_missing_sample_means_full_frame():
    s = [Sample(F(1), F(1), RasterRectangleValue(100, 100, 10, 50, 10, 50))]
    reader = MaskReader(s)
    assert reader.rect_at(F(0), 200, 100) == (Rect(0, 0, 200, 100), "default-no-sample")
    assert reader.rect_at(F("1.5"), 100, 100) == (Rect(10, 10, 50, 50), "mask")
    assert reader.rect_at(F(2), 200, 100) == (Rect(0, 0, 200, 100), "default-no-sample")
    assert MaskReader([]).rect_at(F(0), 10, 10)[1] == "default-no-sample"


def test_malformed_sample_falls_back_to_full_frame():
    s = [Sample(F(0), F(1), RasterRectangleValue(0, 0, 0, 5, 0, 5))]
    assert MaskReader(s).rect_at(F("0.5"), 64, 48) == (Rect(0, 0, 64, 48), "default-malformed")


def test_empty_mask_hides_everything():
    s = [Sample(F(0), F(1), RasterRectangleValue(100, 100, 0, 0, 0, 0))]
    r, status = MaskReader(s).rect_at(F(0), 100, 100)
    assert status == "mask" and r.width == 0 and r.height == 0


def test_overlapping_samples_are_rejected():
    v = RasterRectangleValue(10, 10, 0, 5, 0, 5)
    s = [Sample(F(0), F(2), v), Sample(F(1), F(2), v)]
    assert any("overlaps" in p for p in check_samples(s))
    with pytest.raises(ValueError):
        MaskReader(s)
