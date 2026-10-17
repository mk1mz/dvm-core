"""DVM authoring timeline: parsing, validation and exact evaluation.

Everything here is exact rational arithmetic (fractions.Fraction) with a single
round-half-up step per output value, so independent implementations can
reproduce results bit-for-bit. See docs/specification/dvm-profile-0.1-draft.md.

This is an application-level authoring format, NOT a file format.
"""
from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

MAX_DIM = 65535  # RasterRectangleValue fields are unsigned 16-bit
TRANSITIONS = ("cut", "smooth")
DEFAULT_TRANSITION_DURATION = Fraction(1, 2)  # seconds, capped at event duration

_TOP_KEYS = {"version", "description", "canvas", "events"}
_CANVAS_KEYS = {"width", "height"}
_EVENT_KEYS = {"start", "duration", "viewport", "transition", "transition_duration"}
_VIEWPORT_KEYS = {"x", "y", "width", "height"}


class ValidationError(ValueError):
    """Raised with every problem found, each prefixed by its JSON path."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass(frozen=True)
class Event:
    start: Fraction
    duration: Fraction
    viewport: Rect
    transition: str
    transition_duration: Fraction  # 0 for "cut"

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass(frozen=True)
class Timeline:
    canvas_width: int
    canvas_height: int
    events: tuple[Event, ...]

    @property
    def canvas_rect(self) -> Rect:
        return Rect(0, 0, self.canvas_width, self.canvas_height)


def round_half_up(value: Fraction) -> int:
    """Round to nearest integer, ties toward +infinity. Exact."""
    return math.floor(value + Fraction(1, 2))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


_TIME_STRING = re.compile(r"^([0-9]+(\.[0-9]+)?|[0-9]+/[0-9]+)$")


def _to_time(v: Any) -> Fraction | None:
    """JSON number, decimal string ("1.5") or rational string ("1/3") -> exact
    Fraction, or None if invalid. Floats are read via their shortest decimal
    repr so 0.1 means exactly 1/10."""
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return Fraction(repr(v))
    if not _TIME_STRING.match(v):
        return None
    try:
        return Fraction(v)
    except ZeroDivisionError:
        return None


def _unknown_keys(obj: dict, allowed: set[str], path: str, problems: list[str]) -> None:
    for k in sorted(set(obj) - allowed):
        problems.append(f"{path}: unknown key '{k}' (allowed: {', '.join(sorted(allowed))})")


def parse_timeline(doc: Any) -> Timeline:
    """Validate an authoring document and return a Timeline.

    Raises ValidationError listing *all* problems, not just the first.
    """
    problems: list[str] = []
    if not isinstance(doc, dict):
        raise ValidationError(["$: document must be a JSON object"])
    _unknown_keys(doc, _TOP_KEYS, "$", problems)

    if doc.get("version") != 1 or not _is_int(doc.get("version")):
        problems.append(f"$.version: unsupported or missing version {doc.get('version')!r}; this reader supports version 1 only")

    cw = ch = 0
    canvas = doc.get("canvas")
    if not isinstance(canvas, dict):
        problems.append("$.canvas: required object with integer width and height")
    else:
        _unknown_keys(canvas, _CANVAS_KEYS, "$.canvas", problems)
        for name in ("width", "height"):
            v = canvas.get(name)
            if not _is_int(v) or not (1 <= v <= MAX_DIM):
                problems.append(f"$.canvas.{name}: must be an integer in 1..{MAX_DIM}, got {v!r}")
        if _is_int(canvas.get("width")) and 1 <= canvas["width"] <= MAX_DIM:
            cw = canvas["width"]
        if _is_int(canvas.get("height")) and 1 <= canvas["height"] <= MAX_DIM:
            ch = canvas["height"]

    raw_events = doc.get("events")
    events: list[Event] = []
    if not isinstance(raw_events, list) or not raw_events:
        problems.append("$.events: required non-empty array")
        raw_events = []

    prev_end: Fraction | None = None
    for i, ev in enumerate(raw_events):
        p = f"$.events[{i}]"
        if not isinstance(ev, dict):
            problems.append(f"{p}: must be an object")
            prev_end = None
            continue
        _unknown_keys(ev, _EVENT_KEYS, p, problems)

        start = _to_time(ev.get("start"))
        dur = _to_time(ev.get("duration"))
        ok_time = True
        if start is None or start < 0:
            problems.append(f"{p}.start: must be a finite number >= 0, got {ev.get('start')!r}")
            ok_time = False
        if dur is None or dur <= 0:
            problems.append(f"{p}.duration: must be a finite number > 0, got {ev.get('duration')!r}")
            ok_time = False

        if ok_time:
            if prev_end is None and i == 0:
                if start != 0:
                    problems.append(f"{p}.start: first event must start at 0, got {ev['start']!r}")
            elif prev_end is not None:
                if start < prev_end:
                    problems.append(f"{p}.start: overlaps previous event, which ends at {float(prev_end)}")
                elif start > prev_end:
                    problems.append(f"{p}.start: gap after previous event, which ends at {float(prev_end)}; events must be contiguous")

        vp = ev.get("viewport")
        rect: Rect | None = None
        if not isinstance(vp, dict):
            problems.append(f"{p}.viewport: required object with integer x, y, width, height")
        else:
            _unknown_keys(vp, _VIEWPORT_KEYS, f"{p}.viewport", problems)
            vals = {}
            good = True
            for name in ("x", "y", "width", "height"):
                v = vp.get(name)
                if not _is_int(v):
                    problems.append(f"{p}.viewport.{name}: must be an integer, got {v!r}")
                    good = False
                else:
                    vals[name] = v
            if good:
                for name in ("x", "y"):
                    if vals[name] < 0:
                        problems.append(f"{p}.viewport.{name}: must be >= 0, got {vals[name]}")
                        good = False
                for name in ("width", "height"):
                    if vals[name] < 1:
                        problems.append(f"{p}.viewport.{name}: must be >= 1, got {vals[name]}")
                        good = False
            if good and cw and ch:
                if vals["x"] + vals["width"] > cw:
                    problems.append(f"{p}.viewport: x+width = {vals['x'] + vals['width']} exceeds canvas width {cw}")
                    good = False
                if vals["y"] + vals["height"] > ch:
                    problems.append(f"{p}.viewport: y+height = {vals['y'] + vals['height']} exceeds canvas height {ch}")
                    good = False
            if good:
                rect = Rect(vals["x"], vals["y"], vals["width"], vals["height"])

        trans = ev.get("transition", "cut")
        if trans not in TRANSITIONS:
            problems.append(f"{p}.transition: unsupported {trans!r}; supported: {', '.join(TRANSITIONS)}")
        td = Fraction(0)
        if trans == "cut":
            if "transition_duration" in ev:
                problems.append(f"{p}.transition_duration: not allowed with transition 'cut'")
        elif trans == "smooth":
            if i == 0:
                problems.append(f"{p}.transition: first event has nothing to transition from; use 'cut'")
            if "transition_duration" in ev:
                t = _to_time(ev["transition_duration"])
                if t is None or t <= 0:
                    problems.append(f"{p}.transition_duration: must be a finite number > 0, got {ev['transition_duration']!r}")
                elif dur is not None and t > dur:
                    problems.append(f"{p}.transition_duration: {ev['transition_duration']!r} exceeds event duration {ev.get('duration')!r}")
                else:
                    td = t
            elif dur is not None and dur > 0:
                td = min(DEFAULT_TRANSITION_DURATION, dur)

        if ok_time and rect is not None and trans in TRANSITIONS:
            events.append(Event(start, dur, rect, trans, td))
        prev_end = (start + dur) if ok_time else None

    if problems:
        raise ValidationError(problems)
    return Timeline(cw, ch, tuple(events))


def rect_at(tl: Timeline, t: Fraction) -> Rect:
    """Exact viewport at presentation time t (seconds).

    t < 0 clamps to 0. Times at/after the end of the last event hold the last
    viewport. Smooth transitions interpolate each *edge* linearly from the
    previous event's viewport over `transition_duration`, then round each edge
    once (half up). Output width/height are right-left / bottom-top.
    """
    t = Fraction(t)
    if t < 0:
        t = Fraction(0)
    starts = [e.start for e in tl.events]
    i = bisect.bisect_right(starts, t) - 1
    if i < 0:
        i = 0
    ev = tl.events[i]
    if ev.transition == "cut" or i == 0:
        return ev.viewport
    elapsed = t - ev.start
    if elapsed >= ev.transition_duration:
        return ev.viewport
    p = elapsed / ev.transition_duration
    a, b = tl.events[i - 1].viewport, ev.viewport

    def edge(va: int, vb: int) -> int:
        return round_half_up(va + (vb - va) * p)

    left, right = edge(a.x, b.x), edge(a.right, b.right)
    top, bottom = edge(a.y, b.y), edge(a.bottom, b.bottom)
    return Rect(left, top, right - left, bottom - top)
