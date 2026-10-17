"""RasterRectangleValue payload, compilation to per-frame samples, and reading.

Payload layout follows Apple's "Rectangular Mask Payload Metadata within the
QuickTime Movie File Format", v0.9 (Beta), 2025-06-09: six big-endian uint16
fields (reference_raster_width, reference_raster_height, rectangle_left,
rectangle_width, rectangle_top, rectangle_height).

This module does NOT write MP4/MOV boxes; container carriage is Phase 2 and
must be verified against a reference file first.
"""
from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass
from fractions import Fraction
from typing import Sequence

from .timeline import MAX_DIM, Rect, Timeline, rect_at

PAYLOAD_FORMAT = ">6H"
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FORMAT)  # 12


class MalformedPayload(ValueError):
    pass


@dataclass(frozen=True)
class RasterRectangleValue:
    reference_width: int
    reference_height: int
    left: int
    width: int
    top: int
    height: int

    def pack(self) -> bytes:
        vals = (self.reference_width, self.reference_height, self.left, self.width, self.top, self.height)
        for v in vals:
            if not 0 <= v <= MAX_DIM:
                raise MalformedPayload(f"field value {v} does not fit in uint16")
        return struct.pack(PAYLOAD_FORMAT, *vals)

    @staticmethod
    def unpack(data: bytes) -> "RasterRectangleValue":
        if len(data) != PAYLOAD_SIZE:
            raise MalformedPayload(f"payload must be {PAYLOAD_SIZE} bytes, got {len(data)}")
        return RasterRectangleValue(*struct.unpack(PAYLOAD_FORMAT, data))


@dataclass(frozen=True)
class Sample:
    start: Fraction
    duration: Fraction
    value: RasterRectangleValue

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


def _rhu_div(n: int, d: int) -> int:
    """round-half-up(n / d) for integers, d > 0, n >= 0."""
    return (2 * n + d) // (2 * d)


def scale_to_buffer(v: RasterRectangleValue, buf_w: int, buf_h: int) -> Rect:
    """Map a payload rectangle onto an actual decoded buffer (DVM rule).

    Edges are scaled independently with round-half-up integer math, so
    adjacent rectangles never gap or overlap through rounding, then clipped to
    the buffer. A zero-sized result is a valid empty rectangle.
    Raises MalformedPayload if a reference dimension is zero.
    """
    if v.reference_width == 0 or v.reference_height == 0:
        raise MalformedPayload("reference raster has a zero dimension")
    left = _rhu_div(v.left * buf_w, v.reference_width)
    right = _rhu_div((v.left + v.width) * buf_w, v.reference_width)
    top = _rhu_div(v.top * buf_h, v.reference_height)
    bottom = _rhu_div((v.top + v.height) * buf_h, v.reference_height)
    left, right = min(left, buf_w), min(right, buf_w)
    top, bottom = min(top, buf_h), min(bottom, buf_h)
    return Rect(left, top, right - left, bottom - top)


def cfr_pts(fps: Fraction, frame_count: int) -> list[Fraction]:
    fps = Fraction(fps)
    return [Fraction(i) / fps for i in range(frame_count)]


def compile_timeline(tl: Timeline, pts: Sequence[Fraction], end: Fraction) -> list[Sample]:
    """Evaluate the timeline at each frame's PTS and merge equal runs.

    pts must be strictly increasing; `end` is the end of the last frame.
    Works for variable frame rate because only PTS values matter.
    """
    if not pts:
        return []
    pts = [Fraction(p) for p in pts]
    end = Fraction(end)
    for a, b in zip(pts, pts[1:]):
        if b <= a:
            raise ValueError("pts must be strictly increasing")
    if end <= pts[-1]:
        raise ValueError("end must be after the last frame's pts")
    ref_w, ref_h = tl.canvas_width, tl.canvas_height
    runs: list[Sample] = []
    for i, p in enumerate(pts):
        nxt = pts[i + 1] if i + 1 < len(pts) else end
        r = rect_at(tl, p)
        val = RasterRectangleValue(ref_w, ref_h, r.x, r.width, r.y, r.height)
        if runs and runs[-1].value == val and runs[-1].end == p:
            last = runs[-1]
            runs[-1] = Sample(last.start, nxt - last.start, val)
        else:
            runs.append(Sample(p, nxt - p, val))
    return runs


def to_ticks(t: Fraction, timescale: int) -> int:
    x = Fraction(t) * timescale
    if x.denominator != 1:
        raise ValueError(f"time {t} is not an integer number of 1/{timescale}s ticks")
    return int(x)


def check_samples(samples: Sequence[Sample]) -> list[str]:
    """Return problems that make a sample list non-conforming for one track."""
    problems = []
    for i, s in enumerate(samples):
        if s.duration <= 0:
            problems.append(f"sample {i}: non-positive duration")
        if s.value.reference_width == 0 or s.value.reference_height == 0:
            problems.append(f"sample {i}: zero reference raster")
        if i and s.start < samples[i - 1].end:
            problems.append(f"sample {i}: overlaps or precedes sample {i - 1}")
    return problems


class MaskReader:
    """Reference evaluation of a mask sample list (one metadata track).

    Statuses returned with each lookup:
      "mask"               a valid sample covers t
      "default-no-sample"  no sample covers t -> full frame (Apple rule)
      "default-malformed"  covering sample unusable -> full frame [DVM rule]
    """

    def __init__(self, samples: Sequence[Sample]):
        problems = check_samples(samples)
        if any("overlaps" in p for p in problems):
            raise ValueError("; ".join(problems))
        self.samples = list(samples)
        self._starts = [s.start for s in self.samples]

    def rect_at(self, t: Fraction, buf_w: int, buf_h: int) -> tuple[Rect, str]:
        full = Rect(0, 0, buf_w, buf_h)
        t = Fraction(t)
        i = bisect.bisect_right(self._starts, t) - 1
        if i < 0 or t >= self.samples[i].end:
            return full, "default-no-sample"
        try:
            return scale_to_buffer(self.samples[i].value, buf_w, buf_h), "mask"
        except MalformedPayload:
            return full, "default-malformed"
