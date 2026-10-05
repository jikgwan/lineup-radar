"""KBO(네이버): 8차 소스 점검에서 확인한 응답 모양 그대로 가짜 데이터를 만들어 검증."""

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

NOW = datetime(2026, 9, 23, 17, 50, tzinfo=KST)          # 18:30 경기 40분 전
LG = [f"L{i}" for i in range(1, 10)]                     # LG 주전 타자
LGB = [f"Lb{i}" for i in range(1, 6)]                    # LG 백업
HH = [f"H{i}" for i in range(1, 10)]
POS = ["중견수", "유격수", "좌익수", "지명타자", "1루수", "3루수", "우익수", "2루수", "포수"]


def row(gid, dt, home, away, hs=None, as_=None, status="RESULT"):
    return {"gameId": gid, "categoryId": "kbo", "categoryName": "KBO리그", "gameDateTime": dt, "gameDate": dt[:10],
            "homeTeamCode": home[0], "homeTeamName": home[1], "homeTeamScore": hs, "awayTeamCode": away[0], "awayTeamName": away[1],
            "awayTeamScore": as_, "statusCode": status, "cancel": False}


T_LG, T_HH = ("LG", "LG"), ("HH", "한화")


def past_games():
    out = []
    for i in range(1, 15):                                   # 하루 1경기씩 14일
        d = (NOW - timedelta(days=i)).strftime("%Y-%m-%dT18:30:00")
        home, away = (T_LG, T_HH) if i % 2 else (T_HH, T_LG)
        out.append(row(f"P{i}", d, home, away, 5 if i % 3 else 2, 3))
    return out


def lineup_entries(ids, starter_name, starter_code):
    return [{"positionName": "선발투수", "playerCode": starter_code, "playerName": starter_name, "batsThrows": "우투"}] + \
           [{"positionName": POS[i], "playerCode": pid, "playerName": f"선수{pid}", "batsThrows": "좌타" if i % 3 == 0 else "우타", "backnum": str(i + 1)}
            for i, pid in enumerate(ids)]


def preview(lineup_out=True):
    lg_today = LG[:6] + LGB[:3]                              # LG는 주전 6명만 → 1.5군
    return {"result": {"previewData": {
        "homeTeamLineUp": {"fullLineUp": lineup_entries(lg_today, "임찬규", "SP_LG") if lineup_out else [], "pitcherBullpen": [], "batterCandidate": []},
        "awayTeamLineUp": {"fullLineUp": lineup_entries(HH, "류현진", "SP_HH") if lineup_out else [], "pitcherBullpen": [], "batterCandidate": []},
        "homeStarter": {"playerInfo": {"name": "임찬규", "pCode": "SP_LG", "hitType": "우완투수"},
                        "currentSeasonStats": {"era": "3.10", "whip": "1.18", "inn": "150.1", "gameCount": 26, "kk": 120, "bb": 40, "hr": 12}},
        "awayStarter": {"playerInfo": {"name": "류현진", "pCode": "SP_HH", "hitType": "좌완투수"},
                        "currentSeasonStats": {"era": "4.40", "whip": "1.35", "inn": "130.0", "gameCount": 24, "kk": 100, "bb": 35, "hr": 15}},
        "homeStandings": {"era": "4.05", "rank": 2}, "awayStandings": {"era": "4.60", "rank": 6}}}}


def record(i, home_code):
    """지난 경기: 두 팀 모두 주전 9명이 1~9번, 교체 타자 1명. 선발투수는 5일마다 같은 투수."""
    def bats(ids):
        out = [{"batOrder": o + 1, "playerCode": pid, "name": f"선수{pid}", "pos": "중"} for o, pid in enumerate(ids)]
        out.append({"batOrder": 3, "playerCode": "SUB", "name": "대타", "pos": "타"})     # 대타는 선발 아님
        return out
    def pits(team):
        sp = f"SP_{team}" if i % 5 == 1 else f"{team}_other{i}"
        return [{"pcode": sp, "name": sp, "inn": "6", "er": 2, "bf": 24}, {"pcode": f"{team}_R1", "inn": "1", "er": 0, "bf": 4},
                {"pcode": f"{team}_R2", "inn": "2", "er": 1, "bf": 9}]
    lg_side = "home" if home_code == "LG" else "away"
    hh_side = "away" if lg_side == "home" else "home"
    return {"result": {"recordData": {"battersBoxscore": {lg_side: bats(LG), hh_side: bats(HH)},
                                      "pitchersBoxscore": {lg_side: pits("LG"), hh_side: pits("HH")}}}}


class Fake:
    def __init__(self, lineup_out=True):
        self.count = 0
        self.out_of_time = False
        self.lineup_out = lineup_out

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        p = params or {}
        if url.endswith("/schedule/games"):
            gs = [g for g in past_games() if p["fromDate"] <= g["gameDate"] <= p["toDate"]]
            if p["fromDate"] <= NOW.strftime("%Y-%m-%d") <= p["toDate"]:
                gs.append(row("TODAY", NOW.strftime("%Y-%m-%dT18:30:00"), T_LG, T_HH, status="BEFORE"))
            return {"result": {"games": gs}} if p.get("upperCategoryId") == "kbaseball" else {"result": {"games": []}}
        if url.endswith("/TODAY/preview"):
            return preview(self.lineup_out)
        if url.endswith("/record"):
            gid = url.split("/schedule/games/")[1].split("/")[0]
            g = next(x for x in past_games() if x["gameId"] == gid)
            return record(int(gid[1:]), g["homeTeamCode"])
        return None


CFG = {"mlb_enabled": False, "espn_leagues": [], "naver_baseball_season_start": "2026-09-01",
       "naver_leagues": [{"sport": "야구", "upper": "kbaseball", "category": "kbo", "name": "KBO"}]}


def run_with(fake):
    tmp = tempfile.mkdtemp()
    collect.DATA_DIR = os.path.join(tmp, "data")
    collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
    collect.CACHE_DIR = os.path.join(tmp, "cache")
    collect.ARCHIVE_DIR = os.path.join(tmp, "archive")
    collect.Client = lambda verbose=False: fake
    collect.kst_now = lambda: NOW
    collect.load_config = lambda: CFG
    collect.run()
    idx = json.load(open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8"))
    path = os.path.join(collect.GAME_DIR, collect.game_filename("야구:naver.kbo:TODAY"))
    d = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    return tmp, idx, d


def test_parsers():
    pv = parse.parse_kbo_preview(preview(), "away")
    assert len(pv["lineup"]) == 9 and pv["lineup"][0]["bats"] == "L" and pv["lineup"][1]["bats"] == "R"
    sp = pv["sp"]
    assert sp["name"] == "류현진" and sp["hand"] == "L" and sp["era"] == 4.4 and sp["gs"] == 24 and sp["fip"] is not None
    assert pv["team_era"] == 4.6
    r = parse.parse_kbo_record(record(1, "LG"), "home")
    assert r["starters"] == LG and "SUB" in r["played"] and "SUB" not in r["starters"]
    assert r["sp"] == {"id": "SP_LG", "ip": 6.0, "er": 2, "pitches": 24} and r["pen_pitches"] == 13 and r["pen_ids"] == ["LG_R1", "LG_R2"]
    assert parse.parse_kbo_preview({}, "home") == {"lineup": [], "sp": None, "team_era": None}


def test_kbo_full_flow():
    tmp, idx, d = run_with(Fake())
    try:
        row_ = next(g for g in idx["games"] if g["key"] == "야구:naver.kbo:TODAY")
        assert row_["sport"] == "야구" and row_["home"] == "LG" and row_["lineup_ready"] is True
        H, A = d["teams"]["home"], d["teams"]["away"]
        assert H["grade"] == "1.5군" and H["core_in"] == 6 and A["grade"] == "1군"
        assert H["basis"] == "recent" and 0 < H["matches"] <= collect.KBO_RECENT        # 주전 판정은 최근 20경기까지
        sp = H["pitching"]["sp"]
        assert sp["name"] == "임찬규" and sp["hand"] == "R" and len(sp["recent"]) == 3        # 5일 간격 선발 3번
        assert sp["recent"][0]["date"] == (NOW - timedelta(days=1)).strftime("%Y-%m-%d") and sp["rest_days"] == 1
        pen = H["pitching"]["pen"]
        assert pen["pitches_3d"] == 3 * 13 and pen["b2b"] == 2 and "estimated" not in pen      # bf가 곧 투구수
        assert H["pitching"]["off_rpg"] is not None and H["pitching"]["bats"]["L"] == 3
        pw = d["power"]
        assert pw["mode"] == "mlb" and pw["home"] + pw["away"] == 100 and 5 < pw["exp_total"] < 14
        assert d["summary"]["headline"].endswith("선발 임찬규 vs 류현진")
        assert "rotation" in H and "goals" in H
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_kbo_lineup_not_out():
    tmp, idx, d = run_with(Fake(lineup_out=False))
    try:
        assert d["lineup_ready"] is False
        assert next(g for g in idx["games"] if g["key"] == "야구:naver.kbo:TODAY")["lineup_ready"] is False
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
