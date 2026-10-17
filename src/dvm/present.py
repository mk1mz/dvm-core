"""Output presentation: map a viewport onto a player window ("viewport-fit").

Two sub-modes [P]:
  contain  scale the whole viewport uniformly to fit inside the window; any
           mismatch leaves window space the *player* fills (this is the empty
           space a black-bar-free file can still produce on a mismatched screen).
  cover    scale uniformly to fill the window, cropping the viewport's excess
           (centered). No empty space, but the author's viewport is cropped.
All arithmetic is integer, round-half-up; centering uses floor.
"""
from __future__ import annotations

from dataclasses import dataclass

from .timeline import Rect


@dataclass(frozen=True)
class Placement:
    src: Rect  # region of the decoded buffer to draw (buffer pixels)
    dst: Rect  # where to draw it in the window (window pixels)


def _rhu(n: int, d: int) -> int:
    return (2 * n + d) // (2 * d)


def viewport_fit(viewport: Rect, win_w: int, win_h: int, mode: str = "contain") -> Placement:
    if win_w < 1 or win_h < 1:
        raise ValueError("window must be at least 1x1")
    if viewport.width < 1 or viewport.height < 1:
        # Empty viewport: nothing to draw.
        return Placement(viewport, Rect(0, 0, 0, 0))
    w, h = viewport.width, viewport.height
    if mode == "contain":
        if win_w * h <= win_h * w:  # width-limited
            dw, dh = win_w, _rhu(h * win_w, w)
        else:
            dh, dw = win_h, _rhu(w * win_h, h)
        dw, dh = min(dw, win_w), min(dh, win_h)
        return Placement(viewport, Rect((win_w - dw) // 2, (win_h - dh) // 2, dw, dh))
    if mode == "cover":
        if win_w * h > win_h * w:  # window relatively wider: crop viewport height
            cw, ch = w, min(h, max(1, _rhu(w * win_h, win_w)))
        else:
            ch, cw = h, min(w, max(1, _rhu(h * win_w, win_h)))
        src = Rect(viewport.x + (w - cw) // 2, viewport.y + (h - ch) // 2, cw, ch)
        return Placement(src, Rect(0, 0, win_w, win_h))
    raise ValueError(f"unknown mode {mode!r}; use 'contain' or 'cover'")
