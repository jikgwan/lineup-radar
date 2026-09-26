#!/usr/bin/env python3
"""프로토 대상 리그의 경기 목록과 선발 라인업을 모아 docs/data/*.json 으로 저장한다.

소스
  축구 / 농구 : ESPN 공개 피드 (키 없음)
  야구(MLB)  : MLB 공식 Stats API (statsapi.mlb.com)

GitHub Actions에서 10분마다 실행되는 것을 전제로 짰다.
  - 끝난 경기의 기록은 변하지 않으므로 영구 캐시
  - 팀별 최근 출전 기록은 12시간 캐시
  - 한 번 실행에 쓰는 요청 수에 상한을 둬서 소스에 부담을 주지 않는다
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date as date_, datetime, timedelta, timezone

import requests

from names_ko import _key as team_key
from names_ko import ko_player, ko_team
from grade import (ELO_HOME, add_bench, add_context, core_from_history, elo_table, mark_returning, model_power, rotation_risk, add_periods, analyze_lineup, baseball_power, build_signals, compare_elo, compare_power,
                   enrich, lineup_power, pick_ace, recent_era, strength, summarize, team_leaders)
from parse import (
    KST,
    elo_for,
    parse_elo_names,
    parse_elo_world,
    bullpen_usage,
    parse_espn_schedule_events,
    parse_espn_team_ids,
    parse_espn_team_schedule,
    mlb_history_from_stats,
    pitcher_season,
    pitcher_starts,
    parse_fotmob_league_matches,
    parse_fotmob_lineups,
    parse_fotmob_matches,
    parse_fotmob_team_fixtures,
    parse_fotmob_unavailable,
    parse_kbo_preview,
    person_key,
    same_person,
    parse_kbo_record,
    parse_naver_games,
    parse_naver_lineup,
    parse_naver_record,
    parse_mlb_boxscore_side,
    parse_mlb_team_results,
    position_shape,
    parse_basketball_box_for_team,
    parse_basketball_lineup,
    parse_espn_scoreboard,
    parse_mlb_schedule,
    parse_soccer_lineup,
    parse_soccer_match_for_team,
    synth_history,
    to_kst,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "docs", "data")
GAME_DIR = os.path.join(DATA_DIR, "games")
ARCHIVE_DIR = os.path.join(ROOT, "archive")
DETAIL_ARCHIVE = os.path.join(ARCHIVE_DIR, "details")   # 끝난 경기의 상세 판정 영구 보관 (나중에 xG·선수 기여도 분석용)    # 경기 전 판정 + 실제 결과 영구 기록 (백테스트·점수 조정용)
CACHE_DIR = os.path.join(ROOT, "cache")

# ESPN: 깃허브 서버에서는 site.api 주소가 막히고(403) site.web 주소는 열린다 (2026-09 소스 점검 결과).
# site.web을 먼저 쓰고, 거부되면 같은 경로를 site.api로 한 번 더 시도한다.
ESPN_SITE = "https://site.web.api.espn.com/apis/site/v2/sports"
ESPN_FALLBACK = "https://site.api.espn.com/apis/site/v2/sports"
# site.web은 이 옵션이 있어야 날짜별 목록·라인업·팀 일정이 열린다 (5차 소스 점검에서 확인).
# 날짜는 하나씩만 가능하고 구간(20260901-20260910)은 400이 난다.
ESPN_PARAMS = {"region": "us", "lang": "en", "contentorigin": "espn"}
# 네이버 스포츠 (K리그1·2): 깃허브 서버에서 열리고 한글 이름·출전시간·평점까지 준다
NAVER_API = "https://api-gw.sports.naver.com"
NAVER_HEADERS = {"Referer": "https://m.sports.naver.com/", "Origin": "https://m.sports.naver.com"}
NAVER_CHUNK_DAYS = 14   # 네이버는 한 번에 최대 1000경기라 2주씩 끊어 받는다
MLB_API = "https://statsapi.mlb.com/api/v1"

HISTORY_MATCHES = 6          # 최근 몇 경기로 주전을 판정할지
NATIONAL_MATCHES = 20        # 대표팀은 최근 A매치 몇 경기까지 모을지
ELO_BASE = "https://www.eloratings.net"
SEASON_MATCHES = 40          # 시즌 전체 기록(득점·도움·출전)은 최근 몇 경기까지 모아서 볼지 (리그 한 시즌 38경기)
DETAIL_WINDOW_BEFORE = 6 * 60   # 경기 시작 몇 분 전부터 라인업을 확인할지
DETAIL_WINDOW_AFTER = 4 * 60    # 시작 후 몇 분까지 확인할지
MAX_DETAIL_GAMES = 40
MAX_REQUESTS = 800
REQUEST_PAUSE = 0.25
TIME_BUDGET = 6 * 60      # 한 번 실행에서 새로 받는 시간 한도(초). 넘으면 받은 것까지만 쓰고 다음 실행에서 이어받는다

USER_AGENT = "lineup-radar/1.0 (personal hobby project)"


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ HTTP

BREAKER_LIMIT = 6        # 같은 사이트가 연속 이만큼 실패하면 이번 실행에서는 더 요청하지 않음
NO_RETRY = {400, 401, 403, 404, 410}
NOT_FOUND = {400, 404, 410}   # '그 대회·경기엔 데이터 없음' — 사이트 장애로 세지 않는다


def _host(url):
    return url.split("/")[2] if "://" in url else url


class Client:
    def __init__(self, verbose=False):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.count = 0
        self.started = time.time()
        self.out_of_time = False
        self.verbose = verbose
        self.fails = {}      # 사이트별 연속 실패 수
        os.makedirs(CACHE_DIR, exist_ok=True)

    def host_down(self, url):
        return self.fails.get(_host(url), 0) >= BREAKER_LIMIT

    def note(self, url, ok):
        h = _host(url)
        if ok:
            self.fails[h] = 0
        else:
            self.fails[h] = self.fails.get(h, 0) + 1
            if self.fails[h] == BREAKER_LIMIT:
                print(f"  ! {h} 가 계속 응답하지 않아 이번 실행에서는 요청을 멈춥니다", file=sys.stderr)

    def get_json(self, url, params=None, cache_key=None, ttl=0):
        """ttl<0 이면 영구 캐시. 실패하면 None."""
        if cache_key:
            hit = cache_read(cache_key, ttl)
            if hit is not None:
                return hit
        if self.count >= MAX_REQUESTS:
            print("  ! 요청 상한에 도달해서 이번 실행은 여기까지만 수집합니다", file=sys.stderr)
            return None
        if time.time() - self.started > TIME_BUDGET:
            if not self.out_of_time:
                self.out_of_time = True
                print(f"  ! 시간 한도({TIME_BUDGET // 60}분)에 도달 — 받은 것까지만 쓰고 나머지는 다음 실행에서 이어받습니다", flush=True)
            return None
        if self.host_down(url):
            return None
        if url.startswith(ESPN_SITE):
            params = dict(ESPN_PARAMS, **(params or {}))
        for attempt in range(3):
            self.count += 1
            try:
                extra = NAVER_HEADERS if url.startswith(NAVER_API) else None
                res = self.session.get(url, params=params, timeout=20, headers=extra)
                if res.status_code in NO_RETRY:
                    # 400·404는 '없는 리그/경기'라 사이트 장애가 아니다. 401·403 거부만 실패로 센다.
                    self.note(url, res.status_code in NOT_FOUND)
                    if res.status_code in (401, 403) and url.startswith(ESPN_SITE):
                        # 주 주소가 거부하면 예비 주소로 한 번 더
                        return self.get_json(ESPN_FALLBACK + url[len(ESPN_SITE):], params, cache_key, ttl)
                    return None
                res.raise_for_status()
                data = res.json()
            except Exception as exc:  # 네트워크·JSON 오류 모두 여기서 흡수
                wait = (attempt + 1) * 1.5 + random.random()
                if self.verbose:
                    print(f"  재시도({attempt + 1}/3) {url} -> {exc}", file=sys.stderr)
                if attempt == 2:
                    self.note(url, False)
                    return None
                time.sleep(wait)
                continue
            self.note(url, True)
            time.sleep(REQUEST_PAUSE)
            if cache_key:
                cache_write(cache_key, data)
            return data
        return None


def get_text(client, url, cache_key, ttl):
    """TSV 같은 텍스트 응답. 캐시는 JSON 파일에 문자열로 저장."""
    hit = cache_read(cache_key, ttl)
    if isinstance(hit, dict) and isinstance(hit.get("text"), str):
        return hit["text"]
    if client.count >= MAX_REQUESTS:
        return None
    down = getattr(client, "host_down", None)
    note = getattr(client, "note", None)
    if down and down(url):
        return None
    for attempt in range(3):
        client.count += 1
        try:
            res = client.session.get(url, timeout=20, headers={"Accept": "text/plain,*/*"})
            if res.status_code in NO_RETRY:
                if note:
                    note(url, res.status_code in NOT_FOUND)
                return None
            res.raise_for_status()
            res.encoding = res.encoding or "utf-8"
            text = res.text
        except Exception:
            if attempt == 2:
                if note:
                    note(url, False)
                return None
            time.sleep((attempt + 1) * 1.5)
            continue
        if note:
            note(url, True)
        time.sleep(REQUEST_PAUSE)
        cache_write(cache_key, {"text": text})
        return text
    return None


def cache_path(key):
    return os.path.join(CACHE_DIR, safe_name(key) + ".json")


def cache_read(key, ttl):
    path = cache_path(key)
    if not os.path.exists(path):
        return None
    if ttl >= 0 and time.time() - os.path.getmtime(path) > ttl:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cache_write(key, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path(key), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ------------------------------------------------------------------ 목록 수집

def kst_now():
    return datetime.now(KST)


def date_strings(now, days=(-2, -1, 0, 1)):
    """수집할 날짜들. 해외 대회는 현지 날짜 기준이라, 한국 시간 '어제 새벽·오전' 경기가 빠지지 않게 이틀 전부터 본다."""
    return [(now + timedelta(days=d)).strftime("%Y%m%d") for d in days]


IDLE_TTL = 3 * 3600          # 경기가 없던 대회는 이만큼 건너뛴다 (대회가 많아서 목록 요청을 아낀다)


def collect_espn_games(client, now, leagues):
    games = []
    for lg in leagues:
        if cache_read(f"idle_{lg['slug']}", IDLE_TTL) is not None:
            continue
        found = 0
        for date in date_strings(now):
            data = client.get_json(
                f"{ESPN_SITE}/{lg['path']}/scoreboard",
                params={"dates": date},
                cache_key=f"sb_{lg['slug']}_{date}",
                ttl=300,
            )
            if not data:
                continue
            for g in parse_espn_scoreboard(data, lg["sport"], lg["slug"], lg["name"]):
                g["national"] = bool(lg.get("national"))
                games.append(g)
                found += 1
        if not found and not getattr(client, "out_of_time", False):
            cache_write(f"idle_{lg['slug']}", {"idle": True})     # 이번엔 경기가 없었음
    return games


def collect_mlb_games(client, now):
    start = (now - timedelta(days=2)).strftime("%Y-%m-%d")     # 미국 날짜 기준이라 한국 시간 어제 경기가 빠지지 않게
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    data = client.get_json(
        f"{MLB_API}/schedule",
        params={
            "sportId": 1,
            "startDate": start,
            "endDate": end,
            "hydrate": "lineups,probablePitcher,team",
        },
        cache_key=f"mlb_sched_{start}_{end}",
        ttl=300,
    )
    if not data:
        return []
    return parse_mlb_schedule(data)


def translate_names(games):
    """화면용 팀 이름을 한국어로 (영어 원래 이름은 name_en에 보관 — 대표팀 Elo 비교 등에 씀)."""
    for g in games:
        for side in ("home", "away"):
            t = g.get(side) or {}
            en = t.get("name_en") or t.get("name") or ""
            t["name_en"] = en
            t["name"] = ko_team(en)
            if t.get("short"):
                t["short"] = ko_team(t["short"]) if ko_team(en) == en else t["name"]
    return games


def dedupe(games):
    seen, out = set(), []
    for g in games:
        if g["key"] in seen:
            continue
        seen.add(g["key"])
        out.append(g)
    return out


def needs_detail(game, now):
    if not game.get("start_kst") or game.get("postponed"):
        return False
    start = to_kst(game["start_kst"])
    if not start:
        return False
    minutes = (now - start).total_seconds() / 60
    if game.get("state") == "post":
        return -DETAIL_WINDOW_BEFORE <= minutes <= DETAIL_WINDOW_AFTER + 180
    return -DETAIL_WINDOW_BEFORE <= minutes <= DETAIL_WINDOW_AFTER


# ------------------------------------------------------------------ 축구·농구 상세

def espn_team_history(client, sport_root, league_slug, team_id, before_kst, extractor):
    """팀의 최근 완료 경기에서 선발/출전 기록을 모은다.
    sport_root: 'soccer' 또는 'basketball'
    """
    cache_key = f"hist3_{league_slug}_{team_id}_{before_kst.strftime('%Y%m%d')}"
    cached = cache_read(cache_key, 12 * 3600)
    if cached is not None:
        return cached

    sched = client.get_json(
        f"{ESPN_SITE}/{sport_root}/{league_slug}/teams/{team_id}/schedule",
        cache_key=f"sched_{league_slug}_{team_id}",
        ttl=6 * 3600,
    )
    events = []
    for ev in ((sched if isinstance(sched, dict) else {}).get("events") or []):
        if not isinstance(ev, dict):
            continue
        comps = ev.get("competitions") or []
        if not comps:
            continue
        status = ((comps[0].get("status") or {}).get("type") or {})
        if not status.get("completed"):
            continue
        when = to_kst(ev.get("date"))
        if not when or when >= before_kst:
            continue
        slug = ((ev.get("league") or {}).get("slug")) or league_slug
        events.append((when, str(ev.get("id") or ""), slug))
    events.sort(key=lambda x: x[0], reverse=True)

    history = []
    for when, eid, slug in events[: SEASON_MATCHES + 3]:
        if len(history) >= SEASON_MATCHES:
            break
        if not eid:
            continue
        summary = client.get_json(
            f"{ESPN_SITE}/{sport_root}/{slug}/summary",
            params={"event": eid},
            cache_key=f"sum_{slug}_{eid}",
            ttl=-1,
        )
        if not summary:
            continue
        rec = extractor(summary, team_id)
        if rec and rec.get("starters"):
            if not rec.get("date"):
                rec["date"] = when.isoformat()
            history.append(rec)

    cache_write(cache_key, history)
    return history


DOMESTIC_RE = re.compile(r"^[a-z]{3}\.\d$")   # eng.1, esp.2, kor.1 … (자국 리그)


def domestic_candidates(cfg):
    return [lg["slug"] for lg in cfg.get("espn_leagues", []) if lg.get("sport") == "축구" and DOMESTIC_RE.match(lg["slug"])]


def domestic_slug(client, team_id, game_slug, cfg):
    """팀의 자국 리그 slug. 리그 경기면 그대로, 챔스 같은 대회면 설정된 리그를 돌며 찾는다(30일 캐시)."""
    cands = domestic_candidates(cfg)
    if game_slug in cands or not team_id:
        return game_slug
    key = f"domestic_{team_id}"
    hit = cache_read(key, 30 * 86400)
    if isinstance(hit, dict) and hit.get("slug"):
        return hit["slug"]
    for slug in cands:
        sched = client.get_json(f"{ESPN_SITE}/soccer/{slug}/teams/{team_id}/schedule",
                                cache_key=f"sched_{slug}_{team_id}", ttl=6 * 3600)
        done = 0
        for ev in ((sched if isinstance(sched, dict) else {}).get("events") or []):
            if not isinstance(ev, dict):
                continue
            comps = ev.get("competitions") or []
            if comps and ((comps[0].get("status") or {}).get("type") or {}).get("completed"):
                done += 1
        if done:
            cache_write(key, {"slug": slug})
            return slug
    cache_write(key, {"slug": game_slug})
    return game_slug


def national_slugs(cfg):
    return [lg["slug"] for lg in cfg.get("espn_leagues", []) if lg.get("national")]


def national_history(client, team_id, before_kst, cfg, extractor):
    """대표팀: 친선전·예선·본선 등 여러 대회 기록을 날짜순으로 합쳐 최근 NATIONAL_MATCHES경기."""
    cache_key = f"nathist_{team_id}_{before_kst.strftime('%Y%m%d')}"
    cached = cache_read(cache_key, 12 * 3600)
    if cached is not None:
        return cached
    events, seen = [], set()
    for slug in national_slugs(cfg):
        sched = client.get_json(f"{ESPN_SITE}/soccer/{slug}/teams/{team_id}/schedule",
                                cache_key=f"sched_{slug}_{team_id}", ttl=6 * 3600)
        for ev in ((sched if isinstance(sched, dict) else {}).get("events") or []):
            if not isinstance(ev, dict):
                continue
            comps = ev.get("competitions") or []
            if not comps or not ((comps[0].get("status") or {}).get("type") or {}).get("completed"):
                continue
            eid = str(ev.get("id") or "")
            when = to_kst(ev.get("date"))
            if not eid or eid in seen or not when or when >= before_kst:
                continue
            seen.add(eid)
            events.append((when, eid, slug))
    events.sort(key=lambda x: x[0], reverse=True)
    history = []
    for when, eid, slug in events[: NATIONAL_MATCHES + 3]:
        if len(history) >= NATIONAL_MATCHES:
            break
        summary = client.get_json(f"{ESPN_SITE}/soccer/{slug}/summary", params={"event": eid},
                                  cache_key=f"sum_{slug}_{eid}", ttl=-1)
        if not summary:
            continue
        rec = extractor(summary, team_id)
        if rec and rec.get("starters"):
            rec["date"] = rec.get("date") or when.isoformat()
            rec["comp"] = slug
            history.append(rec)
    cache_write(cache_key, history)
    return history


def load_elo(client):
    """eloratings.net 대표팀 레이팅과 이름표 (12시간 캐시)."""
    world = get_text(client, f"{ELO_BASE}/World.tsv", "elo_world", 12 * 3600)
    names = get_text(client, f"{ELO_BASE}/en.teams.tsv", "elo_names", 7 * 86400)
    if not world or not names:
        return {}, {}
    return parse_elo_world(world), parse_elo_names(names)


EURO_COMPS = ("uefa.champions", "uefa.europa", "uefa.europa.conf")

# ------------------------------------------------------------------ 새 전력 계산 (백테스트 model.json) · 리그 Elo
_MODEL = {}
CALENDAR_LEAGUES = {"jpn.1", "kor.1", "usa.1", "bra.1", "chn.1", "arg.1", "nor.1", "swe.1"}


def get_model():
    """model.json (백테스트가 만든 사이트용 계산값). 없으면 None → 예전 방식."""
    if "m" not in _MODEL:
        path = os.path.join(ROOT, "model.json")
        try:
            with open(path, encoding="utf-8") as f:
                _MODEL["m"] = json.load(f)
        except (OSError, ValueError):
            _MODEL["m"] = None
    return _MODEL["m"]


_ELO_MEMO = {}


def _elo_params():
    m = get_model() or {}
    e = m.get("elo") or {}
    return {"k": e.get("k", 20), "home": e.get("home", 60), "regress": e.get("season_regress", 1 / 3)}


def espn_league_elo(client, slug, start, must=()):
    """리그 전체 팀의 이번 시즌 + 지난 시즌 결과로 Elo (팀 일정은 캐시, 지난 시즌은 영구 저장)."""
    key = ("espn", slug, start.date())
    if key in _ELO_MEMO:
        return _ELO_MEMO[key]
    teams = client.get_json(f"{ESPN_SITE}/soccer/{slug}/teams", cache_key=f"teams_{slug}", ttl=7 * 86400)
    ids = list(dict.fromkeys(parse_espn_team_ids(teams or {}) + [t for t in must if t]))
    prev = start.year - 1 if (slug in CALENDAR_LEAGUES or start.month >= 7) else start.year - 2
    evs = {}
    for tid in ids:
        if getattr(client, "out_of_time", False):
            break
        url = f"{ESPN_SITE}/soccer/{slug}/teams/{tid}/schedule"
        for label, params, ck, ttl in (("c", None, f"sched_{slug}_{tid}", 6 * 3600),
                                       ("p", {"season": prev}, f"schedp_{slug}_{tid}_{prev}", -1)):
            data = client.get_json(url, params=params, cache_key=ck, ttl=ttl)
            for e in parse_espn_schedule_events(data or {}):
                if e["completed"] and e["hg"] is not None and to_kst(e["date"]) < start:
                    evs[e["id"]] = (e["date"], label, e["home_id"], e["away_id"], e["hg"], e["ag"])
    # 지난 시즌 경기가 먼저 오도록 시즌 표시를 날짜 순서와 맞춤
    rows = [(d, "0" if s == "p" else "1", h, a, hg, ag) for d, s, h, a, hg, ag in evs.values()]
    table = elo_table(rows, **_elo_params())
    _ELO_MEMO[key] = table
    return table


def naver_league_elo(client, cfg, cat, start):
    key = ("naver", cat, start.date())
    if key in _ELO_MEMO:
        return _ELO_MEMO[key]
    today = start.date()
    this_start = datetime.fromisoformat(cfg.get("naver_season_start", f"{today.year}-02-01")).date()
    spans = [("0", date_(this_start.year - 1, 2, 1), date_(this_start.year - 1, 12, 20)), ("1", this_start, today)]
    rows, seen = [], set()
    for label, frm, end in spans:
        d = frm
        while d <= end and not getattr(client, "out_of_time", False):
            to = min(d + timedelta(days=NAVER_CHUNK_DAYS - 1), end)
            data = naver_schedule(client, "kfootball", d, to, today)
            for g in (((data or {}).get("result") or {}).get("games") or []):
                if g.get("categoryId") != cat or str(g.get("statusCode")).upper() != "RESULT" or g.get("gameId") in seen:
                    continue
                try:
                    hg, ag = int(g.get("homeTeamScore")), int(g.get("awayTeamScore"))
                except (TypeError, ValueError):
                    continue
                seen.add(g["gameId"])
                rows.append((str(g.get("gameDateTime") or g.get("gameDate")), label, g.get("homeTeamCode"), g.get("awayTeamCode"), hg, ag))
            d = to + timedelta(days=1)
    table = elo_table(rows, **_elo_params())
    _ELO_MEMO[key] = table
    return table


def model_side(history):
    """팀 체급 입력: 이번 시즌 리그 경기당 득실차·득점·실점 (경기 적으면 0 쪽으로 조금 당김)"""
    rs = [m for m in history if m.get("gf") is not None and m.get("ga") is not None]
    n = len(rs)
    if not n:
        return {"gd": 0.0, "gf_pg": 1.35, "ga_pg": 1.35, "n": 0}
    gd = sum(m["gf"] - m["ga"] for m in rs) / n
    return {"gd": gd * n / (n + 2), "gf_pg": sum(m["gf"] for m in rs) / n, "ga_pg": sum(m["ga"] for m in rs) / n, "n": n}


JP_LEAGUES = ("jpn.",)                     # ESPN 일본 리그
JP_FOTMOB = ("fotmob.8974", "fotmob.9011")  # 풋몹 J2 · 일왕배


def is_jp_league(slug):
    """일본 리그면 선수 이름을 로마자 규칙으로 읽는다 (사전에 없을 때만)."""
    sl = str(slug or "")
    return sl.startswith(JP_LEAGUES) or sl in JP_FOTMOB


def korean_players(result, jp=False):
    """선발·교체·빠진 주전·에이스 이름을 한국어로 (사전에 없으면 영어 그대로). 영어 이름은 name_en에 남긴다."""
    for key in ("players", "bench", "missing", "returning"):
        for p in result.get(key) or []:
            ko = ko_player(p.get("name"), jp)
            if ko != p.get("name"):
                p["name_en"], p["name"] = p.get("name"), ko
    ace = result.get("ace")
    if ace and ace.get("name"):
        ko = ko_player(ace["name"], jp)
        if ko != ace["name"]:
            ace["name_en"], ace["name"] = ace["name"], ko
    for x in result.get("absent") or []:
        ko = ko_player(x.get("name"), jp)
        if ko != x.get("name"):
            x["name_en"], x["name"] = x.get("name"), ko
    return result


def model_signals(power, names):
    sig = []
    if not power or power.get("mode") != "model":
        return sig
    if power.get("confident") and power.get("fav"):
        sig.append([f"{names[power['fav']]} 우세 · 확신 높음", "good"])
    if (power.get("exp_goals") or 9) < 2.2:
        sig.append(["무승부 가능성 ↑ · 저득점 예상", "warn"])
    return sig

# ------------------------------------------------------------------ 풋몹: 부상·징계 결장자 (라인업 발표 전에도 있음)
FOTMOB = "https://www.fotmob.com/api/data"


def _team_match(a, b):
    ka, kb = team_key(a), team_key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka in kb or kb in ka or ko_team(a) == ko_team(b) != a


def fotmob_absences(client, game):
    """ESPN 경기와 같은 풋몹 경기를 찾아 양 팀 결장자(부상·징계·복귀 예정)를 돌려준다. 못 찾으면 None."""
    start = to_kst(game["start_kst"])
    if not start:
        return None
    names = {s: (game[s].get("name_en") or game[s]["name"]) for s in ("home", "away")}
    days = {start.strftime("%Y%m%d"), start.astimezone(timezone.utc).strftime("%Y%m%d")}
    found = None
    for d in sorted(days):
        data = client.get_json(f"{FOTMOB}/matches", params={"date": d}, cache_key=f"fm_{d}", ttl=1800)
        for m in parse_fotmob_matches(data or {}):
            if _team_match(m["home"], names["home"]) and _team_match(m["away"], names["away"]):
                found = m
                break
        if found:
            break
    if not found:
        return None
    det = client.get_json(f"{FOTMOB}/matchDetails", params={"matchId": found["id"]}, cache_key=f"fm_d_{found['id']}", ttl=1800)
    return parse_fotmob_unavailable(det) if det else None


def _en_name(p):
    """짝짓기는 영어 이름으로 (화면 이름은 한국어로 바뀌어 있을 수 있다)"""
    return p.get("name_en") or p.get("name")


def attach_absences(teams, absences, names, jp=False):
    """빠진 주전에 결장 사유를 붙이고, 주전이 부상·징계로 2명 이상 빠지면 신호."""
    sig = []
    if not absences:
        return sig
    for side in ("home", "away"):
        t = teams.get(side) or {}
        rows = absences.get(side) or []
        t["absent"] = rows
        hit, used = 0, set()
        missing = t.get("missing") or []
        # 1차: 이름이 똑같은 사람 · 2차: 남은 사람끼리 성·이니셜로 (한 결장자는 한 선수에게만)
        for exact in (True, False):
            for m in missing:
                if m.get("reason"):
                    continue
                for i, x in enumerate(rows):
                    if i in used:
                        continue
                    ok = (person_key(_en_name(m)) == person_key(_en_name(x))) if exact else same_person(_en_name(m), _en_name(x))
                    if ok:
                        m["reason"], m["return"] = x["type"], x["return"]
                        used.add(i)
                        hit += 1
                        break
        for x in rows:                                  # 결장자 이름도 한국어로 (짝짓기가 끝난 뒤)
            ko = ko_player(x.get("name"), jp)
            if ko != x.get("name"):
                x["name_en"], x["name"] = x.get("name"), ko
        if hit >= 2:
            sig.append([f"{names[side]} 주전 {hit}명 부상·징계", "bad"])
    return sig


def _risk_signals(names, risk):
    sig = []
    for side in ("home", "away"):
        r = risk.get(side) or {}
        nx = ((r.get("schedule") or {}).get("next")) or {}
        if r.get("level") == "높음":
            sig.append([f"{names[side]} 로테이션 가능성 ↑", "warn"])
        elif r.get("situation") == "big" and nx.get("in_days") is not None:
            # 패턴을 알 만큼 기록이 없어도, 곧 큰 경기라는 사실 자체는 알려준다
            sig.append([f"{names[side]} {nx['in_days']}일 뒤 {nx.get('comp') or ''}".strip(), "warn"])
    return sig


def pre_power(client, game, hist, slugs, start, cfg):
    """라인업 발표 전 전력 비교.
    백테스트에서 '오늘 라인업'은 결과 예측을 거의 못 바꿨고(개선 0.1% 수준) 팀 체급(득실차)과
    Elo가 대부분을 설명했다. 그래서 라인업이 나오기 전에도 같은 숫자를 미리 낼 수 있다."""
    if game.get("sport") != "축구":
        return None
    if not (hist.get("home") and hist.get("away")):
        return None
    hn, an = game["home"]["name"], game["away"]["name"]
    if game.get("national"):
        ratings, names = load_elo(client)
        eh, _ = elo_for(game["home"].get("name_en") or hn, ratings, names)
        ea, _ = elo_for(game["away"].get("name_en") or an, ratings, names)
        home_adv = ELO_HOME if game.get("league_slug") in (cfg.get("home_advantage_slugs") or []) else 0
        power = compare_elo({}, {}, hn, an, eh, ea, home_adv)
        if power:
            power["basis"] = {"elo": [round(eh), round(ea)]}
        return power
    model = get_model()
    sh, sa = slugs.get("home"), slugs.get("away")
    if not (model and sh and sh == sa and sh not in EURO_COMPS):
        return None                       # 챔스처럼 리그가 다른 팀끼리는 발표 전 숫자를 안 낸다
    elo = espn_league_elo(client, sh, start, must=(game["home"]["id"], game["away"]["id"]))
    h = dict(model_side(hist["home"]), elo=elo.get(game["home"]["id"], 1500.0))
    a = dict(model_side(hist["away"]), elo=elo.get(game["away"]["id"], 1500.0))
    return model_power(model, sh, h, a, hn, an)


def espn_pre_lineup(client, game, now, extractor):
    """라인업 발표 전: 양 팀의 로테이션 가능성 (지난 경기 패턴 + 오늘 일정).
    대표팀은 소집마다 명단이 바뀌어 '로테이션'으로 볼 수 없어서 이 계산을 하지 않는다 (부상·징계만 보여준다)."""
    jp_names = is_jp_league(game.get("league_slug"))
    start = to_kst(game["start_kst"]) or now
    cfg = load_config()
    risk, hist, slug_of = {}, {}, {}
    for side in ("home", "away"):
        team_id = game[side]["id"]
        if not team_id:
            continue
        if game.get("national"):
            history = national_history(client, team_id, start, cfg, extractor)
            slugs = national_slugs(cfg)
        else:
            slug = game["league_slug"]
            if slug in EURO_COMPS:
                slug = domestic_slug(client, team_id, slug, cfg) or slug
            history = espn_team_history(client, "soccer", slug, team_id, start, extractor)
            slugs = [slug] + [s for s in EURO_COMPS if s in {lg["slug"] for lg in cfg.get("espn_leagues", [])} and s != slug]
            slug_of[side] = slug
        hist[side] = history
        events = espn_team_events(client, "soccer", slugs, team_id)
        ctx = espn_schedule_context(client, "soccer", slugs, team_id, start, events=events)
        core = core_from_history(history[:HISTORY_MATCHES], 11)
        r = None if game.get("national") else rotation_risk(history, events, core, 11, ctx.get("next"),
                                                            (ctx.get("last") or {}).get("days_ago"))
        if r:
            r["schedule"] = ctx
            r["core_names"] = [m.get("name") for m in core]
            r["tired"] = [ko_player(x, jp_names) for x in r.get("tired") or []]
            risk[side] = r
        elif game.get("national"):
            risk[side] = {"level": None, "situation": "normal", "situation_text": "평소", "today": {"n": 0}, "base": {"n": 0},
                          "tired": [], "reason": "", "size": 11, "core_names": [m.get("name") for m in core]}
    names = {s: game[s]["name"] for s in ("home", "away")}
    sig = []
    try:
        ab = fotmob_absences(client, game)
    except Exception as exc:
        ab = None
        print(f"  ! 풋몹 결장자 실패: {exc}", file=sys.stderr)
    if ab:
        for side in ("home", "away"):
            rows = ab.get(side) or []
            r = risk.setdefault(side, {"level": None, "situation": "normal", "situation_text": "평소", "today": {"n": 0},
                                       "base": {"n": 0}, "tired": [], "reason": "", "size": 11, "core_names": []})
            core_out = [x for x in rows if any(same_person(n, x["name"]) for n in r.get("core_names") or [])]
            for x in rows:
                ko = ko_player(x.get("name"), jp_names)
                if ko != x.get("name"):
                    x["name_en"], x["name"] = x.get("name"), ko
            r["absent"] = rows
            r["core_absent"] = [x["name"] for x in core_out]
            if len(core_out) >= 2:
                sig.append([f"{names[side]} 주전 {len(core_out)}명 부상·징계", "bad"])
    power = None
    try:
        power = pre_power(client, game, hist, slug_of, start, cfg)
    except Exception as exc:                  # 전력 숫자가 안 나와도 나머지는 그대로
        print(f"  ! 발표 전 전력 계산 실패: {exc}", file=sys.stderr)
    return {"risk": risk, "power": power,
            "signals": (sig + model_signals(power, names) + _risk_signals(names, risk))[:4]}


def _ctx(last, nxt, start):
    out = {"last": None, "next": None}
    if last:
        d = to_kst(last["date"])
        out["last"] = dict(last, days_ago=(start.date() - d.date()).days if d else None)
    if nxt:
        d = to_kst(nxt["date"])
        out["next"] = dict(nxt, in_days=(d.date() - start.date()).days if d else None)
    return out


def espn_team_events(client, sport_root, slugs, team_id):
    """팀의 전체 일정(리그 + 유럽 대회 등) — 이미 받아둔 일정 캐시를 쓴다."""
    cfg = load_config()
    names = {lg["slug"]: lg["name"] for lg in cfg.get("espn_leagues", [])}
    out = []
    for slug in slugs:
        sched = client.get_json(f"{ESPN_SITE}/{sport_root}/{slug}/teams/{team_id}/schedule",
                                cache_key=f"sched_{slug}_{team_id}", ttl=6 * 3600)
        for r in parse_espn_team_schedule(sched or {}, team_id):
            r["comp"] = names.get(slug, slug)
            out.append(r)
    return out


def espn_schedule_context(client, sport_root, slugs, team_id, start, events=None):
    """지난 경기·다음 경기 (리그 + 유럽 대회 일정에서)."""
    past, future = [], []
    for r in (events if events is not None else espn_team_events(client, sport_root, slugs, team_id)):
        when = to_kst(r["date"])
        if not when:
            continue
        if r["completed"] and when < start:
            past.append((when, r))
        elif not r["completed"] and when > start + timedelta(minutes=90):
            future.append((when, r))
    last = dict(max(past, key=lambda x: x[0])[1]) if past else None
    if last and last.get("gf") is not None and last.get("ga") is not None:
        last["res"] = "W" if last["gf"] > last["ga"] else "L" if last["gf"] < last["ga"] else "D"
    nxt = min(future, key=lambda x: x[0])[1] if future else None
    return _ctx(last, nxt, start)

def naver_schedule_context(client, game, side, history, start):
    last = None
    if history:
        h = history[0]
        last = {"date": h.get("date"), "comp": game.get("league"), "opp": h.get("opp"), "home": h.get("home"),
                "gf": h.get("gf"), "ga": h.get("ga"), "res": h.get("res")}
    code = game[side]["id"]
    today = start.date()
    data = client.get_json(f"{NAVER_API}/schedule/games",
                           params={"fields": "basic,categoryName", "upperCategoryId": "kfootball", "size": 1000,
                                   "fromDate": today.isoformat(), "toDate": (today + timedelta(days=30)).isoformat()},
                           cache_key=f"nvnext_{today.isoformat()}", ttl=3600)
    nxt = None
    for g in (((data or {}).get("result") or {}).get("games") or []):
        if code not in (g.get("homeTeamCode"), g.get("awayTeamCode")) or g.get("gameId") == game.get("event_id"):
            continue
        when = parse_naver_games({"result": {"games": [g]}}, {g.get("categoryId"): g.get("categoryName") or ""})
        if not when:
            continue
        t = to_kst(when[0]["start_kst"])
        if t and t > start + timedelta(minutes=90) and (nxt is None or t < to_kst(nxt["date"])):
            mine_home = g.get("homeTeamCode") == code
            nxt = {"date": when[0]["start_kst"], "comp": g.get("categoryName") or "", "home": mine_home,
                   "opp": g.get("awayTeamName") if mine_home else g.get("homeTeamName")}
    return _ctx(last, nxt, start)


def build_espn_detail(client, game, now):
    sport_root = "soccer" if game["sport"] == "축구" else "basketball"
    league_slug = game["league_slug"]
    summary = client.get_json(
        f"{ESPN_SITE}/{sport_root}/{league_slug}/summary",
        params={"event": game["event_id"]},
        cache_key=f"live_{league_slug}_{game['event_id']}",
        ttl=180,
    )
    if not summary:
        return {"lineup_ready": False, "note": "경기 정보를 불러오지 못했습니다."}

    if game["sport"] == "축구":
        sides = parse_soccer_lineup(summary)
        extractor = parse_soccer_match_for_team
        size = 11
    else:
        sides = parse_basketball_lineup(summary)
        extractor = parse_basketball_box_for_team
        size = 5

    size_need = {"축구": 9, "농구": 4}.get(game["sport"], 7)      # 이만큼은 있어야 판정 (11명 중 9명 등)
    counts = [len((sides.get(s) or {}).get("lineup") or []) for s in ("home", "away")] if sides else [0, 0]
    if not sides or max(counts) == 0:
        out = {"lineup_ready": False, "note": "아직 선발 라인업이 나오지 않았습니다."}
        if game["sport"] == "축구":
            try:
                out.update(espn_pre_lineup(client, game, now, extractor))
            except Exception as exc:                   # 발표 전 분석이 실패해도 '라인업 대기'는 그대로 보여준다
                print(f"  ! 발표 전 분석 실패: {exc}", file=sys.stderr)
        return out

    start = to_kst(game["start_kst"]) or now
    cfg = load_config()
    if min(counts) < size_need:                                  # 한쪽이라도 선수가 모자라면 판정하지 않는다
        return {"lineup_ready": False,
                "note": f"라인업 정보가 부족해요 (확인된 선발 {counts[0]}명 · {counts[1]}명). 발표되면 판정해요."}
    teams, slugs = {}, {}
    # 명단은 팀 ID로 먼저 맞추고(홈/원정 표기가 틀려도 안전), 못 찾으면 표기대로
    by_id = {str(v.get("team_id")): v for v in sides.values() if isinstance(v, dict) and v.get("team_id")}
    for side in ("home", "away"):
        info = by_id.get(str(game[side]["id"])) or sides.get(side) or {}
        lineup = info.get("lineup") or []
        team_id = info.get("team_id") or game[side]["id"]
        national = bool(game.get("national"))
        if national:
            hist_slug = "national"
            history = national_history(client, team_id, start, cfg, extractor) if team_id else []
        else:
            hist_slug = domestic_slug(client, team_id, league_slug, cfg) if game["sport"] == "축구" else league_slug
            history = (
                espn_team_history(client, sport_root, hist_slug, team_id, start, extractor) if team_id else []
            )
        slugs[side] = hist_slug
        recent = history[:HISTORY_MATCHES]
        result = analyze_lineup(lineup, recent, size)
        mark_returning(result, recent, history, size)          # 장기 결장 후 복귀한 주전 인정
        formation = info.get("formation") or ""
        enrich(result, lineup, recent, season=history, formation=formation,
               shape=position_shape if game["sport"] == "축구" else None)
        add_bench(result, info.get("bench") or [], recent, season=history)
        strength(result, recent, season=history)
        add_periods(result, recent, history)
        result["ace"] = pick_ace(result)
        if game["sport"] == "축구":
            lineup_power(result, history, shape=position_shape)
            add_context(result, history, "축구")
            result["model_in"] = model_side(history)
            if team_id:
                if national:
                    sl = national_slugs(cfg)
                else:
                    sl = [hist_slug] + [s for s in EURO_COMPS if s in {lg["slug"] for lg in cfg.get("espn_leagues", [])} and s != hist_slug]
                result["schedule"] = espn_schedule_context(client, sport_root, sl, team_id, start)
        korean_players(result, is_jp_league(hist_slug))
        result["league"] = hist_slug
        result["leaders"]["labels"] = ["최다 득점", "최다 도움"] if game["sport"] == "축구" else ["최다 득점", "최다 어시스트"]
        result["team_name"] = game[side]["name"] or info.get("team_name") or ""
        result["formation"] = formation
        teams[side] = result
    power = None
    if game["sport"] == "축구" and game.get("national"):
        ratings, names = load_elo(client)
        eh, ch = elo_for(game["home"].get("name_en") or game["home"]["name"], ratings, names)
        ea, ca = elo_for(game["away"].get("name_en") or game["away"]["name"], ratings, names)
        home_adv = ELO_HOME if league_slug in (cfg.get("home_advantage_slugs") or []) else 0
        power = compare_elo(teams["home"], teams["away"], game["home"]["name"], game["away"]["name"], eh, ea, home_adv)
        if power is None:                                        # Elo에 없는 대표팀(작은 나라 등)은 최근 성적으로
            power = compare_power(teams["home"], teams["away"], game["home"]["name"], game["away"]["name"])
            if power:
                power["note"] = (power.get("note") or "") or "Elo 점수가 없어 최근 성적으로 비교했어요"
        teams["home"]["elo_code"], teams["away"]["elo_code"] = ch, ca
    elif game["sport"] == "축구":
        ls = cfg.get("league_strength") or {}
        fh = fa = 1.0
        if slugs["home"] != slugs["away"]:   # 리그가 다를 때만 리그 수준 보정
            fh, fa = ls.get(slugs["home"], ls.get("default", 0.8)), ls.get(slugs["away"], ls.get("default", 0.8))
        model = get_model()
        same_league = slugs["home"] == slugs["away"] and league_slug not in EURO_COMPS
        if model and same_league and teams["home"].get("model_in") and teams["away"].get("model_in"):
            elo = espn_league_elo(client, slugs["home"], start, must=(game["home"]["id"], game["away"]["id"]))
            h = dict(teams["home"]["model_in"], elo=elo.get(game["home"]["id"], 1500.0))
            a = dict(teams["away"]["model_in"], elo=elo.get(game["away"]["id"], 1500.0))
            power = model_power(model, slugs["home"], h, a, game["home"]["name"], game["away"]["name"], teams)
        else:                                     # 챔스처럼 리그가 다른 팀끼리 · model.json 없을 때: 예전 방식
            power = compare_power(teams["home"], teams["away"], game["home"]["name"], game["away"]["name"], fh, fa)
    names = {s: game[s]["name"] for s in ("home", "away")}
    extra = []
    if game["sport"] == "축구":
        try:
            extra = attach_absences(teams, fotmob_absences(client, game), names, is_jp_league(league_slug))
        except Exception as exc:                 # 풋몹이 막혀도 판정은 그대로
            print(f"  ! 풋몹 결장자 실패: {exc}", file=sys.stderr)
    return {"lineup_ready": True, "teams": teams, "power": power, "national": bool(game.get("national")),
            "signals": (extra + model_signals(power, names) + build_signals(names, teams, game["sport"]))[:4],
            "summary": summarize(game["home"]["name"], game["away"]["name"], teams, game["sport"])}


# ------------------------------------------------------------------ 네이버 (K리그1·2)

def naver_categories(cfg):
    """{(upperCategoryId, 종목): {categoryId: 리그 이름}}"""
    out = {}
    for lg in cfg.get("naver_leagues", []):
        out.setdefault((lg["upper"], lg.get("sport", "축구")), {})[lg["category"]] = lg["name"]
    return out


def naver_schedule(client, upper, frm, to, today):
    """네이버 경기 목록 한 구간 (지난 구간은 영구 캐시)."""
    past = to < (today - timedelta(days=2))
    return client.get_json(
        f"{NAVER_API}/schedule/games",
        params={"fields": "basic", "upperCategoryId": upper, "fromDate": frm.isoformat(), "toDate": to.isoformat(), "size": 1000},
        cache_key=f"nvsched_{upper}_{frm.isoformat()}_{to.isoformat()}", ttl=-1 if past else 300,
    )


def collect_naver_games(client, now, cfg):
    games = []
    today = now.date()
    for (upper, sport), cats in naver_categories(cfg).items():
        data = naver_schedule(client, upper, today - timedelta(days=1), today + timedelta(days=1), today)
        if data:
            games.extend(parse_naver_games(data, cats, sport))
    return games


def naver_team_history(client, cfg, game, side, before_kst):
    """그 팀의 이번 시즌 같은 리그 끝난 경기 (최신순, 최대 SEASON_MATCHES)."""
    code, cat = game[side]["id"], game.get("category")
    upper = next((lg["upper"] for lg in cfg.get("naver_leagues", []) if lg["category"] == cat), "kfootball")
    cache_key = f"nvhist_{cat}_{code}_{before_kst.strftime('%Y%m%d')}"
    cached = cache_read(cache_key, 12 * 3600)
    if cached is not None:
        return cached
    today = before_kst.date()
    start = datetime.fromisoformat(cfg.get("naver_season_start", f"{today.year}-02-01")).date()
    rows, seen = [], set()
    frm = start
    while frm <= today:
        to = min(frm + timedelta(days=NAVER_CHUNK_DAYS - 1), today)
        data = naver_schedule(client, upper, frm, to, today)
        for g in (((data or {}).get("result") or {}).get("games") or []):
            if g.get("categoryId") != cat or str(g.get("statusCode")).upper() != "RESULT" or g.get("gameId") in seen:
                continue
            if code not in (g.get("homeTeamCode"), g.get("awayTeamCode")):
                continue
            when = parse_naver_games({"result": {"games": [g]}}, {cat: ""})
            if not when or to_kst(when[0]["start_kst"]) >= before_kst:
                continue
            seen.add(g.get("gameId"))
            rows.append((when[0]["start_kst"], g))
        frm = to + timedelta(days=1)
    rows.sort(key=lambda x: x[0], reverse=True)

    history = []
    for when, g in rows[:SEASON_MATCHES]:
        gid = g["gameId"]
        lineup = client.get_json(f"{NAVER_API}/schedule/games/{gid}/lineup", cache_key=f"nvlu_{gid}", ttl=-1)
        record = client.get_json(f"{NAVER_API}/schedule/games/{gid}/record", cache_key=f"nvrec_{gid}", ttl=-1)
        if not lineup:
            continue
        mine = "home" if g.get("homeTeamCode") == code else "away"
        other = "away" if mine == "home" else "home"
        rec = parse_naver_record(record or {}, lineup, mine)
        if not rec["starters"]:
            continue
        gf, ga = g.get(f"{mine}TeamScore"), g.get(f"{other}TeamScore")
        try:
            gf, ga = int(gf), int(ga)
            res = "W" if gf > ga else "L" if gf < ga else "D"
        except (TypeError, ValueError):
            gf = ga = None
            res = ""
        rec.update({"date": when, "opp": g.get(f"{other}TeamName") or "", "home": mine == "home",
                    "gf": gf, "ga": ga, "res": res})
        history.append(rec)
    if getattr(client, "out_of_time", False):
        return history          # 시간 한도로 덜 받았으면 캐시하지 않고 다음에 이어받기
    cache_write(cache_key, history)
    return history


NAVER_COMP = {"kleague": "K리그1", "kleague2": "K리그2", "acl": "ACL", "acl2": "ACL2", "facup": "코리아컵",
              "kfootballetc": "국내 컵", "amatch": "A매치"}


def naver_team_events(client, cfg, game, side, start):
    """네이버 일정 구간(이미 받아둔 캐시)에서 이 팀의 모든 대회 경기."""
    code = game[side]["id"]
    today = start.date()
    first = datetime.fromisoformat(cfg.get("naver_season_start", f"{today.year}-02-01")).date()
    out, frm = [], first
    while frm <= today:
        to = min(frm + timedelta(days=NAVER_CHUNK_DAYS - 1), today)
        data = naver_schedule(client, "kfootball", frm, to, today)
        for g in (((data or {}).get("result") or {}).get("games") or []):
            if code in (g.get("homeTeamCode"), g.get("awayTeamCode")):
                w = parse_naver_games({"result": {"games": [g]}}, {g.get("categoryId"): ""})
                if w:
                    out.append({"date": w[0]["start_kst"], "comp": NAVER_COMP.get(g.get("categoryId"), g.get("categoryId") or ""),
                                "completed": str(g.get("statusCode")).upper() == "RESULT"})
        frm = to + timedelta(days=1)
    nxt = naver_schedule_context(client, game, side, [], start).get("next")
    if nxt:
        out.append({"date": nxt["date"], "comp": nxt.get("comp") or "", "completed": False})
    return out


def naver_pre_lineup(client, game, now):
    cfg = load_config()
    start = to_kst(game["start_kst"]) or now
    risk, hist = {}, {}
    for side in ("home", "away"):
        history = naver_team_history(client, cfg, game, side, start)
        hist[side] = history
        events = naver_team_events(client, cfg, game, side, start)
        ctx = naver_schedule_context(client, game, side, history, start)
        core = core_from_history(history[:HISTORY_MATCHES], 11)
        r = rotation_risk(history, events, core, 11, ctx.get("next"), (ctx.get("last") or {}).get("days_ago"))
        if r:
            r["schedule"] = ctx
            r["tired"] = [ko_player(x) for x in r.get("tired") or []]
            risk[side] = r
    names = {s: game[s]["name"] for s in ("home", "away")}
    power = None
    model = get_model()
    if model and hist.get("home") and hist.get("away"):        # 발표 전에도 전력은 낼 수 있다
        try:
            elo = naver_league_elo(client, cfg, game.get("category"), start)
            h = dict(model_side(hist["home"]), elo=elo.get(game["home"]["id"], 1500.0))
            a = dict(model_side(hist["away"]), elo=elo.get(game["away"]["id"], 1500.0))
            power = model_power(model, game.get("category"), h, a, names["home"], names["away"])
        except Exception as exc:
            print(f"  ! 발표 전 전력 계산 실패: {exc}", file=sys.stderr)
    return {"risk": risk, "power": power,
            "signals": (model_signals(power, names) + _risk_signals(names, risk))[:4]}


def build_naver_detail(client, game, now):
    cfg = load_config()
    data = client.get_json(f"{NAVER_API}/schedule/games/{game['event_id']}/lineup",
                           cache_key=f"nvlive_{game['event_id']}", ttl=180)
    sides = parse_naver_lineup(data or {})
    if not any((sides.get(s) or {}).get("lineup") for s in ("home", "away")):
        out = {"lineup_ready": False, "note": "아직 선발 라인업이 나오지 않았습니다."}
        try:
            out.update(naver_pre_lineup(client, game, now))
        except Exception as exc:
            print(f"  ! 발표 전 분석 실패: {exc}", file=sys.stderr)
        return out
    start = to_kst(game["start_kst"]) or now
    teams = {}
    for side in ("home", "away"):
        info = sides.get(side) or {}
        lineup = info.get("lineup") or []
        history = naver_team_history(client, cfg, game, side, start)
        recent = history[:HISTORY_MATCHES]
        result = analyze_lineup(lineup, recent, 11)
        mark_returning(result, recent, history, 11)
        enrich(result, lineup, recent, season=history, formation=info.get("formation") or "", shape=None)
        result["rows"] = info.get("rows") or []
        result["leaders"]["labels"] = ["최다 득점", "최다 도움"]
        add_bench(result, info.get("bench") or [], recent, season=history)
        strength(result, recent, season=history)
        add_periods(result, recent, history)
        result["ace"] = pick_ace(result)
        lineup_power(result, history, shape=position_shape)
        add_context(result, history, "축구")
        result["model_in"] = model_side(history)
        result["schedule"] = naver_schedule_context(client, game, side, history, start)
        result["team_name"] = game[side]["name"]
        result["formation"] = info.get("formation") or ""
        result["league"] = game.get("league_slug")
        teams[side] = result
    model = get_model()
    if model and teams["home"].get("model_in") and teams["away"].get("model_in"):
        elo = naver_league_elo(client, load_config(), game.get("category"), start)
        h = dict(teams["home"]["model_in"], elo=elo.get(game["home"]["id"], 1500.0))
        a = dict(teams["away"]["model_in"], elo=elo.get(game["away"]["id"], 1500.0))
        power = model_power(model, game.get("category"), h, a, game["home"]["name"], game["away"]["name"], teams)
    else:
        power = compare_power(teams["home"], teams["away"], game["home"]["name"], game["away"]["name"])
    names = {s: game[s]["name"] for s in ("home", "away")}
    return {"lineup_ready": True, "teams": teams, "power": power, "national": False,
            "signals": (model_signals(power, names) + build_signals(names, teams, "축구"))[:4],
            "summary": summarize(game["home"]["name"], game["away"]["name"], teams, "축구")}


# ------------------------------------------------------------------ MLB 상세

def mlb_team_regulars(client, team_id, season):
    cache_key = f"mlbreg4_{team_id}_{season}"
    cached = cache_read(cache_key, 12 * 3600)
    if cached is not None:
        return cached

    team_stats = client.get_json(
        f"{MLB_API}/teams/{team_id}/stats",
        params={"stats": "season", "group": "hitting", "season": season},
        cache_key=f"mlbteam_{team_id}_{season}",
        ttl=12 * 3600,
    )
    team_games = 0
    team_runs = 0
    for block in (team_stats or {}).get("stats", []) or []:
        for split in block.get("splits", []) or []:
            try:
                st = split.get("stat") or {}
                g = int(float(st.get("gamesPlayed") or 0))
                if g >= team_games:
                    team_games, team_runs = g, int(float(st.get("runs") or 0))
            except (TypeError, ValueError):
                pass

    roster = client.get_json(
        f"{MLB_API}/teams/{team_id}/roster",
        params={"rosterType": "active"},
        cache_key=f"mlbroster_{team_id}",
        ttl=12 * 3600,
    )
    ids = []
    for entry in (roster or {}).get("roster", []) or []:
        pid = ((entry.get("person") or {}).get("id"))
        pos = ((entry.get("position") or {}).get("abbreviation") or "")
        if pid and pos != "P":
            ids.append(str(pid))
    if not ids or team_games <= 0:
        return {"regulars": [], "names": {}, "team_games": team_games}

    people = client.get_json(
        f"{MLB_API}/people",
        params={
            "personIds": ",".join(ids),
            "hydrate": f"stats(group=[hitting],type=[season],season={season})",
        },
        cache_key=f"mlbpeople_{team_id}_{season}",
        ttl=12 * 3600,
    )
    info = mlb_history_from_stats((people or {}).get("people", []), team_games)
    info["team_games"] = team_games
    info["off_rpg"] = round(team_runs / team_games, 2) if team_games else None
    info["bats"] = {str(p.get("id")): ((p.get("batSide") or {}).get("code") or "")
                    for p in (people or {}).get("people", []) or []}
    info["results"] = mlb_team_form(client, team_id)
    info["form"] = info["results"][:5]
    cache_write(cache_key, info)
    return info


def mlb_team_form(client, team_id):
    """최근 약 2주 끝난 경기 (최신순, gamePk 포함)."""
    today = kst_now()
    start = (today - timedelta(days=16)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    data = client.get_json(
        f"{MLB_API}/schedule",
        params={"sportId": 1, "teamId": team_id, "startDate": start, "endDate": end},
        cache_key=f"mlbform_{team_id}_{end}",
        ttl=6 * 3600,
    )
    return parse_mlb_team_results(data, team_id)[:15] if data else []


MLB_RECENT = 20      # 주전 판정에 쓸 최근 경기 수 (야구는 매일 경기라 10경기면 2주도 안 된다)
MLB_SEASON = 40      # 로테이션했을 때 성적 등 통계에 쓸 경기 수 (경기 기록은 영구 저장이라 처음만 오래 걸린다)


def mlb_team_history(client, results):
    """최근 경기 박스스코어에서 선발 타순·출전 선수 (축구의 '최근 6경기 선발'과 같은 방식)."""
    history = []
    for r in results[:MLB_SEASON]:
        pk = r.get("gamePk")
        if not pk:
            continue
        box = client.get_json(f"{MLB_API}/game/{pk}/boxscore", cache_key=f"mlbbox_{pk}", ttl=-1)
        if not box:
            continue
        rec = parse_mlb_boxscore_side(box, r.get("side") or "home")
        if len(rec["starters"]) >= 9:
            rec.update({k: r.get(k) for k in ("date", "opp", "home", "gf", "ga", "res")})
            history.append(rec)
    return history


NAVER_MLB_FIX = {"시카고화이트삭스": "시카고w", "시카고컵스": "시카고컵스"}


def _compact(name):
    t = str(name or "").replace(" ", "").lower()
    return NAVER_MLB_FIX.get(t, t)


def naver_mlb_starters(client, game):
    """네이버 MLB 미리보기의 한국어 선발투수 이름 {"home": "로돈", "away": "마르티네즈"} (못 찾으면 {})."""
    start = to_kst(game["start_kst"])
    if not start:
        return {}
    day = start.date()
    data = naver_schedule(client, "wbaseball", day, day, kst_now().date())
    for g in (((data or {}).get("result") or {}).get("games") or []):
        if g.get("categoryId") != "mlb":
            continue
        if _compact(g.get("homeTeamName")) == _compact(game["home"]["name"]) and _compact(g.get("awayTeamName")) == _compact(game["away"]["name"]):
            pv = client.get_json(f"{NAVER_API}/schedule/games/{g['gameId']}/preview", cache_key=f"nvpv_{g['gameId']}", ttl=3600)
            out = {}
            for side in ("home", "away"):
                sp = parse_kbo_preview(pv or {}, side).get("sp") or {}
                if sp.get("name"):
                    out[side] = sp["name"]
            return out
    return {}


def mlb_starter(client, pitcher, season, game_day):
    """선발투수: 시즌 기록·좌우·최근 3경기·휴식일."""
    if not pitcher:
        return None
    data = client.get_json(
        f"{MLB_API}/people/{pitcher['id']}",
        params={"hydrate": f"stats(group=[pitching],type=[season],season={season})"},
        cache_key=f"mlbpit_{pitcher['id']}_{season}",
        ttl=12 * 3600,
    )
    people = (data or {}).get("people") or []
    sp = pitcher_season(people[0]) if people else {"id": pitcher["id"], "name": pitcher["name"]}
    sp["name"] = sp.get("name") or pitcher["name"]
    log = client.get_json(
        f"{MLB_API}/people/{pitcher['id']}/stats",
        params={"stats": "gameLog", "group": "pitching", "season": season},
        cache_key=f"mlbplog_{pitcher['id']}_{game_day.isoformat()}",
        ttl=6 * 3600,
    )
    starts = [s for s in pitcher_starts(log or {}) if s["date"] < game_day.isoformat()]
    sp["recent"] = starts[:3]
    sp["recent_era"] = recent_era(starts)
    if starts:
        try:
            sp["rest_days"] = (game_day - datetime.fromisoformat(starts[0]["date"]).date()).days
        except ValueError:
            sp["rest_days"] = None
    return sp


def mlb_bullpen(client, team_id, season, results, game_day):
    """불펜: 평균자책(불펜 기록 없으면 팀 전체), 최근 3일 투구수, 이틀 연속 던진 투수 수."""
    era, basis = None, None
    data = client.get_json(f"{MLB_API}/teams/{team_id}/stats",
                           params={"stats": "statSplits", "group": "pitching", "season": season, "sitCodes": "rp"},
                           cache_key=f"mlbpen_{team_id}_{season}", ttl=12 * 3600)
    for block in (data or {}).get("stats", []) or []:
        for sp in block.get("splits", []) or []:
            v = (sp.get("stat") or {}).get("era")
            if v not in (None, "", "-.--"):
                try:
                    era, basis = float(v), "불펜"
                except ValueError:
                    pass
    if era is None:
        data = client.get_json(f"{MLB_API}/teams/{team_id}/stats",
                               params={"stats": "season", "group": "pitching", "season": season},
                               cache_key=f"mlbtpit_{team_id}_{season}", ttl=12 * 3600)
        for block in (data or {}).get("stats", []) or []:
            for sp in block.get("splits", []) or []:
                v = (sp.get("stat") or {}).get("era")
                try:
                    era, basis = float(v), "팀 전체"
                except (TypeError, ValueError):
                    pass
    by_day = {}
    for r in results:
        try:
            d = datetime.fromisoformat(str(r.get("date"))[:10]).date()
        except ValueError:
            continue
        gap = (game_day - d).days
        if not (1 <= gap <= 3) or not r.get("gamePk"):
            continue
        box = client.get_json(f"{MLB_API}/game/{r['gamePk']}/boxscore", cache_key=f"mlbbox_{r['gamePk']}", ttl=-1)
        if box:
            by_day.setdefault(gap, {}).update(bullpen_usage(box, r.get("side") or "home"))
    pitches_3d = sum(sum(v.values()) for v in by_day.values())
    b2b = len(set(by_day.get(1, {})) & set(by_day.get(2, {})))
    return {"era": era, "basis": basis, "pitches_3d": pitches_3d, "b2b": b2b}


def mlb_pitcher_card(client, pitcher, season):
    if not pitcher:
        return None
    data = client.get_json(
        f"{MLB_API}/people/{pitcher['id']}",
        params={"hydrate": f"stats(group=[pitching],type=[season],season={season})"},
        cache_key=f"mlbpit_{pitcher['id']}_{season}",
        ttl=12 * 3600,
    )
    stat = {}
    for person in (data or {}).get("people", []) or []:
        for block in person.get("stats", []) or []:
            for split in block.get("splits", []) or []:
                stat = split.get("stat") or {}
    return {
        "name": pitcher["name"],
        "starts": stat.get("gamesStarted"),
        "era": stat.get("era"),
        "innings": stat.get("inningsPitched"),
    }


# ------------------------------------------------------------------ KBO (네이버)

KBO_RECENT = 20          # 주전 판정에 쓸 최근 경기 수 (야구는 매일 경기라 10경기면 2주도 안 된다)
KBO_LOOKBACK = 40        # 선발투수 최근 등판·불펜·성적 통계에 쓸 경기 수


def kbo_season_games(client, cfg, upper, cat, before_kst):
    """이번 시즌 끝난 경기 전부 (2주씩 끊어 받은 일정, 지난 구간은 영구 캐시)."""
    today = before_kst.date()
    start = datetime.fromisoformat(cfg.get("naver_baseball_season_start", f"{today.year}-03-01")).date()
    out, seen, frm = [], set(), start
    while frm <= today:
        to = min(frm + timedelta(days=NAVER_CHUNK_DAYS - 1), today)
        data = naver_schedule(client, upper, frm, to, today)
        for g in (((data or {}).get("result") or {}).get("games") or []):
            if g.get("categoryId") != cat or str(g.get("statusCode")).upper() != "RESULT" or g.get("gameId") in seen:
                continue
            w = parse_naver_games({"result": {"games": [g]}}, {cat: ""}, "야구")
            if not w or to_kst(w[0]["start_kst"]) >= before_kst:
                continue
            seen.add(g["gameId"])
            out.append((w[0]["start_kst"], g))
        frm = to + timedelta(days=1)
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def kbo_team_history(client, season, code, n):
    hist = []
    for when, g in season:
        if code not in (g.get("homeTeamCode"), g.get("awayTeamCode")):
            continue
        mine = "home" if g.get("homeTeamCode") == code else "away"
        other = "away" if mine == "home" else "home"
        rec = client.get_json(f"{NAVER_API}/schedule/games/{g['gameId']}/record", cache_key=f"nvrec_{g['gameId']}", ttl=-1)
        r = parse_kbo_record(rec or {}, mine)
        if len(r["starters"]) < 9:
            continue
        try:
            gf, ga = int(g.get(f"{mine}TeamScore")), int(g.get(f"{other}TeamScore"))
        except (TypeError, ValueError):
            continue
        r.update({"date": when, "opp": g.get(f"{other}TeamName") or "", "home": mine == "home", "gf": gf, "ga": ga,
                  "res": "W" if gf > ga else "L" if gf < ga else "D"})
        hist.append(r)
        if len(hist) >= n:
            break
    return hist


def build_kbo_detail(client, game, now):
    cfg = load_config()
    lg = next((x for x in cfg.get("naver_leagues", []) if x["category"] == game.get("category")), {})
    upper = lg.get("upper", "kbaseball")
    pv = client.get_json(f"{NAVER_API}/schedule/games/{game['event_id']}/preview", cache_key=f"nvpv_{game['event_id']}", ttl=180)
    sides = {s: parse_kbo_preview(pv or {}, s) for s in ("home", "away")}
    if not all(len(sides[s]["lineup"]) >= 9 for s in ("home", "away")):
        return {"lineup_ready": False, "note": "아직 선발 라인업이 나오지 않았습니다."}
    start = to_kst(game["start_kst"]) or now
    day = start.date()
    season = kbo_season_games(client, cfg, upper, game.get("category"), start)
    # 리그 평균 득점 (시즌 경기 점수로)
    runs = [int(g.get(k)) for _, g in season for k in ("homeTeamScore", "awayTeamScore") if str(g.get(k, "")).lstrip("-").isdigit()]
    rpg = sum(runs) / len(runs) if len(runs) >= 40 else 5.0
    teams = {}
    for side in ("home", "away"):
        code = game[side]["id"]
        info = sides[side]
        lineup = info["lineup"]
        hist = kbo_team_history(client, season, code, KBO_LOOKBACK)
        recent = hist[:KBO_RECENT]
        result = analyze_lineup(lineup, recent, 9)
        mark_returning(result, recent, hist, 9)                 # 부상자 명단에서 돌아온 주전 인정
        result["basis"] = "recent"
        for p in result.get("players") or []:
            p["season_games"] = None
        # 선발투수: 시즌 기록(미리보기) + 최근 등판(팀 경기 기록에서)
        sp = dict(info["sp"] or {})
        starts = []
        for h in hist:
            if h.get("sp") and sp.get("id") and h["sp"]["id"] == sp["id"]:
                starts.append({"date": str(h["date"])[:10], "opp": h.get("opp"), "ip": h["sp"]["ip"], "er": h["sp"]["er"],
                               "pitches": h["sp"].get("pitches")})
        for r in starts[:3]:                                   # 상대 팀 이름도 한국어로
            r["opp"] = ko_team(r.get("opp"))
        sp["recent"] = starts[:3]
        sp["recent_era"] = recent_era(starts)
        if starts:
            try:
                sp["rest_days"] = (day - datetime.fromisoformat(starts[0]["date"]).date()).days
            except ValueError:
                sp["rest_days"] = None
        # 불펜: 최근 3일 투구수(네이버 기록의 bf가 투구수), 이틀 연속 등판
        by_day = {}
        for h in hist:
            try:
                gap = (day - datetime.fromisoformat(str(h["date"])[:10]).date()).days
            except ValueError:
                continue
            if 1 <= gap <= 3:
                d = by_day.setdefault(gap, {"p": 0, "ids": set()})
                d["p"] += h.get("pen_pitches") or 0
                d["ids"] |= set(h.get("pen_ids") or [])
        pen = {"era": info.get("team_era"), "basis": "팀 전체",
               "pitches_3d": sum(d["p"] for d in by_day.values()),
               "b2b": len(by_day.get(1, {}).get("ids", set()) & by_day.get(2, {}).get("ids", set()))}
        season_runs = [(int(g.get("homeTeamScore")) if g.get("homeTeamCode") == code else int(g.get("awayTeamScore")))
                       for _, g in season if code in (g.get("homeTeamCode"), g.get("awayTeamCode"))
                       and str(g.get("homeTeamScore", "")).isdigit() and str(g.get("awayTeamScore", "")).isdigit()]
        off = round(sum(season_runs) / len(season_runs), 2) if len(season_runs) >= 10 else None
        hands = [p.get("bats") for p in lineup]
        result["pitching"] = {"sp": sp if sp.get("name") else None, "pen": pen, "off_rpg": off,
                              "bats": {k: hands.count(k) for k in ("L", "R", "S")}}
        result["team_name"] = game[side]["name"]
        add_context(result, hist, "야구")
        teams[side] = result

    def inputs(side):
        t = teams[side]
        pit = t["pitching"]
        return {"sp": pit.get("sp") or {}, "off_rpg": pit.get("off_rpg"), "pen_era": (pit.get("pen") or {}).get("era"),
                "pen_3d": (pit.get("pen") or {}).get("pitches_3d") or 0, "core_in": t.get("core_in"), "size": 9, "grade": t.get("grade")}
    power = baseball_power(inputs("home"), inputs("away"), game["home"]["name"], game["away"]["name"], rpg=rpg)
    summary = summarize(game["home"]["name"], game["away"]["name"], teams, "야구")
    summary["points"] = (mlb_points(game, teams, power) + (summary.get("points") or []))[:3]
    sps = [((teams[s].get("pitching") or {}).get("sp") or {}).get("name") for s in ("home", "away")]
    if all(sps):
        summary["headline"] = f"{power.get('verdict', '')} · 선발 {sps[0]} vs {sps[1]}".strip(" ·")
    names = {s: game[s]["name"] for s in ("home", "away")}
    return {"lineup_ready": True, "teams": teams, "power": power, "summary": summary,
            "signals": build_signals(names, teams, "야구")}


# ------------------------------------------------------------------ 풋몹 컵대회 (일왕배 등): 라인업·몇 군·결장 (전력 숫자 없음)

def collect_fotmob_games(client, now, cfg):
    leagues = {int(x["id"]): x for x in cfg.get("fotmob_leagues", [])}
    if not leagues:
        return []
    games, seen = [], set()
    for back in (-1, 0, 1):
        d = (now + timedelta(days=back)).strftime("%Y%m%d")
        data = client.get_json(f"{FOTMOB}/matches", params={"date": d}, cache_key=f"fm_{d}", ttl=1800)
        for m in parse_fotmob_league_matches(data or {}, set(leagues)):
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            lg = leagues[m["league_id"]]
            games.append({"key": f"축구:fotmob.{m['league_id']}:{m['id']}", "sport": "축구", "league": lg["name"],
                          "league_slug": f"fotmob.{m['league_id']}", "source": "fotmob", "event_id": m["id"],
                          "fm_league": m["league_id"], "power": bool(lg.get("power")),
                          "start_kst": m["start_kst"], "state": m["state"], "national": False,
                          "home": {"id": m["home"]["id"], "name": m["home"]["name"], "score": m["home"]["score"] if m["state"] != "pre" else None},
                          "away": {"id": m["away"]["id"], "name": m["away"]["name"], "score": m["away"]["score"] if m["state"] != "pre" else None}})
    return games


def fotmob_team_history(client, team_id, before_kst, n=12, league_id=None):
    """풋몹 팀의 지난 경기 선발 (경기 상세는 끝난 경기라 영구 저장).
    league_id를 주면 그 리그 경기만 본다 — 컵대회 로테이션이 '평소 주전'을 흐리지 않게."""
    page = client.get_json(f"{FOTMOB}/teams", params={"id": team_id}, cache_key=f"fm_team_{team_id}", ttl=6 * 3600)
    fx = [f for f in parse_fotmob_team_fixtures(page or {}) if to_kst(f["utc"]) and to_kst(f["utc"]) < before_kst]
    if league_id:
        only = [f for f in fx if not f.get("league_id") or str(f["league_id"]) == str(league_id)]
        if len(only) >= 6:
            fx = only
    fx.sort(key=lambda f: f["utc"], reverse=True)
    hist = []
    for f in fx[:n]:
        if getattr(client, "out_of_time", False):
            break
        det = client.get_json(f"{FOTMOB}/matchDetails", params={"matchId": f["id"]}, cache_key=f"fm_d_{f['id']}", ttl=-1)
        lu = parse_fotmob_lineups(det or {})
        side = "home" if f["home_id"] == str(team_id) else "away"
        st = (lu.get(side) or {}).get("lineup") or []
        if len(st) < 9:
            continue
        gf, ga = (f["hg"], f["ag"]) if side == "home" else (f["ag"], f["hg"])
        try:
            gf, ga = int(gf), int(ga)
        except (TypeError, ValueError):
            continue
        ids = [p["id"] for p in st]
        hist.append({"date": to_kst(f["utc"]).isoformat(), "starters": ids, "played": ids, "names": {p["id"]: p["name"] for p in st},
                     "minutes": {pid: 90 for pid in ids}, "goals": {}, "assists": {}, "home": side == "home",
                     "gf": gf, "ga": ga, "res": "W" if gf > ga else "L" if gf < ga else "D"})
    return hist


def fotmob_league_form(client, team_id, before_kst, league_id, n=20):
    """풋몹 팀의 그 리그 최근 결과만 (전력 계산용 · 라인업과 무관해서 컵경기는 뺀다)."""
    page = client.get_json(f"{FOTMOB}/teams", params={"id": team_id}, cache_key=f"fm_team_{team_id}", ttl=6 * 3600)
    out = []
    for f in parse_fotmob_team_fixtures(page or {}):
        t = to_kst(f["utc"])
        if not t or t >= before_kst:
            continue
        if league_id and f.get("league_id") and str(f["league_id"]) != str(league_id):
            continue
        try:
            hg, ag = int(f["hg"]), int(f["ag"])
        except (TypeError, ValueError):
            continue
        home = f["home_id"] == str(team_id)
        out.append({"date": t.isoformat(), "gf": hg if home else ag, "ga": ag if home else hg})
    out.sort(key=lambda m: m["date"], reverse=True)
    return out[:n]


def fotmob_power(client, game, teams, start):
    """풋몹 리그(J2 등) 전력 비교: 이번 시즌 득실차만 (리그 Elo는 못 구해서 뺀다)."""
    model = get_model()
    if not model:
        return None
    lid = game.get("fm_league") or (game.get("league_slug") or "").split(".")[-1]
    sides = {}
    for s in ("home", "away"):
        if not game[s]["id"]:
            return None
        form = fotmob_league_form(client, game[s]["id"], start, lid)
        if len(form) < 5:                       # 경기가 너무 적으면 숫자를 안 만든다
            return None
        sides[s] = dict(model_side(form), elo=1500.0)
    power = model_power(model, game["league_slug"], sides["home"], sides["away"],
                        game["home"]["name"], game["away"]["name"], teams)
    power["basis"]["elo"] = [None, None]        # 리그 Elo가 없으니 화면에서도 숨긴다
    power["confident"] = False                  # Elo 없이 득실차만이라 확신 표시는 안 한다
    power["note"] = power.get("note") or "리그 Elo가 없어 이번 시즌 득실차로만 비교했어요"
    return power


def build_fotmob_detail(client, game, now):
    det = client.get_json(f"{FOTMOB}/matchDetails", params={"matchId": game["event_id"]}, cache_key=f"fm_live_{game['event_id']}", ttl=120)
    lu = parse_fotmob_lineups(det or {})
    start = to_kst(game["start_kst"]) or now
    names = {s: game[s]["name"] for s in ("home", "away")}
    ab = parse_fotmob_unavailable(det) if det else None
    jp_names = is_jp_league(game.get("league_slug"))
    only_league = game.get("fm_league") if game.get("power") else None
    hist = {s: fotmob_team_history(client, game[s]["id"], start, league_id=only_league) if game[s]["id"] else [] for s in ("home", "away")}
    have = all(len((lu.get(s) or {}).get("lineup") or []) >= 11 for s in ("home", "away"))
    # 풋몹이 '발표' 표시를 늦게 바꾸는 대회가 있어서, 다음 중 하나면 발표로 본다
    changed = 0
    for s in ("home", "away"):
        last = (hist[s][0]["starters"] if hist[s] else []) or []
        today_ids = [p["id"] for p in (lu.get(s) or {}).get("lineup") or []]
        if last and today_ids:
            changed = max(changed, len(set(today_ids) - set(last)))
    near = (now - start).total_seconds() >= -30 * 60
    why = ("발표 (풋몹)" if lu.get("confirmed") else
           "지난 경기와 선발이 달라짐" if changed >= 3 else
           "경기 임박 (30분 전 지남)" if near else "")
    if not have or not why:
        out = {"lineup_ready": False,
               "note": "풋몹에 아직 라인업이 없어요." if not have else "아직 발표 전이라 예상 라인업만 있어요.",
               "expected": ({s: {"name": game[s]["name"], "formation": (lu.get(s) or {}).get("formation") or "",
                                 "players": (lu.get(s) or {}).get("lineup") or []} for s in ("home", "away")} if have else None)}
        if game.get("power"):
            try:
                out["power"] = fotmob_power(client, game, None, start)
            except Exception as exc:
                print(f"  ! 풋몹 전력 계산 실패: {exc}", file=sys.stderr)
        risk, sig = {}, []
        for s in ("home", "away"):
            core = core_from_history(hist[s][:HISTORY_MATCHES], 11)
            rows = (ab or {}).get(s) or []
            core_out = [x["name"] for x in rows if any(same_person(m.get("name"), x["name"]) for m in core)]
            for x in rows:
                ko = ko_player(x.get("name"), jp_names)
                if ko != x.get("name"):
                    x["name_en"], x["name"] = x.get("name"), ko
            core_out = [ko_player(nm, jp_names) for nm in core_out]
            risk[s] = {"level": None, "situation": "normal", "situation_text": "평소", "today": {"n": 0}, "base": {"n": 0}, "tired": [],
                       "reason": "", "size": 11, "absent": rows, "core_absent": core_out}
            if len(core_out) >= 2:
                sig.append([f"{names[s]} 주전 {len(core_out)}명 부상·징계", "bad"])
        out["signals"] = (model_signals(out.get("power"), names) + sig)[:4]
        out["risk"] = risk
        return out

    teams = {}
    for side in ("home", "away"):
        lineup = lu[side]["lineup"]
        h = hist[side]
        recent, season = h[:HISTORY_MATCHES], h
        result = analyze_lineup(lineup, recent, 11)
        mark_returning(result, recent, season, 11)
        enrich(result, lineup, recent, season=season, formation=lu[side].get("formation") or "", shape=position_shape)
        add_bench(result, [], recent, season)
        strength(result, recent, season=season)
        add_periods(result, recent, season)
        add_context(result, h, "축구")
        result["ace"] = pick_ace(result)
        result["basis"] = "recent"
        result["team_name"] = game[side]["name"]
        result["formation"] = lu[side].get("formation") or ""
        result["league"] = game.get("league_slug")
        korean_players(result, jp_names)
        teams[side] = result
    extra = attach_absences(teams, ab, names, jp_names) if ab else []
    power = None
    if game.get("power"):
        try:
            power = fotmob_power(client, game, teams, start)
        except Exception as exc:                 # 전력 숫자가 안 나와도 라인업 판정은 그대로
            print(f"  ! 풋몹 전력 계산 실패: {exc}", file=sys.stderr)
    summary = summarize(game["home"]["name"], game["away"]["name"], teams, "축구")
    return {"lineup_ready": True, "teams": teams, "power": power, "national": False, "summary": summary, "record_recent": True,
            "lineup_source": why, "signals": (extra + model_signals(power, names) + build_signals(names, teams, "축구"))[:4]}


def build_mlb_detail(client, game, now):
    season = (to_kst(game["start_kst"]) or now).year
    lineups = game.get("lineups") or {}
    if not (lineups.get("home") or lineups.get("away")):
        return {"lineup_ready": False, "note": "아직 선발 라인업이 나오지 않았습니다."}

    try:
        ko_sp = naver_mlb_starters(client, game)
    except Exception as exc:                            # 네이버가 막혀도 영어 이름으로 계속
        ko_sp = {}
        print(f"  ! 네이버 MLB 선발 이름 실패: {exc}", file=sys.stderr)
    teams = {}
    for side in ("home", "away"):
        lineup = lineups.get(side) or []
        info = mlb_team_regulars(client, game[side]["id"], season) if game[side]["id"] else {}
        regulars = info.get("regulars") or []
        names = info.get("names") or {}
        full = mlb_team_history(client, info.get("results") or [])     # 최대 40경기
        recent = full[:MLB_RECENT]                                      # 주전 판정은 최근 20경기
        if len(recent) >= 3:
            history, basis = recent, "recent"
        else:
            # 최근 타순을 못 받으면 시즌 타석으로 대신 (경기 뛴 비주전도 '백업'으로 잡히게 함께 넣음)
            others = [pid for pid, g in (info.get("games") or {}).items() if g and pid not in regulars]
            history = synth_history(regulars, names, matches=10, extra_ids=others) if regulars else []
            basis = "season"
        result = analyze_lineup(lineup, history, 9)
        result["basis"] = basis
        season_games = info.get("games") or {}
        for p in result["players"]:
            try:
                p["season_games"] = int(season_games.get(p["id"]) or 0)
            except (TypeError, ValueError):
                p["season_games"] = 0
        season_rec = [{"goals": info.get("hr") or {}, "assists": info.get("rbi") or {}, "names": names}]
        result["form"] = info.get("form") or []
        result["leaders"] = {
            "goals": team_leaders(season_rec, "goals"),
            "assists": team_leaders(season_rec, "assists"),
            "labels": ["최다 홈런", "최다 타점"],
            "matches": info.get("team_games") or 0,
        }
        result["rows"] = []
        result["ace"] = None
        result["bench"] = []
        result["strength"] = {}
        for m in result["missing"]:
            m["status"] = None
        result["team_name"] = game[side]["name"]
        result["formation"] = ""
        result["pitcher"] = mlb_pitcher_card(client, (game.get("probables") or {}).get(side), season)
        game_day = (to_kst(game["start_kst"]) or now).date()
        sp = mlb_starter(client, (game.get("probables") or {}).get(side), season, game_day)
        for r in (sp or {}).get("recent") or []:
            r["opp"] = ko_team(r.get("opp"))
        if sp and ko_sp.get(side):                      # 네이버의 한국어 선발 이름 (영어 원래 이름은 따로 보관)
            sp["name_en"], sp["name"] = sp.get("name"), ko_sp[side]
        pen = mlb_bullpen(client, game[side]["id"], season, info.get("results") or [], game_day) if game[side]["id"] else {}
        bats = info.get("bats") or {}
        hands = [bats.get(p["id"], "") for p in lineup]
        result["pitching"] = {"sp": sp, "pen": pen, "off_rpg": info.get("off_rpg"),
                              "bats": {k: hands.count(k) for k in ("L", "R", "S")}}
        add_context(result, full if basis == "recent" else history, "야구")   # 통계는 더 많은 경기로
        teams[side] = result

    def inputs(side):
        t = teams[side]
        pit = t.get("pitching") or {}
        return {"sp": pit.get("sp") or {}, "off_rpg": pit.get("off_rpg"), "pen_era": (pit.get("pen") or {}).get("era"),
                "pen_3d": (pit.get("pen") or {}).get("pitches_3d") or 0, "core_in": t.get("core_in"), "size": 9,
                "grade": t.get("grade")}
    power = baseball_power(inputs("home"), inputs("away"), game["home"]["name"], game["away"]["name"])
    summary = summarize(game["home"]["name"], game["away"]["name"], teams, "야구")
    summary["points"] = (mlb_points(game, teams, power) + (summary.get("points") or []))[:3]
    # 야구는 투수가 핵심이라 한 줄 요약도 선발 맞대결 중심으로
    sps = [((teams[s].get("pitching") or {}).get("sp") or {}).get("name") for s in ("home", "away")]
    if all(sps) and power:
        last = lambda n: n.split(" ")[-1]
        summary["headline"] = f"{power.get('verdict', '')} · 선발 {last(sps[0])} vs {last(sps[1])}".strip(" ·")
    names = {s: game[s]["name"] for s in ("home", "away")}
    return {"lineup_ready": True, "teams": teams, "power": power, "summary": summary,
            "signals": build_signals(names, teams, "야구")}


def mlb_points(game, teams, power):
    """야구 핵심 포인트: 선발 맞대결 · 불펜 피로 · 예상 득점."""
    pts = []
    sps = [(teams[s].get("pitching") or {}).get("sp") or {} for s in ("home", "away")]
    if all(sp.get("name") for sp in sps):
        def era(sp):
            e = sp.get("era")
            return f"ERA {e:.2f}" if isinstance(e, (int, float)) else "ERA -"
        rec = [f"최근 3경기 {sp['recent_era']:.2f}" for sp in sps if sp.get("recent_era") is not None]
        pts.append([f"선발 맞대결: {sps[0]['name']}({era(sps[0])}) vs {sps[1]['name']}({era(sps[1])})",
                    " · ".join(rec) if len(rec) == 2 else ""])
    tired = [(game[s]["name"], (teams[s].get("pitching") or {}).get("pen") or {}) for s in ("home", "away")]
    tired = [(n, p) for n, p in tired if (p.get("pitches_3d") or 0) >= 500 or (p.get("b2b") or 0) >= 3]
    for n, p in tired[:1]:
        pts.append([f"{n} 불펜 과부하: 최근 3일 {p.get('pitches_3d')}구", f"이틀 연속 등판 {p.get('b2b', 0)}명"])
    if power and power.get("exp_total") is not None:
        pts.append([f"예상 득점 {power['exp_home']} : {power['exp_away']} (합계 {power['exp_total']})", "선발·불펜·타선 기록으로 계산한 참고값"])
    return pts


# ------------------------------------------------------------------ 저장

def safe_name(text):
    """영문·숫자·._- 만 남긴다 (화면 JS의 파일명 규칙과 반드시 같아야 함)."""
    return "".join(c if (c.isascii() and c.isalnum()) or c in "._-" else "_" for c in text)


def game_filename(key):
    return safe_name(key) + ".json"


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def prune(folder, max_age_days):
    """오래된 파일 정리 — 저장소·캐시가 끝없이 커지지 않게."""
    if not os.path.isdir(folder):
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


# ------------------------------------------------------------------ 예측 기록장

def snapshot(game, detail):
    """경기 전 판정을 한 줄로 요약. 나중에 결과와 맞춰 점수 방식을 검증하는 데 쓴다."""
    teams = detail.get("teams") or {}
    side = {}
    for s in ("home", "away"):
        t = teams.get(s) or {}
        lp = t.get("lineup_power") or {}
        st = t.get("strength") or {}
        ace = t.get("ace") or {}
        side[s] = {
            "name": game[s]["name"],
            "grade": t.get("grade"), "core_in": t.get("core_in"), "size": t.get("size"),
            "retain": st.get("retain"), "goal_share": st.get("goal_share"), "assist_share": st.get("assist_share"),
            "q": lp.get("q"), "team_strength": lp.get("team_strength"), "today": lp.get("today"),
            "elo": lp.get("elo"), "today_elo": lp.get("today_elo"), "xi_ppg": lp.get("xi_ppg"),
            "ppg": (t.get("season_record") or {}).get("ppg"), "season_matches": t.get("season_matches"),
            "missing": len(t.get("missing") or []),
            "ace": ace.get("name"), "ace_status": ace.get("status"),
            "lineup": [p.get("id") for p in t.get("players") or []],
        }
    pw = detail.get("power") or {}
    return {
        "key": game["key"], "sport": game["sport"], "league": game["league"], "national": bool(game.get("national")),
        "start_kst": game["start_kst"], "home": side["home"], "away": side["away"],
        "power": [pw.get("home"), pw.get("away")] if pw else None, "power_mode": pw.get("mode", "club") if pw else None,
        "snap_state": game.get("state"), "result": None,
    }


def frozen_detail(game, now):
    """경기가 시작된 뒤에는 이미 저장해 둔 '경기 전 판정'을 그대로 쓴다.
    (다시 계산하면 오늘 경기 결과가 기록에 섞여서 판정이 뒤집힌다)"""
    if game.get("state") == "pre":
        return None
    start = to_kst(game.get("start_kst"))
    if not start or now < start:
        return None
    path = os.path.join(GAME_DIR, game_filename(game["key"]))
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return None
    if not saved.get("lineup_ready"):
        return None
    saved.pop("game", None)
    saved["frozen"] = True                      # 화면에서 '경기 전 판정'이라고 알려주기 위해
    return saved


def detail_row(detail):
    """상세에서 목록에 넣을 값만 뽑는다."""
    out = {"lineup_ready": bool(detail.get("lineup_ready"))}
    teams = detail.get("teams") or {}
    out["grade_home"] = (teams.get("home") or {}).get("grade", "")
    out["grade_away"] = (teams.get("away") or {}).get("grade", "")
    pw = detail.get("power") or {}
    if pw:
        out["power"] = [pw.get("home"), pw.get("away")]
    if detail.get("signals"):
        out["signals"] = detail["signals"]
    return out


def keep_detail(game, detail):
    """끝난 경기의 상세를 월별 폴더에 보관한다. 화면용 파일은 3일 뒤 지워지지만 이건 남긴다."""
    if game.get("state") != "post" or not detail or not detail.get("lineup_ready"):
        return False
    start = to_kst(game.get("start_kst"))
    month = start.strftime("%Y-%m") if start else "unknown"
    folder = os.path.join(DETAIL_ARCHIVE, month)
    path = os.path.join(folder, game_filename(game["key"]))
    if os.path.exists(path):
        return False
    os.makedirs(folder, exist_ok=True)
    body = {"game": {k: game.get(k) for k in ("key", "sport", "league", "league_slug", "start_kst", "state", "national")},
            "home": game["home"].get("name"), "away": game["away"].get("name"),
            "score": [game["home"].get("score"), game["away"].get("score")],
            "saved_at": kst_now().isoformat(), "detail": detail}
    write_json(path, body)
    return True


def archive_path(start_kst):
    month = (start_kst or "")[:7] or "unknown"
    return os.path.join(ARCHIVE_DIR, f"{month}.jsonl")


def update_archive(games, details, now):
    """경기 전(시작 전) 판정을 저장하고, 끝난 경기는 결과를 채운다. 월별 파일, 한 줄에 한 경기."""
    by_file = {}
    def load(path):
        if path not in by_file:
            rows = {}
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                            rows[r["key"]] = r
                        except (ValueError, KeyError):
                            continue
            by_file[path] = rows
        return by_file[path]

    changed = set()
    for g in games:
        path = archive_path(g.get("start_kst"))
        rows = load(path)
        det = details.get(g["key"])
        # 1) 판정 저장: 시작 전 스냅샷이 우선. 시작 후에 처음 본 경기는 '늦은 스냅샷'으로 표시
        if det and det.get("lineup_ready"):
            old = rows.get(g["key"])
            if old is None or (old.get("snap_state") != "pre" and g.get("state") == "pre") or \
                    (old.get("snap_state") == "pre" and g.get("state") == "pre"):
                snap = snapshot(g, det)
                if old and old.get("result"):
                    snap["result"] = old["result"]
                if old != snap:
                    rows[g["key"]] = snap
                    changed.add(path)
        # 2) 결과 채우기
        r = rows.get(g["key"])
        hs, as_ = (g.get("home") or {}).get("score"), (g.get("away") or {}).get("score")
        if r and not r.get("result") and g.get("state") == "post" and hs is not None and as_ is not None:
            r["result"] = {"home": hs, "away": as_, "res": "H" if hs > as_ else "A" if hs < as_ else "D",
                           "recorded": now.isoformat()}
            changed.add(path)

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    for path in changed:
        rows = by_file[path]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in sorted(rows.values(), key=lambda x: (x.get("start_kst") or "", x["key"])):
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
    return len(changed)


def run(verbose=False):
    cfg = load_config()
    client = Client(verbose=verbose)
    now = kst_now()

    games = []
    games += collect_espn_games(client, now, cfg.get("espn_leagues", []))
    if cfg.get("naver_leagues"):
        games += collect_naver_games(client, now, cfg)
    if cfg.get("fotmob_leagues"):
        try:
            games += collect_fotmob_games(client, now, cfg)
        except Exception as exc:                      # 풋몹이 막혀도 나머지 수집은 그대로
            print(f"  ! 풋몹 컵대회 목록 실패: {exc}", file=sys.stderr)
    if cfg.get("mlb_enabled", True):
        games += collect_mlb_games(client, now)
    games = translate_names(dedupe(games))
    if not cfg.get("show_logos", False):        # 공유용: 구단 로고(상표) 대신 이니셜 표시
        for g in games:
            for side in ("home", "away"):
                (g.get(side) or {})["logo"] = ""
    games.sort(key=lambda g: (g.get("start_kst") or "9999"))

    targets = [g for g in games if needs_detail(g, now)][:MAX_DETAIL_GAMES]
    started = getattr(client, "started", time.time())
    print(f"경기 {len(games)}개 수집, 상세 판정 대상 {len(targets)}개 (요청 {client.count}건)", flush=True)

    index = []
    details = {}
    for g in games:
        row = {
            "key": g["key"],
            "sport": g["sport"],
            "league": g["league"],
            "start_kst": g["start_kst"],
            "state": g["state"],
            "home": g["home"]["name"],
            "away": g["away"]["name"],
            "score": [g["home"].get("score"), g["away"].get("score")] if g["state"] != "pre" else None,
            "home_logo": g["home"].get("logo", ""),
            "away_logo": g["away"].get("logo", ""),
            "national": bool(g.get("national")),
            "game_no": g.get("game_no"),            # 더블헤더 1·2차전
            "postponed": bool(g.get("postponed")),  # 연기된 경기
            "lineup_ready": False,
            "grade_home": "",
            "grade_away": "",
        }
        frozen = frozen_detail(g, now) if g in targets else None
        if frozen:                                   # 경기가 시작된 뒤에는 경기 전 판정을 그대로 둔다
            detail = frozen
            details[g["key"]] = detail
            row.update(detail_row(detail))
            index.append(row)
            continue
        if g in targets:
            print(f"  · 경기 상세 {targets.index(g) + 1}/{len(targets)}: {g['league']} {g['home']['name']} vs {g['away']['name']} "
                  f"(요청 {client.count}건 · {int(time.time() - started)}초)", flush=True)
            try:
                if g.get("source") == "fotmob":
                    detail = build_fotmob_detail(client, g, now)
                elif g.get("source") == "naver" and g["sport"] == "야구":
                    detail = build_kbo_detail(client, g, now)
                elif g.get("source") == "naver":
                    detail = build_naver_detail(client, g, now)
                elif g["sport"] == "야구":
                    detail = build_mlb_detail(client, g, now)
                else:
                    detail = build_espn_detail(client, g, now)
            except Exception as exc:  # 한 경기가 깨져도 전체는 계속
                print(f"  ! {g['key']} 처리 실패: {exc}", file=sys.stderr)
                detail = {"lineup_ready": False, "note": "처리 중 오류가 났습니다."}
            details[g["key"]] = detail
            detail["game"] = row.copy()
            detail["updated"] = now.isoformat()
            write_json(os.path.join(GAME_DIR, game_filename(g["key"])), detail)
            row["lineup_ready"] = bool(detail.get("lineup_ready"))
            if detail.get("lineup_ready"):
                row["grade_home"] = detail["teams"]["home"]["grade"]
                row["grade_away"] = detail["teams"]["away"]["grade"]
                row["insight"] = (detail.get("summary") or {}).get("insight", "")
                pw = detail.get("power") or {}
                row["power"] = [pw["home"], pw["away"]] if pw else None
                row["signals"] = (detail.get("signals") or [])[:3]
                for side in ("home", "away"):
                    ace = detail["teams"][side].get("ace") or {}
                    row[f"ace_{side}"] = {"name": ace.get("name", ""), "status": ace.get("status", "")} if ace else None
            else:                                # 라인업 발표 전에도 전력은 보여준다
                pw = detail.get("power") or {}
                if pw:
                    row["power"] = [pw["home"], pw["away"]]
                if detail.get("signals"):        # 로테이션 가능성·부상 신호
                    row["signals"] = detail["signals"][:3]
            row["note"] = detail.get("note", "")
        index.append(row)

    try:
        update_archive(games, details, now)
    except Exception as exc:   # 기록장이 실패해도 화면 데이터는 계속 만든다
        print(f"  ! 기록장 저장 실패: {exc}", file=sys.stderr)

    kept = 0
    for g in games:                                  # 끝난 경기 상세는 영구 보관 (xG·선수 기여도 분석 대비)
        d = details.get(g["key"])
        try:
            if d and keep_detail(g, d):
                kept += 1
        except Exception as exc:
            print(f"  ! 상세 보관 실패: {exc}", file=sys.stderr)
    if kept:
        print(f"  · 끝난 경기 상세 {kept}개 보관", flush=True)

    prune(GAME_DIR, 3)      # 3일 지난 경기 상세는 삭제 (보관본은 archive/details에 남음)
    prune(CACHE_DIR, 30)    # 30일 지난 캐시는 삭제

    write_json(
        os.path.join(DATA_DIR, "games.json"),
        {"updated": now.isoformat(), "requests": client.count, "games": index},
    )
    print(f"완료: 요청 {client.count}건, docs/data/games.json 갱신")
    return 0


def check_leagues():
    """config.json의 리그 slug가 실제로 응답하는지 한 번에 확인한다."""
    cfg = load_config()
    client = Client(verbose=True)
    now = kst_now()
    for lg in cfg.get("espn_leagues", []):
        total = 0
        ok = False
        for date in date_strings(now, days=(-2, -1, 0, 1, 2)):
            data = client.get_json(f"{ESPN_SITE}/{lg['path']}/scoreboard", params={"dates": date})
            if data is None:
                continue
            ok = True
            total += len(data.get("events") or [])
        mark = "O" if ok else "X (slug 확인 필요)"
        print(f"{mark:22} {lg['slug']:20} 최근 5일 경기 {total}개  ({lg['name']})")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--check-leagues", action="store_true", help="리그 slug가 살아 있는지 점검")
    args = ap.parse_args()
    sys.exit(check_leagues() if args.check_leagues else run(verbose=args.verbose))
