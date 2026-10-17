import hashlib
import json
from pathlib import Path

import pytest

from dvm.cli import main
from dvm.inspect import inspect_container
from dvm.mask import RasterRectangleValue, Sample
from dvm.mux import mux
from dvm.synth import generate_test_mp4

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sample_video_mp4(tmp_path):
    """Generate a clean synthetic MP4 with video track."""
    s = [Sample(0, 1, RasterRectangleValue(1920, 1080, 0, 1080, 0, 1080))]
    mp4_bytes = generate_test_mp4(s, 1920, 1080, meta_timescale=600)
    in_file = tmp_path / "source_video.mp4"
    in_file.write_bytes(mp4_bytes)
    return in_file


@pytest.fixture
def timeline_file(tmp_path):
    tl_doc = {
        "version": 1,
        "canvas": {"width": 1920, "height": 1080},
        "events": [
            {
                "start": "0",
                "duration": "1",
                "viewport": {"x": 420, "y": 0, "width": 1080, "height": 1080},
                "transition": "cut",
            }
        ],
    }
    f = tmp_path / "test_timeline.json"
    f.write_text(json.dumps(tl_doc), encoding="utf-8")
    return f


def test_mux_stream_copy_integrity(tmp_path, sample_video_mp4, timeline_file):
    out_file = tmp_path / "muxed_output.mp4"

    res = mux(
        input_path=sample_video_mp4,
        timeline_path_or_doc=timeline_file,
        output_path=out_file,
        fps=30,
    )

    assert res.re_encoded is False
    # Static cut collapses 30 frames into 1 run-length sample
    assert res.samples_count == 1
    assert out_file.is_file()

    # Re-inspect output container
    info = inspect_container(out_file)
    assert len(info["tracks"]) >= 2
    assert len(info["mask_tracks"]) >= 1

    # Check the mask track targets the video track
    mt = next(m for m in info["mask_tracks"] if m["track_id"] == res.meta_track_id)
    assert mt["rndr_target_track"] == [res.video_track_id]
    assert mt["sample_count"] == 1
    # First sample rectangle matches timeline event
    assert mt["samples"][0]["value"]["left"] == 420
    assert mt["samples"][0]["value"]["width"] == 1080


def test_mux_smooth_transition_produces_per_frame_samples(tmp_path, sample_video_mp4):
    tl_doc = {
        "version": 1,
        "canvas": {"width": 1920, "height": 1080},
        "events": [
            {
                "start": "0",
                "duration": "0.5",
                "viewport": {"x": 0, "y": 0, "width": 1080, "height": 1080},
                "transition": "cut",
            },
            {
                "start": "0.5",
                "duration": "0.5",
                "viewport": {"x": 420, "y": 0, "width": 1080, "height": 1080},
                "transition": "smooth",
                "transition_duration": "0.5",
            },
        ],
    }
    f = tmp_path / "smooth_tl.json"
    f.write_text(json.dumps(tl_doc), encoding="utf-8")
    out = tmp_path / "smooth_muxed.mp4"

    res = mux(sample_video_mp4, f, out, fps=30)
    assert res.re_encoded is False
    # 0.5s cut (1 compressed run) + 0.5s smooth transition at 30fps (14 transition steps) = 15 samples
    assert res.samples_count == 15


def test_mux_deterministic_output(tmp_path, sample_video_mp4, timeline_file):
    out1 = tmp_path / "out1.mp4"
    out2 = tmp_path / "out2.mp4"

    res1 = mux(sample_video_mp4, timeline_file, out1, fps=30)
    res2 = mux(sample_video_mp4, timeline_file, out2, fps=30)

    assert res1.output_file_hash == res2.output_file_hash
    assert out1.read_bytes() == out2.read_bytes()


def test_mux_refuse_overwrite_input(tmp_path, sample_video_mp4, timeline_file):
    with pytest.raises(ValueError, match="Refusing to overwrite input file"):
        mux(sample_video_mp4, timeline_file, sample_video_mp4)


def test_mux_refuse_overwrite_existing_output(tmp_path, sample_video_mp4, timeline_file):
    out_file = tmp_path / "already_exists.mp4"
    out_file.write_bytes(b"existing content")

    with pytest.raises(FileExistsError, match="Output file already exists"):
        mux(sample_video_mp4, timeline_file, out_file, overwrite=False)

    # Allowed with overwrite=True
    res = mux(sample_video_mp4, timeline_file, out_file, overwrite=True)
    assert res.output_path == str(out_file.resolve())


def test_mux_cli(tmp_path, sample_video_mp4, timeline_file, capsys):
    out_file = tmp_path / "cli_muxed.mp4"
    ret = main(["mux", str(sample_video_mp4), str(timeline_file), str(out_file), "--fps", "30"])
    assert ret == 0

    captured = capsys.readouterr()
    assert "re-encoded: no" in captured.out
    assert "video stream SHA-256:" in captured.out
    assert out_file.is_file()
