import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grade import analyze_lineup, build_profiles, core_players, grade_label, role_of  # noqa: E402


def make_history(n_matches, regulars, subs):
    """regulars는 매 경기 선발, subs는 교체 출전."""
    hist = []
    for _ in range(n_matches):
        hist.append(
            {
                "starters": list(regulars),
                "played": list(regulars) + list(subs),
                "names": {p: f"P{p}" for p in list(regulars) + list(subs)},
            }
        )
    return hist


def test_profiles_count():
    hist = make_history(5, [1, 2, 3], [4])
    prof = build_profiles(hist)
    assert prof[1]["starts"] == 5 and prof[1]["apps"] == 5
    assert prof[4]["starts"] == 0 and prof[4]["apps"] == 5
    assert prof[1]["name"] == "P1"


def test_starter_counted_even_if_missing_from_played():
    hist = [{"starters": [7], "played": [], "names": {7: "P7"}}]
    prof = build_profiles(hist)
    assert prof[7] == {"name": "P7", "starts": 1, "apps": 1}


def test_core_ranking_is_deterministic():
    prof = {
        "a": {"name": "a", "starts": 3, "apps": 3},
        "b": {"name": "b", "starts": 3, "apps": 5},
        "c": {"name": "c", "starts": 1, "apps": 5},
    }
    assert core_players(prof, 2) == ["b", "a"]
    assert core_players(prof, 10) == ["b", "a", "c"]


def test_role_thresholds():
    prof = {
        "s": {"name": "s", "starts": 6, "apps": 6},
        "r": {"name": "r", "starts": 4, "apps": 8},
        "b": {"name": "b", "starts": 1, "apps": 6},
    }
    assert role_of("s", prof, 10) == "주전"
    assert role_of("r", prof, 10) == "로테이션"
    assert role_of("b", prof, 10) == "백업"
    assert role_of("x", prof, 10) == "신규·복귀"
    assert role_of("s", prof, 0) == "불명"


def test_grade_labels():
    assert grade_label(11, 11) == "1군"
    assert grade_label(9, 11) == "1군"
    assert grade_label(8, 11) == "1.5군"
    assert grade_label(6, 11) == "1.5군"
    assert grade_label(5, 11) == "2군"
    assert grade_label(0, 0) == "판단불가"


def test_full_strength_lineup():
    regulars = list(range(1, 12))
    hist = make_history(6, regulars, [50, 51])
    lineup = [{"id": p, "name": f"P{p}", "pos": "M"} for p in regulars]
    out = analyze_lineup(lineup, hist, 11)
    assert out["grade"] == "1군"
    assert out["core_in"] == 11
    assert out["missing"] == []
    assert all(p["role"] == "주전" for p in out["players"])
    assert out["players"][0]["starts"] == 6


def test_rotated_lineup_flags_missing_regulars():
    regulars = list(range(1, 12))
    bench = list(range(20, 31))
    hist = make_history(6, regulars, bench)
    lineup = [{"id": p, "name": f"P{p}", "pos": "M"} for p in regulars[:4] + bench[:7]]
    out = analyze_lineup(lineup, hist, 11)
    assert out["core_in"] == 4
    assert out["grade"] == "2군"
    missing_ids = {m["id"] for m in out["missing"]}
    assert missing_ids == set(regulars[4:])


def test_half_rotation_is_one_and_half():
    regulars = list(range(1, 12))
    bench = list(range(20, 31))
    hist = make_history(6, regulars, bench)
    lineup = [{"id": p} for p in regulars[:7] + bench[:4]]
    out = analyze_lineup(lineup, hist, 11)
    assert out["core_in"] == 7
    assert out["grade"] == "1.5군"


def test_empty_lineup_is_unknown_not_second_team():
    hist = make_history(6, list(range(1, 12)), [])
    out = analyze_lineup([], hist, 11)
    assert out["grade"] == "판단불가"


def test_not_enough_history_is_unknown():
    hist = make_history(2, [1, 2, 3], [])
    out = analyze_lineup([{"id": 1}], hist, 11)
    assert out["grade"] == "판단불가"


def test_empty_history_does_not_crash():
    out = analyze_lineup([{"id": 1, "name": "P1"}], [], 11)
    assert out["grade"] == "판단불가"
    assert out["core_in"] == 0
    assert out["players"][0]["role"] == "불명"
    assert out["key_players"] == []


def test_baseball_size_nine():
    regulars = list(range(1, 10))
    hist = make_history(10, regulars, [90])
    lineup = [{"id": p, "pos": "CF"} for p in regulars[:8]] + [{"id": 90, "pos": "C"}]
    out = analyze_lineup(lineup, hist, 9)
    assert out["core_in"] == 8
    assert out["grade"] == "1군"
    assert out["missing"][0]["id"] == 9


def test_new_player_in_lineup_is_reported():
    hist = make_history(5, [1, 2, 3, 4, 5], [])
    out = analyze_lineup([{"id": 99, "name": "새선수"}], hist, 5)
    assert out["players"][0]["role"] == "신규·복귀"
    assert out["players"][0]["starts"] == 0


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  ok  {name}")
    print(f"\n{passed} passed")
