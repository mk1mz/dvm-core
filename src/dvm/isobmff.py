"""Read-only ISOBMFF / QTFF box parser for DVM inspection.

Phase M0 — evidence milestone.  Every byte layout assumption about mebx/keys
is labelled [A] until verified against a real Apple-produced reference file.
Layouts for standard ISOBMFF boxes (ftyp, moov, trak, tref, stbl, etc.) are
taken from ISO 14496-12:2020 text [V via public summaries and FFmpeg source].

This module intentionally has NO external dependencies beyond the stdlib.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from io import BytesIO
from typing import BinaryIO

from .mask import PAYLOAD_SIZE, RasterRectangleValue, MalformedPayload


# ---------------------------------------------------------------------------
# Low-level box reading
# ---------------------------------------------------------------------------

@dataclass
class Box:
    """One ISOBMFF box (or atom in QTFF terminology)."""
    fourcc: str          # e.g. "moov", "trak"
    offset: int          # absolute file offset where the box header starts
    header_size: int     # 8 or 16 (extended size)
    total_size: int      # header + body; 0 means "to end of file"
    children: list["Box"] = field(default_factory=list)
    body: bytes | None = None  # leaf payload (only stored for selected boxes)

    @property
    def body_offset(self) -> int:
        return self.offset + self.header_size

    @property
    def body_size(self) -> int:
        if self.total_size == 0:
            raise ValueError("box extends to EOF; body_size unknown without file length")
        return self.total_size - self.header_size

    def find(self, fourcc: str) -> "Box | None":
        """First child with the given fourcc, or None."""
        for c in self.children:
            if c.fourcc == fourcc:
                return c
        return None

    def find_all(self, fourcc: str) -> list["Box"]:
        return [c for c in self.children if c.fourcc == fourcc]

    def find_deep(self, *path: str) -> "Box | None":
        """Walk a path of fourccs through children. Returns None if any step fails."""
        node = self
        for cc in path:
            node = node.find(cc)
            if node is None:
                return None
        return node


def read_box_header(f: BinaryIO) -> tuple[str, int, int, int] | None:
    """Read one box header. Returns (fourcc, offset, header_size, total_size) or None at EOF.

    total_size == 0 means the box extends to the end of the enclosing container.
    """
    start = f.tell()
    data = f.read(8)
    if len(data) < 8:
        return None
    size = struct.unpack(">I", data[:4])[0]
    fourcc = data[4:8].decode("latin-1")
    header_size = 8
    if size == 1:
        # 64-bit extended size
        ext = f.read(8)
        if len(ext) < 8:
            return None
        size = struct.unpack(">Q", ext)[0]
        header_size = 16
    return fourcc, start, header_size, size


# Containers that should be recursed into.  ISOBMFF specifies these as
# container boxes; we also include 'meta' which has a 4-byte version/flags
# prefix before its children (handled below).
_CONTAINER_TYPES = frozenset([
    "moov", "trak", "mdia", "minf", "stbl", "udta", "edts",
    "dinf", "tref", "mvex", "moof", "traf", "mfra",
    "sinf", "schi", "rinf",
])

# 'meta' and 'stsd' are full boxes (have a 4-byte version+flags header)
# before their children, but we still want to recurse.
_FULLBOX_CONTAINERS = frozenset(["meta"])

# Boxes whose body we want to keep in memory for later parsing.
_LEAF_KEEP = frozenset([
    "ftyp", "mvhd", "tkhd", "mdhd", "hdlr", "stsd", "stts", "stsc",
    "stsz", "stco", "co64", "ctts", "elst", "keys", "rndr",
])


def parse_boxes(f: BinaryIO, end: int | None = None, depth: int = 0) -> list[Box]:
    """Recursively parse the box tree from `f` up to byte position `end`."""
    boxes = []
    while True:
        if end is not None and f.tell() >= end:
            break
        hdr = read_box_header(f)
        if hdr is None:
            break
        fourcc, offset, header_size, total_size = hdr
        if total_size == 0:
            # Box extends to end of container
            if end is not None:
                total_size = end - offset
            else:
                # last box in file — seek to real end to find size
                cur = f.tell()
                f.seek(0, 2)
                total_size = f.tell() - offset
                f.seek(cur)

        box_end = offset + total_size
        body_start = offset + header_size

        box = Box(fourcc=fourcc, offset=offset, header_size=header_size,
                  total_size=total_size)

        if fourcc in _CONTAINER_TYPES:
            f.seek(body_start)
            box.children = parse_boxes(f, box_end, depth + 1)
        elif fourcc in _FULLBOX_CONTAINERS:
            # Full-box: skip 4 bytes of version + flags, then parse children
            f.seek(body_start + 4)
            box.children = parse_boxes(f, box_end, depth + 1)
        elif fourcc == "stsd":
            # stsd is a full box with a 4-byte version+flags and 4-byte entry count
            # We store the raw body AND try to parse sample entries as children
            f.seek(body_start)
            box.body = f.read(box_end - body_start)
        elif fourcc in _LEAF_KEEP:
            f.seek(body_start)
            box.body = f.read(box_end - body_start)
        # else: skip

        f.seek(box_end)
        boxes.append(box)
    return boxes


def parse_file(f: BinaryIO) -> list[Box]:
    """Parse all top-level boxes in an MP4/MOV file."""
    return parse_boxes(f)


# ---------------------------------------------------------------------------
# Track-level extraction
# ---------------------------------------------------------------------------

@dataclass
class TrackInfo:
    """Parsed information about one track in the file."""
    track_id: int
    handler_type: str       # 'vide', 'soun', 'meta', etc.
    handler_name: str
    timescale: int
    duration: int           # in timescale units
    sample_entry_type: str  # e.g. 'avc1', 'hev1', 'mebx', 'mp4a'
    track_references: dict[str, list[int]]  # e.g. {'rndr': [1]}
    stbl_box: Box | None    # the stbl container, for sample extraction
    trak_box: Box           # the raw trak box


def _u16(data: bytes, off: int) -> int:
    return struct.unpack(">H", data[off:off+2])[0]

def _u32(data: bytes, off: int) -> int:
    return struct.unpack(">I", data[off:off+4])[0]

def _u64(data: bytes, off: int) -> int:
    return struct.unpack(">Q", data[off:off+8])[0]


def parse_tkhd(body: bytes) -> tuple[int, int]:
    """Parse tkhd body -> (track_id, duration). Handles v0 and v1."""
    version = body[0]
    if version == 0:
        track_id = _u32(body, 12)
        duration = _u32(body, 20)
    else:
        track_id = _u32(body, 20)
        duration = _u64(body, 28)
    return track_id, duration


def parse_mdhd(body: bytes) -> tuple[int, int]:
    """Parse mdhd body -> (timescale, duration). Handles v0 and v1."""
    version = body[0]
    if version == 0:
        timescale = _u32(body, 12)
        duration = _u32(body, 16)
    else:
        timescale = _u32(body, 20)
        duration = _u64(body, 24)
    return timescale, duration


def parse_hdlr(body: bytes) -> tuple[str, str]:
    """Parse hdlr body -> (handler_type, handler_name)."""
    # version(1) + flags(3) + pre_defined(4) + handler_type(4)
    handler_type = body[8:12].decode("latin-1")
    # name starts at offset 24 and is a null-terminated string (or Pascal in QTFF)
    name_data = body[24:]
    # Try null-terminated first
    if b"\x00" in name_data:
        name = name_data[:name_data.index(b"\x00")].decode("utf-8", errors="replace")
    else:
        name = name_data.decode("utf-8", errors="replace")
    return handler_type, name


def parse_tref(tref_box: Box) -> dict[str, list[int]]:
    """Parse track reference box children -> {ref_type: [track_ids]}."""
    refs: dict[str, list[int]] = {}
    for child in tref_box.children:
        if child.body is not None:
            ids = []
            for off in range(0, len(child.body), 4):
                if off + 4 <= len(child.body):
                    ids.append(_u32(child.body, off))
            refs[child.fourcc] = ids
        else:
            # tref children are leaf boxes with track IDs as body
            refs[child.fourcc] = []
    return refs


def parse_stsd_entry_type(stsd_body: bytes) -> str:
    """Extract the fourcc of the first sample entry from stsd body.

    stsd layout: version(1) + flags(3) + entry_count(4) + entries...
    Each entry: size(4) + fourcc(4) + ...
    """
    if len(stsd_body) < 16:
        return "????"
    # entry_count = _u32(stsd_body, 4)
    fourcc = stsd_body[12:16].decode("latin-1")
    return fourcc


def extract_tracks(top_boxes: list[Box]) -> list[TrackInfo]:
    """Extract TrackInfo for every trak in the moov box."""
    moov = None
    for b in top_boxes:
        if b.fourcc == "moov":
            moov = b
            break
    if moov is None:
        return []

    tracks = []
    for trak in moov.find_all("trak"):
        tkhd = trak.find_deep("tkhd")
        mdia = trak.find("mdia")
        if tkhd is None or tkhd.body is None or mdia is None:
            continue

        track_id, tk_duration = parse_tkhd(tkhd.body)

        mdhd = mdia.find("mdhd")
        hdlr = mdia.find("hdlr")
        timescale, md_duration = (0, 0)
        handler_type, handler_name = "????", ""
        if mdhd is not None and mdhd.body is not None:
            timescale, md_duration = parse_mdhd(mdhd.body)
        if hdlr is not None and hdlr.body is not None:
            handler_type, handler_name = parse_hdlr(hdlr.body)

        stbl = mdia.find_deep("minf", "stbl")
        stsd_box = stbl.find("stsd") if stbl else None
        entry_type = "????"
        if stsd_box is not None and stsd_box.body is not None:
            entry_type = parse_stsd_entry_type(stsd_box.body)

        # Track references
        tref = trak.find("tref")
        track_refs: dict[str, list[int]] = {}
        if tref is not None:
            track_refs = parse_tref(tref)

        tracks.append(TrackInfo(
            track_id=track_id,
            handler_type=handler_type,
            handler_name=handler_name,
            timescale=timescale,
            duration=md_duration,
            sample_entry_type=entry_type,
            track_references=track_refs,
            stbl_box=stbl,
            trak_box=trak,
        ))
    return tracks


# ---------------------------------------------------------------------------
# Sample table extraction (stts, stsc, stsz, stco/co64)
# ---------------------------------------------------------------------------

@dataclass
class SampleTableEntry:
    """One sample's timing and location in the file."""
    index: int
    offset: int       # byte offset in file
    size: int         # byte size
    dts: int          # decode timestamp in timescale ticks
    duration: int     # in timescale ticks
    cts_offset: int   # composition offset (PTS = DTS + cts_offset)


def parse_stts(body: bytes) -> list[tuple[int, int]]:
    """Parse stts (decoding time-to-sample) -> [(count, delta), ...]."""
    version = body[0]
    entry_count = _u32(body, 4)
    entries = []
    off = 8
    for _ in range(entry_count):
        count = _u32(body, off)
        delta = _u32(body, off + 4)
        entries.append((count, delta))
        off += 8
    return entries


def parse_ctts(body: bytes) -> list[tuple[int, int]]:
    """Parse ctts (composition offset) -> [(count, offset), ...]."""
    version = body[0]
    entry_count = _u32(body, 4)
    entries = []
    off = 8
    for _ in range(entry_count):
        count = _u32(body, off)
        if version == 0:
            ct_offset = _u32(body, off + 4)
        else:
            ct_offset = struct.unpack(">i", body[off+4:off+8])[0]
        entries.append((count, ct_offset))
        off += 8
    return entries


def parse_stsz(body: bytes) -> list[int]:
    """Parse stsz (sample size) -> [size_per_sample].

    If default_sample_size != 0, all samples have that size.
    """
    # version = body[0]
    default_size = _u32(body, 4)
    sample_count = _u32(body, 8)
    if default_size != 0:
        return [default_size] * sample_count
    sizes = []
    off = 12
    for _ in range(sample_count):
        sizes.append(_u32(body, off))
        off += 4
    return sizes


def parse_stsc(body: bytes) -> list[tuple[int, int, int]]:
    """Parse stsc (sample-to-chunk) -> [(first_chunk_1based, samples_per_chunk, desc_idx), ...]."""
    # version = body[0]
    entry_count = _u32(body, 4)
    entries = []
    off = 8
    for _ in range(entry_count):
        first_chunk = _u32(body, off)
        spc = _u32(body, off + 4)
        desc_idx = _u32(body, off + 8)
        entries.append((first_chunk, spc, desc_idx))
        off += 12
    return entries


def parse_chunk_offsets(stbl: Box) -> list[int]:
    """Parse stco or co64 -> [chunk_offset, ...]."""
    stco = stbl.find("stco")
    if stco is not None and stco.body is not None:
        count = _u32(stco.body, 4)
        return [_u32(stco.body, 8 + i * 4) for i in range(count)]
    co64 = stbl.find("co64")
    if co64 is not None and co64.body is not None:
        count = _u32(co64.body, 4)
        return [_u64(co64.body, 8 + i * 8) for i in range(count)]
    return []


def _expand_stsc(stsc_entries: list[tuple[int, int, int]], num_chunks: int) -> list[int]:
    """Expand stsc entries -> [samples_per_chunk] for every chunk."""
    result = [0] * num_chunks
    for i, (first_chunk, spc, _desc) in enumerate(stsc_entries):
        if i + 1 < len(stsc_entries):
            end_chunk = stsc_entries[i + 1][0]
        else:
            end_chunk = num_chunks + 1
        for c in range(first_chunk, end_chunk):
            if 1 <= c <= num_chunks:
                result[c - 1] = spc
    return result


def extract_sample_table(stbl: Box, f: BinaryIO) -> list[SampleTableEntry]:
    """Read all samples from a track's stbl, returning timing and data."""
    stts_box = stbl.find("stts")
    stsz_box = stbl.find("stsz")
    stsc_box = stbl.find("stsc")

    if any(b is None or b.body is None for b in [stts_box, stsz_box, stsc_box]):
        return []

    stts_entries = parse_stts(stts_box.body)
    sizes = parse_stsz(stsz_box.body)
    stsc_entries = parse_stsc(stsc_box.body)
    chunk_offsets = parse_chunk_offsets(stbl)

    ctts_box = stbl.find("ctts")
    ctts_entries = parse_ctts(ctts_box.body) if (ctts_box and ctts_box.body) else []

    num_samples = len(sizes)
    if num_samples == 0:
        return []

    # Expand stts -> per-sample DTS and duration
    dts_list = []
    dur_list = []
    dts = 0
    for count, delta in stts_entries:
        for _ in range(count):
            dts_list.append(dts)
            dur_list.append(delta)
            dts += delta

    # Expand ctts -> per-sample CTS offset
    cts_list = [0] * num_samples
    if ctts_entries:
        idx = 0
        for count, ct_off in ctts_entries:
            for _ in range(count):
                if idx < num_samples:
                    cts_list[idx] = ct_off
                    idx += 1

    # Expand stsc + chunk offsets -> per-sample file offset
    offsets = [0] * num_samples
    if chunk_offsets:
        spc_per_chunk = _expand_stsc(stsc_entries, len(chunk_offsets))
        sample_idx = 0
        for chunk_idx, chunk_off in enumerate(chunk_offsets):
            n = spc_per_chunk[chunk_idx]
            pos = chunk_off
            for _ in range(n):
                if sample_idx < num_samples:
                    offsets[sample_idx] = pos
                    pos += sizes[sample_idx]
                    sample_idx += 1

    samples = []
    for i in range(min(num_samples, len(dts_list))):
        samples.append(SampleTableEntry(
            index=i,
            offset=offsets[i] if i < len(offsets) else 0,
            size=sizes[i],
            dts=dts_list[i],
            duration=dur_list[i],
            cts_offset=cts_list[i] if i < len(cts_list) else 0,
        ))
    return samples


def read_sample_data(f: BinaryIO, entry: SampleTableEntry) -> bytes:
    """Read the raw bytes for one sample from the file."""
    f.seek(entry.offset)
    return f.read(entry.size)


# ---------------------------------------------------------------------------
# DVM-specific: extract mask samples from mebx tracks
# ---------------------------------------------------------------------------

@dataclass
class MaskSampleInfo:
    """One decoded mask sample from the file."""
    sample_index: int
    dts: int
    duration: int
    cts_offset: int
    timescale: int
    value: RasterRectangleValue | None  # None if malformed
    raw_bytes: bytes
    error: str | None = None  # set if parsing failed


def extract_mask_samples(track: TrackInfo, f: BinaryIO) -> list[MaskSampleInfo]:
    """Read and decode all RasterRectangleValue samples from an mebx track.

    [A] The assumption is that each sample's payload is exactly the 12-byte
    RasterRectangleValue.  Apple's spec says the sample carries an item with
    key com.apple.quicktime.video.display-maskrect.mono; how the key is
    associated with the payload inside each sample is [A] not yet verified.

    In QTFF, the mebx sample entry + keys box provides the key mapping.
    In ISOBMFF, readers must know the key's type (spec §3, item 5).

    For now we attempt to parse the last 12 bytes of each sample as
    RasterRectangleValue, since there may be a key-index prefix [A].
    We also try the full payload if it's exactly 12 bytes.
    """
    if track.stbl_box is None:
        return []

    table = extract_sample_table(track.stbl_box, f)
    results = []

    for entry in table:
        raw = read_sample_data(f, entry)
        value = None
        error = None

        if len(raw) == PAYLOAD_SIZE:
            # Exactly 12 bytes — likely the bare payload
            try:
                value = RasterRectangleValue.unpack(raw)
            except MalformedPayload as e:
                error = str(e)
        elif len(raw) > PAYLOAD_SIZE:
            # May have a key-index or other prefix [A]; try last 12 bytes
            try:
                value = RasterRectangleValue.unpack(raw[-PAYLOAD_SIZE:])
            except MalformedPayload as e:
                error = f"tried last {PAYLOAD_SIZE} bytes: {e}"
            if value is None:
                error = f"payload is {len(raw)} bytes; expected {PAYLOAD_SIZE} or a known wrapper"
        else:
            error = f"payload too short: {len(raw)} bytes, need {PAYLOAD_SIZE}"

        results.append(MaskSampleInfo(
            sample_index=entry.index,
            dts=entry.dts,
            duration=entry.duration,
            cts_offset=entry.cts_offset,
            timescale=track.timescale,
            value=value,
            raw_bytes=raw,
            error=error,
        ))
    return results


# ---------------------------------------------------------------------------
# Convenience: box-tree pretty print
# ---------------------------------------------------------------------------

def format_box_tree(boxes: list[Box], indent: int = 0) -> str:
    """Return a human-readable tree of the box structure."""
    lines = []
    for b in boxes:
        prefix = "  " * indent
        size_str = f"{b.total_size}" if b.total_size else "→EOF"
        extra = ""
        if b.body is not None:
            extra = f"  [{len(b.body)} bytes body]"
        lines.append(f"{prefix}{b.fourcc}  offset={b.offset}  size={size_str}{extra}")
        if b.children:
            lines.append(format_box_tree(b.children, indent + 1))
    return "\n".join(lines)
