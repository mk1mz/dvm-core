import struct
from io import BytesIO

import pytest

from dvm.isobmff import (
    Box,
    extract_mask_samples,
    extract_sample_table,
    extract_tracks,
    format_box_tree,
    parse_boxes,
    parse_chunk_offsets,
    parse_ctts,
    parse_file,
    parse_hdlr,
    parse_mdhd,
    parse_stsc,
    parse_stsd_entry_type,
    parse_stsz,
    parse_stts,
    parse_tkhd,
    parse_tref,
    read_box_header,
)
from dvm.mask import RasterRectangleValue


def test_read_box_header_standard():
    bio = BytesIO(struct.pack(">I", 16) + b"test" + b"12345678")
    hdr = read_box_header(bio)
    assert hdr == ("test", 0, 8, 16)


def test_read_box_header_extended_64bit():
    bio = BytesIO(struct.pack(">I", 1) + b"test" + struct.pack(">Q", 100) + b"extra")
    hdr = read_box_header(bio)
    assert hdr == ("test", 0, 16, 100)


def test_read_box_header_eof_and_short():
    assert read_box_header(BytesIO(b"")) is None
    assert read_box_header(BytesIO(b"1234")) is None
    assert read_box_header(BytesIO(struct.pack(">I", 1) + b"test" + b"short")) is None


def test_box_navigation_methods():
    b_inner = Box(fourcc="leaf", offset=16, header_size=8, total_size=20, body=b"data")
    b_mid = Box(fourcc="trak", offset=8, header_size=8, total_size=30, children=[b_inner])
    b_root = Box(fourcc="moov", offset=0, header_size=8, total_size=40, children=[b_mid])

    assert b_root.find("trak") is b_mid
    assert b_root.find("none") is None
    assert b_root.find_all("trak") == [b_mid]
    assert b_root.find_all("none") == []
    assert b_root.find_deep("trak", "leaf") is b_inner
    assert b_root.find_deep("trak", "missing") is None

    assert b_inner.body_offset == 24
    assert b_inner.body_size == 12

    b_eof = Box(fourcc="mdat", offset=0, header_size=8, total_size=0)
    with pytest.raises(ValueError, match="box extends to EOF"):
        _ = b_eof.body_size


def test_parse_tkhd_v0_and_v1():
    # v0: version(1) + flags(3) + ctime(4) + mtime(4) + track_id(4) + reserved(4) + duration(4)
    body_v0 = b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 42, 0) + struct.pack(">I", 1200)
    tid, dur = parse_tkhd(body_v0)
    assert tid == 42
    assert dur == 1200

    # v1: version(1) + flags(3) + ctime(8) + mtime(8) + track_id(4) + reserved(4) + duration(8)
    body_v1 = b"\x01\x00\x00\x00" + b"\x00" * 16 + struct.pack(">II", 99, 0) + struct.pack(">Q", 5000000000)
    tid1, dur1 = parse_tkhd(body_v1)
    assert tid1 == 99
    assert dur1 == 5000000000


def test_parse_mdhd_v0_and_v1():
    # v0: version(1) + flags(3) + ctime(4) + mtime(4) + timescale(4) + duration(4)
    body_v0 = b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 30000, 60000)
    ts, dur = parse_mdhd(body_v0)
    assert ts == 30000
    assert dur == 60000

    # v1: version(1) + flags(3) + ctime(8) + mtime(8) + timescale(4) + duration(8)
    body_v1 = b"\x01\x00\x00\x00" + b"\x00" * 16 + struct.pack(">IQ", 600, 1200000000)
    ts1, dur1 = parse_mdhd(body_v1)
    assert ts1 == 600
    assert dur1 == 1200000000


def test_parse_hdlr():
    # hdlr: version(1) + flags(3) + pre_defined(4) + handler_type(4) + reserved(12) + name
    body = b"\x00\x00\x00\x00\x00\x00\x00\x00" + b"meta" + b"\x00" * 12 + b"MetadataHandler\x00extra"
    ht, hn = parse_hdlr(body)
    assert ht == "meta"
    assert hn == "MetadataHandler"

    # Non-null terminated
    body2 = b"\x00\x00\x00\x00\x00\x00\x00\x00" + b"vide" + b"\x00" * 12 + b"VideoHandler"
    ht2, hn2 = parse_hdlr(body2)
    assert ht2 == "vide"
    assert hn2 == "VideoHandler"


def test_parse_tref():
    rndr_box = Box(fourcc="rndr", offset=0, header_size=8, total_size=16,
                   body=struct.pack(">II", 1, 2))
    tref_box = Box(fourcc="tref", offset=0, header_size=8, total_size=24,
                   children=[rndr_box])
    refs = parse_tref(tref_box)
    assert refs == {"rndr": [1, 2]}


def test_parse_stsd_entry_type():
    # version(1) + flags(3) + entry_count(4) + size(4) + fourcc(4)
    stsd_body = b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">I", 24) + b"mebx" + b"\x00" * 8
    assert parse_stsd_entry_type(stsd_body) == "mebx"
    assert parse_stsd_entry_type(b"short") == "????"


def test_parse_sample_table_components():
    # stts: version/flags(4) + count(4) + [(count, delta)]
    stts_data = b"\x00\x00\x00\x00" + struct.pack(">I", 2) + struct.pack(">II", 3, 100) + struct.pack(">II", 2, 200)
    assert parse_stts(stts_data) == [(3, 100), (2, 200)]

    # ctts: v0 vs v1
    ctts_v0 = b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">II", 5, 50)
    assert parse_ctts(ctts_v0) == [(5, 50)]
    ctts_v1 = b"\x01\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">Ii", 5, -10)
    assert parse_ctts(ctts_v1) == [(5, -10)]

    # stsz: default size vs variable
    stsz_const = b"\x00\x00\x00\x00" + struct.pack(">II", 12, 3)
    assert parse_stsz(stsz_const) == [12, 12, 12]
    stsz_var = b"\x00\x00\x00\x00" + struct.pack(">II", 0, 2) + struct.pack(">II", 12, 24)
    assert parse_stsz(stsz_var) == [12, 24]

    # stsc: version/flags(4) + count(4) + [(first_chunk, spc, desc)]
    stsc_data = b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">III", 1, 4, 1)
    assert parse_stsc(stsc_data) == [(1, 4, 1)]

    # chunk offsets (stco vs co64)
    stbl_stco = Box(fourcc="stbl", offset=0, header_size=8, total_size=20,
                    children=[Box("stco", 0, 8, 16, body=b"\x00\x00\x00\x00" + struct.pack(">II", 1, 1024))])
    assert parse_chunk_offsets(stbl_stco) == [1024]

    stbl_co64 = Box(fourcc="stbl", offset=0, header_size=8, total_size=24,
                    children=[Box("co64", 0, 8, 20, body=b"\x00\x00\x00\x00" + struct.pack(">IQ", 1, 0x100000000))])
    assert parse_chunk_offsets(stbl_co64) == [0x100000000]


def test_extract_sample_table_roundtrip():
    # Construct complete stbl with 2 samples
    stts = Box("stts", 0, 8, 16, body=b"\x00\x00\x00\x00" + struct.pack(">III", 1, 2, 30))
    stsz = Box("stsz", 0, 8, 20, body=b"\x00\x00\x00\x00" + struct.pack(">III", 0, 2, 12) + struct.pack(">I", 12))
    stsc = Box("stsc", 0, 8, 20, body=b"\x00\x00\x00\x00" + struct.pack(">IIII", 1, 1, 2, 1))
    stco = Box("stco", 0, 8, 16, body=b"\x00\x00\x00\x00" + struct.pack(">II", 1, 100))
    stbl = Box("stbl", 0, 8, 80, children=[stts, stsz, stsc, stco])

    mock_file = BytesIO(b"\x00" * 100 + b"sample1_data" + b"sample2_data")
    entries = extract_sample_table(stbl, mock_file)
    assert len(entries) == 2
    assert entries[0].index == 0 and entries[0].offset == 100 and entries[0].size == 12 and entries[0].dts == 0
    assert entries[1].index == 1 and entries[1].offset == 112 and entries[1].size == 12 and entries[1].dts == 30


def test_extract_mask_samples_handling():
    # Valid 12-byte payload
    valid_payload = RasterRectangleValue(1920, 1080, 10, 100, 20, 200).pack()
    # Malformed payload (< 12 bytes)
    short_payload = b"\x00\x01\x02"

    stts = Box("stts", 0, 8, 16, body=b"\x00\x00\x00\x00" + struct.pack(">III", 1, 2, 30))
    stsz = Box("stsz", 0, 8, 20, body=b"\x00\x00\x00\x00" + struct.pack(">III", 0, 2, len(valid_payload)) + struct.pack(">I", len(short_payload)))
    stsc = Box("stsc", 0, 8, 20, body=b"\x00\x00\x00\x00" + struct.pack(">IIII", 1, 1, 2, 1))
    stco = Box("stco", 0, 8, 16, body=b"\x00\x00\x00\x00" + struct.pack(">II", 1, 0))
    stbl = Box("stbl", 0, 8, 80, children=[stts, stsz, stsc, stco])

    from dvm.isobmff import TrackInfo
    track = TrackInfo(
        track_id=2, handler_type="meta", handler_name="MetadataHandler",
        timescale=30, duration=60, sample_entry_type="mebx",
        track_references={"rndr": [1]}, stbl_box=stbl, trak_box=Box("trak", 0, 8, 80)
    )

    mock_file = BytesIO(valid_payload + short_payload)
    samples = extract_mask_samples(track, mock_file)
    assert len(samples) == 2
    assert samples[0].value == RasterRectangleValue(1920, 1080, 10, 100, 20, 200)
    assert samples[0].error is None
    assert samples[1].value is None
    assert "too short" in samples[1].error


def test_format_box_tree():
    child = Box(fourcc="tkhd", offset=8, header_size=8, total_size=32, body=b"\x00" * 24)
    root = Box(fourcc="trak", offset=0, header_size=8, total_size=40, children=[child])
    tree = format_box_tree([root])
    assert "trak  offset=0  size=40" in tree
    assert "  tkhd  offset=8  size=32  [24 bytes body]" in tree
