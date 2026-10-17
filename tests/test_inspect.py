import json
import subprocess
import sys
from pathlib import Path

import pytest

from dvm.cli import main
from dvm.inspect import inspect_container
from dvm.mask import RasterRectangleValue, Sample
from dvm.synth import generate_test_mp4

ROOT = Path(__file__).resolve().parent.parent


def test_inspect_container_programmatic(tmp_path):
    s = [Sample(start=0, duration=1, value=RasterRectangleValue(1920, 1080, 100, 800, 50, 600))]
    data = generate_test_mp4(s, 1920, 1080)
    mp4_file = tmp_path / "test.mp4"
    mp4_file.write_bytes(data)

    info = inspect_container(mp4_file)
    assert info["file_size"] == len(data)
    assert "moov" in info["top_level_boxes"]
    assert len(info["tracks"]) == 2
    assert len(info["mask_tracks"]) == 1

    mt = info["mask_tracks"][0]
    assert mt["rndr_target_track"] == [1]
    assert mt["sample_count"] == 1
    assert mt["samples"][0]["value"]["width"] == 800


def test_inspect_file_not_found():
    with pytest.raises(FileNotFoundError):
        inspect_container("non_existent_file_12345.mp4")


def test_cli_inspect_text(tmp_path, capsys):
    s = [Sample(start=0, duration=1, value=RasterRectangleValue(1920, 1080, 0, 1080, 0, 1080))]
    mp4_file = tmp_path / "test.mp4"
    mp4_file.write_bytes(generate_test_mp4(s, 1920, 1080))

    ret = main(["inspect", str(mp4_file)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Track 1: type='vide'" in captured.out
    assert "Track 2: type='meta'" in captured.out
    assert "rndr -> [1]" in captured.out
    assert "raster=1920x1080" in captured.out


def test_cli_inspect_json(tmp_path, capsys):
    s = [Sample(start=0, duration=1, value=RasterRectangleValue(1920, 1080, 0, 1080, 0, 1080))]
    mp4_file = tmp_path / "test.mp4"
    mp4_file.write_bytes(generate_test_mp4(s, 1920, 1080))

    ret = main(["inspect", "--json", str(mp4_file)])
    assert ret == 0
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert doc["top_level_boxes"] == ["ftyp", "moov", "mdat"]
    assert doc["mask_tracks"][0]["samples"][0]["value"]["reference_width"] == 1920


def test_cli_inspect_tree(tmp_path, capsys):
    s = [Sample(start=0, duration=1, value=RasterRectangleValue(1920, 1080, 0, 1080, 0, 1080))]
    mp4_file = tmp_path / "test.mp4"
    mp4_file.write_bytes(generate_test_mp4(s, 1920, 1080))

    ret = main(["inspect", "--tree", str(mp4_file)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Box Tree:" in captured.out
    assert "moov" in captured.out
    assert "trak" in captured.out


def test_cli_inspect_missing_file():
    ret = main(["inspect", "does_not_exist_404.mp4"])
    assert ret == 1
