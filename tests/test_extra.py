import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parse  # noqa: E402
from grade import (  # noqa: E402
    analyze_lineup,
    enrich,
    formation_rows,
    minutes_total,
    parse_formation,
    player_log,
    team_form,
    team_leaders,
)


def P(pid, starter, pos="M", sub_in=False, sub_out=False, stats=None, place=None):
    d = {"starter": starter, "athlete": {"id": pid, "displayName": f"P{pid}", "shortName": f"S{pid}"},
         "position": {"abbreviation": pos}, "subbedIn": sub_in, "subbedOut": sub_out}
    if stats is not None:
        d["stats"] = stats
    if place is not None:
        d["formationPlace"] = str(place)
    return d


PAST = {
    "header": {"competitions": [{"date": "2026-09-13T14:00Z", "competitors": [
        {"id": "10", "homeAway": "home", "score": "2", "team": {"id": "10", "displayName": "우리팀"}},
        {"id": "20", "homeAway": "away", "score": {"value": 1}, "team": {"id": "20", "displayName": "상대팀"}},
    ]}]},
    "keyEvents": [
        {"type": {"text": "Substitution"}, "clock": {"displayValue": "67'"},
         "participants": [{"athlete": {"id": "12"}}, {"athlete": {"id": "3"}}]},
        {"type": {"text": "Substitution"}, "clock": {"displayValue": "90'+3'"},
         "participants": [{"athlete": {"id": "4"}}, {"athlete": {"id": "13"}}]},
    ],
    "rosters": [{
        "homeAway": "home", "team": {"id": "10"},
        "roster": [
            P("1", True, "G", stats=[{"name": "totalGoals", "value": 0}]),
            P("2", True, "CD-L", stats=[{"name": "totalGoals", "value": 1}, {"name": "goalAssists", "value": 0}]),
            P("3", True, "CM", sub_out=True, stats=[{"name": "goalAssists", "value": 1}]),
            P("4", True, "F", sub_out=True, stats=[{"name": "totalGoals", "value": 1}, {"name": "goalAssists", "value": 1}]),
            P("12", False, "M", sub_in=True, stats=[]),
            P("13", False, "F", sub_in=True, stats=[]),
            P("14", False, "D", stats=[]),
        ],
    }],
}


def test_minutes_from_substitutions():
    rec = parse.parse_soccer_match_for_team(PAST, "10")
    assert rec["minutes"]["1"] == 90
    assert rec["minutes"]["3"] == 67      # 67분 교체 아웃
    assert rec["minutes"]["12"] == 23     # 67분 교체 인 -> 23분
    assert rec["minutes"]["4"] == 90      # 90+3분 아웃은 90분으로 본다
    assert rec["minutes"]["13"] == 1      # 추가시간 투입은 최소 1분
    assert "14" not in rec["minutes"]     # 미출전


def test_goals_assists_and_result():
    rec = parse.parse_soccer_match_for_team(PAST, "10")
    assert rec["goals"] == {"2": 1, "4": 1}
    assert rec["assists"] == {"3": 1, "4": 1}
    assert rec["opp"] == "상대팀" and rec["home"] is True
    assert (rec["gf"], rec["ga"], rec["res"]) == (2, 1, "W")
    assert rec["date"].startswith("2026-09-13")


def test_goals_fallback_to_key_events_when_no_stats():
    summary = {
        "keyEvents": [
            {"scoringPlay": True, "type": {"text": "Goal"},
             "participants": [{"athlete": {"id": "7"}}, {"athlete": {"id": "8"}}]},
            {"scoringPlay": True, "type": {"text": "Own Goal"},
             "participants": [{"athlete": {"id": "7"}}]},
        ],
        "rosters": [{"team": {"id": "1"}, "roster": [P("7", True), P("8", True)]}],
    }
    rec = parse.parse_soccer_match_for_team(summary, "1")
    assert rec["goals"] == {"7": 1}          # 자책골은 빼고
    assert rec["assists"] == {"8": 1}


def test_unknown_sub_minute_is_none_not_guess():
    summary = {"rosters": [{"team": {"id": "1"}, "roster": [
        P("1", True, sub_out=True), P("2", False, sub_in=True)]}]}
    rec = parse.parse_soccer_match_for_team(summary, "1")
    assert rec["minutes"]["1"] is None and rec["minutes"]["2"] is None
    total, complete = minutes_total([{"played": True, "min": None}, {"played": True, "min": 90}])
    assert total == 90 and complete is False


def test_lineup_has_place_and_short():
    summary = {"rosters": [{"homeAway": "home", "team": {"id": "1"}, "formation": "4-3-3",
                            "roster": [P("9", True, "F", place=9)]}]}
    side = parse.parse_soccer_lineup(summary)["home"]
    assert side["lineup"][0]["place"] == 9 and side["lineup"][0]["short"] == "S9"


def test_parse_formation():
    assert parse_formation("4-2-3-1") == [4, 2, 3, 1]
    assert parse_formation("") == [] and parse_formation("4-x-3") == []


def test_formation_rows_433():
    pos = ["G", "RB", "CD-R", "CD-L", "LB", "DM", "CM-R", "CM-L", "RW", "F", "LW"]
    lineup = [{"pos": p, "place": i + 1} for i, p in enumerate(pos)]
    rows = formation_rows(lineup, "4-3-3", parse.position_shape)
    named = [[pos[i] for i in row] for row in rows]
    assert named[0] == ["G"]
    assert named[1] == ["LB", "CD-L", "CD-R", "RB"]     # 왼쪽 -> 오른쪽
    assert sorted(named[2]) == sorted(["DM", "CM-R", "CM-L"])
    assert named[3] == ["LW", "F", "RW"]
    assert sum(len(r) for r in rows) == 11


def test_formation_rows_without_formation_groups_by_depth():
    lineup = [{"pos": p} for p in ["G", "D", "D", "M", "F"]]
    rows = formation_rows(lineup, "", parse.position_shape)
    assert [len(r) for r in rows] == [1, 2, 1, 1]


def test_formation_rows_bad_data_does_not_crash():
    assert formation_rows([], "4-4-2", parse.position_shape) == []
    rows = formation_rows([{"pos": ""}, {"pos": "??"}], "4-4-2", parse.position_shape)
    assert sum(len(r) for r in rows) == 2


HIST = [
    {"date": "d1", "opp": "A", "home": True, "gf": 2, "ga": 0, "res": "W",
     "starters": ["x", "y"], "played": ["x", "y", "z"], "names": {"x": "엑스", "y": "와이", "z": "지"},
     "minutes": {"x": 90, "y": 70, "z": 20}, "goals": {"x": 2}, "assists": {"y": 1}},
    {"date": "d2", "opp": "B", "home": False, "gf": 1, "ga": 1, "res": "D",
     "starters": ["x", "z"], "played": ["x", "z"], "names": {},
     "minutes": {"x": 90, "z": 90}, "goals": {"z": 1}, "assists": {"x": 1, "y": 1}},
]


def test_player_log_and_form():
    log = player_log(HIST, "y")
    assert [r["min"] for r in log] == [70, 0]
    assert log[0]["started"] and not log[1]["played"]
    assert minutes_total(log) == (70, True)
    form = team_form(HIST)
    assert [f["res"] for f in form] == ["W", "D"] and form[1]["opp"] == "B"


def test_team_leaders_ties():
    assert team_leaders(HIST, "goals") == [{"name": "엑스", "n": 2}]
    assert team_leaders(HIST, "assists") == [{"name": "와이", "n": 2}]
    assert team_leaders([], "goals") == []


def test_enrich_adds_everything():
    lineup = [{"id": "x", "name": "엑스", "pos": "F", "short": "X"}, {"id": "z", "name": "지", "pos": "G"}]
    res = analyze_lineup(lineup, HIST, 2)
    enrich(res, lineup, HIST, formation="1", shape=parse.position_shape)
    px = res["players"][0]
    assert px["minutes"] == 180 and px["goals"] == 2 and px["assists"] == 1 and px["short"] == "X"
    assert res["leaders"]["goals"][0]["name"] == "엑스"
    assert res["rows"][0] == [1]  # 골키퍼(지)가 첫 줄
    assert len(res["form"]) == 2


def test_mlb_team_results():
    data = {"dates": [{"games": [
        {"gameDate": "2026-09-19T23:00Z", "status": {"abstractGameState": "Final"},
         "teams": {"home": {"team": {"id": 147, "name": "Yankees"}, "score": 5},
                   "away": {"team": {"id": 111, "name": "Red Sox"}, "score": 3}}},
        {"gameDate": "2026-09-20T23:00Z", "status": {"abstractGameState": "Final"},
         "teams": {"home": {"team": {"id": 111, "name": "Red Sox"}, "score": 4},
                   "away": {"team": {"id": 147, "name": "Yankees"}, "score": 2}}},
        {"gameDate": "2026-09-21T23:00Z", "status": {"abstractGameState": "Preview"},
         "teams": {"home": {"team": {"id": 147}}, "away": {"team": {"id": 1}}}},
    ]}]}
    out = parse.parse_mlb_team_results(data, 147)
    assert [r["res"] for r in out] == ["L", "W"]     # 최신순, 예정 경기 제외
    assert out[0]["opp"] == "Red Sox" and out[0]["home"] is False


def test_basketball_minutes_points():
    box = {"header": {"competitions": [{"competitors": [
        {"homeAway": "home", "score": "110", "team": {"id": "1", "displayName": "H"}},
        {"homeAway": "away", "score": "100", "team": {"id": "2", "displayName": "A"}}]}]},
        "boxscore": {"players": [{"team": {"id": "1"}, "statistics": [{
            "names": ["MIN", "PTS", "AST"],
            "athletes": [
                {"starter": True, "athlete": {"id": "a"}, "stats": ["36", "30", "8"]},
                {"starter": False, "athlete": {"id": "b"}, "stats": ["12:30", "4", "--"]},
                {"didNotPlay": True, "athlete": {"id": "c"}, "stats": []}]}]}]}}
    rec = parse.parse_basketball_box_for_team(box, "1")
    assert rec["minutes"] == {"a": 36, "b": 12}
    assert rec["goals"] == {"a": 30, "b": 4} and rec["assists"] == {"a": 8}
    assert rec["res"] == "W" and rec["opp"] == "A"


def test_bench_parsed_and_status():
    summary = {"rosters": [{"homeAway": "home", "team": {"id": "1"}, "formation": "",
                            "roster": [P("x", True, "F"), P("z", False, "G"), P("q", False, "M")]}]}
    side = parse.parse_soccer_lineup(summary)["home"]
    assert [b["id"] for b in side["bench"]] == ["z", "q"]
    # 라인업이 없으면 벤치도 비운다
    none = parse.parse_soccer_lineup({"rosters": [{"homeAway": "home", "team": {"id": "1"}, "roster": [P("z", False)]}]})
    assert none["home"]["bench"] == []

    from grade import add_bench
    lineup = [{"id": "x", "name": "엑스", "pos": "F"}]
    res = analyze_lineup(lineup, HIST, 2)          # 주전: x, z / y는 빠짐
    enrich(res, lineup, HIST)
    add_bench(res, [{"id": "z", "name": "지"}, {"id": "q", "name": "큐"}], HIST)
    st = {m["name"]: m["status"] for m in res["missing"]}
    assert st == {"지": "벤치"}
    bz = next(b for b in res["bench"] if b["id"] == "z")
    assert bz["core"] is True and bz["minutes"] == 110 and bz["goals"] == 1
    bq = next(b for b in res["bench"] if b["id"] == "q")
    assert bq["role"] == "신규·복귀" and bq["apps"] == 0
    # 명단 정보가 없으면 구분하지 않는다
    res2 = analyze_lineup(lineup, HIST, 2); add_bench(res2, [], HIST)
    assert all(m["status"] is None for m in res2["missing"])


def test_strength_metrics():
    from grade import strength
    lineup = [{"id": "x"}, {"id": "y"}]
    res = analyze_lineup(lineup, HIST, 2)   # 주전(선발 많은 순) x(2), 그다음 z/y 중 선발 1회
    strength(res, HIST)
    s = res["strength"]
    assert s["team_goals"] == 3 and s["team_assists"] == 3
    assert s["goal_share"] == 67          # x 2골 / 팀 3골
    assert s["assist_share"] == 100       # x 1 + y 2 / 3
    assert 0 < s["retain"] <= 100
    empty = analyze_lineup([{"id": "a"}], [], 11); strength(empty, [])
    assert empty["strength"]["retain"] is None and empty["strength"]["goal_share"] is None


def _team(grade, core_in, size, missing, goals=0, form=None):
    return {"grade": grade, "core_in": core_in, "size": size, "missing": missing,
            "strength": {"team_goals": goals}, "form": form or []}


def test_summarize_rotated_team():
    from grade import summarize
    miss = [{"name": "음바페", "goals": 8, "assists": 3, "starts": 6, "status": "벤치"},
            {"name": "벨링엄", "goals": 2, "assists": 2, "starts": 5, "status": "명단 제외"},
            {"name": "추아메니", "goals": 0, "assists": 0, "starts": 5, "status": "벤치"}]
    form = [{"res": "W"}, {"res": "D"}, {"res": "L"}, {"res": "W"}, {"res": "D"}]
    s = summarize("레알", "헤타페", {"home": _team("2군", 4, 11, miss, 14), "away": _team("1군", 10, 11, [{"name": "x"}], form=form)})
    assert s["focus"] == "home"
    assert s["headline"] == "레알, 음바페·벨링엄 빼고 2군 가동"
    assert s["insight"] == "레알 주전 3명 빠짐 · 음바페 벤치"
    assert s["points"][0] == ["레알 주전 11명 중 3명이 선발에서 빠짐", "벤치 대기 2 · 명단 제외 1"]
    assert s["points"][1][0] == "팀 14골 중 10골(71%) 넣은 선수가 선발에 없음"
    assert s["points"][2] == ["헤타페는 주전 10명 그대로", "최근 5경기 2승 2무 1패"]


def test_summarize_both_best_and_missing_grade():
    from grade import summarize
    s = summarize("A", "B", {"home": _team("1군", 11, 11, []), "away": _team("1군", 11, 11, [])})
    assert s["headline"] == "양 팀 모두 베스트 라인업" and s["points"] == []
    s2 = summarize("A", "B", {"home": _team("1군", 10, 11, [{"name": "손", "goals": 3, "starts": 6}]), "away": _team("1군", 11, 11, [])})
    assert s2["headline"] == "양 팀 사실상 베스트, A 손 제외"
    assert summarize("A", "B", {"home": _team("판단불가", 0, 11, []), "away": _team("1군", 11, 11, [])})["headline"] == ""


def test_summarize_baseball_skips_goal_point():
    from grade import summarize
    s = summarize("다저스", "파드리스", {"home": _team("1군", 8, 9, [{"name": "콘포르토", "starts": 10}]),
                                        "away": _team("1.5군", 6, 9, [{"name": "a", "starts": 10}, {"name": "b", "starts": 10}, {"name": "c", "starts": 10}])}, "야구")
    assert s["focus"] == "away" and s["headline"].startswith("파드리스 1.5군")
    assert all("골" not in p[0] for p in s["points"])


def test_missing_gets_goals():
    lineup = [{"id": "x", "name": "엑스"}]
    res = analyze_lineup(lineup, HIST, 2)
    enrich(res, lineup, HIST)
    z = next(m for m in res["missing"] if m["id"] == "z")
    assert z["goals"] == 1 and z["assists"] == 0



def _hist(n, starters_by_match, res="W"):
    out = []
    for i in range(n):
        st = starters_by_match(i)
        out.append({"starters": st, "played": st, "names": {}, "minutes": {p: 90 for p in st},
                    "goals": {}, "assists": {}, "res": res, "gf": 2, "ga": 1})
    return out


def test_periods_trend_and_record():
    from grade import add_periods, aggregate, form_trend, season_record
    # 시즌 12경기: 'a'는 앞 6경기만 선발(최근 밀림), 'b'는 뒤 6경기만 선발(최근 도약), 'c'는 전부 선발
    season = _hist(12, lambda i: ["c", "b"] if i < 6 else ["c", "a"])   # 최신순: 0~5가 최근
    recent = season[:6]
    assert aggregate(season, "a")["starts"] == 6 and aggregate(recent, "a")["starts"] == 0
    assert form_trend(aggregate(recent, "a"), aggregate(season, "a")) == "down"
    assert form_trend(aggregate(recent, "b"), aggregate(season, "b")) == "up"
    assert form_trend(aggregate(recent, "c"), aggregate(season, "c")) == ""
    # 시즌이 최근과 같은 길이면 변화 표시 안 함
    assert form_trend(aggregate(recent, "b"), aggregate(recent, "b")) == ""
    rec = season_record(season)
    assert (rec["w"], rec["gf"], rec["ga"], rec["ppg"]) == (12, 24, 12, 3.0)
    assert season_record([])["ppg"] is None

    lineup = [{"id": "c"}, {"id": "a"}]
    res = analyze_lineup(lineup, recent, 2)
    add_periods(res, recent, season)
    pa = next(p for p in res["players"] if p["id"] == "a")
    assert pa["recent"]["starts"] == 0 and pa["season"]["starts"] == 6 and pa["trend"] == "down"
    assert res["season_core_in"] == 2    # 시즌 기준 주전(c 12, a/b 6 → 이름순 a)
    assert res["season_matches"] == 12



def test_pick_ace_prefers_combined_form_and_reports_status():
    from grade import ace_score, pick_ace
    def P(pid, name, s, r):
        return {"id": pid, "name": name, "season": s, "recent": r}
    per = lambda n, g, a, m: {"matches": n, "goals": g, "assists": a, "minutes": m, "apps": n}
    scorer = P("s", "득점왕", per(12, 10, 2, 1000), per(6, 5, 1, 520))
    cold = P("c", "시즌만", per(12, 8, 2, 1000), per(6, 0, 0, 90))
    hot = P("h", "최근만", per(12, 2, 0, 400), per(6, 4, 1, 500))
    assert ace_score(scorer) > ace_score(hot) > ace_score(cold)
    res = {"players": [cold, hot], "bench": [], "missing": [dict(scorer, status="벤치")]}
    ace = pick_ace(res)
    assert ace["name"] == "득점왕" and ace["status"] == "벤치"
    res2 = {"players": [cold, hot], "bench": [], "missing": [dict(scorer, status="명단 제외")]}
    assert pick_ace(res2)["status"] == "명단 제외"
    assert pick_ace({"players": [hot], "bench": [], "missing": []})["status"] == "선발"
    assert pick_ace({"players": [], "bench": [], "missing": []}) is None
    # 한 경기도 안 뛴 선수는 후보가 아님
    ghost = P("g", "유령", per(0, 0, 0, 0), per(0, 0, 0, 0)); ghost["season"]["apps"] = 0
    assert pick_ace({"players": [ghost], "bench": [], "missing": []}) is None



def _mk(res_list, starters_fn, ga=1):
    out = []
    for i, r in enumerate(res_list):
        st = starters_fn(i)
        out.append({"starters": st, "played": st, "names": {}, "minutes": {p: 90 for p in st},
                    "goals": {}, "assists": {}, "res": r, "gf": 2 if r == "W" else 1, "ga": ga if r != "W" else 0})
    return out


def test_on_pitch_and_team_strength():
    from grade import on_pitch, team_strength
    season = _mk(["W", "W", "L", "D"], lambda i: ["a"] if i < 2 else ["b"])
    assert on_pitch(season, "a") == {"starts": 2, "ppg": 3.0, "ga_pg": 0.0, "pts": 6}
    assert on_pitch(season, "b")["ppg"] == 0.5
    assert on_pitch(season, "zz")["ppg"] is None
    # 경기 수가 적으면 리그 평균(1.37) 쪽으로 당겨진다
    assert team_strength({"matches": 2, "ppg": 3.0}) < 3.0
    assert abs(team_strength({"matches": 38, "ppg": 2.5}) - 2.5) < 0.15
    assert team_strength({"matches": 0, "ppg": None}) is None


def test_lineup_power_and_compare():
    import parse
    from grade import add_periods, compare_power, lineup_power, strength
    season = _mk(["W"] * 8 + ["D"] * 2, lambda i: ["g", "d1", "m1", "f1"])
    for m in season:
        m["goals"] = {"f1": 1}; m["assists"] = {"m1": 1}
    lineup = [{"id": x, "pos": pos} for x, pos in [("g", "G"), ("d1", "CD-L"), ("m1", "CM"), ("f1", "F")]]
    res = analyze_lineup(lineup, season[:6], 4)
    enrich(res, lineup, season[:6], season=season)
    strength(res, season[:6], season=season); add_periods(res, season[:6], season)
    lineup_power(res, season, shape=parse.position_shape)
    lp = res["lineup_power"]
    assert lp["totals"]["goals"] == 10 and lp["totals"]["assists"] == 10
    assert lp["top_scorer"] == [{"name": "", "n": 10}]
    assert set(lp["lines"]) == {"GK", "DF", "MF", "FW"}
    assert lp["lines"]["FW"]["goals"] == 10 and lp["back_ga_pg"] == 0.2
    assert lp["q"] == 1.0 and lp["today"] == lp["team_strength"]
    assert lp["xi_ppg"] == 2.6 and lp["xi_ppg_n"] == 4          # 8승 2무 = 26점 / 10경기
    # 선발 3경기 미만 선수만 있으면 계산하지 않는다
    few = analyze_lineup([{"id": "new", "pos": "F"}], season[:6], 1)
    few["season_record"] = {"ppg": 2.6, "matches": 10}
    lineup_power(few, season, shape=parse.position_shape)
    assert few["lineup_power"]["xi_ppg"] is None and few["lineup_power"]["xi_ppg_n"] == 0

    strong_b = {"grade": "2군", "lineup_power": {"today": 1.56}}
    weak_a = {"grade": "1군", "lineup_power": {"today": 1.59}}
    c = compare_power(strong_b, weak_a, "레알", "헤타페")
    assert c["verdict"] == "비슷한 전력" and c["home"] + c["away"] == 100
    assert c["note"] == "레알은 로테이션을 했지만 체급 덕에 비슷한 수준"
    c2 = compare_power({"grade": "2군", "lineup_power": {"today": 2.3}}, {"grade": "1군", "lineup_power": {"today": 1.4}}, "레알", "헤타페")
    assert c2["fav"] == "home" and c2["verdict"] == "레알 우위" and "2군이지만" in c2["note"]
    c3 = compare_power({"grade": "1군", "lineup_power": {"today": 1.8}}, {"grade": "1군", "lineup_power": {"today": 1.5}}, "A", "B")
    assert c3["verdict"] == "A 근소 우위"
    assert compare_power({"lineup_power": {}}, {"lineup_power": {"today": 1}}, "A", "B") is None
    # 리그 보정
    c4 = compare_power({"grade": "1군", "lineup_power": {"today": 2.0}}, {"grade": "1군", "lineup_power": {"today": 2.0}}, "A", "B", 1.0, 0.8)
    assert c4["home"] > 50 and c4["league_adjusted"] is True



def test_josa():
    from grade import josa
    assert josa("레알", "은", "는") == "레알은"
    assert josa("헤타페", "은", "는") == "헤타페는"
    assert josa("토트넘", "은", "는") == "토트넘은"
    assert josa("브라이튼", "은", "는") == "브라이튼은"
    assert josa("", "은", "는") == "는"



WORLD_TSV = "1\t1\tES\t2259\t1\t2259\n1\t2\tAR\t2173\n19\t22\tKR\t1805\n3\t44\tJP\t1888\nbad line\n"
TEAMS_TSV = "ES\tSpain\nAR\tArgentina\nKR\tSouth Korea\tKorea\nKR_loc\tin South Korea\nJP\tJapan\nCI\tIvory Coast\tCôte d'Ivoire\n"


def test_elo_parsing_and_name_matching():
    world = parse.parse_elo_world(WORLD_TSV)
    assert world == {"ES": 2259, "AR": 2173, "KR": 1805, "JP": 1888}
    names = parse.parse_elo_names(TEAMS_TSV)
    assert names["south korea"] == "KR" and "in south korea" not in names
    assert parse.elo_for("South Korea", world, names) == (1805, "KR")
    assert parse.elo_for("Korea Republic", world, names) == (1805, "KR")     # ESPN/FIFA 표기
    assert parse.elo_for("Japan", world, names)[0] == 1888
    assert parse.nation_key("Côte d'Ivoire") == "ivory coast"
    assert parse.elo_for("Atlantis", world, names) == (None, None)


def test_compare_elo_rotation_and_home():
    from grade import compare_elo
    full = {"grade": "1군", "lineup_power": {"q": 1.0}}
    b_team = {"grade": "2군", "lineup_power": {"q": 0.4}}
    same = compare_elo(dict(full), dict(full), "A", "B", 1800, 1800)
    assert same["home"] == 50 and same["verdict"] == "비슷한 전력"
    # 홈 이점 100점 → 약 64:36
    assert compare_elo(dict(full), dict(full), "A", "B", 1800, 1800, home_adv=100)["home"] == 64
    # 강팀이 2군이면 격차가 줄어든다
    strong_full = compare_elo(dict(full), dict(full), "스페인", "한국", 2259, 1805)["home"]
    h = dict(b_team); a = dict(full)
    strong_b = compare_elo(h, a, "스페인", "한국", 2259, 1805)
    assert strong_b["home"] < strong_full and strong_b["home"] > 50
    assert h["lineup_power"]["today_elo"] == 2259 - 120 and h["lineup_power"]["elo"] == 2259
    assert strong_b["mode"] == "elo"
    assert compare_elo(dict(full), dict(full), "A", "B", None, 1800) is None


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"  ok  {name}")
    print(f"\n{n} passed")
