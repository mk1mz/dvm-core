"""Synthetic MP4 generator for DVM testing.

Creates a minimal valid MP4 file with:
 - A video track (no actual video data — just sample table entries pointing
   to empty mdat, since we only need the container structure)
 - An mebx timed metadata track with rndr reference to the video track
 - RasterRectangleValue samples in the metadata track

This is NOT derived from Apple tooling. Every structural assumption about
the mebx sample entry and keys box layout is [A] and must be verified
against a real Apple-produced file before the muxer (M1) is built.

The generator follows the ISOBMFF (ISO 14496-12) and QTFF conventions
as understood from public documentation and the Apple v0.9 Beta spec text.
"""
from __future__ import annotations

import struct
from fractions import Fraction
from io import BytesIO

from .mask import RasterRectangleValue, Sample


# ---------------------------------------------------------------------------
# Low-level box-writing helpers
# ---------------------------------------------------------------------------

def _box(fourcc: str, body: bytes) -> bytes:
    """Build a simple box: 4-byte size + 4-byte fourcc + body."""
    size = 8 + len(body)
    return struct.pack(">I", size) + fourcc.encode("latin-1") + body


def _fullbox(fourcc: str, version: int, flags: int, body: bytes) -> bytes:
    """Build a full box (with version byte + 3-byte flags)."""
    vf = struct.pack(">I", (version << 24) | (flags & 0xFFFFFF))
    return _box(fourcc, vf + body)


def _container(fourcc: str, *children: bytes) -> bytes:
    """Build a container box from its children."""
    body = b"".join(children)
    return _box(fourcc, body)


# ---------------------------------------------------------------------------
# Track-level boxes
# ---------------------------------------------------------------------------

def _ftyp() -> bytes:
    """File type box: isom brand."""
    body = b"isom"             # major brand
    body += struct.pack(">I", 0x200)  # minor version
    body += b"isom" + b"iso2"  # compatible brands
    return _box("ftyp", body)


def _mvhd(timescale: int, duration: int, next_track_id: int) -> bytes:
    """Movie header box (version 0)."""
    body = b""
    body += struct.pack(">I", 0)         # creation_time
    body += struct.pack(">I", 0)         # modification_time
    body += struct.pack(">I", timescale)
    body += struct.pack(">I", duration)
    body += struct.pack(">I", 0x00010000)  # rate = 1.0
    body += struct.pack(">H", 0x0100)      # volume = 1.0
    body += b"\x00" * 10                   # reserved
    # 3x3 identity matrix (9 x int32)
    matrix = [0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000]
    for m in matrix:
        body += struct.pack(">I", m)
    body += b"\x00" * 24                   # pre_defined
    body += struct.pack(">I", next_track_id)
    return _fullbox("mvhd", 0, 0, body)


def _tkhd(track_id: int, duration: int, width: int = 0, height: int = 0,
           is_video: bool = False) -> bytes:
    """Track header box (version 0)."""
    flags = 0x000003  # track_enabled | track_in_movie
    body = b""
    body += struct.pack(">I", 0)         # creation_time
    body += struct.pack(">I", 0)         # modification_time
    body += struct.pack(">I", track_id)
    body += struct.pack(">I", 0)         # reserved
    body += struct.pack(">I", duration)
    body += b"\x00" * 8                  # reserved
    body += struct.pack(">H", 0)         # layer
    body += struct.pack(">H", 0)         # alternate_group
    body += struct.pack(">H", 0x0100 if not is_video else 0)  # volume
    body += struct.pack(">H", 0)         # reserved
    # identity matrix
    matrix = [0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000]
    for m in matrix:
        body += struct.pack(">I", m)
    body += struct.pack(">I", width << 16)   # width (fixed-point 16.16)
    body += struct.pack(">I", height << 16)  # height (fixed-point 16.16)
    return _fullbox("tkhd", 0, flags, body)


def _mdhd(timescale: int, duration: int) -> bytes:
    """Media header box (version 0)."""
    body = b""
    body += struct.pack(">I", 0)         # creation_time
    body += struct.pack(">I", 0)         # modification_time
    body += struct.pack(">I", timescale)
    body += struct.pack(">I", duration)
    body += struct.pack(">H", 0x55C4)   # language: undetermined
    body += struct.pack(">H", 0)         # pre_defined
    return _fullbox("mdhd", 0, 0, body)


def _hdlr(handler_type: str, name: str) -> bytes:
    """Handler reference box."""
    body = b""
    body += struct.pack(">I", 0)    # pre_defined
    body += handler_type.encode("latin-1")[:4].ljust(4, b"\x00")
    body += b"\x00" * 12           # reserved
    body += name.encode("utf-8") + b"\x00"
    return _fullbox("hdlr", 0, 0, body)


def _stts(entries: list[tuple[int, int]]) -> bytes:
    """Decoding time-to-sample box."""
    body = struct.pack(">I", len(entries))
    for count, delta in entries:
        body += struct.pack(">II", count, delta)
    return _fullbox("stts", 0, 0, body)


def _stsc(entries: list[tuple[int, int, int]]) -> bytes:
    """Sample-to-chunk box."""
    body = struct.pack(">I", len(entries))
    for first_chunk, spc, desc_idx in entries:
        body += struct.pack(">III", first_chunk, spc, desc_idx)
    return _fullbox("stsc", 0, 0, body)


def _stsz(sizes: list[int]) -> bytes:
    """Sample size box."""
    all_same = len(set(sizes)) <= 1
    if all_same and sizes:
        body = struct.pack(">II", sizes[0], len(sizes))
    else:
        body = struct.pack(">II", 0, len(sizes))
        for s in sizes:
            body += struct.pack(">I", s)
    return _fullbox("stsz", 0, 0, body)


def _stco(offsets: list[int]) -> bytes:
    """Chunk offset box (32-bit)."""
    body = struct.pack(">I", len(offsets))
    for o in offsets:
        body += struct.pack(">I", o)
    return _fullbox("stco", 0, 0, body)


def _dref() -> bytes:
    """Data reference box with a single self-reference entry."""
    url_entry = _fullbox("url ", 0, 1, b"")  # flag 1 = self-contained
    body = struct.pack(">I", 1) + url_entry
    return _fullbox("dref", 0, 0, body)


def _dinf() -> bytes:
    return _container("dinf", _dref())


def _nmhd() -> bytes:
    """Null media header (for metadata tracks)."""
    return _fullbox("nmhd", 0, 0, b"")


def _tref_rndr(video_track_id: int) -> bytes:
    """Track reference box with 'rndr' reference to the video track.

    [A] Apple's spec says the metadata track must have a 'rndr' track
    reference pointing to the video track.  The rndr box body is
    just the referenced track_id(s) as uint32 array.
    """
    rndr_body = struct.pack(">I", video_track_id)
    return _container("tref", _box("rndr", rndr_body))


# ---------------------------------------------------------------------------
# mebx sample entry and keys box  [A] — layout assumptions
# ---------------------------------------------------------------------------

# [A] The mebx sample entry is assumed to follow the QTFF timed metadata
# sample entry pattern:
#   - size(4) + 'mebx'(4) + reserved(6) + data_ref_index(2) = 16 bytes header
# This is the standard sample entry header for ISOBMFF.

# [A] The keys box in QTFF provides the key-value mapping for metadata items.
# Based on Apple's documentation pattern, a keys box would be structured as:
#   - Full box header (version=0, flags=0)
#   - entry_count (uint32)
#   - For each entry: key_size(uint32) + key_namespace(4) + key_value(variable)
#
# The key namespace for 'com.apple.quicktime.video.display-maskrect.mono'
# is 'mdta' (metadata atom).

DVM_MASK_KEY = "com.apple.quicktime.video.display-maskrect.mono"
MDTA_NAMESPACE = "mdta"


def _keys_box() -> bytes:
    """[A] Construct a keys box with the mask key.

    Layout assumed from QTFF documentation [A]:
    fullbox(version=0, flags=0) + entry_count(4) + entries
    Each entry: key_size(4) + key_namespace(4) + key_value(variable)
    key_size includes the 4 bytes of key_size itself + namespace + value.
    """
    key_bytes = DVM_MASK_KEY.encode("utf-8")
    key_size = 4 + 4 + len(key_bytes)  # key_size field + namespace + value
    entry = struct.pack(">I", key_size) + MDTA_NAMESPACE.encode("latin-1") + key_bytes
    body = struct.pack(">I", 1) + entry  # 1 key entry
    return _fullbox("keys", 0, 0, body)


def _mebx_sample_entry(data_ref_index: int = 1) -> bytes:
    """[A] Construct the mebx sample entry.

    Standard ISOBMFF sample entry header:
      size(4) + format(4) + reserved(6) + data_ref_index(2) = 16 bytes
    """
    body = b"\x00" * 6 + struct.pack(">H", data_ref_index)
    return _box("mebx", body)


def _stsd_meta() -> bytes:
    """Sample description box containing an mebx entry."""
    entry = _mebx_sample_entry()
    body = struct.pack(">I", 1) + entry  # 1 entry
    return _fullbox("stsd", 0, 0, body)


# ---------------------------------------------------------------------------
# Full synthetic MP4 generation
# ---------------------------------------------------------------------------

def generate_test_mp4(
    samples: list[Sample],
    canvas_width: int,
    canvas_height: int,
    video_timescale: int = 30000,
    meta_timescale: int = 600,
) -> bytes:
    """Generate a minimal MP4 file with a dummy video track and an mebx mask track.

    The video track has no actual encoded data (mdat contains only the mask
    payloads).  This is sufficient for testing the parser and inspect command.

    Returns the complete MP4 file as bytes.
    """
    # Compute metadata sample table
    payload_data = []
    sample_durations = []
    for s in samples:
        payload = s.value.pack()
        payload_data.append(payload)
        # Convert duration to timescale ticks
        dur_ticks = int(Fraction(s.duration) * meta_timescale)
        if dur_ticks < 1:
            dur_ticks = 1
        sample_durations.append(dur_ticks)

    total_meta_duration = sum(sample_durations)

    # Movie timescale and duration
    movie_timescale = meta_timescale
    movie_duration = total_meta_duration

    # Video track: single sample covering the whole duration
    video_track_id = 1
    meta_track_id = 2
    video_timescale_actual = movie_timescale
    video_duration = movie_duration

    # Build mdat: first a dummy 4-byte video sample, then all mask payloads
    dummy_video_sample = b"\x00\x00\x00\x01"  # dummy NAL unit
    mdat_body = dummy_video_sample
    mask_payload_offset_in_mdat = len(mdat_body)
    for p in payload_data:
        mdat_body += p

    # We need to know where mdat starts to compute sample offsets.
    # We'll build the moov first, then adjust.

    # --- Video track stbl ---
    video_stts = _stts([(1, video_duration)])
    video_stsc = _stsc([(1, 1, 1)])
    video_stsz = _stsz([len(dummy_video_sample)])
    avc1_body = b"\x00" * 6 + struct.pack(">H", 1)  # reserved + data_ref_index
    avc1_body += struct.pack(">H", 0)  # pre_defined
    avc1_body += struct.pack(">H", 0)  # reserved
    avc1_body += b"\x00" * 12          # pre_defined
    avc1_body += struct.pack(">HH", canvas_width, canvas_height)
    avc1_body += struct.pack(">I", 0x00480000)  # horiz resolution 72 dpi
    avc1_body += struct.pack(">I", 0x00480000)  # vert resolution 72 dpi
    avc1_body += struct.pack(">I", 0)           # reserved
    avc1_body += struct.pack(">H", 1)           # frame count
    avc1_body += b"\x00" * 32                   # compressor name (32 bytes)
    avc1_body += struct.pack(">H", 0x0018)      # depth = 24
    avc1_body += struct.pack(">h", -1)          # pre_defined
    video_stsd = _fullbox("stsd", 0, 0, struct.pack(">I", 1) + _box("avc1", avc1_body))
    # --- Metadata track stbl components ---
    stts_runs: list[tuple[int, int]] = []
    for d in sample_durations:
        if stts_runs and stts_runs[-1][1] == d:
            stts_runs[-1] = (stts_runs[-1][0] + 1, d)
        else:
            stts_runs.append((1, d))

    meta_stts = _stts(stts_runs)
    meta_stsc = _stsc([(1, len(payload_data), 1)])  # all samples in one chunk
    meta_stsz = _stsz([len(p) for p in payload_data])
    meta_stsd = _stsd_meta()

    # Function to build moov with specified chunk offsets
    def _build_moov(v_offset: int, m_offset: int) -> bytes:
        v_stco = _stco([v_offset])
        v_stbl = _container("stbl", video_stsd, video_stts, video_stsc, video_stsz, v_stco)
        v_minf = _container("minf", _fullbox("vmhd", 0, 1, b"\x00" * 8), _dinf(), v_stbl)
        v_mdia = _container("mdia",
                            _mdhd(video_timescale_actual, video_duration),
                            _hdlr("vide", "VideoHandler"),
                            v_minf)
        v_trak = _container("trak",
                            _tkhd(video_track_id, movie_duration, canvas_width, canvas_height, is_video=True),
                            v_mdia)

        m_stco = _stco([m_offset])
        m_stbl = _container("stbl", meta_stsd, meta_stts, meta_stsc, meta_stsz, m_stco)
        m_minf = _container("minf", _nmhd(), _dinf(), m_stbl)
        m_mdia = _container("mdia",
                            _mdhd(meta_timescale, total_meta_duration),
                            _hdlr("meta", "MetadataHandler"),
                            m_minf)
        m_trak = _container("trak",
                            _tkhd(meta_track_id, movie_duration),
                            m_mdia,
                            _tref_rndr(video_track_id))

        return _container("moov",
                          _mvhd(movie_timescale, movie_duration, meta_track_id + 1),
                          v_trak,
                          m_trak)

    ftyp = _ftyp()

    # Pass 1: compute exact moov length with placeholder offsets
    dummy_moov = _build_moov(0, 0)
    mdat_header_size = 8
    mdat_offset = len(ftyp) + len(dummy_moov)
    video_sample_offset = mdat_offset + mdat_header_size
    meta_samples_offset = video_sample_offset + len(dummy_video_sample)

    # Pass 2: build final moov with exact sample offsets
    moov = _build_moov(video_sample_offset, meta_samples_offset)
    assert len(moov) == len(dummy_moov), "moov size must not vary with offset values"

    mdat = _box("mdat", mdat_body)
    return ftyp + moov + mdat
