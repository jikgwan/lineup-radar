"""ESPN / MLB 공식 API 응답에서 필요한 값만 뽑아내는 순수 함수 모음.

네트워크를 타지 않으므로 고정 샘플(fixture)로 테스트할 수 있다.
응답 구조가 조금씩 다르거나 필드가 없어도 예외 없이 빈 값으로 떨어지게 짰다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def _d(x):
    return x if isinstance(x, dict) else {}


def _l(x):
    return x if isinstance(x, list) else []


def _s(x):
    return x.strip() if isinstance(x, str) else ""


def to_kst(iso):
    """ESPN의 '2026-09-18T14:00Z' 또는 ISO8601 문자열 -> KST datetime (실패 시 None)."""
    text = _s(iso)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%dT%H:%M%z")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def _state(competition):
    t = _d(_d(_d(competition).get("status")).get("type"))
    state = _s(t.get("state"))  # pre / in / post
    return state or "pre"


# ---------------------------------------------------------------- 축구 (ESPN)

def parse_espn_scoreboard(data, sport, league_slug, league_name):
    """스코어보드 -> 경기 목록."""
    out = []
    for ev in _l(_d(data).get("events")):
        ev = _d(ev)
        comps = _l(ev.get("competitions"))
        if not comps:
            continue
        comp = _d(comps[0])
        home = away = None
        for c in _l(comp.get("competitors")):
            c = _d(c)
            team = _d(c.get("team"))
            side = {
                "id": _s(team.get("id")),
                "name": _s(team.get("displayName")) or _s(team.get("name")),
                "short": _s(team.get("shortDisplayName")) or _s(team.get("abbreviation")),
                "logo": _s(team.get("logo")),
                "score": _score(c.get("score")),
            }
            if _s(c.get("homeAway")) == "home":
                home = side
            else:
                away = side
        if not home or not away:
            continue
        kst = to_kst(ev.get("date") or comp.get("date"))
        out.append(
            {
                "key": f"{sport}:{league_slug}:{_s(ev.get('id'))}",
                "sport": sport,
                "league": league_name,
                "league_slug": league_slug,
                "event_id": _s(ev.get("id")),
                "start_kst": kst.isoformat() if kst else "",
                "state": _state(comp),
                "home": home,
                "away": away,
            }
        )
    return out


def _stat_map(stats):
    """ESPN stats 배열([{name, value}] 또는 {name: value}) -> dict."""
    out = {}
    if isinstance(stats, dict):
        for k, v in stats.items():
            out[str(k)] = v
        return out
    for st in _l(stats):
        st = _d(st)
        name = _s(st.get("name")) or _s(st.get("abbreviation"))
        if name:
            out[name] = st.get("value", st.get("displayValue"))
    return out


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _score(value):
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    return _int(value)


def header_info(summary):
    """summary.header -> {"date": iso, "teams": {team_id: {"name","home","score","winner"}}}"""
    header = _d(_d(summary).get("header"))
    comps = _l(header.get("competitions"))
    comp = _d(comps[0]) if comps else {}
    teams = {}
    for c in _l(comp.get("competitors")):
        c = _d(c)
        team = _d(c.get("team"))
        tid = _s(team.get("id")) or _s(c.get("id"))
        if not tid:
            continue
        teams[tid] = {
            "name": _s(team.get("displayName")) or _s(team.get("shortDisplayName")),
            "home": _s(c.get("homeAway")) == "home",
            "score": _score(c.get("score")),
            "winner": c.get("winner"),
        }
    return {"date": _s(comp.get("date")) or _s(header.get("date")), "teams": teams}


def _match_result(info, team_id):
    """팀 기준 상대·스코어·승무패."""
    teams = info.get("teams") or {}
    me = teams.get(team_id)
    opp = next((v for k, v in teams.items() if k != team_id), None)
    if not me or not opp:
        return {"opp": "", "home": None, "gf": None, "ga": None, "res": ""}
    gf, ga = me.get("score"), opp.get("score")
    res = ""
    if gf is not None and ga is not None:
        res = "W" if gf > ga else "L" if gf < ga else "D"
    return {"opp": opp.get("name", ""), "home": me.get("home"), "gf": gf, "ga": ga, "res": res}


# 포지션 약어 -> (앞뒤 깊이, 좌우). 깊이 0=골키퍼 … 5=최전방, 좌우 -1=왼쪽 0=가운데 1=오른쪽
_DEPTH = [
    (("G", "GK"), 0),
    (("LWB", "RWB", "WB"), 1.5),
    (("LB", "RB", "CB", "CD", "D", "SW", "DF"), 1),
    (("DM", "CDM"), 2),
    (("LM", "RM", "CM", "M", "MF"), 3),
    (("AM", "CAM", "LAM", "RAM"), 4),
    (("LW", "RW", "F", "CF", "ST", "S", "FW", "LF", "RF"), 5),
]


_LEFT = {"LB", "LWB", "LM", "LW", "LF", "LAM"}
_RIGHT = {"RB", "RWB", "RM", "RW", "RF", "RAM"}


def position_shape(abbr):
    """포지션 약어 -> (깊이, 좌우). 좌우는 -2(왼쪽 측면) ~ +2(오른쪽 측면). 모르는 약어면 깊이 None."""
    a = _s(abbr).upper()
    base, side = a, 0
    if "-" in a:
        base, suffix = a.split("-", 1)
        side = -1 if suffix.startswith("L") else 1 if suffix.startswith("R") else 0
    if side == 0:
        # 풀백·윙어처럼 측면 전용 포지션은 -2/+2, 'CD-L' 같은 중앙 좌우는 -1/+1
        side = -2 if base in _LEFT else 2 if base in _RIGHT else 0
    for names, depth in _DEPTH:
        if base in names:
            return depth, side
    return None, side


def _minute(clock):
    clock = _d(clock)
    text = _s(clock.get("displayValue"))
    if text:
        nums = [int(n) for n in re.findall(r"\d+", text)]
        if nums:
            return min(90, sum(nums[:2]))
    val = _int(clock.get("value"))
    if val is not None:
        return min(90, max(0, round(val / 60)))
    return None


def _sub_events(summary):
    """keyEvents의 교체 기록 -> [(분, [선수id...])]"""
    out = []
    for ev in _l(_d(summary).get("keyEvents")) + _l(_d(summary).get("commentary")):
        ev = _d(ev)
        play = _d(ev.get("play")) or ev
        t = _d(play.get("type"))
        kind = (_s(t.get("text")) + " " + _s(t.get("type"))).lower()
        if "substitution" not in kind:
            continue
        minute = _minute(play.get("clock"))
        ids = []
        for part in _l(play.get("participants")):
            pid = _s(_d(_d(part).get("athlete")).get("id"))
            if pid:
                ids.append(pid)
        if minute is not None and ids:
            out.append((minute, ids))
    return out


def _goal_events(summary):
    """keyEvents 득점 -> [(득점자id, 도움id|None)] (로스터에 기록이 없을 때만 씀)"""
    out = []
    for ev in _l(_d(summary).get("keyEvents")):
        ev = _d(ev)
        if not ev.get("scoringPlay"):
            continue
        t = (_s(_d(ev.get("type")).get("text"))).lower()
        if "own" in t:
            continue
        ids = [_s(_d(_d(p).get("athlete")).get("id")) for p in _l(ev.get("participants"))]
        ids = [i for i in ids if i]
        if ids:
            out.append((ids[0], ids[1] if len(ids) > 1 else None))
    return out


FULL_MATCH = 90


def parse_soccer_lineup(summary):
    """summary -> {"home": {...}, "away": {...}}; 라인업 미발표면 lineup 비어 있음."""
    result = {}
    for r in _l(_d(summary).get("rosters")):
        r = _d(r)
        side = _s(r.get("homeAway")) or "home"
        team = _d(r.get("team"))
        players, bench = [], []
        for p in _l(r.get("roster")):
            p = _d(p)
            ath = _d(p.get("athlete"))
            if not _s(ath.get("id")):
                continue
            pos = _s(_d(p.get("position")).get("abbreviation")) or _s(
                _d(ath.get("position")).get("abbreviation")
            )
            entry = {
                "id": _s(ath.get("id")),
                "name": _s(ath.get("displayName")) or _s(ath.get("shortName")),
                "short": _s(ath.get("shortName")) or _s(ath.get("lastName")) or _s(ath.get("displayName")),
                "pos": pos,
                "jersey": _s(p.get("jersey")),
                "place": _int(p.get("formationPlace")),
            }
            (players if p.get("starter") else bench).append(entry)
        result[side] = {
            "team_id": _s(team.get("id")),
            "team_name": _s(team.get("displayName")),
            "formation": _s(r.get("formation")),
            "lineup": players,
            "bench": bench if players else [],
        }
    return result


def parse_soccer_match_for_team(summary, team_id):
    """지난 경기 summary에서 해당 팀의 선발·출전·출전시간·득점·도움과 경기 결과를 뽑는다."""
    team_id = _s(team_id)
    for r in _l(_d(summary).get("rosters")):
        r = _d(r)
        if _s(_d(r.get("team")).get("id")) != team_id:
            continue
        starters, played, names = [], [], {}
        subbed_in, subbed_out = set(), set()
        goals, assists = {}, {}
        has_stats = False
        for p in _l(r.get("roster")):
            p = _d(p)
            ath = _d(p.get("athlete"))
            pid = _s(ath.get("id"))
            if not pid:
                continue
            names[pid] = _s(ath.get("displayName")) or _s(ath.get("shortName"))
            came_on = _truthy_sub(p.get("subbedIn"))
            if p.get("starter"):
                starters.append(pid)
                played.append(pid)
            elif came_on or _positive(p.get("appearances")):
                played.append(pid)
            if came_on:
                subbed_in.add(pid)
            if _truthy_sub(p.get("subbedOut")):
                subbed_out.add(pid)
            stats = _stat_map(p.get("stats"))
            if stats:
                has_stats = True
            g = _int(stats.get("totalGoals", stats.get("goals")))
            a = _int(stats.get("goalAssists", stats.get("assists")))
            if g:
                goals[pid] = g
            if a:
                assists[pid] = a

        on_min, off_min = {}, {}
        for minute, ids in _sub_events(summary):
            for pid in ids:
                if pid in subbed_in and pid not in on_min:
                    on_min[pid] = minute
                elif pid in subbed_out and pid not in off_min:
                    off_min[pid] = minute

        minutes = {}
        start_set = set(starters)
        for pid in played:
            start = 0 if pid in start_set else on_min.get(pid)
            if pid in subbed_out:
                end = off_min.get(pid)
            else:
                end = FULL_MATCH
            if start is None or end is None:
                minutes[pid] = None
            else:
                minutes[pid] = max(1, end - start)

        if not has_stats:
            mine = set(names)
            for scorer, helper in _goal_events(summary):
                if scorer in mine:
                    goals[scorer] = goals.get(scorer, 0) + 1
                if helper and helper in mine:
                    assists[helper] = assists.get(helper, 0) + 1

        info = header_info(summary)
        rec = {
            "date": info.get("date", ""),
            "starters": starters,
            "played": played,
            "names": names,
            "minutes": minutes,
            "goals": goals,
            "assists": assists,
        }
        rec.update(_match_result(info, team_id))
        return rec
    return None


def _truthy_sub(value):
    if isinstance(value, dict):
        return bool(value.get("didSub") or value.get("value") or value.get("subbedIn"))
    return bool(value)


def _positive(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 농구 (ESPN)

def _minutes_text(value):
    text = _s(value)
    if not text or text in ("--", "-"):
        return None
    if ":" in text:
        text = text.split(":", 1)[0]
    return _int(text)


def parse_basketball_box_for_team(summary, team_id=None):
    """boxscore.players -> 팀별 기록. team_id를 주면 그 팀만, 없으면 {team_id: {...}} 전체."""
    info = header_info(summary)
    per_team = {}
    for block in _l(_d(_d(summary).get("boxscore")).get("players")):
        block = _d(block)
        tid = _s(_d(block.get("team")).get("id"))
        starters, played, names = [], [], {}
        minutes, points, assists = {}, {}, {}
        for group in _l(block.get("statistics")):
            group = _d(group)
            labels = [(_s(x)).upper() for x in (_l(group.get("names")) or _l(group.get("labels")) or _l(group.get("keys")))]
            def col(stats, *want):
                for w in want:
                    if w in labels:
                        i = labels.index(w)
                        return stats[i] if i < len(stats) else None
                return None
            for a in _l(group.get("athletes")):
                a = _d(a)
                ath = _d(a.get("athlete"))
                pid = _s(ath.get("id"))
                if not pid:
                    continue
                names[pid] = _s(ath.get("displayName")) or _s(ath.get("shortName"))
                if a.get("didNotPlay"):
                    continue
                played.append(pid)
                if a.get("starter"):
                    starters.append(pid)
                stats = _l(a.get("stats"))
                minutes[pid] = _minutes_text(col(stats, "MIN", "MINUTES"))
                p = _int(col(stats, "PTS", "POINTS"))
                s = _int(col(stats, "AST", "ASSISTS"))
                if p:
                    points[pid] = p
                if s:
                    assists[pid] = s
        rec = {
            "date": info.get("date", ""),
            "starters": starters,
            "played": sorted(set(played), key=played.index),
            "names": names,
            "minutes": minutes,
            "goals": points,
            "assists": assists,
        }
        rec.update(_match_result(info, tid))
        per_team[tid] = rec
    if team_id is None:
        return per_team
    return per_team.get(_s(team_id))


def parse_basketball_lineup(summary):
    """오늘 경기 summary -> {"home": {...}, "away": {...}} (스타터 5명)."""
    per_team = parse_basketball_box_for_team(summary)
    sides = {}
    header = _d(summary).get("header")
    comps = _l(_d(header).get("competitions"))
    order = []
    if comps:
        for c in _l(_d(comps[0]).get("competitors")):
            c = _d(c)
            order.append((_s(c.get("homeAway")) or "home", _s(_d(c.get("team")).get("id")),
                          _s(_d(c.get("team")).get("displayName"))))
    for side, tid, tname in order:
        block = per_team.get(tid) or {}
        starters = block.get("starters") or []
        names = block.get("names") or {}
        sides[side] = {
            "team_id": tid,
            "team_name": tname,
            "formation": "",
            "lineup": [{"id": pid, "name": names.get(pid, ""), "pos": "", "jersey": ""} for pid in starters],
        }
    return sides


# ---------------------------------------------------------------- 야구 (MLB 공식)

def parse_mlb_schedule(data):
    """statsapi schedule(hydrate=lineups,probablePitcher) -> 경기 목록."""
    games = []
    for day in _l(_d(data).get("dates")):
        for g in _l(_d(day).get("games")):
            g = _d(g)
            teams = _d(g.get("teams"))
            home = _d(teams.get("home"))
            away = _d(teams.get("away"))
            lineups = _d(g.get("lineups"))
            kst = to_kst(g.get("gameDate"))
            state = _s(_d(g.get("status")).get("abstractGameState")).lower()
            games.append(
                {
                    "key": f"mlb::{g.get('gamePk')}",
                    "sport": "야구",
                    "league": "MLB",
                    "league_slug": "mlb",
                    "event_id": str(g.get("gamePk") or ""),
                    "start_kst": kst.isoformat() if kst else "",
                    "state": {"preview": "pre", "live": "in", "final": "post"}.get(state, "pre"),
                    "home": {
                        "id": str(_d(_d(home).get("team")).get("id") or ""),
                        "name": _s(_d(_d(home).get("team")).get("name")),
                        "short": _s(_d(_d(home).get("team")).get("teamName")),
                        "logo": "",
                        "score": _int(_d(home).get("score")),
                    },
                    "away": {
                        "id": str(_d(_d(away).get("team")).get("id") or ""),
                        "name": _s(_d(_d(away).get("team")).get("name")),
                        "short": _s(_d(_d(away).get("team")).get("teamName")),
                        "logo": "",
                        "score": _int(_d(away).get("score")),
                    },
                    "lineups": {
                        "home": _mlb_players(lineups.get("homePlayers")),
                        "away": _mlb_players(lineups.get("awayPlayers")),
                    },
                    "probables": {
                        "home": _mlb_person(_d(home).get("probablePitcher")),
                        "away": _mlb_person(_d(away).get("probablePitcher")),
                    },
                }
            )
    return games


def _mlb_players(players):
    out = []
    for p in _l(players):
        p = _d(p)
        out.append(
            {
                "id": str(p.get("id") or ""),
                "name": _s(p.get("fullName")),
                "pos": _s(_d(p.get("primaryPosition")).get("abbreviation")),
                "jersey": _s(p.get("primaryNumber")),
            }
        )
    return out


def _mlb_person(person):
    person = _d(person)
    if not person.get("id"):
        return None
    return {"id": str(person.get("id")), "name": _s(person.get("fullName"))}


def mlb_season_stat(person, group="hitting"):
    """people?hydrate=stats(...) 응답의 한 선수에서 시즌 스탯 dict를 꺼낸다."""
    for block in _l(_d(person).get("stats")):
        block = _d(block)
        gname = _s(_d(block.get("group")).get("displayName")) or _s(block.get("group"))
        tname = _s(_d(block.get("type")).get("displayName")) or _s(block.get("type"))
        if gname and gname != group:
            continue
        if tname and tname != "season":
            continue
        splits = _l(block.get("splits"))
        if splits:
            return _d(_d(splits[-1]).get("stat"))
    return {}


def mlb_history_from_stats(people, team_games, pa_per_game=2.2):
    """타자 시즌 기록으로 '주전 여부'를 만든다.
    반환: grade.analyze_lineup 이 먹을 수 있는 history 형태(가상의 경기 목록).
    선발 출전 횟수를 직접 주는 대신, 타석/경기 비율로 주전을 가려낸다.
    """
    regulars, names, apps, hr, rbi = [], {}, {}, {}, {}
    for person in _l(people):
        person = _d(person)
        pid = str(person.get("id") or "")
        if not pid:
            continue
        names[pid] = _s(person.get("fullName"))
        stat = mlb_season_stat(person, "hitting")
        pa = _num(stat.get("plateAppearances"))
        games = _num(stat.get("gamesPlayed"))
        apps[pid] = games
        if _int(stat.get("homeRuns")):
            hr[pid] = _int(stat.get("homeRuns"))
        if _int(stat.get("rbi")):
            rbi[pid] = _int(stat.get("rbi"))
        if team_games > 0 and pa / max(team_games, 1) >= pa_per_game:
            regulars.append(pid)
    return {"regulars": regulars, "names": names, "games": apps, "hr": hr, "rbi": rbi}


def parse_mlb_team_results(data, team_id):
    """statsapi schedule(teamId=...) -> 최근 결과 기록(최신순), grade.team_form 형식."""
    team_id = str(team_id)
    out = []
    for day in _l(_d(data).get("dates")):
        for g in _l(_d(day).get("games")):
            g = _d(g)
            if _s(_d(g.get("status")).get("abstractGameState")).lower() != "final":
                continue
            teams = _d(g.get("teams"))
            home, away = _d(teams.get("home")), _d(teams.get("away"))
            if str(_d(home.get("team")).get("id")) == team_id:
                me, opp, is_home = home, away, True
            elif str(_d(away.get("team")).get("id")) == team_id:
                me, opp, is_home = away, home, False
            else:
                continue
            gf, ga = _int(me.get("score")), _int(opp.get("score"))
            if gf is None or ga is None:
                continue
            out.append({
                "date": _s(g.get("gameDate")),
                "opp": _s(_d(opp.get("team")).get("name")),
                "home": is_home,
                "gf": gf,
                "ga": ga,
                "res": "W" if gf > ga else "L" if gf < ga else "D",
            })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def synth_history(regular_ids, names, matches=10, extra_ids=()):
    """'시즌 주전' 목록을 grade 모듈이 쓰는 history 형태로 바꾼다."""
    hist = []
    for _ in range(matches):
        hist.append(
            {
                "starters": list(regular_ids),
                "played": list(regular_ids) + list(extra_ids),
                "names": dict(names),
            }
        )
    return hist



# ---------------------------------------------------------------- 국가대표 Elo (eloratings.net)
# World.tsv 한 줄: 지역순위 \t 세계순위 \t 팀코드(2글자) \t 레이팅 \t …
# en.teams.tsv 한 줄: 팀코드 \t 이름 \t 다른 표기 …  (코드_loc 줄은 "in Korea" 같은 장소 표현이라 제외)

import unicodedata

# ESPN 표기와 eloratings 표기가 다른 나라들
NATION_ALIASES = {
    "korea republic": "south korea", "republic of korea": "south korea", "korea": "south korea",
    "korea dpr": "north korea", "dpr korea": "north korea",
    "usa": "united states", "united states of america": "united states", "usmnt": "united states",
    "ir iran": "iran", "china pr": "china", "czechia": "czech republic", "turkiye": "turkey",
    "cote divoire": "ivory coast", "cabo verde": "cape verde", "congo dr": "dr congo",
    "democratic republic of the congo": "dr congo", "bosnia herzegovina": "bosnia and herzegovina",
    "north macedonia": "macedonia", "republic of ireland": "ireland", "eswatini": "swaziland",
    "chinese taipei": "taiwan", "kyrgyz republic": "kyrgyzstan", "timor leste": "east timor",
}


def nation_key(name):
    """비교용 이름: 악센트·기호 제거, 소문자, 별칭 통일."""
    text = unicodedata.normalize("NFKD", _s(name)).encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("'", "").replace("`", "")
    text = re.sub(r"[^a-z ]+", " ", text.replace("&", " and "))
    text = re.sub(r"\s+", " ", text).strip()
    return NATION_ALIASES.get(text, text)


def parse_elo_world(text):
    """World.tsv -> {팀코드: 레이팅}"""
    out = {}
    for line in _s(text).splitlines():
        f = line.split("\t")
        if len(f) < 4:
            continue
        code, rating = f[2].strip(), _int(f[3])
        if code and rating:
            out[code] = rating
    return out


def parse_elo_names(text):
    """en.teams.tsv -> {비교용 이름: 팀코드}"""
    out = {}
    for line in _s(text).splitlines():
        f = line.split("\t")
        if len(f) < 2 or f[0].endswith("_loc"):
            continue
        code = f[0].strip()
        for label in f[1:]:
            key = nation_key(label)
            if key and key not in out:
                out[key] = code
    return out


def elo_for(team_name, ratings, names):
    """ESPN 팀 이름 -> (레이팅, 팀코드). 못 찾으면 (None, None)."""
    key = nation_key(team_name)
    for k in (key, key.replace(" women", ""), key.replace(" u23", "")):
        code = names.get(k)
        if code and code in ratings:
            return ratings[code], code
    return None, None
