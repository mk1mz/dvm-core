# DVM Core & Stream-Copy Muxer (`dvm-core`)

[![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Tests: 135+ Passing](https://img.shields.io/badge/Tests-135%2B%20Passing-brightgreen.svg)](tests/)
[![Dependencies: Zero](https://img.shields.io/badge/Dependencies-Zero-success.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](LICENSE)

> The reference Python implementation of **Dynamic Viewport Media (DVM)**.
> Features a zero-dependency ISOBMFF/QTFF container parser, an exact 16.16 fixed-point arithmetic evaluator, and a **lossless stream-copy MP4 muxer** that embeds dynamic camera viewports into video files with **zero video re-encoding**.

---

## 🚀 Key Innovations

### 1. 100% Bit-Identical Video Streams (No Re-encoding)
Adding or tweaking viewport trajectories takes milliseconds. Media packets in `mdat` are copied byte-for-byte untouched.
- `re-encoded: no`
- Input video elementary stream SHA-256 == Output video elementary stream SHA-256.

### 2. Two-Pass `stco`/`co64` Chunk Offset Resolution
When injecting timed metadata, the `moov` header grows, shifting downstream `mdat` byte positions. `dvm-core` calculates exact `moov` expansion in Pass 1 and generates bit-accurate shifted chunk offsets in Pass 2, preventing playback corruption.

### 3. Run-Length Metadata Compression
When a camera viewport holds steady, `dvm-core` combines consecutive static frames into a single sample with extended duration (`stts`). A 10-second static hold at 60 fps uses **1 metadata sample** instead of 600, shrinking metadata overhead by over 95%.

---

## 🔄 How the Muxer Works

```
┌────────────────────────┐
│  Input MP4 / MOV Video │───────┐
└────────────────────────┘       │
                                 ▼
┌────────────────────────┐   ┌───────────────────────────┐   ┌──────────────────────────┐
│  Authoring Timeline    │──▶│   DVM Stream-Copy Muxer   │──▶│ Output DVM MP4 Container │
│    (timeline.json)     │   │                           │   │  • Video Track (Untouched│
└────────────────────────┘   │ • Zero re-encoding        │   │  • Audio Track (Untouched│
                             │ • 2-pass stco adjustment  │   │  • mebx Metadata Track   │
                             │ • Run-length compression  │   │  • tref -> rndr Link     │
                             └───────────────────────────┘   └──────────────────────────┘
```

---

## 📦 Installation

`dvm-core` requires Python 3.10+ and has **zero required runtime dependencies**.

```bash
# Clone the repository
git clone https://github.com/mk1mz/dvm-core.git
cd dvm-core

# Optional: Install test dependencies (pytest)
pip install -e ".[test]"

# Run the 135+ unit and conformance tests
pytest tests/ -v
```

---

## 🛠️ CLI Quick Start

The unified `dvm` command line tool provides tools to mux, inspect, query, and validate DVM media:

### 1. Lossless Stream-Copy Muxing
Inject camera viewport instructions from a `timeline.json` into an existing video:
```bash
python -m dvm mux input.mp4 timeline.json output_dvm.mp4
```

### 2. Deep Container & Metadata Inspection
Inspect atom hierarchy, sample counts, and sample-by-sample viewport coordinates:
```bash
# Tabular summary:
python -m dvm inspect output_dvm.mp4

# Full atom tree:
python -m dvm inspect --tree output_dvm.mp4

# Machine-readable JSON output:
python -m dvm inspect --json output_dvm.mp4
```

### 3. Exact Presentation Timestamp (PTS) Query
Query the exact viewport rectangle at any second of playback:
```bash
python -m dvm at timeline.json 3.25
# Output: {"x": 210, "y": 0, "width": 1500, "height": 1920}
```

### 4. Semantic Validation
Validate authoring JSON against the strict DVM profile schema:
```bash
python -m dvm validate timeline.json
```

### 5. Run Conformance Suite
Verify container parsing against the 5 formal mathematical test vectors:
```bash
python -m dvm conformance
```

---

## 💻 Programmatic Python API

```python
from dvm.mux import mux_dvm
from dvm.isobmff import parse_isobmff, extract_metadata_samples

# 1. Losslessly mux a timeline into an existing MP4
result = mux_dvm(
    input_path="master_video.mp4",
    timeline_path="camera_path.json",
    output_path="dynamic_video.mp4"
)
print(f"Muxed {result.sample_count} metadata samples. Re-encoded: {result.re_encoded}")

# 2. Inspect timed metadata samples
with open("dynamic_video.mp4", "rb") as f:
    boxes = parse_isobmff(f.read())
    samples = extract_metadata_samples(boxes)
    for s in samples:
        print(f"Time: {s.pts:.2f}s | Crop: [{s.x}, {s.y}, {s.width}, {s.height}]")
```

---

## 🏛️ Part of the DVM Standard

- **Specification & Schemas**: [`dvm-spec`](https://github.com/mk1mz/dvm-spec)
- **Web Editor & Reference Player**: [`dvm-studio`](https://github.com/mk1mz/dvm-studio)

---

## 📄 License

Licensed under the **Apache License, Version 2.0** (see [`LICENSE`](LICENSE)).
