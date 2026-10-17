"""Inspection of MP4/MOV container structure, tracks, and DVM mask samples.

Phase M0/M1: inspect command.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from typing import Any

from .isobmff import (
    Box,
    TrackInfo,
    extract_mask_samples,
    extract_tracks,
    format_box_tree,
    parse_file,
)


def inspect_container(path: str | Path) -> dict[str, Any]:
    """Inspect an MP4/MOV file and return a dictionary of container metadata,

    tracks, and DVM mask samples.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    file_size = path.stat().st_size

    with open(path, "rb") as f:
        boxes = parse_file(f)
        tracks = extract_tracks(boxes)

        track_list: list[dict[str, Any]] = []
        mask_track_list: list[dict[str, Any]] = []

        for t in tracks:
            duration_sec = float(Fraction(t.duration, t.timescale)) if t.timescale > 0 else 0.0
            t_dict = {
                "track_id": t.track_id,
                "handler_type": t.handler_type,
                "handler_name": t.handler_name,
                "timescale": t.timescale,
                "duration_ticks": t.duration,
                "duration_sec": duration_sec,
                "sample_entry_type": t.sample_entry_type,
                "track_references": t.track_references,
            }
            track_list.append(t_dict)

            # Check if this track is an mebx mask track or has rndr reference
            is_mebx = t.sample_entry_type == "mebx" or t.handler_type == "meta"
            if is_mebx and t.stbl_box is not None:
                mask_samples = extract_mask_samples(t, f)
                samples_out = []
                for s in mask_samples:
                    pts_sec = float(Fraction(s.dts + s.cts_offset, s.timescale)) if s.timescale > 0 else 0.0
                    dur_sec = float(Fraction(s.duration, s.timescale)) if s.timescale > 0 else 0.0
                    val_dict = None
                    if s.value is not None:
                        val_dict = {
                            "reference_width": s.value.reference_width,
                            "reference_height": s.value.reference_height,
                            "left": s.value.left,
                            "width": s.value.width,
                            "top": s.value.top,
                            "height": s.value.height,
                        }
                    samples_out.append({
                        "sample_index": s.sample_index,
                        "dts": s.dts,
                        "duration": s.duration,
                        "pts_sec": pts_sec,
                        "duration_sec": dur_sec,
                        "value": val_dict,
                        "error": s.error,
                    })

                mask_track_list.append({
                    "track_id": t.track_id,
                    "rndr_target_track": t.track_references.get("rndr", []),
                    "sample_count": len(samples_out),
                    "samples": samples_out,
                })

        return {
            "path": str(path),
            "file_size": file_size,
            "top_level_boxes": [b.fourcc for b in boxes],
            "tracks": track_list,
            "mask_tracks": mask_track_list,
            "_boxes": boxes,  # kept for tree printing
        }


def format_inspection_text(info: dict[str, Any], show_tree: bool = False) -> str:
    """Format inspection dict into human-readable text."""
    lines = []
    lines.append(f"File: {info['path']} ({info['file_size']} bytes)")
    lines.append(f"Top-level boxes: {', '.join(info['top_level_boxes'])}")
    lines.append("")
    lines.append(f"Tracks ({len(info['tracks'])}):")

    for t in info["tracks"]:
        refs_str = ""
        if t["track_references"]:
            refs_str = " | refs: " + ", ".join(f"{k} -> {v}" for k, v in t["track_references"].items())
        lines.append(
            f"  Track {t['track_id']}: type='{t['handler_type']}' entry='{t['sample_entry_type']}' "
            f"timescale={t['timescale']} duration={t['duration_sec']:.3f}s ({t['duration_ticks']} ticks)"
            f"{refs_str}"
        )

    lines.append("")
    mask_tracks = info["mask_tracks"]
    if not mask_tracks:
        lines.append("DVM Mask Metadata: None found")
    else:
        lines.append(f"DVM Mask Metadata Tracks ({len(mask_tracks)}):")
        for mt in mask_tracks:
            rndr = mt["rndr_target_track"]
            rndr_str = f"points to video track {rndr}" if rndr else "WARNING: missing 'rndr' reference!"
            lines.append(f"  Track {mt['track_id']}: {mt['sample_count']} samples ({rndr_str})")
            for s in mt["samples"]:
                v = s["value"]
                if v:
                    rect_str = (
                        f"raster={v['reference_width']}x{v['reference_height']} "
                        f"rect=[x={v['left']}, y={v['top']}, w={v['width']}, h={v['height']}]"
                    )
                else:
                    rect_str = f"MALFORMED ({s['error']})"
                lines.append(
                    f"    [{s['sample_index']:4d}] pts={s['pts_sec']:.3f}s dur={s['duration_sec']:.3f}s: {rect_str}"
                )

    if show_tree and "_boxes" in info:
        lines.append("")
        lines.append("Box Tree:")
        lines.append(format_box_tree(info["_boxes"]))

    return "\n".join(lines)
