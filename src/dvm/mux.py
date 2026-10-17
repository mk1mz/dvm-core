"""Stream-copy muxer for Dynamic Viewport Media (DVM).

Milestone M1: Muxer.
Injects a compiled 'mebx' timed metadata track with 'rndr' reference
into an existing MP4/MOV container without re-encoding media streams.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO

from .isobmff import (
    Box,
    TrackInfo,
    extract_mask_samples,
    extract_sample_table,
    extract_tracks,
    parse_file,
    parse_tkhd,
)
from .mask import (
    RasterRectangleValue,
    Sample,
    cfr_pts,
    compile_timeline,
)
from .synth import (
    _box,
    _container,
    _dinf,
    _fullbox,
    _hdlr,
    _mdhd,
    _nmhd,
    _stco,
    _stsc,
    _stsd_meta,
    _stsz,
    _stts,
    _tkhd,
    _tref_rndr,
)
from .timeline import Timeline, parse_timeline


@dataclass
class MuxResult:
    input_path: str
    output_path: str
    re_encoded: bool
    video_track_id: int
    meta_track_id: int
    samples_count: int
    video_stream_hash: str
    output_file_hash: str

    def summary(self) -> str:
        lines = [
            f"input: {self.input_path}",
            f"output: {self.output_path}",
            f"re-encoded: {'yes' if self.re_encoded else 'no'}",
            f"video track id: {self.video_track_id}",
            f"metadata track id: {self.meta_track_id} (rndr -> [{self.video_track_id}])",
            f"metadata samples: {self.samples_count}",
            f"video stream SHA-256: {self.video_stream_hash}",
            f"output file SHA-256: {self.output_file_hash}",
        ]
        return "\n".join(lines)


def _compute_track_media_hash(track: TrackInfo, f: BinaryIO) -> str:
    """Compute SHA-256 hash over all sample bytes of a track in sequence."""
    if track.stbl_box is None:
        return ""
    entries = extract_sample_table(track.stbl_box, f)
    h = hashlib.sha256()
    for entry in entries:
        f.seek(entry.offset)
        h.update(f.read(entry.size))
    return h.hexdigest()


def _build_meta_trak(
    meta_track_id: int,
    video_track_id: int,
    duration_ticks: int,
    timescale: int,
    samples: list[Sample],
    chunk_offset: int,
    edts_box_body: bytes | None = None,
) -> bytes:
    """Build the mebx trak box bytes with rndr reference."""
    payload_data = [s.value.pack() for s in samples]
    durations_ticks = [max(1, int(Fraction(s.duration) * timescale)) for s in samples]

    # Run-length encode stts
    stts_runs: list[tuple[int, int]] = []
    for d in durations_ticks:
        if stts_runs and stts_runs[-1][1] == d:
            stts_runs[-1] = (stts_runs[-1][0] + 1, d)
        else:
            stts_runs.append((1, d))

    stts_bytes = _stts(stts_runs)
    stsc_bytes = _stsc([(1, len(payload_data), 1)])
    stsz_bytes = _stsz([len(p) for p in payload_data])
    stco_bytes = _stco([chunk_offset])
    stsd_bytes = _stsd_meta()

    stbl_children = [stsd_bytes, stts_bytes, stsc_bytes, stsz_bytes, stco_bytes]
    stbl = _container("stbl", *stbl_children)
    minf = _container("minf", _nmhd(), _dinf(), stbl)
    mdia = _container("mdia", _mdhd(timescale, duration_ticks), _hdlr("meta", "MetadataHandler"), minf)

    trak_children = [_tkhd(meta_track_id, duration_ticks), mdia, _tref_rndr(video_track_id)]
    if edts_box_body is not None:
        trak_children.insert(1, _box("edts", edts_box_body))

    return _container("trak", *trak_children)


def mux(
    input_path: str | Path,
    timeline_path_or_doc: str | Path | dict,
    output_path: str | Path,
    fps: Fraction | int | float | None = None,
    overwrite: bool = False,
) -> MuxResult:
    """Mux a DVM timeline into an existing MP4 file without re-encoding."""
    in_path = Path(input_path).resolve()
    out_path = Path(output_path).resolve()

    if not in_path.is_file():
        raise FileNotFoundError(f"Input file not found: {in_path}")
    if in_path == out_path:
        raise ValueError(f"Refusing to overwrite input file: {in_path}")
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists: {out_path} (use overwrite=True to force)")

    # 1. Load and validate timeline
    if isinstance(timeline_path_or_doc, (str, Path)):
        with open(timeline_path_or_doc, "r", encoding="utf-8") as f:
            tl_doc = json.load(f)
    else:
        tl_doc = timeline_path_or_doc
    tl = parse_timeline(tl_doc)

    # 2. Parse input MP4
    with open(in_path, "rb") as f_in:
        in_bytes = f_in.read()

    f_in_mem = open(in_path, "rb")
    try:
        boxes = parse_file(f_in_mem)
        tracks = extract_tracks(boxes)

        moov_box = None
        mdat_box = None
        for b in boxes:
            if b.fourcc == "moov":
                moov_box = b
            elif b.fourcc == "mdat":
                mdat_box = b

        if moov_box is None:
            raise ValueError(f"{in_path} missing 'moov' box")
        if mdat_box is None:
            raise ValueError(f"{in_path} missing 'mdat' box")

        # Find primary video track
        video_track = None
        for t in tracks:
            if t.handler_type == "vide":
                video_track = t
                break
        if video_track is None:
            raise ValueError(f"{in_path} has no video track ('vide')")

        # Check existing track IDs
        existing_track_ids = [t.track_id for t in tracks]
        meta_track_id = max(existing_track_ids) + 1 if existing_track_ids else 1

        # Calculate media duration in seconds
        video_dur_sec = Fraction(video_track.duration, video_track.timescale) if video_track.timescale > 0 else Fraction(0)

        # Compute video stream hash before muxing
        video_hash_before = _compute_track_media_hash(video_track, f_in_mem)

        # Extract edit list from video track if present
        edts_box_body = None
        v_edts = video_track.trak_box.find("edts")
        if v_edts is not None and v_edts.body is not None:
            edts_box_body = v_edts.body
    finally:
        f_in_mem.close()

    # 3. Determine compilation frame rate & compile timeline
    if fps is None:
        # Determine fps from video track stts if constant
        stts_box = video_track.stbl_box.find("stts") if video_track.stbl_box else None
        if stts_box and stts_box.body and len(stts_box.body) >= 16:
            entry_count = struct.unpack(">I", stts_box.body[4:8])[0]
            if entry_count == 1:
                sample_count = struct.unpack(">I", stts_box.body[8:12])[0]
                sample_delta = struct.unpack(">I", stts_box.body[12:16])[0]
                if sample_delta > 0:
                    fps = Fraction(video_track.timescale, sample_delta)

        if fps is None:
            fps = Fraction(30, 1)  # Default fallback 30 fps
    else:
        fps = Fraction(fps)

    total_frames = max(1, int(video_dur_sec * fps))
    pts_list = cfr_pts(fps, total_frames)
    compiled_samples = compile_timeline(tl, pts_list, Fraction(total_frames, fps))

    # Convert compiled samples to raw payload bytes
    meta_payload_bytes = b"".join(s.value.pack() for s in compiled_samples)
    meta_timescale = 600
    meta_duration_ticks = int(video_dur_sec * meta_timescale)

    # 4. Construct new container layout
    # We maintain standard fast-start layout: ftyp -> moov -> mdat
    # Existing media data inside mdat is kept untouched.
    old_mdat_body = in_bytes[mdat_box.offset + mdat_box.header_size : mdat_box.offset + mdat_box.total_size]
    new_mdat_body = old_mdat_body + meta_payload_bytes

    # Read ftyp box
    ftyp_box = next((b for b in boxes if b.fourcc == "ftyp"), None)
    if ftyp_box:
        ftyp_bytes = in_bytes[ftyp_box.offset : ftyp_box.offset + ftyp_box.total_size]
    else:
        # Generate standard ftyp if missing
        ftyp_bytes = _box("ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2")

    # We build the updated moov with patched chunk offsets.
    # We test with placeholder chunk offset to measure exact moov size, then patch.
    def _assemble_moov(stco_shift: int, meta_chunk_offset: int) -> bytes:
        # Copy raw moov box body, update mvhd.next_track_id, shift stco/co64, append meta_trak
        raw_moov = bytearray(in_bytes[moov_box.offset + moov_box.header_size : moov_box.offset + moov_box.total_size])

        # Patch mvhd next_track_id if needed
        mvhd_sub = moov_box.find("mvhd")
        if mvhd_sub and mvhd_sub.body:
            rel_mvhd_body = mvhd_sub.offset + mvhd_sub.header_size - (moov_box.offset + moov_box.header_size)
            version = raw_moov[rel_mvhd_body]
            # next_track_id is the last 4 bytes of mvhd body
            next_tid_off = rel_mvhd_body + len(mvhd_sub.body) - 4
            cur_next_id = struct.unpack(">I", raw_moov[next_tid_off : next_tid_off + 4])[0]
            if meta_track_id >= cur_next_id:
                struct.pack_into(">I", raw_moov, next_tid_off, meta_track_id + 1)

        # Shift existing stco and co64 offsets by stco_shift
        for t in tracks:
            if t.stbl_box is None:
                continue
            stco = t.stbl_box.find("stco")
            if stco and stco.body:
                rel_stco = stco.offset + stco.header_size - (moov_box.offset + moov_box.header_size)
                count = struct.unpack(">I", raw_moov[rel_stco + 4 : rel_stco + 8])[0]
                pos = rel_stco + 8
                for _ in range(count):
                    old_off = struct.unpack(">I", raw_moov[pos : pos + 4])[0]
                    struct.pack_into(">I", raw_moov, pos, old_off + stco_shift)
                    pos += 4

            co64 = t.stbl_box.find("co64")
            if co64 and co64.body:
                rel_co64 = co64.offset + co64.header_size - (moov_box.offset + moov_box.header_size)
                count = struct.unpack(">I", raw_moov[rel_co64 + 4 : rel_co64 + 8])[0]
                pos = rel_co64 + 8
                for _ in range(count):
                    old_off = struct.unpack(">Q", raw_moov[pos : pos + 8])[0]
                    struct.pack_into(">Q", raw_moov, pos, old_off + stco_shift)
                    pos += 8

        # Build meta trak
        meta_trak = _build_meta_trak(
            meta_track_id=meta_track_id,
            video_track_id=video_track.track_id,
            duration_ticks=meta_duration_ticks,
            timescale=meta_timescale,
            samples=compiled_samples,
            chunk_offset=meta_chunk_offset,
            edts_box_body=edts_box_body,
        )

        return _box("moov", bytes(raw_moov) + meta_trak)

    # Pass 1: compute dummy moov to get exact byte length
    dummy_moov = _assemble_moov(0, 0)
    mdat_header_len = 8
    new_mdat_offset = len(ftyp_bytes) + len(dummy_moov)
    meta_sample_offset = new_mdat_offset + mdat_header_len + len(old_mdat_body)

    # Shift for existing media chunks:
    # In original file, mdat started at mdat_box.offset.
    # In new file, mdat starts at new_mdat_offset.
    # So chunk shift is new_mdat_offset - mdat_box.offset.
    shift = new_mdat_offset - mdat_box.offset

    # Pass 2: build final moov with exact shifts and metadata sample offset
    final_moov = _assemble_moov(shift, meta_sample_offset)
    assert len(final_moov) == len(dummy_moov), "moov size must not vary with offset values"

    # Assemble mdat
    final_mdat = _box("mdat", new_mdat_body)
    out_bytes = ftyp_bytes + final_moov + final_mdat

    # Write output file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f_out:
        f_out.write(out_bytes)

    # 5. Validation of output file
    with open(out_path, "rb") as f_verify:
        v_boxes = parse_file(f_verify)
        v_tracks = extract_tracks(v_boxes)
        v_video_track = next((t for t in v_tracks if t.track_id == video_track.track_id), None)
        assert v_video_track is not None, "video track missing from output"

        # Verify video stream media hash is bit-identical!
        video_hash_after = _compute_track_media_hash(v_video_track, f_verify)
        if video_hash_after != video_hash_before:
            raise RuntimeError(
                f"Media stream hash mismatch! Stream copy failed: {video_hash_before} != {video_hash_after}"
            )

        # Verify metadata track and samples
        v_meta_track = next((t for t in v_tracks if t.track_id == meta_track_id), None)
        assert v_meta_track is not None, "metadata track missing from output"
        assert v_meta_track.track_references.get("rndr") == [video_track.track_id]

        v_masks = extract_mask_samples(v_meta_track, f_verify)
        assert len(v_masks) == len(compiled_samples), "sample count mismatch in output"
        for i, s in enumerate(compiled_samples):
            assert v_masks[i].value == s.value, f"sample {i} value mismatch"

    output_file_hash = hashlib.sha256(out_bytes).hexdigest()

    return MuxResult(
        input_path=str(in_path),
        output_path=str(out_path),
        re_encoded=False,
        video_track_id=video_track.track_id,
        meta_track_id=meta_track_id,
        samples_count=len(compiled_samples),
        video_stream_hash=video_hash_after,
        output_file_hash=output_file_hash,
    )
