"""J1·MLS·A리그: ESPN이 라인업을 늦게 줄 때 풋몹 라인업으로 먼저 판정하는지."""

import os
import shutil
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect  # noqa: E402
import test_fotmob_cup as cup  # noqa: E402  (같은 풋몹 응답 모양을 그대로 쓴다)

NOW = cup.NOW                                   # 17:00 경기 40분 전
GAME = {"key": "축구:jpn.1:777", "sport": "축구", "league": "J리그", "league_slug": "jpn.1", "event_id": "777",
        "start_kst": "2026-09-23T17:00:00+09:00", "state": "pre", "national": False,
        "home": {"id": "11", "name": "감바 오사카", "name_en": "Gamba Osaka"},
        "away": {"id": "22", "name": "도쿠시마", "name_en": "Tokushima Vortis"}}
PRE = {"power": {"home": 60, "away": 40, "win": {"home": 50, "draw": 25, "away": 25}}, "signals": []}


class Fake(cup.Fake):
    """일왕배 응답을 J1(풋몹 223)으로 바꾼 것 + ESPN 요약은 라인업 없음."""

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        if url.endswith("/api/data/matches"):
            self.count += 1
            data = cup.match_list((params or {})["date"])
            for lg in data["leagues"]:
                if lg["id"] == 9011:
                    lg["id"] = lg["primaryId"] = 223
            return data
        if "/summary" in url:
            self.count += 1
            return {"rosters": []}
        return super().get_json(url, params, cache_key, ttl)


def _setup():
    tmp = tempfile.mkdtemp()
    collect.CACHE_DIR = os.path.join(tmp, "cache")
    return tmp


def test_fallback_uses_fotmob_lineup_and_keeps_espn_power():
    tmp = _setup()
    try:
        d = collect.fotmob_fallback_detail(Fake(), GAME, NOW, PRE)
        assert d and d["lineup_ready"] is True
        assert d["teams"]["home"]["grade"] != "1군"            # 감바는 주전 4명만 → 로테이션
        assert d["teams"]["away"]["grade"] == "1군"
        assert d["power"] == PRE["power"]                         # 전력·승률은 ESPN 기준 그대로
        assert "풋몹 라인업" in d["lineup_source"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fallback_waits_for_confirmed_lineup():
    tmp = _setup()
    try:
        # 예상 라인업(발표 전)이면 40분 전에는 판정하지 않는다 ('선발이 달라짐' 규칙은 쓰지 않음)
        assert collect.fotmob_fallback_detail(Fake(confirmed=False), GAME, NOW, PRE) is None
        # 30분 전이 지나면 예상 라인업이라도 판정 (풋몹 원래 규칙과 같음)
        assert collect.fotmob_fallback_detail(Fake(confirmed=False), GAME, NOW + timedelta(minutes=15), PRE)["lineup_ready"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fallback_skips_outside_window_other_leagues_and_unknown_match():
    tmp = _setup()
    try:
        f = Fake()
        assert collect.fotmob_fallback_detail(f, GAME, NOW - timedelta(hours=3), PRE) is None   # 3시간 40분 전
        assert collect.fotmob_fallback_detail(f, dict(GAME, league_slug="eng.1"), NOW, PRE) is None
        assert f.count == 0                                       # 둘 다 요청 없이 끝
        other = dict(GAME, home={"id": "1", "name": "x", "name_en": "Chelsea"}, away={"id": "2", "name": "y", "name_en": "Arsenal"})
        assert collect.fotmob_fallback_detail(Fake(), other, NOW, PRE) is None             # 두 팀 다 다르면 못 찾음
        # 한 팀 표기만 달라도 같은 리그·같은 시각이면 같은 경기로 (JEF United Ichihara-Chiba ↔ JEF United Chiba 같은 경우)
        renamed = dict(GAME, away={"id": "22", "name": "도쿠시마", "name_en": "Vortis Tokushima FC"})
        assert collect.fotmob_fallback_detail(Fake(), renamed, NOW, PRE)["lineup_ready"]
        # 시각이 다르면 한 팀만 맞는 걸로는 안 붙인다
        late = dict(renamed, start_kst="2026-09-23T18:00:00+09:00")
        assert collect.fotmob_fallback_detail(Fake(), late, NOW + timedelta(minutes=30), PRE) is None   # 70분 전(창 안)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_espn_detail_falls_back_when_espn_has_no_lineup():
    tmp = _setup()
    saved = collect.espn_pre_lineup
    try:
        collect.espn_pre_lineup = lambda client, game, now, extractor: dict(PRE)
        d = collect.build_espn_detail(Fake(), GAME, NOW)
        assert d["lineup_ready"] is True and d["power"] == PRE["power"]
        # 풋몹 대상이 아닌 리그는 예전처럼 '라인업 대기'
        d2 = collect.build_espn_detail(Fake(), dict(GAME, league_slug="eng.1"), NOW)
        assert d2["lineup_ready"] is False
    finally:
        collect.espn_pre_lineup = saved
        shutil.rmtree(tmp, ignore_errors=True)
