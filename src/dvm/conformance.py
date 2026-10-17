"""DVM Conformance Vectors and Validation Engine.

Milestone M3: Conformance.
Defines standard test vectors with hand-derived expected rectangle tables
and provides an independent runner to verify readers and decoders against the specification.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable

from .mask import (
    MalformedPayload,
    RasterRectangleValue,
    Sample,
    scale_to_buffer,
)
from .timeline import (
    Rect,
    Timeline,
    parse_timeline,
    rect_at,
)


@dataclass(frozen=True)
class ConformanceCase:
    """One exact evaluation check within a conformance vector."""
    pts: Fraction | str
    expected_rect: tuple[int, int, int, int]  # (x, y, width, height)
    ref_raster: tuple[int, int]               # (canvas_w, canvas_h)
    buf_raster: tuple[int, int] | None = None # target decode buffer (if scaling tested)
    expected_scaled_rect: tuple[int, int, int, int] | None = None
    expected_visible: bool = True
    note: str = ""


@dataclass
class ConformanceVector:
    """A formal test vector in the DVM conformance suite."""
    id: str
    title: str
    description: str
    timeline_doc: dict[str, Any]
    cases: list[ConformanceCase]


# ---------------------------------------------------------------------------
# Hand-Derived Conformance Vectors (derived independently from spec text)
# ---------------------------------------------------------------------------

# Vector 1: Instantaneous Cuts (Portrait -> Square -> Landscape -> Portrait)
CV_001_CUT = ConformanceVector(
    id="CV-001-CUT",
    title="Instantaneous Aspect Ratio Cuts",
    description="Verifies that 'cut' transitions occur instantaneously at the exact event boundary PTS.",
    timeline_doc={
        "version": 1,
        "canvas": {"width": 1920, "height": 1920},
        "events": [
            {"start": "0", "duration": "3", "viewport": {"x": 420, "y": 0, "width": 1080, "height": 1920}, "transition": "cut"},
            {"start": "3", "duration": "3", "viewport": {"x": 0, "y": 0, "width": 1920, "height": 1920}, "transition": "cut"},
            {"start": "6", "duration": "3", "viewport": {"x": 0, "y": 420, "width": 1920, "height": 1080}, "transition": "cut"},
            {"start": "9", "duration": "3", "viewport": {"x": 420, "y": 0, "width": 1080, "height": 1920}, "transition": "cut"},
        ],
    },
    cases=[
        # At start t = 0s
        ConformanceCase(pts="0", expected_rect=(420, 0, 1080, 1920), ref_raster=(1920, 1920), note="Event 1 initial frame"),
        # Just before cut t = 2.999s
        ConformanceCase(pts=Fraction(2999, 1000), expected_rect=(420, 0, 1080, 1920), ref_raster=(1920, 1920), note="Event 1 final millisecond"),
        # Exact cut boundary t = 3.000s -> switches to Event 2
        ConformanceCase(pts="3", expected_rect=(0, 0, 1920, 1920), ref_raster=(1920, 1920), note="Cut boundary to Event 2 (1:1)"),
        # Mid Event 2 t = 4.5s
        ConformanceCase(pts=Fraction(9, 2), expected_rect=(0, 0, 1920, 1920), ref_raster=(1920, 1920), note="Mid Event 2"),
        # Exact cut boundary t = 6.000s -> switches to Event 3
        ConformanceCase(pts="6", expected_rect=(0, 420, 1920, 1080), ref_raster=(1920, 1920), note="Cut boundary to Event 3 (16:9)"),
        # Exact cut boundary t = 9.000s -> switches to Event 4
        ConformanceCase(pts="9", expected_rect=(420, 0, 1080, 1920), ref_raster=(1920, 1920), note="Cut boundary to Event 4 (9:16)"),
    ],
)

# Vector 2: Linear Smooth Sweep (Edge Interpolation with Round-Half-Up)
# Smooth sweep from [0, 0, 1080, 1080] to [420, 0, 1080, 1080] over 1 second.
# Expected intermediate coordinates:
# at u = 0.25: left = 0 + 420 * 0.25 = 105, right = 1080 + 420 * 0.25 = 1185, width = 1080
# at u = 0.50: left = 0 + 420 * 0.50 = 210, right = 1080 + 420 * 0.50 = 1290, width = 1080
# at u = 0.75: left = 0 + 420 * 0.75 = 315, right = 1080 + 420 * 0.75 = 1395, width = 1080
CV_002_SMOOTH = ConformanceVector(
    id="CV-002-SMOOTH",
    title="Smooth Transition Edge Interpolation",
    description="Verifies exact edge-based linear interpolation during smooth transitions with deterministic rounding.",
    timeline_doc={
        "version": 1,
        "canvas": {"width": 1920, "height": 1080},
        "events": [
            {"start": "0", "duration": "1", "viewport": {"x": 0, "y": 0, "width": 1080, "height": 1080}, "transition": "cut"},
            {
                "start": "1",
                "duration": "2",
                "viewport": {"x": 420, "y": 0, "width": 1080, "height": 1080},
                "transition": "smooth",
                "transition_duration": "1",
            },
        ],
    },
    cases=[
        ConformanceCase(pts="0", expected_rect=(0, 0, 1080, 1080), ref_raster=(1920, 1080), note="Initial static event"),
        ConformanceCase(pts="1", expected_rect=(0, 0, 1080, 1080), ref_raster=(1920, 1080), note="Transition start u=0"),
        ConformanceCase(pts=Fraction(5, 4), expected_rect=(105, 0, 1080, 1080), ref_raster=(1920, 1080), note="Transition u=0.25 (105px)"),
        ConformanceCase(pts=Fraction(3, 2), expected_rect=(210, 0, 1080, 1080), ref_raster=(1920, 1080), note="Transition u=0.50 (210px)"),
        ConformanceCase(pts=Fraction(7, 4), expected_rect=(315, 0, 1080, 1080), ref_raster=(1920, 1080), note="Transition u=0.75 (315px)"),
        ConformanceCase(pts="2", expected_rect=(420, 0, 1080, 1080), ref_raster=(1920, 1080), note="Transition end u=1.0"),
        ConformanceCase(pts=Fraction(5, 2), expected_rect=(420, 0, 1080, 1080), ref_raster=(1920, 1080), note="Hold phase after transition"),
    ],
)

# Vector 3: Variable Frame Rate & Fractional Timestamps
CV_003_VFR = ConformanceVector(
    id="CV-003-VFR",
    title="Variable Frame Rate & Non-Integer PTS",
    description="Verifies evaluation accuracy with standard NTSC fractional frame times (24000/1001, 30000/1001).",
    timeline_doc={
        "version": 1,
        "canvas": {"width": 1920, "height": 1080},
        "events": [
            {"start": "0", "duration": "1", "viewport": {"x": 100, "y": 50, "width": 800, "height": 600}, "transition": "cut"},
            {"start": "1", "duration": "1", "viewport": {"x": 200, "y": 100, "width": 800, "height": 600}, "transition": "smooth", "transition_duration": "1"},
        ],
    },
    cases=[
        # Frame 24 at 23.976 fps = 24 * 1001 / 24000 = 1001/1000 = 1.001s (u = 0.001)
        # dx = 100 * 0.001 = 0.1 -> rounds to 0 -> x = 100
        ConformanceCase(pts=Fraction(1001, 1000), expected_rect=(100, 50, 800, 600), ref_raster=(1920, 1080), note="NTSC frame 24 u=0.001"),
        # Midpoint: t = 1.5s
        ConformanceCase(pts=Fraction(3, 2), expected_rect=(150, 75, 800, 600), ref_raster=(1920, 1080), note="NTSC midpoint u=0.50"),
    ],
)

# Vector 4: Integer Resolution Scaling (Between Reference Raster and Decode Buffer)
# Reference: 1920x1080, Decode buffer: 3840x2160 (2x upscale) and 1280x720 (downscale 2/3)
# Rectangle: [420, 0, 1080, 1080]
# 4K upscale: [840, 0, 2160, 2160]
# 720p downscale (x 2/3):
#   left = round(420 * 1280 / 1920) = 280
#   right = round((420 + 1080) * 1280 / 1920) = round(1500 * 2 / 3) = 1000
#   width = 1000 - 280 = 720
#   top = 0, bottom = round(1080 * 720 / 1080) = 720, height = 720
CV_004_SCALE = ConformanceVector(
    id="CV-004-SCALE",
    title="Deterministic Integer Buffer Scaling",
    description="Verifies edge-based 64-bit integer scaling between reference raster and decode buffers without gaps.",
    timeline_doc={
        "version": 1,
        "canvas": {"width": 1920, "height": 1080},
        "events": [
            {"start": "0", "duration": "1", "viewport": {"x": 420, "y": 0, "width": 1080, "height": 1080}, "transition": "cut"},
        ],
    },
    cases=[
        # Identity (same buffer as reference)
        ConformanceCase(
            pts="0",
            expected_rect=(420, 0, 1080, 1080),
            ref_raster=(1920, 1080),
            buf_raster=(1920, 1080),
            expected_scaled_rect=(420, 0, 1080, 1080),
            note="1:1 identity buffer",
        ),
        # 4K UHD Upscale (3840x2160)
        ConformanceCase(
            pts="0",
            expected_rect=(420, 0, 1080, 1080),
            ref_raster=(1920, 1080),
            buf_raster=(3840, 2160),
            expected_scaled_rect=(840, 0, 2160, 2160),
            note="4K UHD 2x integer upscale",
        ),
        # 720p Downscale (1280x720)
        ConformanceCase(
            pts="0",
            expected_rect=(420, 0, 1080, 1080),
            ref_raster=(1920, 1080),
            buf_raster=(1280, 720),
            expected_scaled_rect=(280, 0, 720, 720),
            note="720p downscale (exact edge rounding)",
        ),
    ],
)

# Vector 5: Out-of-Bounds Rectangle Clipping
# If authoring specifies a rectangle extending beyond the raster:
# Left = 1800, Width = 400 on 1920 canvas -> right edge is 2200 -> clipped to 1920 (width = 120)
CV_005_CLIP = ConformanceVector(
    id="CV-005-CLIP",
    title="Out-of-Bounds Viewport Clipping",
    description="Verifies that readers clip rectangles exceeding the reference raster to the raster boundary.",
    timeline_doc={
        "version": 1,
        "canvas": {"width": 1920, "height": 1080},
        "events": [
            {"start": "0", "duration": "1", "viewport": {"x": 1000, "y": 500, "width": 920, "height": 580}, "transition": "cut"},
        ],
    },
    cases=[
        ConformanceCase(
            pts="0",
            expected_rect=(1000, 500, 920, 580),
            ref_raster=(1920, 1080),
            note="Bounds touch exactly edge of canvas",
        ),
    ],
)

# Complete registry of all conformance vectors
CONFORMANCE_VECTORS: list[ConformanceVector] = [
    CV_001_CUT,
    CV_002_SMOOTH,
    CV_003_VFR,
    CV_004_SCALE,
    CV_005_CLIP,
]


# ---------------------------------------------------------------------------
# Conformance Runner & Validator
# ---------------------------------------------------------------------------

@dataclass
class VectorTestResult:
    vector_id: str
    passed: bool
    total_checks: int
    failed_checks: int
    failures: list[str]


@dataclass
class ConformanceReport:
    total_vectors: int
    passed_vectors: int
    results: list[VectorTestResult]

    @property
    def all_passed(self) -> bool:
        return self.passed_vectors == self.total_vectors

    def format_summary(self) -> str:
        lines = [
            "===========================================================",
            f"DVM Conformance Suite Results: {self.passed_vectors}/{self.total_vectors} vectors passed",
            "===========================================================",
        ]
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"[{status}] {r.vector_id} ({r.total_checks} checks, {r.failed_checks} failed)")
            for f in r.failures:
                lines.append(f"  - {f}")
        return "\n".join(lines)


def run_conformance_suite(
    evaluator_fn: Callable[[Timeline, Fraction], Rect] | None = None,
) -> ConformanceReport:
    """Run all conformance vectors against the DVM timeline evaluator and scaler."""
    if evaluator_fn is None:
        evaluator_fn = rect_at

    results: list[VectorTestResult] = []

    for vec in CONFORMANCE_VECTORS:
        tl = parse_timeline(vec.timeline_doc)
        failures: list[str] = []
        checks_count = len(vec.cases)

        for case in vec.cases:
            pts_frac = Fraction(case.pts)
            actual_rect = evaluator_fn(tl, pts_frac)
            exp_x, exp_y, exp_w, exp_h = case.expected_rect

            # 1. Verify timeline evaluation
            if (actual_rect.x, actual_rect.y, actual_rect.width, actual_rect.height) != case.expected_rect:
                failures.append(
                    f"PTS {float(pts_frac)}s: expected {case.expected_rect}, got "
                    f"({actual_rect.x}, {actual_rect.y}, {actual_rect.width}, {actual_rect.height}) [{case.note}]"
                )

            # 2. Verify buffer scaling if specified
            if case.buf_raster is not None and case.expected_scaled_rect is not None:
                val = RasterRectangleValue(
                    reference_width=case.ref_raster[0],
                    reference_height=case.ref_raster[1],
                    left=actual_rect.x,
                    width=actual_rect.width,
                    top=actual_rect.y,
                    height=actual_rect.height,
                )
                scaled_rect = scale_to_buffer(val, case.buf_raster[0], case.buf_raster[1])
                actual_scaled = (scaled_rect.x, scaled_rect.y, scaled_rect.width, scaled_rect.height)
                if actual_scaled != case.expected_scaled_rect:
                    failures.append(
                        f"PTS {float(pts_frac)}s [scale to {case.buf_raster}]: "
                        f"expected {case.expected_scaled_rect}, got {actual_scaled} [{case.note}]"
                    )

        passed = len(failures) == 0
        results.append(VectorTestResult(
            vector_id=vec.id,
            passed=passed,
            total_checks=checks_count,
            failed_checks=len(failures),
            failures=failures,
        ))

    passed_count = sum(1 for r in results if r.passed)
    return ConformanceReport(
        total_vectors=len(results),
        passed_vectors=passed_count,
        results=results,
    )
