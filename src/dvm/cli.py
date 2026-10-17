"""Command-line tool: validate / at / compile (no container writing yet)."""
from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction

from .mask import compile_timeline, cfr_pts
from .timeline import ValidationError, parse_timeline, rect_at


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {path}: {e}", file=sys.stderr)
        raise SystemExit(2)
    try:
        return parse_timeline(doc)
    except ValidationError as e:
        print(f"error: {path} is not a valid DVM timeline:", file=sys.stderr)
        for p in e.problems:
            print(f"  - {p}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dvm", description="Dynamic Viewport Media tools (draft, unofficial)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate an authoring timeline")
    v.add_argument("timeline")

    a = sub.add_parser("at", help="print the viewport at a time (seconds)")
    a.add_argument("timeline")
    a.add_argument("time")

    c = sub.add_parser("compile", help="expand to per-frame mask samples")
    c.add_argument("timeline")
    c.add_argument("--fps", required=True, help="e.g. 30 or 30000/1001")
    c.add_argument("--duration", required=True, help="media duration in seconds")

    i = sub.add_parser("inspect", help="inspect container boxes, tracks, and mask samples")
    i.add_argument("file", help="path to MP4 or MOV file")
    i.add_argument("--json", action="store_true", help="output machine-readable JSON")
    i.add_argument("--tree", action="store_true", help="print full box tree")

    m = sub.add_parser("mux", help="stream-copy video and add DVM mebx metadata track")
    m.add_argument("input", help="path to input MP4/MOV file")
    m.add_argument("timeline", help="path to DVM authoring timeline JSON")
    m.add_argument("output", help="path to output MP4 file")
    m.add_argument("--fps", help="compilation frame rate (e.g. 30, 60, 30000/1001)")
    m.add_argument("-f", "--force", action="store_true", help="overwrite existing output file")

    p = sub.add_parser("player", help="launch DVM Studio reference player & timeline editor in browser")
    p.add_argument("--port", type=int, default=8000, help="local port (default: 8000)")
    p.add_argument("--no-browser", action="store_true", help="do not open browser automatically")

    conf = sub.add_parser("conformance", help="run official DVM conformance test suite")

    args = ap.parse_args(argv)

    if args.cmd == "conformance":
        from .conformance import run_conformance_suite
        report = run_conformance_suite()
        print(report.format_summary())
        return 0 if report.all_passed else 1

    if args.cmd == "player":
        import functools
        import http.server
        import socketserver
        import webbrowser
        from pathlib import Path

        web_dir = Path(__file__).resolve().parent.parent.parent / "web"
        if not web_dir.is_dir():
            print(f"error: web directory not found at {web_dir}", file=sys.stderr)
            return 1

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web_dir))
        port = args.port

        print(f"Starting DVM Studio at http://localhost:{port}")
        if not args.no_browser:
            webbrowser.open(f"http://localhost:{port}")

        try:
            with socketserver.TCPServer(("", port), handler) as httpd:
                httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down DVM Studio.")
            return 0
        except OSError as e:
            print(f"error: cannot start server on port {port}: {e}", file=sys.stderr)
            return 1

    if args.cmd == "mux":
        from .mux import mux
        try:
            res = mux(
                input_path=args.input,
                timeline_path_or_doc=args.timeline,
                output_path=args.output,
                fps=Fraction(args.fps) if args.fps else None,
                overwrite=args.force,
            )
            print(res.summary())
            return 0
        except Exception as e:
            print(f"error: mux failed: {e}", file=sys.stderr)
            return 1

    if args.cmd == "inspect":
        from .inspect import inspect_container, format_inspection_text
        try:
            info = inspect_container(args.file)
        except Exception as e:
            print(f"error: cannot inspect {args.file}: {e}", file=sys.stderr)
            return 1
        if args.json:
            info_json = {k: v for k, v in info.items() if not k.startswith("_")}
            print(json.dumps(info_json, indent=2))
        else:
            print(format_inspection_text(info, show_tree=args.tree))
        return 0

    tl = _load(args.timeline)

    if args.cmd == "validate":
        print(f"ok: canvas {tl.canvas_width}x{tl.canvas_height}, {len(tl.events)} events, "
              f"ends at {float(tl.events[-1].end)}s")
        return 0
    if args.cmd == "at":
        r = rect_at(tl, Fraction(args.time))
        print(json.dumps({"x": r.x, "y": r.y, "width": r.width, "height": r.height}))
        return 0
    if args.cmd == "compile":
        fps, dur = Fraction(args.fps), Fraction(args.duration)
        n = int(dur * fps)
        if n < 1:
            print("error: duration * fps yields no frames", file=sys.stderr)
            return 2
        samples = compile_timeline(tl, cfr_pts(fps, n), Fraction(n) / fps)
        out = [{"start": float(s.start), "duration": float(s.duration),
                "value": [s.value.reference_width, s.value.reference_height, s.value.left,
                          s.value.width, s.value.top, s.value.height]} for s in samples]
        print(json.dumps({"frames": n, "samples": len(out), "payload_bytes": 12 * len(out), "runs": out}, indent=1))
        return 0
    return 2
