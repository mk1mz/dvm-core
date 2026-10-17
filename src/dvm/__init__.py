"""Dynamic Viewport Media (DVM) core library. Draft, unofficial, not a standard."""
from .mask import (MalformedPayload, MaskReader, RasterRectangleValue, Sample,
                   cfr_pts, check_samples, compile_timeline, scale_to_buffer, to_ticks)
from .present import Placement, viewport_fit
from .timeline import Event, Rect, Timeline, ValidationError, parse_timeline, rect_at

__version__ = "0.1.0.dev0"
__all__ = [
    "Event", "Rect", "Timeline", "ValidationError", "parse_timeline", "rect_at",
    "RasterRectangleValue", "Sample", "MaskReader", "MalformedPayload",
    "compile_timeline", "cfr_pts", "check_samples", "scale_to_buffer", "to_ticks",
    "Placement", "viewport_fit",
]
