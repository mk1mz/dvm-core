import pytest

from dvm import Rect, viewport_fit


def test_contain_portrait_in_landscape_window():
    p = viewport_fit(Rect(420, 0, 1080, 1920), 1920, 1080, "contain")
    assert p.src == Rect(420, 0, 1080, 1920)
    assert p.dst == Rect(656, 0, 608, 1080)  # 1080*1080/1920 = 607.5 -> 608


def test_contain_same_aspect_fills_window_exactly():
    p = viewport_fit(Rect(0, 420, 1920, 1080), 1280, 720, "contain")
    assert p.dst == Rect(0, 0, 1280, 720)


def test_contain_landscape_in_portrait_window():
    p = viewport_fit(Rect(0, 0, 1920, 1080), 1080, 1920, "contain")
    assert p.dst == Rect(0, 656, 1080, 608)


def test_cover_crops_viewport_to_window_aspect():
    p = viewport_fit(Rect(420, 0, 1080, 1920), 1920, 1080, "cover")
    assert p.dst == Rect(0, 0, 1920, 1080)
    assert p.src == Rect(420, 656, 1080, 608)


def test_cover_never_exceeds_viewport():
    for win in [(640, 480), (1, 1), (3000, 100), (100, 3000)]:
        vp = Rect(10, 20, 300, 200)
        p = viewport_fit(vp, *win, mode="cover")
        assert p.src.x >= vp.x and p.src.right <= vp.right
        assert p.src.y >= vp.y and p.src.bottom <= vp.bottom
        assert p.src.width >= 1 and p.src.height >= 1


def test_empty_viewport_draws_nothing():
    p = viewport_fit(Rect(0, 0, 0, 0), 100, 100)
    assert p.dst.width == 0 and p.dst.height == 0


def test_bad_arguments():
    with pytest.raises(ValueError):
        viewport_fit(Rect(0, 0, 10, 10), 0, 10)
    with pytest.raises(ValueError):
        viewport_fit(Rect(0, 0, 10, 10), 10, 10, "stretch")
