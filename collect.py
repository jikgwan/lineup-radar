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
from datetime import datetime, timedelta

import requests

from grade import (ELO_HOME, add_bench, add_periods, analyze_lineup, compare_elo, compare_power, enrich,
                   lineup_power, pick_ace, strength, summarize, team_leaders)
from parse import (
    KST,
    elo_for,
    parse_elo_names,
    parse_elo_world,
    mlb_history_from_stats,
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
ARCHIVE_DIR = os.path.join(ROOT, "archive")    # 경기 전 판정 + 실제 결과 영구 기록 (백테스트·점수 조정용)
CACHE_DIR = os.path.join(ROOT, "cache")

# ESPN: 깃허브 서버에서는 site.api 주소가 막히고(403) site.web 주소는 열린다 (2026-09 소스 점검 결과).
# site.web을 먼저 쓰고, 거부되면 같은 경로를 site.api로 한 번 더 시도한다.
ESPN_SITE = "https://site.web.api.espn.com/apis/site/v2/sports"
ESPN_FALLBACK = "https://site.api.espn.com/apis/site/v2/sports"
MLB_API = "https://statsapi.mlb.com/api/v1"

HISTORY_MATCHES = 6          # 최근 몇 경기로 주전을 판정할지
NATIONAL_MATCHES = 20        # 대표팀은 최근 A매치 몇 경기까지 모을지
ELO_BASE = "https://www.eloratings.net"
SEASON_MATCHES = 40          # 시즌 전체 기록(득점·도움·출전)은 최근 몇 경기까지 모아서 볼지 (리그 한 시즌 38경기)
DETAIL_WINDOW_BEFORE = 6 * 60   # 경기 시작 몇 분 전부터 라인업을 확인할지
DETAIL_WINDOW_AFTER = 4 * 60    # 시작 후 몇 분까지 확인할지
MAX_DETAIL_GAMES = 40
MAX_REQUESTS = 800
REQUEST_PAUSE = 0.4

USER_AGENT = "lineup-radar/1.0 (personal hobby project)"


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ HTTP

BREAKER_LIMIT = 6        # 같은 사이트가 연속 이만큼 실패하면 이번 실행에서는 더 요청하지 않음
NO_RETRY = {400, 401, 403, 404, 410}


def _host(url):
    return url.split("/")[2] if "://" in url else url


class Client:
    def __init__(self, verbose=False):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.count = 0
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
        if self.host_down(url):
            return None
        for attempt in range(3):
            self.count += 1
            try:
                res = self.session.get(url, params=params, timeout=20)
                if res.status_code in NO_RETRY:
                    # 404는 '없는 리그/경기'라 정상 응답으로 본다. 403 등 거부는 실패로 센다.
                    self.note(url, res.status_code == 404)
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
                    note(url, res.status_code == 404)
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


def date_strings(now, days=(-1, 0, 1)):
    return [(now + timedelta(days=d)).strftime("%Y%m%d") for d in days]


def collect_espn_games(client, now, leagues):
    games = []
    for lg in leagues:
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
    return games


def collect_mlb_games(client, now):
    start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
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


def dedupe(games):
    seen, out = set(), []
    for g in games:
        if g["key"] in seen:
            continue
        seen.add(g["key"])
        out.append(g)
    return out


def needs_detail(game, now):
    if not game.get("start_kst"):
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
    for ev in (sched or {}).get("events", []) or []:
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
        for ev in (sched or {}).get("events", []) or []:
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
        for ev in (sched or {}).get("events", []) or []:
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

    if not sides or not any((s or {}).get("lineup") for s in sides.values()):
        return {"lineup_ready": False, "note": "아직 선발 라인업이 나오지 않았습니다."}

    start = to_kst(game["start_kst"]) or now
    cfg = load_config()
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
        formation = info.get("formation") or ""
        enrich(result, lineup, recent, season=history, formation=formation,
               shape=position_shape if game["sport"] == "축구" else None)
        add_bench(result, info.get("bench") or [], recent, season=history)
        strength(result, recent, season=history)
        add_periods(result, recent, history)
        result["ace"] = pick_ace(result)
        if game["sport"] == "축구":
            lineup_power(result, history, shape=position_shape)
        result["league"] = hist_slug
        result["leaders"]["labels"] = ["최다 득점", "최다 도움"] if game["sport"] == "축구" else ["최다 득점", "최다 어시스트"]
        result["team_name"] = game[side]["name"] or info.get("team_name") or ""
        result["formation"] = formation
        teams[side] = result
    power = None
    if game["sport"] == "축구" and game.get("national"):
        ratings, names = load_elo(client)
        eh, ch = elo_for(game["home"]["name"], ratings, names)
        ea, ca = elo_for(game["away"]["name"], ratings, names)
        home_adv = ELO_HOME if league_slug in (cfg.get("home_advantage_slugs") or []) else 0
        power = compare_elo(teams["home"], teams["away"], game["home"]["name"], game["away"]["name"], eh, ea, home_adv)
        teams["home"]["elo_code"], teams["away"]["elo_code"] = ch, ca
    elif game["sport"] == "축구":
        ls = cfg.get("league_strength") or {}
        fh = fa = 1.0
        if slugs["home"] != slugs["away"]:   # 리그가 다를 때만 리그 수준 보정
            fh, fa = ls.get(slugs["home"], ls.get("default", 0.8)), ls.get(slugs["away"], ls.get("default", 0.8))
        power = compare_power(teams["home"], teams["away"], game["home"]["name"], game["away"]["name"], fh, fa)
    return {"lineup_ready": True, "teams": teams, "power": power, "national": bool(game.get("national")),
            "summary": summarize(game["home"]["name"], game["away"]["name"], teams, game["sport"])}


# ------------------------------------------------------------------ MLB 상세

def mlb_team_regulars(client, team_id, season):
    cache_key = f"mlbreg2_{team_id}_{season}"
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
    for block in (team_stats or {}).get("stats", []) or []:
        for split in block.get("splits", []) or []:
            try:
                team_games = max(team_games, int(float((split.get("stat") or {}).get("gamesPlayed") or 0)))
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
    info["form"] = mlb_team_form(client, team_id)
    cache_write(cache_key, info)
    return info


def mlb_team_form(client, team_id):
    today = kst_now()
    start = (today - timedelta(days=12)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    data = client.get_json(
        f"{MLB_API}/schedule",
        params={"sportId": 1, "teamId": team_id, "startDate": start, "endDate": end},
        cache_key=f"mlbform_{team_id}_{end}",
        ttl=6 * 3600,
    )
    return parse_mlb_team_results(data, team_id)[:5] if data else []


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


def build_mlb_detail(client, game, now):
    season = (to_kst(game["start_kst"]) or now).year
    lineups = game.get("lineups") or {}
    if not (lineups.get("home") or lineups.get("away")):
        return {"lineup_ready": False, "note": "아직 선발 라인업이 나오지 않았습니다."}

    teams = {}
    for side in ("home", "away"):
        lineup = lineups.get(side) or []
        info = mlb_team_regulars(client, game[side]["id"], season) if game[side]["id"] else {}
        regulars = info.get("regulars") or []
        names = info.get("names") or {}
        history = synth_history(regulars, names, matches=10) if regulars else []
        result = analyze_lineup(lineup, history, 9)
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
        teams[side] = result
    return {"lineup_ready": True, "teams": teams,
            "summary": summarize(game["home"]["name"], game["away"]["name"], teams, "야구")}


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
    if cfg.get("mlb_enabled", True):
        games += collect_mlb_games(client, now)
    games = dedupe(games)
    games.sort(key=lambda g: (g.get("start_kst") or "9999"))

    targets = [g for g in games if needs_detail(g, now)][:MAX_DETAIL_GAMES]
    print(f"경기 {len(games)}개 수집, 상세 판정 대상 {len(targets)}개")

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
            "lineup_ready": False,
            "grade_home": "",
            "grade_away": "",
        }
        if g in targets:
            try:
                detail = build_mlb_detail(client, g, now) if g["sport"] == "야구" else build_espn_detail(client, g, now)
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
                for side in ("home", "away"):
                    ace = detail["teams"][side].get("ace") or {}
                    row[f"ace_{side}"] = {"name": ace.get("name", ""), "status": ace.get("status", "")} if ace else None
            row["note"] = detail.get("note", "")
        index.append(row)

    try:
        update_archive(games, details, now)
    except Exception as exc:   # 기록장이 실패해도 화면 데이터는 계속 만든다
        print(f"  ! 기록장 저장 실패: {exc}", file=sys.stderr)

    prune(GAME_DIR, 3)      # 3일 지난 경기 상세는 삭제
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
