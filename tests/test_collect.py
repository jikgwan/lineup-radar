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


def soccer_roster(team_id, starters, subs_in=(), bench=()):
    roster = []
    for pid in starters:
        roster.append(
            {"starter": True, "jersey": "9", "athlete": {"id": pid, "displayName": f"P{pid}"},
             "position": {"abbreviation": "M"}}
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


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print(f"  ok  {name}")
    print(f"\n{n} passed")
