"""가짜 응답을 넣어 collect.py 전체 흐름(목록 -> 상세 -> 파일 저장)을 검증한다."""

import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect  # noqa: E402
from parse import KST  # noqa: E402

NOW = datetime(2026, 9, 18, 20, 0, tzinfo=KST)
START = (NOW + timedelta(minutes=40))


def iso_utc(dt):
    return dt.astimezone(collect.KST).astimezone(tz=None).strftime("%Y-%m-%dT%H:%MZ")


def soccer_roster(team_id, starters, subs_in=(), bench=(), scorers=("h1", "a1")):
    roster = []
    for pid in starters:
        stats = [{"name": "totalGoals", "value": 1}] if pid in scorers else []
        roster.append(
            {"starter": True, "jersey": "9", "athlete": {"id": pid, "displayName": f"P{pid}"},
             "position": {"abbreviation": "M"}, "stats": stats}
        )
    for pid in subs_in:
        roster.append({"starter": False, "subbedIn": True, "athlete": {"id": pid, "displayName": f"P{pid}"}})
    for pid in bench:
        roster.append({"starter": False, "subbedIn": False, "athlete": {"id": pid, "displayName": f"P{pid}"}})
    return {"homeAway": "home" if team_id == "100" else "away",
            "formation": "4-3-3",
            "team": {"id": team_id, "displayName": "홈팀" if team_id == "100" else "원정팀"},
            "roster": roster}


HOME_REGULARS = [f"h{i}" for i in range(1, 12)]
HOME_BENCH = [f"hb{i}" for i in range(1, 12)]
AWAY_REGULARS = [f"a{i}" for i in range(1, 12)]


class FakeClient:
    """collect.Client 대체. URL 패턴에 따라 미리 만든 응답을 돌려준다."""

    def __init__(self):
        self.count = 0
        self.calls = []

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        params = params or {}
        self.calls.append(url)

        if url.endswith("/soccer/eng.1/scoreboard"):
            if params.get("dates") != NOW.strftime("%Y%m%d"):
                return {"events": []}
            return {
                "events": [
                    {
                        "id": "500",
                        "date": START.astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "competitions": [
                            {
                                "status": {"type": {"state": "pre"}},
                                "competitors": [
                                    {"homeAway": "home", "team": {"id": "100", "displayName": "홈팀"}},
                                    {"homeAway": "away", "team": {"id": "200", "displayName": "원정팀"}},
                                ],
                            }
                        ],
                    }
                ]
            }

        if url.endswith("/soccer/eng.1/summary") and params.get("event") == "500":
            # 홈은 주전 4명만 선발(2군), 원정은 주전 11명 그대로(1군)
            return {
                "rosters": [
                    soccer_roster("100", HOME_REGULARS[:4] + HOME_BENCH[:7]),
                    soccer_roster("200", AWAY_REGULARS),
                ]
            }

        if "/teams/100/schedule" in url or "/teams/200/schedule" in url:
            events = []
            for i in range(1, 7):
                day = NOW - timedelta(days=i * 4)
                events.append(
                    {
                        "id": f"past{i}",
                        "date": day.astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "league": {"slug": "eng.1"},
                        "competitions": [{"status": {"type": {"completed": True}}}],
                    }
                )
            events.append(
                {
                    "id": "future",
                    "date": (NOW + timedelta(days=3)).astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "competitions": [{"status": {"type": {"completed": False}}}],
                }
            )
            return {"events": events}

        if url.endswith("/soccer/eng.1/summary") and str(params.get("event", "")).startswith("past"):
            return {
                "rosters": [
                    soccer_roster("100", HOME_REGULARS, subs_in=HOME_BENCH[:2]),
                    soccer_roster("200", AWAY_REGULARS),
                ]
            }

        if url.endswith("/schedule") and "statsapi" in url:
            return {"dates": []}

        return None


def run_with_fake(tmpdir):
    fake = FakeClient()
    collect.ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    collect.DATA_DIR = os.path.join(tmpdir, "data")
    collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
    collect.CACHE_DIR = os.path.join(tmpdir, "cache")
    collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
    collect.Client = lambda verbose=False: fake
    collect.kst_now = lambda: NOW
    collect.load_config = lambda: {
        "mlb_enabled": False,
        "espn_leagues": [{"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"}],
    }
    rc = collect.run()
    assert rc == 0
    with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
        index = json.load(f)
    detail_path = os.path.join(collect.GAME_DIR, collect.game_filename("축구:eng.1:500"))
    with open(detail_path, encoding="utf-8") as f:
        detail = json.load(f)
    return index, detail, fake


def test_end_to_end_grades_and_files():
    tmpdir = tempfile.mkdtemp()
    try:
        index, detail, fake = run_with_fake(tmpdir)

        assert len(index["games"]) == 1
        row = index["games"][0]
        assert row["home"] == "홈팀" and row["away"] == "원정팀"
        assert row["lineup_ready"] is True
        assert row["grade_home"] == "2군"
        assert row["grade_away"] == "1군"

        home = detail["teams"]["home"]
        assert home["core_in"] == 4
        assert home["matches"] == 6
        assert len(home["missing"]) == 7
        assert {m["name"] for m in home["missing"]} == {f"P{p}" for p in HOME_REGULARS[4:]}
        assert home["formation"] == "4-3-3"

        # 오늘 선발로 나온 벤치 자원은 백업으로 분류된다
        roles = {p["name"]: p["role"] for p in home["players"]}
        assert roles["Ph1"] == "주전"
        assert roles["Phb1"] == "백업"      # 최근 6경기 교체로만 2회 → 선발 0회
        assert roles["Phb3"] == "신규·복귀"  # 기록 자체가 없음
        starts = {p["name"]: p["starts"] for p in home["players"]}
        assert starts["Ph1"] == 6 and starts["Phb1"] == 0

        # 새 항목: 출전시간(6경기 x 90분), 교체 출전(시각 불명 -> 합계 불완전), 포메이션 배치, 기록 1위 칸
        mins = {p["name"]: (p["minutes"], p["minutes_complete"]) for p in home["players"]}
        assert mins["Ph1"] == (540, True)
        assert mins["Phb1"][1] is False
        assert sum(len(r) for r in home["rows"]) == 11
        assert home["leaders"]["labels"] == ["최다 득점", "최다 도움"]
        assert len(home["players"][0]["log"]) == 6
        # 교체명단·전력 지표 칸이 항상 존재 (이 가짜 경기는 교체명단을 안 줌 -> 구분 안 함)
        assert home["bench"] == [] and all(m["status"] is None for m in home["missing"])
        assert set(home["strength"]) >= {"retain", "goal_share", "assist_share"}
        assert detail["teams"]["away"]["strength"]["retain"] == 100
        # 한 줄 요약이 목록·상세에 모두 들어간다
        assert detail["summary"]["headline"].startswith("홈팀,") and detail["summary"]["focus"] == "home"
        assert row["insight"].startswith("홈팀 주전 7명 빠짐")
        # 최근 폼 / 시즌 전체 두 가지 기록
        p1 = next(p for p in home["players"] if p["name"] == "Ph1")
        assert p1["recent"]["starts"] == 6 and p1["season"]["matches"] == 6
        assert home["season_record"]["matches"] == 0 or home["season_record"]["matches"] == 6
        assert home["season_core_in"] == 4
        # 에이스: 선발·벤치·빠진 선수 중 1명, 목록에도 상태가 들어간다
        assert home["ace"] and home["ace"]["status"] in ("선발", "벤치", "명단 제외", "선발 제외")
        assert home["ace"]["name"] in {f"Ph{i}" for i in range(1, 12)}
        assert row["ace_home"]["status"] == home["ace"]["status"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_needs_detail_window():
    game = {"start_kst": (NOW + timedelta(minutes=30)).isoformat(), "state": "pre"}
    assert collect.needs_detail(game, NOW) is True
    far = {"start_kst": (NOW + timedelta(hours=20)).isoformat(), "state": "pre"}
    assert collect.needs_detail(far, NOW) is False
    old = {"start_kst": (NOW - timedelta(hours=30)).isoformat(), "state": "post"}
    assert collect.needs_detail(old, NOW) is False
    assert collect.needs_detail({"start_kst": ""}, NOW) is False


def test_roster_matched_by_team_id_even_if_labels_wrong():
    """ESPN 명단의 홈/원정 표기가 뒤바뀌어도 팀 ID로 제자리를 찾는다."""
    tmpdir = tempfile.mkdtemp()
    try:
        fake = FakeClient()
        orig = fake.get_json
        def swapped(url, params=None, cache_key=None, ttl=0):
            out = orig(url, params, cache_key, ttl)
            if url.endswith("/soccer/eng.1/summary") and (params or {}).get("event") == "500":
                for r in out["rosters"]:
                    r["homeAway"] = "away" if r["homeAway"] == "home" else "home"
            return out
        fake.get_json = swapped
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [{"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"}]}
        collect.run()
        with open(os.path.join(collect.GAME_DIR, collect.game_filename("축구:eng.1:500")), encoding="utf-8") as f:
            d = json.load(f)
        assert d["teams"]["home"]["core_in"] == 4 and d["teams"]["away"]["core_in"] == 11
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_dedupe_keeps_first():
    games = [{"key": "a", "n": 1}, {"key": "a", "n": 2}, {"key": "b", "n": 3}]
    out = collect.dedupe(games)
    assert [g["n"] for g in out] == [1, 3]


def test_game_filename_is_safe():
    assert collect.game_filename("축구:eng.1:500").endswith(".json")
    assert "/" not in collect.game_filename("a/b:c")
    assert ":" not in collect.game_filename("a:b")
    # 화면(index.html)의 fileOf와 같은 결과여야 경기를 눌렀을 때 파일을 찾는다
    assert collect.game_filename("축구:eng.1:500") == "___eng.1_500.json"
    assert collect.game_filename("mlb::778123") == "mlb__778123.json"
    assert collect.game_filename("농구:nba:401") == "___nba_401.json"


def test_prune_removes_only_old_files():
    tmpdir = tempfile.mkdtemp()
    try:
        old = os.path.join(tmpdir, "old.json")
        new = os.path.join(tmpdir, "new.json")
        keep = os.path.join(tmpdir, ".gitkeep")
        for p in (old, new, keep):
            open(p, "w").close()
        past = time.time() - 10 * 86400
        os.utime(old, (past, past))
        os.utime(keep, (past, past))
        assert collect.prune(tmpdir, 3) == 1
        assert not os.path.exists(old) and os.path.exists(new) and os.path.exists(keep)
        assert collect.prune(os.path.join(tmpdir, "없는폴더"), 3) == 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



class UefaFake(FakeClient):
    """챔스 경기: 홈(100)은 라리가, 원정(200)은 EPL 팀. 각자 자국 리그 기록을 찾아야 한다."""

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        params = params or {}
        self.calls.append(url)
        if url.endswith("/soccer/uefa.champions/scoreboard"):
            if params.get("dates") != NOW.strftime("%Y%m%d"):
                return {"events": []}
            return {"events": [{"id": "900", "date": START.astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "competitions": [{"status": {"type": {"state": "pre"}}, "competitors": [
                                    {"homeAway": "home", "team": {"id": "100", "displayName": "레알"}},
                                    {"homeAway": "away", "team": {"id": "200", "displayName": "아스날"}}]}]}]}
        if url.endswith("/scoreboard"):
            return {"events": []}
        if url.endswith("/soccer/uefa.champions/summary") and params.get("event") == "900":
            return {"rosters": [soccer_roster("100", HOME_REGULARS), soccer_roster("200", AWAY_REGULARS)]}
        m = re.search(r"/soccer/([a-z.0-9_]+)/teams/(\d+)/schedule$", url)
        if m:
            slug, tid = m.group(1), m.group(2)
            own = {"100": "esp.1", "200": "eng.1"}[tid]
            if slug != own:
                return {"events": []}
            return {"events": [{"id": f"{slug}-{tid}-{i}", "date": (NOW - timedelta(days=i * 4)).astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "league": {"slug": slug}, "competitions": [{"status": {"type": {"completed": True}}}]} for i in range(1, 9)]}
        m = re.search(r"/soccer/([a-z.0-9_]+)/summary$", url)
        if m and str(params.get("event", "")).count("-") == 2:
            slug, tid, i = params["event"].split("-")
            team = "레알" if tid == "100" else "아스날"
            regs = HOME_REGULARS if tid == "100" else AWAY_REGULARS
            win = tid == "100" or int(i) % 2 == 0   # 레알 전승, 아스날 절반 승
            return {"header": {"competitions": [{"date": "2026-09-01T14:00Z", "competitors": [
                        {"homeAway": "home", "score": "2" if win else "0", "team": {"id": tid, "displayName": team}},
                        {"homeAway": "away", "score": "0" if win else "1", "team": {"id": "999", "displayName": "상대"}}]}]},
                    "rosters": [soccer_roster(tid, regs)]}
        return None


def test_uefa_match_uses_each_domestic_league_and_factor():
    tmpdir = tempfile.mkdtemp()
    try:
        fake = UefaFake()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        cfg = {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
            {"sport": "축구", "slug": "esp.1", "path": "soccer/esp.1", "name": "라리가"},
            {"sport": "축구", "slug": "uefa.champions", "path": "soccer/uefa.champions", "name": "챔스"}],
            "league_strength": {"eng.1": 1.0, "esp.1": 0.9, "default": 0.8}}
        collect.load_config = lambda: cfg
        assert collect.run() == 0
        with open(os.path.join(collect.GAME_DIR, collect.game_filename("축구:uefa.champions:900")), encoding="utf-8") as f:
            d = json.load(f)
        H, A = d["teams"]["home"], d["teams"]["away"]
        assert H["league"] == "esp.1" and A["league"] == "eng.1"        # 각자 자국 리그
        assert H["season_record"]["w"] == 8 and A["season_record"]["w"] == 4
        assert H["season_matches"] == 8
        assert d["power"]["league_adjusted"] is True
        # 레알 체급이 높아도 라리가 보정(0.9)이 들어간다
        raw = H["lineup_power"]["today"] / (H["lineup_power"]["today"] + A["lineup_power"]["today"]) * 100
        assert d["power"]["home"] < round(raw)
        assert H["lineup_power"]["totals"]["starts"] > 0
        assert set(H["lineup_power"]["lines"]) >= {"DF", "MF", "FW"} or H["lineup_power"]["lines"]
        # 자국 리그 찾기 결과는 캐시된다
        assert os.path.exists(collect.cache_path("domestic_100"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



class _Resp:
    def __init__(self, text, code=200):
        self.text, self.status_code, self.encoding = text, code, "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def get(self, url, timeout=None, headers=None):
        if url.endswith("/World.tsv"):
            return _Resp("1\t1\tES\t2259\n19\t22\tKR\t1805\n3\t15\tJP\t1888\n")
        if url.endswith("/en.teams.tsv"):
            return _Resp("ES\tSpain\nKR\tSouth Korea\tKorea\nJP\tJapan\n")
        return _Resp("", 404)


class NationalFake(FakeClient):
    """A매치: 한국(1) vs 일본(2). 기록은 친선전·예선 두 대회에 흩어져 있다."""

    def __init__(self):
        super().__init__()
        self.session = _Session()

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        params = params or {}
        if url.endswith("/soccer/fifa.friendly/scoreboard"):
            if params.get("dates") != NOW.strftime("%Y%m%d"):
                return {"events": []}
            return {"events": [{"id": "77", "date": START.astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "competitions": [{"status": {"type": {"state": "pre"}}, "competitors": [
                                    {"homeAway": "home", "team": {"id": "1", "displayName": "South Korea"}},
                                    {"homeAway": "away", "team": {"id": "2", "displayName": "Japan"}}]}]}]}
        if url.endswith("/scoreboard"):
            return {"events": []}
        if url.endswith("/soccer/fifa.friendly/summary") and params.get("event") == "77":
            # 한국은 주전 4명만(2군), 일본은 풀주전
            return {"rosters": [dict(soccer_roster("1", HOME_REGULARS[:4] + HOME_BENCH[:7]), homeAway="home"), dict(soccer_roster("2", AWAY_REGULARS), homeAway="away")]}
        m = re.search(r"/soccer/([a-z.0-9_]+)/teams/(\d+)/schedule$", url)
        if m:
            slug, tid = m.group(1), m.group(2)
            if slug not in ("fifa.friendly", "fifa.worldq.afc"):
                return {"events": []}
            base = 0 if slug == "fifa.friendly" else 1      # 두 대회 경기가 번갈아 있다
            return {"events": [{"id": f"{slug}-{tid}-{i}", "date": (NOW - timedelta(days=30 * i)).astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "competitions": [{"status": {"type": {"completed": True}}}]} for i in range(1 + base, 13, 2)]}
        m = re.search(r"/soccer/([a-z.0-9_]+)/summary$", url)
        if m and str(params.get("event", "")).count("-") == 2:
            slug, tid, i = params["event"].split("-")
            regs = HOME_REGULARS if tid == "1" else AWAY_REGULARS
            return {"header": {"competitions": [{"competitors": [
                        {"homeAway": "home", "score": "1", "team": {"id": tid, "displayName": "X"}},
                        {"homeAway": "away", "score": "0", "team": {"id": "9", "displayName": "상대"}}]}]},
                    "rosters": [soccer_roster(tid, regs)]}
        return None


def test_a_match_merges_competitions_and_uses_elo():
    tmpdir = tempfile.mkdtemp()
    try:
        fake = NationalFake()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        cfg = {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
            {"sport": "축구", "slug": "fifa.friendly", "path": "soccer/fifa.friendly", "name": "A매치 친선", "national": True},
            {"sport": "축구", "slug": "fifa.worldq.afc", "path": "soccer/fifa.worldq.afc", "name": "예선", "national": True}],
            "home_advantage_slugs": ["fifa.friendly"]}
        collect.load_config = lambda: cfg
        assert collect.run() == 0
        with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
            idx = json.load(f)
        row = idx["games"][0]
        assert row["national"] is True and row["power"]
        with open(os.path.join(collect.GAME_DIR, collect.game_filename("축구:fifa.friendly:77")), encoding="utf-8") as f:
            d = json.load(f)
        H, A = d["teams"]["home"], d["teams"]["away"]
        assert d["national"] is True and d["power"]["mode"] == "elo" and d["power"]["home_adv"] == 100
        assert H["season_matches"] == 12                       # 친선 6 + 예선 6 합쳐서
        assert H["grade"] == "2군" and A["grade"] == "1군"
        assert H["core_in"] == 4 and len(H["players"]) == 11 and len(A["players"]) == 11
        assert H["lineup_power"]["elo"] == 1805 and A["lineup_power"]["elo"] == 1888
        assert H["lineup_power"]["today_elo"] < 1805            # 2군이라 깎임
        assert d["power"]["home"] < 50                          # 홈인데도 2군+낮은 Elo로 열세
        assert H["elo_code"] == "KR" and A["elo_code"] == "JP"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



class FinishedFake(FakeClient):
    """같은 경기가 끝난 뒤: 스코어보드 상태가 post, 점수 2:1."""
    def get_json(self, url, params=None, cache_key=None, ttl=0):
        out = super().get_json(url, params, cache_key, ttl)
        if url.endswith("/soccer/eng.1/scoreboard") and out and out.get("events"):
            comp = out["events"][0]["competitions"][0]
            comp["status"] = {"type": {"state": "post"}}
            comp["competitors"][0]["score"] = "2"
            comp["competitors"][1]["score"] = {"value": 1}
        return out


def test_archive_keeps_prediction_then_fills_result():
    tmpdir = tempfile.mkdtemp()
    try:
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [{"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"}]}
        def rows():
            files = sorted(os.listdir(collect.ARCHIVE_DIR))
            assert files == ["2026-09.jsonl"], files
            with open(os.path.join(collect.ARCHIVE_DIR, files[0]), encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]

        # 1) 경기 전: 판정 저장, 결과 없음
        pre = FakeClient()
        collect.Client = lambda verbose=False: pre
        collect.run()
        r = rows()
        assert len(r) == 1 and r[0]["key"] == "축구:eng.1:500"
        assert r[0]["snap_state"] == "pre" and r[0]["result"] is None
        assert r[0]["home"]["grade"] == "2군" and r[0]["away"]["grade"] == "1군"
        assert len(r[0]["home"]["lineup"]) == 11
        mtime = os.path.getmtime(os.path.join(collect.ARCHIVE_DIR, "2026-09.jsonl"))

        # 2) 같은 상태로 다시 돌면 파일을 다시 쓰지 않는다 (불필요한 커밋 방지)
        collect.run()
        assert os.path.getmtime(os.path.join(collect.ARCHIVE_DIR, "2026-09.jsonl")) == mtime

        # 3) 경기 끝: 경기 전 판정은 그대로, 결과만 채워진다
        post = FinishedFake()
        collect.Client = lambda verbose=False: post
        collect.run()
        r = rows()
        assert r[0]["snap_state"] == "pre"
        assert r[0]["result"]["home"] == 2 and r[0]["result"]["away"] == 1 and r[0]["result"]["res"] == "H"
        assert r[0]["home"]["grade"] == "2군"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



class MlbFake:
    """MLB: 최근 10경기 박스스코어가 있는 팀 / 없는 팀."""
    def __init__(self, with_box=True):
        self.count = 0
        self.with_box = with_box

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        params = params or {}
        if url.endswith("/schedule") and params.get("teamId"):
            games = [{"gamePk": 5000 + i, "gameDate": f"2026-09-{20 - i:02d}T23:00Z", "status": {"abstractGameState": "Final"},
                      "teams": {"home": {"team": {"id": 147, "name": "Yankees"}, "score": 5},
                                "away": {"team": {"id": 1, "name": "Opp"}, "score": 3}}} for i in range(12)]
            return {"dates": [{"games": games}]}
        if url.endswith("/schedule/games") and (params or {}).get("upperCategoryId") == "wbaseball":
            return {"result": {"games": [{"gameId": "NV1", "categoryId": "mlb", "homeTeamName": "뉴욕양키스", "awayTeamName": "보스턴"}]}}
        if url.endswith("/NV1/preview"):
            return {"result": {"previewData": {"homeStarter": {"playerInfo": {"name": "에이스", "pCode": "1"}, "currentSeasonStats": {}},
                                               "awayStarter": {"playerInfo": {"name": "다른투수", "pCode": "2"}, "currentSeasonStats": {}}}}}
        if url.endswith("/people/77"):
            return {"people": [{"id": 77, "fullName": "Ace Starter", "pitchHand": {"code": "L"}, "stats": [{"splits": [{"stat": {
                "inningsPitched": "160.0", "strikeOuts": 180, "baseOnBalls": 40, "homeRuns": 14, "era": "2.80", "whip": "1.02", "gamesStarted": 27}}]}]}]}
        if url.endswith("/people/77/stats"):
            return {"stats": [{"splits": [{"date": f"2026-09-{d}", "opponent": {"name": "O"}, "stat": {"gamesStarted": 1, "inningsPitched": "6.0", "earnedRuns": 1, "numberOfPitches": 96}}
                                          for d in ("06", "12", "18")]}]}
        if url.endswith("/stats") and params.get("sitCodes") == "rp":
            return {"stats": [{"splits": [{"stat": {"era": "3.70"}}]}]}
        if "/boxscore" in url:
            if not self.with_box:
                return None
            pk = int(url.split("/game/")[1].split("/")[0])
            # 최근 10경기: 1~6번은 매일, 7~9번 자리는 A조(201~203)가 7경기, B조(301~303)가 3경기
            regular = [str(i) for i in range(1, 7)]
            tail = ["201", "202", "203"] if pk % 10 < 7 else ["301", "302", "303"]
            order = [int(x) for x in regular + tail]
            pl = {f"ID{x}": {"person": {"id": x, "fullName": f"P{x}"}, "position": {"abbreviation": "1B"}} for x in order}
            for pid, n in ((900, 95), (901, 30), (902, 25)):          # 선발 900, 불펜 901·902
                pl[f"ID{pid}"] = {"person": {"id": pid}, "position": {"abbreviation": "P"}, "stats": {"pitching": {"numberOfPitches": n}}}
            return {"teams": {"home": {"battingOrder": order, "batters": order, "pitchers": [900, 901, 902], "players": pl}}}
        if url.endswith("/stats"):
            return {"stats": [{"splits": [{"stat": {"gamesPlayed": 150, "runs": 750, "era": "4.10"}}]}]}
        if url.endswith("/roster"):
            return {"roster": [{"person": {"id": i}, "position": {"abbreviation": "OF"}} for i in list(range(1, 7)) + [201, 202, 203, 301, 302, 303]]}
        if url.endswith("/people"):
            ppl = []
            for pid in params["personIds"].split(","):
                pa = 600 if int(pid) < 100 else 300
                ppl.append({"id": int(pid), "fullName": f"P{pid}", "stats": [{"group": {"displayName": "hitting"}, "type": {"displayName": "season"},
                            "splits": [{"stat": {"plateAppearances": pa, "gamesPlayed": 120}}]}]})
            return {"people": ppl}
        return None


def test_mlb_uses_recent_batting_orders_and_falls_back():
    tmpdir = tempfile.mkdtemp()
    try:
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.kst_now = lambda: NOW
        game = {"key": "mlb::1", "sport": "야구", "league": "MLB", "start_kst": NOW.isoformat(), "state": "pre",
                "home": {"id": "147", "name": "Yankees"}, "away": {"id": "", "name": "X"},
                "lineups": {"home": [{"id": str(i), "name": f"P{i}", "pos": "1B"} for i in list(range(1, 7)) + [301, 302, 303]], "away": []},
                "probables": {}}
        game["probables"] = {"home": {"id": "77", "name": "Ace Starter"}}
        d = collect.build_mlb_detail(MlbFake(True), game, NOW)
        h = d["teams"]["home"]
        assert h["basis"] == "recent" and 0 < h["matches"] <= collect.MLB_RECENT        # 주전 판정은 최근 20경기까지
        # 투수 분석
        sp = h["pitching"]["sp"]
        # 경기일(9/18) 당일 등판은 '지난 등판'이 아니므로 빠지고, 휴식일은 9/12 이후 6일
        assert sp["hand"] == "L" and sp["era"] == 2.8
        assert [s["date"] for s in sp["recent"]] == ["2026-09-12", "2026-09-06"]
        assert sp["recent_era"] == 1.5 and sp["rest_days"] == 6
        pen = h["pitching"]["pen"]
        assert pen["era"] == 3.7 and pen["basis"] == "불펜"
        assert pen["pitches_3d"] == 3 * 55 and pen["b2b"] == 2        # 최근 3일 × (30+25), 이틀 연속 2명
        assert h["pitching"]["off_rpg"] == 5.0
        assert d["power"]["mode"] == "mlb" and d["power"]["home"] + d["power"]["away"] == 100
        assert d["power"]["exp_total"] > 0
        # 원정 선발이 없으면 '선발 맞대결' 줄은 빠지고, 예상 득점 줄은 들어간다
        assert not any(p[0].startswith("선발 맞대결") for p in d["summary"]["points"])
        assert any(p[0].startswith("예상 득점") for p in d["summary"]["points"])
        assert h["pitching"]["bats"] == {"L": 0, "R": 0, "S": 0}   # 가짜 선수엔 좌우 정보가 없음
        game2 = dict(game, probables={"home": {"id": "77", "name": "Ace Starter"}, "away": {"id": "77", "name": "Ace Starter"}})
        d2 = collect.build_mlb_detail(MlbFake(True), game2, NOW)
        # 한국어 선발 이름: 네이버 경기(팀 이름 띄어쓰기 달라도)와 짝지어 바꾼다
        game3 = dict(game2, home={"id": "147", "name": "뉴욕 양키스"}, away={"id": "147", "name": "보스턴"})
        d3 = collect.build_mlb_detail(MlbFake(True), game3, NOW)
        sp3 = d3["teams"]["home"]["pitching"]["sp"]
        assert sp3["name"] == "에이스" and sp3["name_en"] == "Ace Starter"
        assert d3["summary"]["headline"].endswith("선발 에이스 vs 다른투수")
        assert all(not r["opp"] or "Yankees" not in r["opp"] for r in (sp3["recent"] or [])) if sp3.get("recent") else True
        first = d2["summary"]["points"][0]
        assert first[0] == "선발 맞대결: Ace Starter(ERA 2.80) vs Ace Starter(ERA 2.80)"
        assert first[1] == "최근 3경기 1.50 · 최근 3경기 1.50"
        assert d2["summary"]["headline"].endswith("선발 Starter vs Starter")    # 야구 요약은 선발 중심
        roles = {p["id"]: p["role"] for p in h["players"]}
        assert roles["1"] == "주전" and roles["301"] == "백업"          # 12경기 중 3선발이면 백업 (범위를 넓혀 더 정확해짐)
        assert h["core_in"] == 6 and h["grade"] == "1.5군"
        assert all(r != "신규·복귀" for r in roles.values())

        # 박스스코어를 못 받으면 시즌 타석으로 대신하고, 경기 뛴 비주전은 '백업'
        shutil.rmtree(collect.CACHE_DIR, ignore_errors=True)
        d2 = collect.build_mlb_detail(MlbFake(False), game, NOW)
        h2 = d2["teams"]["home"]
        assert h2["basis"] == "season"
        roles2 = {p["id"]: p["role"] for p in h2["players"]}
        assert roles2["1"] == "주전" and roles2["301"] == "백업"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



class ScheduleFake(FakeClient):
    """리그 일정(다음 리그 경기 7일 뒤) + 챔스 일정(3일 뒤)이 있는 팀."""
    def get_json(self, url, params=None, cache_key=None, ttl=0):
        m = re.search(r"/soccer/([a-z.0-9_]+)/teams/(\d+)/schedule$", url)
        if m and m.group(1) in ("eng.1", "uefa.champions"):
            self.count += 1
            slug, tid = m.group(1), m.group(2)
            def ev(eid, days, done, opp):
                return {"id": eid, "date": (NOW + timedelta(days=days)).astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "league": {"slug": slug},
                        "competitions": [{"status": {"type": {"completed": done}}, "competitors": [
                            {"id": tid, "homeAway": "home", "team": {"id": tid}, "score": {"value": 2} if done else None},
                            {"id": "777", "homeAway": "away", "team": {"id": "777", "displayName": opp}, "score": {"value": 0} if done else None}]}]}
            if slug == "uefa.champions":
                return {"events": [ev("ucl1", 3, False, "Man City")]}
            return {"events": [ev(f"past{i}", -4 * i, True, "Opp") for i in range(1, 7)] + [ev("next", 7, False, "Leeds")]}
        return super().get_json(url, params, cache_key, ttl)


def test_next_match_includes_european_competition_and_signal():
    tmpdir = tempfile.mkdtemp()
    try:
        fake = ScheduleFake()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
            {"sport": "축구", "slug": "uefa.champions", "path": "soccer/uefa.champions", "name": "챔스"}]}
        collect.run()
        with open(os.path.join(collect.GAME_DIR, collect.game_filename("축구:eng.1:500")), encoding="utf-8") as f:
            d = json.load(f)
        sch = d["teams"]["home"]["schedule"]
        assert sch["next"]["comp"] == "챔스" and sch["next"]["in_days"] == 3 and sch["next"]["opp"] == "Man City"
        assert sch["last"]["days_ago"] == 4 and sch["last"]["res"] == "W" and sch["last"]["comp"] == "EPL"
        assert ["홈팀 3일 뒤 챔스", "warn"] in d["signals"]
        with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
            row = json.load(f)["games"][0]
        assert row["signals"] and len(row["signals"]) <= 3
        # 로테이션·비슷한 라인업·득실이 상세에 들어간다
        H = d["teams"]["home"]
        assert set(H["rotation"]) == {"1군", "1.5군", "2군"} and "n" in H["similar"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



def test_logos_hidden_unless_enabled():
    class LogoFake(FakeClient):
        def get_json(self, url, params=None, cache_key=None, ttl=0):
            out = super().get_json(url, params, cache_key, ttl)
            if url.endswith("/soccer/eng.1/scoreboard") and out and out.get("events"):
                for c in out["events"][0]["competitions"][0]["competitors"]:
                    c["team"]["logo"] = "https://example.com/crest.png"
            return out
    for show, expect in ((False, ""), (True, "https://example.com/crest.png")):
        tmpdir = tempfile.mkdtemp()
        try:
            fake = LogoFake()
            collect.DATA_DIR = os.path.join(tmpdir, "data")
            collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
            collect.CACHE_DIR = os.path.join(tmpdir, "cache")
            collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
            collect.Client = lambda verbose=False: fake
            collect.kst_now = lambda: NOW
            collect.load_config = lambda: {"mlb_enabled": False, "show_logos": show,
                                           "espn_leagues": [{"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"}]}
            collect.run()
            with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
                row = json.load(f)["games"][0]
            assert row["home_logo"] == expect and row["away_logo"] == expect, (show, row["home_logo"])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)



def test_pre_lineup_rotation_risk_and_signal():
    class PreFake(ScheduleFake):
        def get_json(self, url, params=None, cache_key=None, ttl=0):
            if url.endswith("/soccer/eng.1/summary") and (params or {}).get("event") == "500":
                self.count += 1
                return {"rosters": []}                          # 라인업 아직 없음
            return super().get_json(url, params, cache_key, ttl)
    tmpdir = tempfile.mkdtemp()
    try:
        fake = PreFake()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
            {"sport": "축구", "slug": "uefa.champions", "path": "soccer/uefa.champions", "name": "챔스"}]}
        collect.run()
        with open(os.path.join(collect.GAME_DIR, collect.game_filename("축구:eng.1:500")), encoding="utf-8") as f:
            d = json.load(f)
        assert d["lineup_ready"] is False
        r = d["risk"]["home"]
        assert r["situation"] == "big" and r["schedule"]["next"]["comp"] == "챔스"
        assert r["reason"].startswith("다음 경기 3일 뒤 챔스")
        assert "today" in r and "base" in r and isinstance(r["tired"], list)
        with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
            row = json.load(f)["games"][0]
        assert row["lineup_ready"] is False
        # 이 팀은 과거에 '큰 경기 앞' 기록이 없어 패턴은 모르지만(level 없음), 일정 신호는 띄운다
        assert r["level"] is None and r["today"]["n"] == 0 and r["base"]["n"] >= 3
        assert ["홈팀 3일 뒤 챔스", "warn"] in d["signals"]
        assert row["signals"] == d["signals"][:3]
        # 전력은 팀 체급·Elo로 내므로 라인업 발표 전에도 나온다
        pw = d.get("power")
        assert pw and pw["mode"] == "model"
        assert pw["home"] + pw["away"] == 100
        assert row["power"] == [pw["home"], pw["away"]]          # 목록에도 같이 나간다
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



class FotmobFake:
    def __init__(self, blocked=False):
        self.count = 0
        self.blocked = blocked

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        if self.blocked:
            return None
        if url.endswith("/matches"):
            return {"leagues": [{"name": "Premier League", "matches": [
                {"id": 7, "home": {"name": "Leeds United"}, "away": {"name": "Crystal Palace"}, "status": {"utcTime": "2026-09-18T14:00:00Z"}},
                {"id": 8, "home": {"name": "Real Madrid"}, "away": {"name": "Getafe"}, "status": {"utcTime": "2026-09-18T19:00:00Z"}}]}]}
        if url.endswith("/matchDetails") and params.get("matchId") == "8":
            return {"content": {"lineup": {"homeTeam": {"unavailable": [
                {"name": "Jude Bellingham", "unavailability": {"type": "injury", "expectedReturn": "Mid October 2026"}},
                {"name": "Eder Militao", "unavailability": {"type": "suspension", "expectedReturn": "Late September 2026"}}]},
                "awayTeam": {"unavailable": []}}}}
        return None


def test_fotmob_absences_attach_reasons():
    tmpdir = tempfile.mkdtemp()
    try:
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        game = {"start_kst": "2026-09-19T04:00:00+09:00", "home": {"name": "레알 마드리드", "name_en": "Real Madrid"},
                "away": {"name": "헤타페", "name_en": "Getafe"}}
        ab = collect.fotmob_absences(FotmobFake(), game)
        assert [x["name"] for x in ab["home"]] == ["Jude Bellingham", "Eder Militao"] and ab["away"] == []
        teams = {"home": {"missing": [{"name": "Bellingham", "status": "명단 제외"}, {"name": "Éder Militão", "status": "명단 제외"},
                                      {"name": "Vinicius Junior", "status": "벤치"}]}, "away": {"missing": []}}
        sig = collect.attach_absences(teams, ab, {"home": "레알 마드리드", "away": "헤타페"})
        miss = {m["name"]: m for m in teams["home"]["missing"]}
        assert miss["Bellingham"]["reason"] == "부상" and miss["Bellingham"]["return"] == "10월 중순"
        assert miss["Éder Militão"]["reason"] == "징계" and "reason" not in miss["Vinicius Junior"]
        assert sig == [["레알 마드리드 주전 2명 부상·징계", "bad"]]
        # 번호가 붙은 이름·같은 성: 한 결장자는 한 명에게만, 똑같은 이름이 먼저
        t2 = {"home": {"missing": [{"name": "Ph5"}, {"name": "Ph10"}, {"name": "Silva"}, {"name": "Bernardo Silva"}]}, "away": {"missing": []}}
        ab2 = {"home": [{"name": "Ph10", "type": "부상", "return": "미정"}, {"name": "Bernardo Silva", "type": "징계", "return": "미정"}], "away": []}
        collect.attach_absences(t2, ab2, {"home": "a", "away": "b"})
        got = {m["name"]: m.get("reason") for m in t2["home"]["missing"]}
        assert got == {"Ph5": None, "Ph10": "부상", "Silva": None, "Bernardo Silva": "징계"}, got
        # 경기를 못 찾거나 풋몹이 막혀도 문제없이 None
        assert collect.fotmob_absences(FotmobFake(), dict(game, home={"name": "x", "name_en": "Chelsea"})) is None
        assert collect.fotmob_absences(FotmobFake(blocked=True), game) is None
        assert collect.attach_absences(teams, None, {}) == []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_pre_lineup_injury_signal():
    """발표 전: 주전(지난 경기 기록으로 뽑은) 중 부상·징계 2명 이상이면 빨간 신호."""
    class PreFake(ScheduleFake):
        def get_json(self, url, params=None, cache_key=None, ttl=0):
            if url.endswith("/soccer/eng.1/summary") and (params or {}).get("event") == "500":
                self.count += 1
                return {"rosters": []}
            return super().get_json(url, params, cache_key, ttl)
    tmpdir = tempfile.mkdtemp()
    saved = collect.fotmob_absences
    try:
        collect.fotmob_absences = lambda client, game: {
            "home": [{"name": "Ph1", "type": "부상", "return": "10월 중순"}, {"name": "Ph2", "type": "징계", "return": "9월 말"},
                     {"name": "Nobody", "type": "부상", "return": "미정"}], "away": []}
        fake = PreFake()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
            {"sport": "축구", "slug": "uefa.champions", "path": "soccer/uefa.champions", "name": "챔스"}]}
        collect.run()
        with open(os.path.join(collect.GAME_DIR, collect.game_filename("축구:eng.1:500")), encoding="utf-8") as f:
            d = json.load(f)
        r = d["risk"]["home"]
        assert r["core_absent"] == ["Ph1", "Ph2"] and len(r["absent"]) == 3
        assert d["signals"][0] == ["홈팀 주전 2명 부상·징계", "bad"]
    finally:
        collect.fotmob_absences = saved
        shutil.rmtree(tmpdir, ignore_errors=True)



def test_model_power_for_club_league():
    """model.json이 있으면 같은 리그 경기는 새 방식(득실차 + Elo + 리그 홈 이점)으로 전력 비교."""
    tmpdir = tempfile.mkdtemp()
    try:
        fake = ScheduleFake()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        collect._ELO_MEMO.clear()
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
            {"sport": "축구", "slug": "uefa.champions", "path": "soccer/uefa.champions", "name": "챔스"}]}
        assert collect.get_model() is not None, "저장소의 model.json을 읽어야 함"
        collect.run()
        with open(os.path.join(collect.GAME_DIR, collect.game_filename("축구:eng.1:500")), encoding="utf-8") as f:
            d = json.load(f)
        pw = d["power"]
        assert pw["mode"] == "model" and pw["home"] + pw["away"] == 100
        assert set(pw["basis"]) == {"gd", "elo", "home_edge"} and pw["basis"]["home_edge"] == 0.0     # EPL 기준
        assert abs(sum(pw["probs"]) - 1) < 1e-6 and pw["exp_goals"] > 0 and pw["league_over"] == 55
        # 라인업(2군)은 전력 숫자에 안 들어가고 설명으로만
        assert d["teams"]["home"]["grade"] == "2군" and "전력은 팀 체급 기준" in pw["note"]
        with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
            row = json.load(f)["games"][0]
        assert row["power"] == [pw["home"], pw["away"]]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_model_power_math():
    m = collect.get_model()
    same = {"gd": 0.0, "elo": 1500.0, "gf_pg": 1.4, "ga_pg": 1.4}
    epl = collect.model_power(m, "eng.1", same, same, "홈", "원정")
    liga = collect.model_power(m, "esp.1", same, same, "홈", "원정")
    kl = collect.model_power(m, "kleague", same, same, "홈", "원정")
    assert liga["home"] > epl["home"] > kl["home"] >= 50                  # 리그별 홈 이점: 라리가 > EPL > K리그
    strong = collect.model_power(m, "esp.1", {"gd": 1.4, "elo": 1700, "gf_pg": 2.2, "ga_pg": 0.8}, {"gd": -0.3, "elo": 1480, "gf_pg": 1.1, "ga_pg": 1.5}, "레알", "헤타페")
    assert strong["home"] >= 75 and strong["confident"] and strong["fav"] == "home" and strong["verdict"] == "레알 우위"
    low = collect.model_power(m, "ita.1", dict(same, gf_pg=0.8, ga_pg=0.8), dict(same, gf_pg=0.8, ga_pg=0.8), "a", "b")
    assert low["exp_goals"] < 2.2 and ["무승부 가능성 ↑ · 저득점 예상", "warn"] in collect.model_signals(low, {"home": "a", "away": "b"})
    assert ["레알 우세 · 확신 높음", "good"] in collect.model_signals(strong, {"home": "레알", "away": "헤타페"})
    # Elo: 이긴 팀이 오르고, 시즌이 바뀌면 평균 쪽으로
    e = collect.elo_table([("2025-08-01", "0", "A", "B", 3, 0), ("2026-08-01", "1", "C", "D", 1, 1)])
    assert e["A"] < 1500 + 20 and e["A"] > 1500 and e["B"] < 1500



def test_idle_league_is_skipped_next_run():
    """대회가 83개라 목록 요청이 많다 → 경기가 없던 대회는 다음 실행에서 건너뛴다."""
    tmpdir = tempfile.mkdtemp()
    try:
        fake = FakeClient()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
            {"sport": "축구", "slug": "zzz.1", "path": "soccer/zzz.1", "name": "경기 없는 리그"}]}
        collect.run()
        first = sum(1 for u in fake.calls if "zzz.1" in u)
        assert first == 4, first                                   # 처음엔 날짜 4개 확인 (그저께~내일)
        collect.run()
        assert sum(1 for u in fake.calls if "zzz.1" in u) == first  # 두 번째엔 건너뜀
        assert sum(1 for u in fake.calls if "eng.1/scoreboard" in u) > 3   # 경기 있는 대회는 계속 확인
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



def test_national_team_has_no_rotation_card():
    """대표팀은 소집마다 명단이 바뀌어서 '로테이션 가능성'을 계산하지 않는다 (부상·징계만)."""
    class NatPreFake(NationalFake):
        def get_json(self, url, params=None, cache_key=None, ttl=0):
            if url.endswith("/summary"):
                self.count += 1
                return {"rosters": []}                      # 라인업 아직 없음
            return super().get_json(url, params, cache_key, ttl)
    tmpdir = tempfile.mkdtemp()
    try:
        fake = NatPreFake()
        collect.DATA_DIR = os.path.join(tmpdir, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmpdir, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.Client = lambda verbose=False: fake
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: {"mlb_enabled": False, "espn_leagues": [
            {"sport": "축구", "slug": "fifa.friendly", "path": "soccer/fifa.friendly", "name": "A매치 친선", "national": True}]}
        collect.run()
        files = os.listdir(collect.GAME_DIR)
        d = json.load(open(os.path.join(collect.GAME_DIR, files[0]), encoding="utf-8"))
        for side in ("home", "away"):
            r = (d.get("risk") or {}).get(side) or {}
            assert not r.get("level") and not (r.get("today") or {}).get("n")      # 로테이션 카드 없음
        assert not any("로테이션" in s[0] for s in d.get("signals") or [])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



def test_collect_window_covers_two_days_back():
    """해외 대회는 현지 날짜 기준이라, 한국 시간 어제 새벽·오전 경기가 빠지지 않게 그저께부터 본다."""
    days = collect.date_strings(NOW)
    assert len(days) == 4 and days[0] == (NOW - timedelta(days=2)).strftime("%Y%m%d")
    assert days[-1] == (NOW + timedelta(days=1)).strftime("%Y%m%d")



def test_doubleheader_and_postponed():
    """더블헤더 1·2차전 구분, 연기된 경기는 판정하지 않는다."""
    data = {"dates": [{"games": [
        {"gamePk": 1, "gameDate": "2026-09-24T17:35:00Z", "doubleHeader": "S", "gameNumber": 1,
         "status": {"abstractGameState": "Final", "detailedState": "Final"},
         "teams": {"home": {"team": {"id": 110, "name": "Baltimore Orioles"}, "score": 3},
                   "away": {"team": {"id": 141, "name": "Toronto Blue Jays"}, "score": 1}}},
        {"gamePk": 2, "gameDate": "2026-09-24T22:35:00Z", "doubleHeader": "S", "gameNumber": 2,
         "status": {"abstractGameState": "Preview", "detailedState": "Scheduled"},
         "teams": {"home": {"team": {"id": 110, "name": "Baltimore Orioles"}},
                   "away": {"team": {"id": 141, "name": "Toronto Blue Jays"}}}},
        {"gamePk": 3, "gameDate": "2026-09-24T23:05:00Z", "doubleHeader": "N", "gameNumber": 1,
         "status": {"abstractGameState": "Preview", "detailedState": "Postponed"},
         "teams": {"home": {"team": {"id": 147, "name": "New York Yankees"}},
                   "away": {"team": {"id": 139, "name": "Tampa Bay Rays"}}}}]}]}
    import parse
    games = parse.parse_mlb_schedule(data)
    assert [g["game_no"] for g in games] == [1, 2, None]         # 더블헤더만 차수를 붙인다
    assert [g["postponed"] for g in games] == [False, False, True]
    # 연기된 경기는 판정 대상에서 빠진다
    start = collect.to_kst(games[2]["start_kst"])
    assert collect.needs_detail(games[1], start) is True
    assert collect.needs_detail(games[2], start) is False



def test_finished_detail_is_archived_forever():
    """끝난 경기 상세는 월별 폴더에 영구 보관한다 (나중에 xG·선수 기여도 분석에 쓰려고)."""
    tmpdir = tempfile.mkdtemp()
    try:
        collect.ARCHIVE_DIR = os.path.join(tmpdir, "archive")
        collect.DETAIL_ARCHIVE = os.path.join(collect.ARCHIVE_DIR, "details")
        collect.kst_now = lambda: NOW
        game = {"key": "축구:esp.1:1", "sport": "축구", "league": "라리가", "league_slug": "esp.1",
                "start_kst": "2026-09-23T22:38:00+09:00", "state": "post", "national": False,
                "home": {"name": "레알", "score": 2}, "away": {"name": "헤타페", "score": 1}}
        detail = {"lineup_ready": True, "teams": {"home": {"grade": "2군", "players": [{"id": "1", "name": "음바페"}]}}}
        assert collect.keep_detail(game, detail) is True
        path = os.path.join(collect.DETAIL_ARCHIVE, "2026-09", collect.game_filename(game["key"]))
        saved = json.load(open(path, encoding="utf-8"))
        assert saved["home"] == "레알" and saved["score"] == [2, 1]
        assert saved["detail"]["teams"]["home"]["grade"] == "2군"
        assert collect.keep_detail(game, detail) is False          # 이미 있으면 다시 쓰지 않음
        # 끝나지 않았거나 판정 전이면 보관하지 않음
        assert collect.keep_detail(dict(game, state="pre"), detail) is False
        assert collect.keep_detail(game, {"lineup_ready": False}) is False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



def test_judgment_frozen_after_kickoff():
    """경기가 시작되면 경기 전 판정을 그대로 유지한다 (다시 계산하면 오늘 결과가 섞여 뒤집힌다)."""
    tmpdir = tempfile.mkdtemp()
    try:
        collect.GAME_DIR = os.path.join(tmpdir, "games")
        os.makedirs(collect.GAME_DIR)
        game = {"key": "야구:mlb:1", "sport": "야구", "state": "post", "start_kst": "2026-09-23T02:35:00+09:00",
                "home": {"name": "볼티모어"}, "away": {"name": "토론토"}}
        saved = {"lineup_ready": True, "power": {"home": 66, "away": 34, "verdict": "볼티모어 우위"},
                 "teams": {"home": {"grade": "1군"}, "away": {"grade": "1.5군"}}, "signals": [["토론토 주전 3명 결장", "bad"]],
                 "game": {"key": "야구:mlb:1"}}
        collect.write_json(os.path.join(collect.GAME_DIR, collect.game_filename(game["key"])), saved)
        after = collect.to_kst("2026-09-23T06:00:00+09:00")
        got = collect.frozen_detail(game, after)
        assert got and got["power"]["home"] == 66 and got["frozen"] is True     # 경기 전 판정 그대로
        assert "game" not in got                                                # 경기 정보는 새로 붙인다
        row = collect.detail_row(got)
        assert row["power"] == [66, 34] and row["grade_home"] == "1군" and row["lineup_ready"] is True
        # 시작 전이거나 판정 전이면 얼리지 않는다
        before = collect.to_kst("2026-09-23T01:00:00+09:00")
        assert collect.frozen_detail(game, before) is None
        assert collect.frozen_detail(dict(game, state="pre"), after) is None
        collect.write_json(os.path.join(collect.GAME_DIR, collect.game_filename("야구:mlb:2")), {"lineup_ready": False})
        assert collect.frozen_detail(dict(game, key="야구:mlb:2"), after) is None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"  ok  {name}")
    print(f"\n{n} passed")
