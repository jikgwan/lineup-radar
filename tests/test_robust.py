"""나쁜 상황에서도 수집기가 죽지 않는지: 네트워크 전부 실패, 이상한 응답."""

import json
import os
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect  # noqa: E402
from parse import KST  # noqa: E402

NOW = datetime(2026, 9, 18, 20, 0, tzinfo=KST)


class _S:
    def get(self, *a, **k):
        raise ConnectionError("offline")


class DeadClient:
    """모든 요청 실패."""
    def __init__(self):
        self.count = 0
        self.session = _S()

    def get_json(self, *a, **k):
        self.count += 1
        return None


class JunkClient:
    """형식이 엉망인 응답을 섞어서 준다."""
    JUNK = [None, {}, [], "text", 0, {"events": "x"}, {"events": [None, 1, "a", {"competitions": "x"}]},
            {"rosters": [None, {"roster": [None, {"athlete": None}]}]}, {"dates": [{"games": [None, {"teams": None}]}]}]

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.count = 0
        self.session = _S()

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        self.count += 1
        if url.endswith("/scoreboard") and self.rng.random() < 0.5:
            start = (NOW + timedelta(minutes=30)).astimezone(tz=None).strftime("%Y-%m-%dT%H:%M:%S%z")
            return {"events": [{"id": "1", "date": start, "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"id": "10", "displayName": "A"}},
                {"homeAway": "away", "team": {"id": "20", "displayName": "B"}}]}]}]}
        return self.rng.choice(self.JUNK)


def _run(client, cfg):
    tmp = tempfile.mkdtemp()
    try:
        collect.DATA_DIR = os.path.join(tmp, "data")
        collect.GAME_DIR = os.path.join(collect.DATA_DIR, "games")
        collect.CACHE_DIR = os.path.join(tmp, "cache")
        collect.ARCHIVE_DIR = os.path.join(tmp, "archive")
        collect.Client = lambda verbose=False: client
        collect.kst_now = lambda: NOW
        collect.load_config = lambda: cfg
        assert collect.run() == 0
        with open(os.path.join(collect.DATA_DIR, "games.json"), encoding="utf-8") as f:
            return json.load(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CFG = {"mlb_enabled": True, "espn_leagues": [
    {"sport": "축구", "slug": "eng.1", "path": "soccer/eng.1", "name": "EPL"},
    {"sport": "축구", "slug": "uefa.champions", "path": "soccer/uefa.champions", "name": "챔스"},
    {"sport": "축구", "slug": "fifa.friendly", "path": "soccer/fifa.friendly", "name": "A매치", "national": True},
    {"sport": "농구", "slug": "nba", "path": "basketball/nba", "name": "NBA"}],
    "home_advantage_slugs": ["fifa.friendly"], "league_strength": {"eng.1": 1.0}}


def test_offline_writes_empty_list():
    out = _run(DeadClient(), CFG)
    assert out["games"] == []


def test_junk_responses_never_crash():
    for seed in range(40):
        out = _run(JunkClient(seed), CFG)
        assert isinstance(out["games"], list)


def test_circuit_breaker_stops_hammering_dead_host():
    """사이트가 죽어 있으면 몇 번 실패 후 요청을 멈춘다 (실행 시간이 폭주하지 않게)."""
    class R:
        status_code = 503
        def raise_for_status(self):
            raise RuntimeError("503")
    calls = []
    class S:
        headers = {}
        def get(self, url, params=None, timeout=None, headers=None):
            calls.append(url)
            return R()
    tmp = tempfile.mkdtemp()
    try:
        collect.CACHE_DIR = os.path.join(tmp, "cache")
        saved = collect.time.sleep
        collect.time.sleep = lambda s: None
        c = collect.Client()
        c.session = S()
        for i in range(50):
            assert c.get_json(f"https://dead.example.com/x{i}") is None
        collect.time.sleep = saved
        assert len(calls) == collect.BREAKER_LIMIT * 3      # 6번 실패(각 3회 시도) 후 멈춤
        assert c.host_down("https://dead.example.com/y")
        assert not c.host_down("https://other.example.com/y")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_403_is_not_retried_and_404_is_not_failure():
    class R:
        def __init__(self, code): self.status_code = code
    seq = []
    class S:
        headers = {}
        def get(self, url, params=None, timeout=None, headers=None):
            seq.append(url)
            return R(403 if "forbid" in url else 404)
    tmp = tempfile.mkdtemp()
    try:
        collect.CACHE_DIR = os.path.join(tmp, "cache")
        c = collect.Client(); c.session = S()
        assert c.get_json("https://a.example.com/forbid") is None and len(seq) == 1
        for i in range(20):
            c.get_json(f"https://b.example.com/missing{i}")
        assert not c.host_down("https://b.example.com/")          # 404는 사이트 장애가 아님
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
