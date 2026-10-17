# DVM Core & Stream-Copy Muxer (`dvm-core`)

> Reference Python implementation of Dynamic Viewport Media (DVM).
> Provides timeline validation, exact arithmetic evaluation, ISOBMFF/QTFF container parsing, and lossless stream-copy MP4 muxing.

---

## Features

- **Stream-Copy Muxer (`dvm mux`):** Injects `mebx` timed metadata tracks linked via `rndr` track references into existing MP4 files without re-encoding media (bit-identical streams).
- **Container Inspector (`dvm inspect`):** Decodes box trees, tracks, and extracts all `RasterRectangleValue` mask samples (with `--json` and `--tree` modes).
- **Exact Evaluator:** Pure integer arithmetic edge interpolation conforming to the DVM profile specification.
- **Semantic Validator:** Verifies authoring timeline JSON against strict boundary and continuity rules.
- **135 Automated Unit Tests:** 100% test pass rate across parser, muxer, compiler, and CLI.

---

## Installation & Testing

```bash
# Editable install with test dependencies
pip install -e ".[test]"

# Run test suite
pytest
```

---

## Command Line Usage

### 1. Validate Authoring Timeline
```bash
python -m dvm validate examples/portrait-square-landscape.json
```

### 2. Query Viewport at Exact Timestamp
```bash
python -m dvm at examples/portrait-square-landscape.json 3.25
# Output: {"x": 210, "y": 0, "width": 1500, "height": 1920}
```

### 3. Compile to Per-Frame Samples
```bash
python -m dvm compile examples/portrait-square-landscape.json --fps 30 --duration 12
```

### 4. Stream-Copy Mux into MP4
```bash
python -m dvm mux input_video.mp4 timeline.json muxed_output.mp4
```

### 5. Inspect MP4 Container & Mask Tracks
```bash
python -m dvm inspect muxed_output.mp4
# Or JSON export:
python -m dvm inspect --json muxed_output.mp4
```

---

## License

Licensed under the Apache License, Version 2.0 (see [`LICENSE`](LICENSE)).
