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
                "id": _idstr(team.get("id")),
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


def _idstr(v):
    """번호(숫자·문자 모두) → 문자열. 공급처가 번호를 숫자로 줘도 빈 값이 되지 않게."""
    if v is None or isinstance(v, (dict, list)) or v == "":
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


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
                "id": _idstr(ath.get("id")),
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
                        "logo": f"https://www.mlbstatic.com/team-logos/{_d(_d(home).get('team')).get('id')}.svg" if _d(_d(home).get("team")).get("id") else "",
                        "score": _int(_d(home).get("score")),
                    },
                    "away": {
                        "id": str(_d(_d(away).get("team")).get("id") or ""),
                        "name": _s(_d(_d(away).get("team")).get("name")),
                        "short": _s(_d(_d(away).get("team")).get("teamName")),
                        "logo": f"https://www.mlbstatic.com/team-logos/{_d(_d(away).get('team')).get('id')}.svg" if _d(_d(away).get("team")).get("id") else "",
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
                "gamePk": g.get("gamePk"),
                "side": "home" if is_home else "away",
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



def parse_mlb_boxscore_side(box, side):
    """statsapi boxscore 한 팀 -> {"starters": 선발 타순 id, "played": 타석에 선 선수 id, "names": {...}}"""
    t = _d(_d(_d(box).get("teams")).get(side))
    order = [str(x) for x in _l(t.get("battingOrder")) if x]
    batters = [str(x) for x in _l(t.get("batters")) if x]
    names = {}
    for key, p in _d(t.get("players")).items():
        person = _d(_d(p).get("person"))
        pid = str(person.get("id") or str(key).replace("ID", ""))
        if pid:
            names[pid] = _s(person.get("fullName"))
    played = []
    for pid in order + batters:
        if pid not in played:
            played.append(pid)
    # 투수는 타석에 안 서면 batters에 들어와도 타자 기록이 아니다 — 타순에 없고 포지션이 P면 뺀다
    pos = {str(_d(_d(p).get("person")).get("id")): _s(_d(_d(p).get("position")).get("abbreviation"))
           for p in _d(t.get("players")).values()}
    played = [pid for pid in played if pid in order or pos.get(pid) != "P"]
    return {"starters": order[:9], "played": played, "names": names}


# ---------------------------------------------------------------- 네이버 스포츠 (K리그1·2)
# 6차 소스 점검에서 확인한 모양:
#  경기 목록: result.games[] {gameId, categoryId, gameDateTime(한국시간, 시간대 표기 없음), statusCode BEFORE/…/RESULT,
#             homeTeamCode, homeTeamName, homeTeamScore, homeTeamEmblemUrl, cancel …}
#  라인업:   result.lineUpData.lineup.{home,away} = {players: [[줄1 선수…], [줄2 …]] , formation}
#             선수 {playerId, name, pos(GK/DF/MF/FW), shirtNumber, positionOrder, goal, assists}
#             result.lineUpData.substitution.{home,away} = [교체명단 선수…]
#  선수 기록: result.recordData.{home,away}PlayerStats[] {playerId, playerName, position, goals, assists,
#             playerPoint(평점), workTime(출전 분)}

NAVER_STATE = {"BEFORE": "pre", "READY": "pre", "STARTED": "in", "LIVE": "in", "RESULT": "post", "END": "post"}


def naver_kst(text):
    """'2026-09-27T19:00:00' (한국시간) -> KST datetime"""
    t = _s(text)
    if not t:
        return None
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)


def parse_naver_games(data, categories, sport="축구"):
    """경기 목록 -> 우리 형식. categories: {categoryId: 리그 이름}"""
    out = []
    for g in _l(_d(_d(data).get("result")).get("games")):
        g = _d(g)
        cat = _s(g.get("categoryId"))
        if cat not in categories or g.get("cancel"):
            continue
        kst = naver_kst(g.get("gameDateTime"))
        gid = _idstr(g.get("gameId"))
        if not gid or not kst:
            continue
        side = lambda p: {
            "id": _idstr(g.get(f"{p}TeamCode")), "name": _s(g.get(f"{p}TeamName")), "short": _s(g.get(f"{p}TeamName")),
            "logo": _s(g.get(f"{p}TeamEmblemUrl")), "score": _int(g.get(f"{p}TeamScore")),
        }
        status = _s(g.get("statusCode")).upper()
        out.append({
            "key": f"{sport}:naver.{cat}:{gid}", "sport": sport, "league": categories[cat], "league_slug": f"naver.{cat}",
            "event_id": gid, "start_kst": kst.isoformat(), "state": NAVER_STATE.get(status, "in" if status else "pre"),
            "home": side("home"), "away": side("away"), "source": "naver", "category": cat,
        })
    return out


def _naver_player(p):
    p = _d(p)
    return {
        "id": _idstr(p.get("playerId")), "name": _s(p.get("name")) or _s(p.get("playerName")),
        "short": _s(p.get("name")) or _s(p.get("playerName")), "pos": _s(p.get("pos")) or _s(p.get("position")),
        "jersey": _s(p.get("shirtNumber")), "place": _int(p.get("positionOrder")),
    }


def _naver_raw_rows(data, side):
    """라인업 원본에서 한 팀의 줄별 선수(가공 전) 목록."""
    lu = _d(_d(_d(data).get("result")).get("lineUpData"))
    players = _d(_d(lu.get("lineup")).get(side)).get("players")
    if isinstance(players, dict):              # 유럽 경기 모양: {"lineup": [[…]], "row": …}
        players = players.get("lineup")
    return [[_d(p) for p in (r if isinstance(r, list) else [r])] for r in _l(players)]


def parse_naver_lineup(data):
    """라인업 -> {"home"/"away": {"lineup": [...], "rows": [[인덱스…]…], "formation", "bench": [...]}}
    줄 순서는 네이버가 준 그대로(골키퍼 줄부터)."""
    lu = _d(_d(_d(data).get("result")).get("lineUpData"))
    out = {}
    for side in ("home", "away"):
        team = _d(_d(lu.get("lineup")).get(side))
        lineup, rows = [], []
        for r in _naver_raw_rows(data, side):
            idxs = []
            for p in r:
                pl = _naver_player(p)
                if pl["id"]:
                    idxs.append(len(lineup))
                    lineup.append(pl)
            if idxs:
                rows.append(idxs)
        bench = [_naver_player(p) for p in _l(_d(lu.get("substitution")).get(side))]
        out[side] = {"lineup": lineup, "rows": rows, "formation": _s(team.get("formation")),
                     "bench": [b for b in bench if b["id"]]}
    return out


def parse_naver_record(record, lineup, side):
    """지난 경기 하나에서 한 팀: 선발(라인업)·출전(출전시간>0)·출전시간·골·도움·평점."""
    lu = parse_naver_lineup(lineup).get(side) or {}
    starters = [p["id"] for p in lu.get("lineup") or []]
    names = {p["id"]: p["name"] for p in (lu.get("lineup") or []) + (lu.get("bench") or [])}
    stats = _l(_d(_d(_d(record).get("result")).get("recordData")).get(f"{side}PlayerStats"))
    minutes, goals, assists, rating, played = {}, {}, {}, {}, list(starters)
    for s in stats:
        s = _d(s)
        pid = _idstr(s.get("playerId"))
        if not pid:
            continue
        names.setdefault(pid, _s(s.get("playerName")))
        wt = _int(s.get("workTime"))
        if wt:
            minutes[pid] = min(wt, 90)
            if pid not in played:
                played.append(pid)
        if _int(s.get("goals")):
            goals[pid] = _int(s.get("goals"))
        if _int(s.get("assists")):
            assists[pid] = _int(s.get("assists"))
        try:
            if s.get("playerPoint") is not None and wt:
                rating[pid] = float(s.get("playerPoint"))
        except (TypeError, ValueError):
            pass
    if not stats:                                  # 선수 기록이 없으면 라인업의 골·도움으로 대신
        for p in [p for r in _naver_raw_rows(lineup, side) for p in r]:
            pid = _idstr(p.get("playerId"))
            if _int(p.get("goal")):
                goals[pid] = _int(p.get("goal"))
            if _int(p.get("assists")):
                assists[pid] = _int(p.get("assists"))
    for pid in starters:
        minutes.setdefault(pid, None if stats else 90)
    return {"starters": starters, "played": played, "names": names, "minutes": minutes,
            "goals": goals, "assists": assists, "rating": rating}


# ---------------------------------------------------------------- 야구 투수 (MLB 공식)

def ip_to_float(ip):
    """'6.2'(6과 2/3이닝) -> 6.667"""
    t = _s(str(ip)) if ip is not None else ""
    if not t:
        return 0.0
    try:
        whole, _, frac = t.partition(".")
        return int(whole or 0) + {"": 0, "0": 0, "1": 1 / 3, "2": 2 / 3}.get(frac, 0)
    except ValueError:
        return 0.0


FIP_CONST = 3.1


def pitcher_season(person):
    """people?hydrate=stats(group=[pitching],type=[season]) 한 명 -> 시즌 요약."""
    person = _d(person)
    st = {}
    for block in _l(person.get("stats")):
        for sp in _l(_d(block).get("splits")):
            st = _d(_d(sp).get("stat")) or st
    ip = ip_to_float(st.get("inningsPitched"))
    k, bb, hr = _num(st.get("strikeOuts")), _num(st.get("baseOnBalls")), _num(st.get("homeRuns"))
    gs = int(_num(st.get("gamesStarted")))
    fip = round((13 * hr + 3 * bb - 2 * k) / ip + FIP_CONST, 2) if ip >= 10 else None
    era = _num(st.get("era")) if st.get("era") not in (None, "-.--", "") else None
    whip = _num(st.get("whip")) if st.get("whip") not in (None, "-.--", "") else None
    return {
        "id": str(person.get("id") or ""), "name": _s(person.get("fullName")),
        "hand": _s(_d(person.get("pitchHand")).get("code")),
        "era": era, "whip": whip, "fip": fip, "ip": round(ip, 1), "gs": gs,
        "ip_per_start": round(ip / gs, 2) if gs else None, "k": int(k), "bb": int(bb),
    }


def pitcher_starts(data):
    """people/{id}/stats?stats=gameLog&group=pitching -> 선발 등판 목록 (최신순)."""
    out = []
    for block in _l(_d(data).get("stats")):
        for sp in _l(_d(block).get("splits")):
            sp = _d(sp)
            st = _d(sp.get("stat"))
            if not _num(st.get("gamesStarted")):
                continue
            out.append({
                "date": _s(sp.get("date")), "opp": _s(_d(sp.get("opponent")).get("name")),
                "ip": round(ip_to_float(st.get("inningsPitched")), 1), "er": int(_num(st.get("earnedRuns"))),
                "pitches": int(_num(st.get("numberOfPitches") or st.get("pitchesThrown"))),
            })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def bullpen_usage(box, side):
    """박스스코어 한 팀: 선발(첫 투수)을 뺀 불펜 투수별 투구수."""
    t = _d(_d(_d(box).get("teams")).get(side))
    pitchers = [str(x) for x in _l(t.get("pitchers")) if x]
    out = {}
    for pid in pitchers[1:]:
        st = _d(_d(_d(_d(t.get("players")).get(f"ID{pid}")).get("stats")).get("pitching"))
        n = int(_num(st.get("numberOfPitches") or st.get("pitchesThrown")))
        out[pid] = n
    return out


def parse_espn_team_schedule(data, team_id):
    """ESPN 팀 일정 -> [{"date"(KST iso), "completed", "opp", "home", "gf", "ga"}]"""
    out = []
    tid = _s(team_id)
    for ev in _l(_d(data).get("events")):
        ev = _d(ev)
        comp = _d((_l(ev.get("competitions")) or [{}])[0])
        when = to_kst(ev.get("date") or comp.get("date"))
        if not when:
            continue
        mine = other = None
        for c in _l(comp.get("competitors")):
            c = _d(c)
            cid = _s(c.get("id")) or _s(_d(c.get("team")).get("id"))
            if cid == tid:
                mine = c
            else:
                other = c
        if mine is None:
            continue
        done = bool(_d(_d(comp.get("status")).get("type")).get("completed"))
        out.append({"date": when.isoformat(), "completed": done,
                    "opp": _s(_d(_d(other).get("team")).get("displayName")) if other else "",
                    "home": _s(mine.get("homeAway")) == "home",
                    "gf": _score(mine.get("score")) if done else None,
                    "ga": _score(_d(other).get("score")) if (done and other) else None})
    return out



# ---------------------------------------------------------------- 네이버 KBO (8차 소스 점검에서 확인한 모양)
#  preview: result.previewData.{home,away}TeamLineUp.fullLineUp[] {positionName, playerCode, playerName, batsThrows, backnum}
#           result.previewData.{home,away}Starter {playerInfo{name, pCode, hitType}, currentSeasonStats{era, whip, inn, gameCount, kk, bb, hr}}
#           result.previewData.{home,away}Standings {era, hra, w, l, d, rank}
#  record : result.recordData.battersBoxscore.{home,away}[] {batOrder, playerCode, name?, pos}
#           result.recordData.pitchersBoxscore.{home,away}[] {pcode, name, inn, er, bf, ...}  (첫 투수 = 선발)
#           ※ bf는 '상대 타자 수'가 아니라 '투구수' (같은 경기 문자중계 ballCount와 일치 확인: 6이닝 82구)

def _kbo_hand(text):
    t = _s(text)
    return "L" if "좌" in t[:2] else ("R" if "우" in t[:2] else "")


def parse_kbo_preview(data, side):
    pv = _d(_d(_d(data).get("result")).get("previewData"))
    lu = _l(_d(pv.get(f"{side}TeamLineUp")).get("fullLineUp"))
    batters = []
    for p in lu:
        p = _d(p)
        if "투수" in _s(p.get("positionName")):
            continue
        pid = _idstr(p.get("playerCode"))
        if pid:
            bats = _s(p.get("batsThrows"))
            batters.append({"id": pid, "name": _s(p.get("playerName")), "pos": _s(p.get("positionName")),
                            "jersey": _s(p.get("backnum")), "bats": "L" if "좌타" in bats else "S" if "양타" in bats else "R"})
    st = _d(pv.get(f"{side}Starter"))
    info, cs = _d(st.get("playerInfo")), _d(st.get("currentSeasonStats"))
    sp = None
    if info.get("name") or info.get("pCode"):
        ip = ip_to_float(cs.get("inn"))
        k, bb, hr = _num(cs.get("kk")), _num(cs.get("bb")), _num(cs.get("hr"))
        gs = int(_num(cs.get("gameCount")))
        sp = {"id": _idstr(info.get("pCode")), "name": _s(info.get("name")), "hand": _kbo_hand(info.get("hitType")),
              "era": _num(cs.get("era")) if cs.get("era") not in (None, "", "-") else None,
              "whip": _num(cs.get("whip")) if cs.get("whip") not in (None, "", "-") else None,
              "fip": round((13 * hr + 3 * bb - 2 * k) / ip + FIP_CONST, 2) if ip >= 10 else None,
              "ip": round(ip, 1), "gs": gs, "ip_per_start": round(ip / gs, 2) if gs else None}
    stand = _d(pv.get(f"{side}Standings"))
    return {"lineup": batters[:9], "sp": sp,
            "team_era": _num(stand.get("era")) if stand.get("era") not in (None, "", "-") else None}


def parse_kbo_record(data, side):
    """지난 경기 한 팀: 선발 타순(1~9번 첫 타자)·출전 타자·선발투수 기록·불펜 투구수."""
    rd = _d(_d(_d(data).get("result")).get("recordData"))
    bats = _l(_d(rd.get("battersBoxscore")).get(side))
    first, played, names = {}, [], {}
    for b in bats:
        b = _d(b)
        pid = _idstr(b.get("playerCode"))
        if not pid:
            continue
        names[pid] = _s(b.get("name") or b.get("playerName"))
        o = _int(b.get("batOrder"))
        if o and 1 <= o <= 9 and o not in first:
            first[o] = pid
        if pid not in played:
            played.append(pid)
    pits = [_d(p) for p in _l(_d(rd.get("pitchersBoxscore")).get(side))]
    sp = None
    if pits:
        p0 = pits[0]
        sp = {"id": _idstr(p0.get("pcode")), "ip": round(ip_to_float(p0.get("inn")), 1), "er": _int(p0.get("er")),
              "pitches": _int(p0.get("bf")) or None}
    pen_pitches = sum(_int(p.get("bf")) for p in pits[1:])
    pen_ids = [_idstr(p.get("pcode")) for p in pits[1:] if p.get("pcode")]
    return {"starters": [first[o] for o in sorted(first)], "played": played, "names": names,
            "sp": sp, "pen_pitches": pen_pitches, "pen_ids": pen_ids}


# ---------------------------------------------------------------- 풋몹 결장자 (9차 소스 점검에서 확인한 모양)
#  목록: /api/data/matches?date=YYYYMMDD → leagues[].matches[] {id, home{name,id}, away{name,id}, status{utcTime, finished, started}}
#  상세: /api/data/matchDetails?matchId= → content.lineup.{homeTeam,awayTeam}.unavailable[]
#        {id, name, unavailability{type: injury|suspension, expectedReturn: "Mid October 2026", expectedReturnDate?}}

_MONTH_KO = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                          "September", "October", "November", "December"], 1)}


def fotmob_return_text(text):
    """'Late September 2026' → '9월 말', 'Mid October 2026' → '10월 중순', 'Early January 2027' → '1월 초'"""
    t = _s(text)
    if not t or t.lower().startswith("unknown"):
        return "미정"
    part = {"Early": "초", "Mid": "중순", "Late": "말"}
    words = t.split()
    when = part.get(words[0]) if words else None
    month = next((_MONTH_KO[w] for w in words if w in _MONTH_KO), None)
    if month:
        return f"{month}월 {when}".strip() if when else f"{month}월"
    return t


def parse_fotmob_matches(data):
    out = []
    for lg in _l(_d(data).get("leagues")):
        lg = _d(lg)
        for m in _l(lg.get("matches")):
            m = _d(m)
            st = _d(m.get("status"))
            out.append({"id": _idstr(m.get("id")), "home": _s(_d(m.get("home")).get("name")), "away": _s(_d(m.get("away")).get("name")),
                        "utc": _s(st.get("utcTime") or m.get("time")), "league": _s(lg.get("name"))})
    return out


def parse_fotmob_unavailable(data):
    lu = _d(_d(_d(data).get("content")).get("lineup"))
    out = {}
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        rows = []
        for u in _l(_d(lu.get(key)).get("unavailable")):
            u = _d(u)
            un = _d(u.get("unavailability"))
            typ = _s(un.get("type")).lower()
            rows.append({"id": _idstr(u.get("id")), "name": _s(u.get("name")),
                         "type": "부상" if typ == "injury" else "징계" if typ == "suspension" else "결장",
                         "return": fotmob_return_text(un.get("expectedReturn")),
                         "rating": _d(u.get("performance")).get("seasonRating")})
        out[side] = rows
    return out


def person_key(name):
    """선수 이름 비교용: 악센트 없애고 소문자 글자만."""
    t = unicodedata.normalize("NFKD", _s(name)).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def same_person(a, b):
    ka, kb = person_key(a), person_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    wa, wb = ka.split(), kb.split()
    # 성이 같고 이름 첫 글자가 같거나, 한쪽이 한 단어(예: 'Rodrygo')로 다른 쪽에 들어 있을 때
    if wa[-1] == wb[-1] and (len(wa) == 1 or len(wb) == 1 or wa[0][0] == wb[0][0]):
        return True
    return (len(wa) == 1 and wa[0] in wb) or (len(wb) == 1 and wb[0] in wa)
