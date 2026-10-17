from fractions import Fraction as F

import pytest

from dvm import Rect, ValidationError, parse_timeline, rect_at

from conftest import EXAMPLES, load

PORTRAIT = Rect(420, 0, 1080, 1920)
SQUARE = Rect(0, 0, 1920, 1920)
LANDSCAPE = Rect(0, 420, 1920, 1080)


@pytest.fixture
def tl(psl_doc):
    return parse_timeline(psl_doc)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(path):
    import json
    parse_timeline(json.loads(path.read_text()))


# ---- evaluation: expected values worked out by hand (edge-wise linear) -----

@pytest.mark.parametrize("t,expected", [
    (F(0), PORTRAIT),
    (F("2.999"), PORTRAIT),
    (F(3), PORTRAIT),                      # transition starts at p=0 == previous viewport
    (F("3.125"), Rect(315, 0, 1290, 1920)),  # p=1/4: left 420*3/4, right 1500+420/4
    (F("3.25"), Rect(210, 0, 1500, 1920)),   # p=1/2
    (F("3.5"), SQUARE),                    # transition complete
    (F(5), SQUARE),
    (F("6.25"), Rect(0, 210, 1920, 1500)),   # square->landscape, p=1/2
    (F("6.5"), LANDSCAPE),
    (F("9.25"), Rect(210, 210, 1500, 1500)),  # landscape->portrait, p=1/2
    (F(10), PORTRAIT),
    (F(12), PORTRAIT),                     # after the end: hold last
    (F(100), PORTRAIT),
    (F(-1), PORTRAIT),                     # before 0 clamps
])
def test_rect_at_hand_computed(tl, t, expected):
    assert rect_at(tl, t) == expected


def test_cut_is_instantaneous():
    tl = parse_timeline(load("era-cuts.json"))
    assert rect_at(tl, F("1.9999999")) == Rect(0, 0, 3840, 2160)
    assert rect_at(tl, F(2)) == Rect(480, 0, 2880, 2160)
    assert rect_at(tl, F("3.99")) == Rect(480, 0, 2880, 2160)
    assert rect_at(tl, F(4)) == Rect(0, 276, 3840, 1608)


def test_explicit_transition_duration_whole_event():
    tl = parse_timeline(load("slow-push-in.json"))
    assert rect_at(tl, F(2)) == Rect(0, 0, 1920, 1080)
    assert rect_at(tl, F(4)) == Rect(210, 0, 1500, 1080)  # p=1/2: left 420/2, right 1920-420/2
    assert rect_at(tl, F(6)) == Rect(420, 0, 1080, 1080)


def _two_events(a, b, td="1"):
    return {
        "version": 1, "canvas": {"width": 100, "height": 100},
        "events": [
            {"start": 0, "duration": 1, "viewport": a},
            {"start": 1, "duration": 1, "viewport": b, "transition": "smooth", "transition_duration": td},
        ],
    }


def test_rounding_is_half_up_on_left_edge():
    tl = parse_timeline(_two_events({"x": 0, "y": 0, "width": 100, "height": 100},
                                    {"x": 1, "y": 0, "width": 99, "height": 100}))
    assert rect_at(tl, F("1.5")).x == 1    # 0.5 -> 1
    assert rect_at(tl, F("1.49")).x == 0   # 0.49 -> 0


def test_rounding_is_half_up_on_right_edge():
    tl = parse_timeline(_two_events({"x": 0, "y": 0, "width": 100, "height": 100},
                                    {"x": 0, "y": 0, "width": 99, "height": 100}))
    assert rect_at(tl, F("1.5")) == Rect(0, 0, 100, 100)  # right edge 99.5 -> 100


def test_interpolated_rects_stay_valid_for_whole_sweep(tl):
    prev = None
    for k in range(0, 12 * 120):
        r = rect_at(tl, F(k, 120))
        assert r.width >= 1 and r.height >= 1
        assert 0 <= r.x and r.right <= tl.canvas_width
        assert 0 <= r.y and r.bottom <= tl.canvas_height
        prev = r


def test_evaluation_is_a_pure_function_of_time(tl):
    """Seeking/pausing/rate changes only change *which* media time is asked for."""
    times = [F(k, 7) for k in range(0, 90)]
    forward = [rect_at(tl, t) for t in times]
    backward = [rect_at(tl, t) for t in reversed(times)][::-1]
    assert forward == backward


# ---- validation -----------------------------------------------------------

def problems(doc):
    with pytest.raises(ValidationError) as e:
        parse_timeline(doc)
    return e.value.problems


def has(ps, text):
    return any(text in p for p in ps)


def test_rejects_negative_dimension(mutate):
    d = mutate(lambda d: d["events"][1]["viewport"].update(width=-5))
    assert has(problems(d), "viewport.width: must be >= 1")


def test_rejects_zero_dimension(mutate):
    d = mutate(lambda d: d["events"][1]["viewport"].update(height=0))
    assert has(problems(d), "viewport.height: must be >= 1")


def test_rejects_negative_origin(mutate):
    d = mutate(lambda d: d["events"][0]["viewport"].update(x=-1))
    assert has(problems(d), "viewport.x: must be >= 0")


def test_rejects_out_of_bounds_right(mutate):
    d = mutate(lambda d: d["events"][0]["viewport"].update(x=1000))
    assert has(problems(d), "exceeds canvas width 1920")


def test_rejects_out_of_bounds_bottom(mutate):
    d = mutate(lambda d: d["events"][2]["viewport"].update(y=900))
    assert has(problems(d), "exceeds canvas height 1920")


@pytest.mark.parametrize("bad", [-1, "abc", True, None, [], float("nan"), float("inf"), "1/0", "-2", " 1", "1e3"])
def test_rejects_invalid_start(mutate, bad):
    d = mutate(lambda d: d["events"][1].update(start=bad))
    assert has(problems(d), "events[1].start")


@pytest.mark.parametrize("bad", [0, -3, "x", False, float("nan")])
def test_rejects_invalid_duration(mutate, bad):
    d = mutate(lambda d: d["events"][1].update(duration=bad))
    assert has(problems(d), "events[1].duration")


def test_accepts_decimal_and_rational_strings(mutate):
    d = mutate(lambda d: (d["events"][1].update(start="3", duration="3/2"),
                          d["events"][2].update(start="9/2", duration="1.5"),
                          d["events"][3].update(start="6", duration=3)))
    parse_timeline(d)


def test_rejects_unsupported_transition(mutate):
    d = mutate(lambda d: d["events"][1].update(transition="ease-in-out"))
    assert has(problems(d), "unsupported 'ease-in-out'")


def test_rejects_smooth_on_first_event(mutate):
    d = mutate(lambda d: d["events"][0].update(transition="smooth"))
    assert has(problems(d), "nothing to transition from")


def test_rejects_transition_longer_than_event(mutate):
    d = mutate(lambda d: d["events"][1].update(transition_duration=3.5))
    assert has(problems(d), "exceeds event duration")


def test_rejects_transition_duration_with_cut(mutate):
    d = mutate(lambda d: d["events"][0].update(transition_duration=1))
    assert has(problems(d), "not allowed with transition 'cut'")


def test_rejects_overlap(mutate):
    d = mutate(lambda d: d["events"][1].update(start=2.5))
    assert has(problems(d), "overlaps previous event")


def test_rejects_gap(mutate):
    d = mutate(lambda d: d["events"][2].update(start=7))
    assert has(problems(d), "gap after previous event")


def test_rejects_first_event_not_at_zero(mutate):
    d = mutate(lambda d: d["events"][0].update(start=0.5))
    assert has(problems(d), "first event must start at 0")


def test_rejects_unknown_keys(mutate):
    d = mutate(lambda d: d.update(extra=1))
    assert has(problems(d), "unknown key 'extra'")
    d = mutate(lambda d: d["events"][0]["viewport"].update(rotation=90))
    assert has(problems(d), "unknown key 'rotation'")


@pytest.mark.parametrize("ver", [2, 0, "1", None, 1.0])
def test_rejects_unsupported_version(mutate, ver):
    d = mutate(lambda d: d.update(version=ver))
    assert has(problems(d), "version")


@pytest.mark.parametrize("bad", [1.5, "10", True, None])
def test_rejects_non_integer_coordinates(mutate, bad):
    d = mutate(lambda d: d["events"][0]["viewport"].update(y=bad))
    assert has(problems(d), "viewport.y: must be an integer")


def test_rejects_canvas_out_of_range(mutate):
    d = mutate(lambda d: d["canvas"].update(width=70000))
    assert has(problems(d), "canvas.width")


def test_rejects_empty_events_and_non_object():
    assert has(problems({"version": 1, "canvas": {"width": 10, "height": 10}, "events": []}), "non-empty array")
    assert has(problems([]), "must be a JSON object")


def test_reports_all_problems_at_once(mutate):
    def f(d):
        d["events"][0]["viewport"]["width"] = -1
        d["events"][1]["transition"] = "wobble"
        d["events"][2]["duration"] = 0
    ps = problems(mutate(f))
    assert len(ps) >= 3
    assert has(ps, "events[0]") and has(ps, "events[1]") and has(ps, "events[2]")
