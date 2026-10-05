"""네이버 K리그 경로: 6차 소스 점검에서 확인한 실제 응답 모양 그대로 가짜 데이터를 만들어 검증."""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect  # noqa: E402
import parse  # noqa: E402
from parse import KST  # noqa: E402

NOW = datetime(2026, 9, 27, 18, 20, tzinfo=KST)          # 강원 vs 인천 19:00 킥오프 40분 전
HOME_REG = [f"1{i:02d}" for i in range(1, 12)]            # 강원 주전
HOME_SUB = [f"1{i:02d}" for i in range(20, 29)]           # 강원 벤치 자원
AWAY_REG = [f"2{i:02d}" for i in range(1, 12)]
POS = ["GK", "DF", "DF", "DF", "DF", "MF", "MF", "MF", "MF", "FW", "FW"]


def rows_of(ids):
    """4-4-2 줄 묶음 (골키퍼 줄부터) — 네이버 K리그 모양"""
    ps = [{"playerId": pid, "positionOrder": i + 1, "shirtNumber": str(i + 1), "goal": 0, "assists": 0,
           "name": f"선수{pid}", "pos": POS[i], "changed": False} for i, pid in enumerate(ids)]
    return [ps[0:1], ps[1:5], ps[5:9], ps[9:11]]


def lineup_body(home_ids, away_ids, home_bench=(), away_bench=()):
    bench = lambda ids: [{"playerId": p, "name": f"선수{p}", "pos": "MF", "shirtNumber": "30", "positionOrder": 0} for p in ids]
    return {"result": {"lineUpData": {
        "lineup": {"home": {"players": rows_of(home_ids), "formation": "4-4-2", "row": 3},
                   "away": {"players": rows_of(away_ids), "formation": "4-4-2", "row": 3}},
        "substitution": {"home": bench(home_bench), "away": bench(away_bench)},
        "changedPlayer": {"home": [], "away": []}}}}


def record_body(home_ids, away_ids, goals=None):
    goals = goals or {}
    stat = lambda pid, mins: {"playerId": pid, "playerName": f"선수{pid}", "position": "MF", "goals": goals.get(pid, 0),
                              "assists": 1 if pid == "110" else 0, "playerPoint": 7.1, "workTime": mins}
    return {"result": {"recordData": {
        "homePlayerStats": [stat(p, 97 if p != "109" else 64) for p in home_ids] + [stat("120", 33)],
        "awayPlayerStats": [stat(p, 90) for p in away_ids]}}}


def game_row(gid, dt, home, away, hs=None, as_=None, status="RESULT", cat="kleague"):
    return {"gameId": gid, "categoryId": cat, "categoryName": "K리그1", "gameDate": dt[:10], "gameDateTime": dt,
            "homeTeamCode": home[0], "homeTeamName": home[1], "homeTeamScore": hs, "awayTeamCode": away[0],
            "awayTeamName": away[1], "awayTeamScore": as_, "statusCode": status, "cancel": False,
            "homeTeamEmblemUrl": "", "awayTeamEmblemUrl": ""}


GW, IC = ("21", "강원"), ("18", "인천")


class NaverFake:
    def __init__(self, lineup_out=True):
        self.count = 0
        self.lineup_out = lineup_out
        self.out_of_time = False

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        p = params or {}
        if url.endswith("/schedule/games"):
            frm, to = p["fromDate"], p["toDate"]
            games = []
            if frm <= "2026-09-27" <= to:
                games.append(game_row("NEXT", "2026-09-27T19:00:00", GW, IC, status="BEFORE"))
                games.append(game_row("OTHERLEAGUE", "2026-09-27T14:00:00", ("9", "X"), ("8", "Y"), status="BEFORE", cat="jleague"))
            for i in range(1, 9):                       # 지난 8경기 (4일 간격)
                d = (NOW - timedelta(days=4 * i)).strftime("%Y-%m-%dT19:00:00")
                if frm <= d[:10] <= to:
                    games.append(game_row(f"P{i}", d, GW if i % 2 else IC, IC if i % 2 else GW,
                                          2 if i % 3 else 0, 1))
            return {"result": {"games": games}}
        if url.endswith("/NEXT/lineup"):
            if not self.lineup_out:
                return {"result": {"lineUpData": {}}}
            # 강원은 주전 4명만(2군), 인천은 풀주전
            return lineup_body(HOME_REG[:4] + HOME_SUB[:7], AWAY_REG, home_bench=HOME_REG[4:9], away_bench=[])
        if "/lineup" in url:
            gid = url.split("/schedule/games/")[1].split("/")[0]
            i = int(gid[1:])
            home, away = (HOME_REG, AWAY_REG) if i % 2 else (AWAY_REG, HOME_REG)
            return lineup_body(home, away)
        if "/record" in url:
            gid = url.split("/schedule/games/")[1].split("/")[0]
            i = int(gid[1:])
            home, away = (HOME_REG, AWAY_REG) if i % 2 else (AWAY_REG, HOME_REG)
            return record_body(home, away, goals={"111": 1})
        return None


CFG = {"mlb_enabled": False, "espn_leagues": [],
       "naver_leagues": [{"sport": "축구", "upper": "kfootball", "category": "kleague", "name": "K리그1"},
                         {"sport": "축구", "upper": "kfootball", "category": "kleague2", "name": "K리그2"}],
       "naver_season_start": "2026-08-01"}


def run_with(fake):
    tmp = tempfile.mkdtemp()
    collect.DATA_DIR = os.path.join(tmp, "data")
    collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
    collect.CACHE_DIR = os.path.join(tmp, "cache")
    collect.ARCHIVE_DIR = os.path.join(tmp, "archive")
    collect.Client = lambda verbose=False: fake
    collect.kst_now = lambda: NOW
    collect.load_config = lambda: CFG
    assert collect.run() == 0
    with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
        idx = json.load(f)
    path = os.path.join(collect.GAME_DIR, collect.game_filename("축구:naver.kleague:NEXT"))
    det = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    return tmp, idx, det


def test_parse_naver_games_and_lineup():
    games = parse.parse_naver_games({"result": {"games": [
        game_row("A", "2026-09-27T19:00:00", GW, IC, status="BEFORE"),
        game_row("B", "2026-09-27T14:00:00", GW, IC, status="RESULT", cat="jleague"),
        dict(game_row("C", "2026-09-27T16:00:00", GW, IC, status="BEFORE"), cancel=True)]}}, {"kleague": "K리그1"})
    assert [g["event_id"] for g in games] == ["A"]          # 다른 리그·취소 경기는 뺀다
    g = games[0]
    assert g["start_kst"].startswith("2026-09-27T19:00:00+09:00") and g["state"] == "pre"
    assert g["home"]["name"] == "강원" and g["source"] == "naver" and g["league"] == "K리그1"
    lu = parse.parse_naver_lineup(lineup_body(HOME_REG, AWAY_REG, home_bench=["150"]))
    assert len(lu["home"]["lineup"]) == 11 and lu["home"]["rows"] == [[0], [1, 2, 3, 4], [5, 6, 7, 8], [9, 10]]
    assert lu["home"]["lineup"][0]["pos"] == "GK" and lu["home"]["formation"] == "4-4-2"
    assert [b["id"] for b in lu["home"]["bench"]] == ["150"]
    # 유럽 경기 모양({"lineup": [[…]]})도 읽는다
    eu = {"result": {"lineUpData": {"lineup": {"home": {"players": {"lineup": rows_of(HOME_REG)}, "formation": "4231"}}}}}
    assert len(parse.parse_naver_lineup(eu)["home"]["lineup"]) == 11
    assert parse.parse_naver_lineup({}) == {"home": {"lineup": [], "rows": [], "formation": "", "bench": []},
                                            "away": {"lineup": [], "rows": [], "formation": "", "bench": []}}


def test_parse_naver_record_minutes_goals_rating():
    rec = parse.parse_naver_record(record_body(HOME_REG, AWAY_REG, goals={"111": 1}), lineup_body(HOME_REG, AWAY_REG), "home")
    assert rec["starters"] == HOME_REG
    assert rec["minutes"]["101"] == 90 and rec["minutes"]["109"] == 64     # 97분은 90으로, 교체 아웃 64분
    assert "120" in rec["played"] and rec["minutes"]["120"] == 33           # 교체 투입
    assert rec["goals"] == {"111": 1} and rec["assists"] == {"110": 1}
    assert rec["rating"]["101"] == 7.1
    # 선수 기록이 없으면 라인업 골로 대신하고 출전시간은 90분으로 본다
    lb = lineup_body(HOME_REG, AWAY_REG)
    lb["result"]["lineUpData"]["lineup"]["home"]["players"][3][0]["goal"] = 2
    rec2 = parse.parse_naver_record({}, lb, "home")
    assert rec2["goals"] == {"110": 2} and rec2["minutes"]["101"] == 90


def test_naver_full_flow():
    tmp, idx, d = run_with(NaverFake())
    try:
        keys = [g["key"] for g in idx["games"]]
        assert "축구:naver.kleague:NEXT" in keys and not any("OTHERLEAGUE" in k for k in keys)
        row = next(g for g in idx["games"] if g["key"] == "축구:naver.kleague:NEXT")
        assert row["home"] == "강원" and row["lineup_ready"] is True
        H, A = d["teams"]["home"], d["teams"]["away"]
        assert H["grade"] == "2군" and H["core_in"] == 4 and A["grade"] == "1군"
        assert H["matches"] == 6 and H["season_matches"] == 8
        assert H["rows"] == [[0], [1, 2, 3, 4], [5, 6, 7, 8], [9, 10]] and H["formation"] == "4-4-2"
        # 벤치/명단 제외 구분
        st = {m["id"]: m["status"] for m in H["missing"]}
        assert st["105"] == "벤치" and st["110"] == "명단 제외"
        # 출전시간은 네이버 출전시간 그대로 (97분→90), 시즌 골·도움
        p1 = next(p for p in A["players"] if p["id"] == "201")
        assert p1["season"]["minutes"] == 8 * 90
        miss110 = next(m for m in H["missing"] if m["id"] == "110")
        assert miss110["assists"] == 8
        # 에이스·전력·요약
        assert H["ace"] and H["ace"]["status"] in ("벤치", "명단 제외")
        assert d["power"] and d["power"]["home"] + d["power"]["away"] == 100
        assert d["power"]["mode"] == "model" and d["power"]["basis"]["home_edge"] < 0     # K리그: 홈 이점이 가장 작은 리그
        assert d["summary"]["headline"].startswith("강원")
        assert H["season_record"]["matches"] == 8
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_naver_lineup_not_out():
    tmp, idx, d = run_with(NaverFake(lineup_out=False))
    try:
        row = next(g for g in idx["games"] if g["key"] == "축구:naver.kleague:NEXT")
        assert row["lineup_ready"] is False and d["lineup_ready"] is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"  ok  {name}")
    print(f"\n{n} passed")
