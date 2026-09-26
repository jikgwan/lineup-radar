"""풋몹 컵대회(일왕배): 12차 소스 점검에서 본 응답 모양 그대로 가짜 데이터로 검증."""

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

NOW = datetime(2026, 9, 23, 16, 20, tzinfo=KST)                 # 17:00 경기 40분 전
G_REG = [str(100 + i) for i in range(11)]                          # 감바 주전
G_SUB = [str(200 + i) for i in range(11)]                          # 감바 백업
T_REG = [str(300 + i) for i in range(11)]                          # 도쿠시마 주전
POS = [11, 32, 34, 36, 38, 64, 66, 77, 83, 105, 107]


def starters(ids, name_prefix):
    return [{"id": int(pid), "name": f"{name_prefix} {pid}", "positionId": POS[i], "usualPlayingPositionId": 0, "shirtNumber": str(i + 1)}
            for i, pid in enumerate(ids)]


def match_list(day):
    if day != NOW.strftime("%Y%m%d"):
        return {"leagues": []}
    return {"leagues": [
        {"ccode": "JPN", "id": 9011, "primaryId": 9011, "name": "Cup", "matches": [
            {"id": 6097138, "home": {"id": 6582, "score": 0, "name": "Gamba Osaka"}, "away": {"id": 162199, "score": 0, "name": "Tokushima Vortis"},
             "status": {"utcTime": "2026-09-23T08:00:00.000Z", "started": False, "cancelled": False, "finished": False}}]},
        {"ccode": "ENG", "id": 47, "primaryId": 47, "name": "Premier League", "matches": [{"id": 1, "home": {"id": 1, "name": "x"}, "away": {"id": 2, "name": "y"},
                                                                                           "status": {"utcTime": "2026-09-23T14:00:00.000Z"}}]}]}


def team_page(tid):
    fx = []
    for i in range(1, 9):
        d = (NOW - timedelta(days=4 * i)).astimezone().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        gf, ga = (2, 1) if tid == "6582" else (1, 1)                   # 감바가 더 센 팀
        fx.append({"id": int(f"{tid}{i}"), "home": {"id": int(tid), "name": "me", "score": gf}, "away": {"id": 999, "name": "opp", "score": ga},
                   "tournament": {"leagueId": 8974 if i <= 6 else 9011},
                   "status": {"finished": True, "utcTime": d}})
    fx.append({"id": 77, "home": {"id": int(tid), "name": "me"}, "away": {"id": 1, "name": "x"}, "status": {"finished": False, "utcTime": "2026-10-01T10:00:00.000Z"}})
    return {"fixtures": {"allFixtures": {"fixtures": fx}}}


class Fake:
    def __init__(self, confirmed=True):
        self.count = 0
        self.out_of_time = False
        self.confirmed = confirmed

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        p = params or {}
        if url.endswith("/api/data/matches"):
            return match_list(p["date"])
        if url.endswith("/api/data/teams"):
            return team_page(str(p["id"]))
        if url.endswith("/api/data/matchDetails"):
            mid = str(p["matchId"])
            if mid == "6097138":                                    # 오늘 경기: 감바는 주전 4명 + 백업 7명 (2군)
                return {"content": {"lineup": {"lineupType": "standard" if self.confirmed else "lastStarting11",
                        "homeTeam": {"id": 6582, "formation": "4-2-3-1", "starters": starters(G_REG[:4] + G_SUB[:7], "G"),
                                     "unavailable": [{"name": f"G {G_REG[9]}", "unavailability": {"type": "injury", "expectedReturn": "Mid October 2026"}},
                                                     {"name": f"G {G_REG[10]}", "unavailability": {"type": "suspension", "expectedReturn": "Late September 2026"}}]},
                        "awayTeam": {"id": 162199, "formation": "4-4-2", "starters": starters(T_REG, "T"), "unavailable": []}}}}
            tid = mid[:-1]
            ids = G_REG if tid == "6582" else T_REG
            return {"content": {"lineup": {"lineupType": "standard", "homeTeam": {"id": int(tid), "starters": starters(ids, "G" if tid == "6582" else "T")},
                                           "awayTeam": {"id": 999, "starters": starters([str(900 + i) for i in range(11)], "O")}}}}
        return None


CFG = {"mlb_enabled": False, "espn_leagues": [], "fotmob_leagues": [{"id": 9011, "name": "일왕배"}]}


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
    path = os.path.join(collect.GAME_DIR, collect.game_filename("축구:fotmob.9011:6097138"))
    return tmp, idx, json.load(open(path, encoding="utf-8"))


def test_parsers():
    assert [parse.fotmob_pos(x) for x in (11, 32, 64, 83, 105, None)] == ["G", "D", "M", "M", "F", ""]
    ms = parse.parse_fotmob_league_matches(match_list(NOW.strftime("%Y%m%d")), {9011})
    assert len(ms) == 1 and ms[0]["id"] == "6097138" and ms[0]["home"]["id"] == "6582" and ms[0]["state"] == "pre"
    assert ms[0]["start_kst"].startswith("2026-09-23T17:00")                  # UTC 08:00 → 한국 17:00
    lu = parse.parse_fotmob_lineups({"content": {"lineup": {"lineupType": "lastStarting11", "homeTeam": {"starters": starters(G_REG, "G")}}}})
    assert lu["confirmed"] is False and len(lu["home"]["lineup"]) == 11 and lu["home"]["lineup"][0]["pos"] == "G"
    fx = parse.parse_fotmob_team_fixtures(team_page("6582"))
    assert len(fx) == 8 and all(f["home_id"] == "6582" for f in fx)             # 끝난 경기만
    assert [f["league_id"] for f in fx].count("8974") == 6                     # 대회 id도 같이 읽는다
    assert parse.parse_fotmob_league_matches({}, {9011}) == [] and parse.parse_fotmob_lineups({})["confirmed"] is False


def test_cup_full_flow():
    """풋몹이 '발표(standard)'라고 준 경우"""
    tmp, idx, d = run_with(Fake())
    try:
        row = next(g for g in idx["games"] if g["key"] == "축구:fotmob.9011:6097138")
        assert row["league"] == "일왕배" and row["home"] == "감바 오사카" and row["lineup_ready"] is True
        assert not any("Premier" in g["league"] for g in idx["games"])            # 설정에 없는 대회는 안 가져옴
        H, A = d["teams"]["home"], d["teams"]["away"]
        assert H["grade"] == "2군" and H["core_in"] == 4 and A["grade"] == "1군"
        assert d["power"] is None                                                # 전력 숫자 없음 (1부·2부 섞임)
        miss = {m["name"]: m for m in H["missing"]}
        assert miss[f"G {G_REG[9]}"]["reason"] == "부상" and miss[f"G {G_REG[10]}"]["reason"] == "징계"
        assert ["감바 오사카 주전 2명 부상·징계", "bad"] in d["signals"]
        assert "rotation" in H and "goals" in H and H["formation"] == "4-2-3-1"
        assert d["lineup_source"] == "발표 (풋몹)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cup_lineup_changed_counts_as_announced():
    """풋몹이 '발표' 표시를 안 바꿔도, 지난 경기와 선발이 크게 다르면 발표로 본다."""
    tmp, idx, d = run_with(Fake(confirmed=False))
    try:
        assert d["lineup_ready"] is True and d["lineup_source"] == "지난 경기와 선발이 달라짐"
        assert d["teams"]["home"]["grade"] == "2군"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class SameAsLastFake(Fake):
    """오늘 선발이 지난 경기와 똑같음 = 진짜 '예상 라인업'"""
    def get_json(self, url, params=None, cache_key=None, ttl=0):
        out = super().get_json(url, params, cache_key, ttl)
        if url.endswith("/api/data/matchDetails") and str((params or {}).get("matchId")) == "6097138":
            out["content"]["lineup"]["homeTeam"]["starters"] = starters(G_REG, "G")
        return out


def test_cup_expected_lineup_shown_but_not_judged():
    tmp, idx, d = run_with(SameAsLastFake(confirmed=False))
    try:
        assert d["lineup_ready"] is False and "예상" in d["note"]
        exp = d["expected"]
        assert len(exp["home"]["players"]) == 11 and exp["home"]["name"] == "감바 오사카"     # 화면이 비지 않게 11명은 보여줌
        assert exp["home"]["formation"] == "4-2-3-1"
        r = d["risk"]["home"]
        assert len(r["absent"]) == 2 and set(r["core_absent"]) == {f"G {G_REG[9]}", f"G {G_REG[10]}"}
        assert d["signals"] == [["감바 오사카 주전 2명 부상·징계", "bad"]]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- J2처럼 '리그'로 넣은 풋몹 대회

L_CFG = {"mlb_enabled": False, "espn_leagues": [], "fotmob_leagues": [{"id": 8974, "name": "J2리그", "power": True}]}


def league_match_list(day):
    if day != NOW.strftime("%Y%m%d"):
        return {"leagues": []}
    out = match_list(day)
    out["leagues"][0]["id"] = out["leagues"][0]["primaryId"] = 8974
    out["leagues"][0]["name"] = "J. League 2"
    return out


class LeagueFake(Fake):
    def get_json(self, url, params=None, cache_key=None, ttl=0):
        if url.endswith("/api/data/matches"):
            self.count += 1
            return league_match_list((params or {})["date"])
        return super().get_json(url, params, cache_key, ttl)


def test_league_gets_power_numbers():
    """컵대회와 달리 단일 리그는 득실차로 전력 비율까지 낸다."""
    tmp = tempfile.mkdtemp()
    collect.DATA_DIR = os.path.join(tmp, "data")
    collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
    collect.CACHE_DIR = os.path.join(tmp, "cache")
    collect.ARCHIVE_DIR = os.path.join(tmp, "archive")
    collect.Client = lambda verbose=False: LeagueFake()
    collect.kst_now = lambda: NOW
    collect.load_config = lambda: L_CFG
    try:
        collect.run()
        idx = json.load(open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8"))
        row = next(g for g in idx["games"] if g["key"] == "축구:fotmob.8974:6097138")
        assert row["league"] == "J2리그"
        d = json.load(open(os.path.join(collect.GAME_DIR, collect.game_filename(row["key"])), encoding="utf-8"))
        pw = d["power"]
        assert pw and pw["mode"] == "model"
        assert pw["home"] + pw["away"] == 100 and pw["fav"] == "home"          # 득실차 +1 vs 0 → 감바 우위
        assert pw["basis"]["elo"] == [None, None]                              # 리그 Elo는 없어서 숨김
        assert pw["confident"] is False                                        # Elo 없이 득실차만 → 확신 표시 안 함
        assert "2군" in pw["note"]                                             # 로테이션 안내가 우선
        assert d["teams"]["home"]["grade"] == "2군"                             # 라인업 판정은 그대로
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_league_history_skips_cup_matches():
    """리그로 넣은 대회는 '평소 주전'도 그 리그 경기만 본다."""
    fake = LeagueFake()
    hist = collect.fotmob_team_history(fake, "6582", NOW, league_id=8974)
    assert len(hist) == 6 and all(len(h["starters"]) == 11 for h in hist)
    form = collect.fotmob_league_form(fake, "6582", NOW, 8974)
    assert len(form) == 6 and form[0]["gf"] == 2 and form[0]["ga"] == 1
    assert len(collect.fotmob_team_history(fake, "6582", NOW)) == 8            # 리그를 안 주면 다 본다


def test_league_power_note_when_no_rotation():
    """로테이션 안내가 없을 때는 'Elo 없이 득실차' 안내가 뜬다."""
    fake = LeagueFake()
    game = {"fm_league": "8974", "league_slug": "fotmob.8974", "power": True,
            "home": {"id": "6582", "name": "감바 오사카"}, "away": {"id": "162199", "name": "도쿠시마 보르티스"}}
    pw = collect.fotmob_power(fake, game, None, NOW)
    assert pw["mode"] == "model" and pw["fav"] == "home"
    assert "득실차" in pw["note"] and pw["basis"]["elo"] == [None, None]
    assert pw["exp_goals"] > 0


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"  ok  {name}")
    print(f"\n{n} passed")
