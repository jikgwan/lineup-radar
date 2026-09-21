#!/usr/bin/env python3
"""5차 소스 점검 (읽기만 함): 유럽 리그·A매치의 교체·도움·출전시간을 줄 수 있는 곳 찾기.

후보: ESPN(옵션 추가) · UEFA 공식 · FIFA 공식 · 소파스코어 · 풋몹, 그리고 네이버 eventMap
각 후보마다: ① 경기 목록이 오는지 ② 끝난 경기 하나에서 선발·벤치·교체 시각·도움·출전시간이 오는지
로그를 그대로 복사해서 보내주면 된다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import requests

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
BASE_H = {"User-Agent": CHROME, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
TODAY = date.today()
CLUB_DAY = TODAY - timedelta(days=((TODAY.weekday() - 6) % 7) or 7)   # 가장 최근 지난 일요일


def get(url, params=None, headers=None):
    try:
        return requests.get(url, params=params, headers=dict(BASE_H, **(headers or {})), timeout=20)
    except Exception as exc:
        print(f"      ! 접속 실패: {type(exc).__name__} {str(exc)[:100]}")
        return None


def jget(url, params=None, headers=None):
    r = get(url, params, headers)
    if r is None:
        return None, "실패"
    if r.status_code != 200:
        return None, r.status_code
    try:
        return r.json(), 200
    except ValueError:
        return None, "JSON 아님"


def head(t):
    print()
    print("=" * 74)
    print(t)
    print("=" * 74)


def verdict(label, starters=None, bench=None, sub_min=None, assists=None, minutes=None, note=""):
    def m(x):
        return "?" if x is None else ("O" if x else "X")
    print(f"  ▶ {label}")
    print(f"     선발 {m(starters)} · 벤치 {m(bench)} · 교체 시각 {m(sub_min)} · 도움 {m(assists)} · 출전시간 {m(minutes)}  {note}")


# ---------------------------------------------------------------- ESPN (옵션 추가)
def espn():
    head("1) ESPN site.web + 지역·언어 옵션")
    base = "https://site.web.api.espn.com/apis/site/v2/sports/soccer"
    opt = {"region": "us", "lang": "en", "contentorigin": "espn"}
    d = CLUB_DAY.strftime("%Y%m%d")
    ev = None
    for label, params in (("날짜 하나", dict(opt, dates=d)), ("날짜 구간", dict(opt, dates=f"{(CLUB_DAY - timedelta(days=2)).strftime('%Y%m%d')}-{d}")),
                          ("옵션만", opt)):
        body, st = jget(f"{base}/eng.1/scoreboard", params)
        evs = (body or {}).get("events") or []
        print(f"  {'O' if body else 'X'}  EPL 목록 {label:6} HTTP {st} · 경기 {len(evs) if body else None}")
        done = [e for e in evs if ((e.get('competitions') or [{}])[0].get('status') or {}).get('type', {}).get('completed')]
        if done and not ev:
            ev = done[0]
    for slug in ("uefa.nations", "fifa.friendly"):
        body, st = jget(f"{base}/{slug}/scoreboard", dict(opt, dates=(TODAY + timedelta(days=3)).strftime("%Y%m%d")))
        print(f"  {'O' if body else 'X'}  {slug} 목록 HTTP {st} · 경기 {len((body or {}).get('events') or []) if body else None}")
    if not ev:
        print("  - 끝난 경기를 못 찾아 라인업 확인 생략")
        return
    body, st = jget(f"{base}/eng.1/summary", dict(opt, event=ev["id"]))
    if not body:
        verdict(f"summary HTTP {st}")
        return
    ros = body.get("rosters") or []
    r0 = (ros[0].get("roster") or []) if ros else []
    starters = sum(1 for p in r0 if p.get("starter"))
    bench = sum(1 for p in r0 if not p.get("starter"))
    subs = [k for k in (body.get("keyEvents") or []) if "ubstitution" in str((k.get("type") or {}).get("text"))]
    stats = {s.get("name") for p in r0 for s in (p.get("stats") or []) if isinstance(s, dict)}
    verdict(f"ESPN {ev.get('name', '')[:50]} (HTTP {st})", starters >= 10, bench > 0, bool(subs),
            "goalAssists" in stats, None, f"교체 이벤트 {len(subs)}건 · 선수 통계 {sorted(stats)[:8]}")
    body, st = jget(f"{base}/eng.1/teams/{(ev['competitions'][0]['competitors'][0].get('team') or {}).get('id')}/schedule", opt)
    print(f"  {'O' if body else 'X'}  팀 일정 HTTP {st} · 경기 {len((body or {}).get('events') or []) if body else None}")


# ---------------------------------------------------------------- UEFA 공식
def uefa():
    head("2) UEFA 공식 (챔스·네이션스리그)")
    base = "https://match.uefa.com/v5/matches"
    found = None
    for comp, name in (("1", "챔스"), ("9", "네이션스리그?"), ("14", "유로파")):
        body, st = jget(base, {"competitionId": comp, "seasonYear": "2027", "status": "FINISHED", "limit": "5",
                               "order": "DESC", "offset": "0"})
        rows = body if isinstance(body, list) else (body or {}).get("matches") if isinstance(body, dict) else None
        print(f"  {'O' if body is not None else 'X'}  목록 {name}(competitionId={comp}) HTTP {st} · 경기 {len(rows) if rows is not None else None}")
        if rows and not found:
            found = rows[0]
    if not found:
        return
    mid = found.get("id")
    body, st = jget(f"{base}/{mid}/lineups")
    if not body:
        verdict(f"UEFA 라인업 HTTP {st}")
        return
    ht = body.get("homeTeam") or {}
    field = ht.get("field") or []
    bench = ht.get("bench") or []
    txt = json.dumps(body)[:200000]
    verdict(f"UEFA {((found.get('homeTeam') or {}).get('internationalName'))} vs {((found.get('awayTeam') or {}).get('internationalName'))} (HTTP {st})",
            len(field) >= 10, len(bench) > 0, None, None, None, f"라인업 키 {list(body.keys())[:8]} · 홈 키 {list(ht.keys())[:10]}")
    ev, st2 = jget(f"https://match.uefa.com/v5/matches/{mid}/events", {"filter": "LINEUP_EVENTS"})
    print(f"     이벤트 HTTP {st2} · {json.dumps(ev)[:250] if ev else ''}")
    print(f"     'substitut' 포함: {'substitut' in txt.lower()} · 'assist' 포함: {'assist' in txt.lower()}")


# ---------------------------------------------------------------- FIFA 공식
def fifa():
    head("3) FIFA 공식 (A매치·월드컵)")
    frm = (TODAY - timedelta(days=100)).strftime("%Y-%m-%dT00:00:00Z")
    to = TODAY.strftime("%Y-%m-%dT23:59:59Z")
    body, st = jget("https://api.fifa.com/api/v3/calendar/matches", {"from": frm, "to": to, "language": "en", "count": "500"})
    res = (body or {}).get("Results") or []
    print(f"  {'O' if body else 'X'}  경기 목록 HTTP {st} · 경기 {len(res) if body else None}")
    if not res:
        return
    comps = {}
    for m in res:
        name = ((m.get("CompetitionName") or [{}])[0] or {}).get("Description")
        comps[name] = comps.get(name, 0) + 1
    print("     대회: " + ", ".join(f"{k} {v}" for k, v in sorted(comps.items(), key=lambda x: -x[1])[:10]))
    done = [m for m in res if m.get("MatchStatus") == 0]
    if not done:
        print("  - 끝난 경기 없음")
        return
    m = done[-1]
    url = f"https://api.fifa.com/api/v3/live/football/{m.get('IdCompetition')}/{m.get('IdSeason')}/{m.get('IdStage')}/{m.get('IdMatch')}"
    body, st = jget(url, {"language": "en"})
    if not body:
        verdict(f"FIFA 경기 상세 HTTP {st}")
        return
    team = body.get("HomeTeam") or {}
    players = team.get("Players") or []
    subs = team.get("Substitutions") or []
    starters = [p for p in players if p.get("Status") == 1]
    bench = [p for p in players if p.get("Status") == 2]
    goals = team.get("Goals") or []
    verdict(f"FIFA {((team.get('TeamName') or [{}])[0] or {}).get('Description')} (HTTP {st})",
            len(starters) >= 10, len(bench) > 0, bool(subs and subs[0].get("Minute")),
            any(g.get("IdAssistPlayer") for g in goals), None, f"교체 {len(subs)}건 · 득점 {len(goals)}")


# ---------------------------------------------------------------- 소파스코어
def sofascore():
    head("4) 소파스코어")
    ev = None
    for host in ("https://api.sofascore.com", "https://www.sofascore.com"):
        body, st = jget(f"{host}/api/v1/sport/football/scheduled-events/{CLUB_DAY.isoformat()}",
                        headers={"Referer": "https://www.sofascore.com/", "Origin": "https://www.sofascore.com"})
        evs = (body or {}).get("events") or []
        print(f"  {'O' if body else 'X'}  {host} 목록 HTTP {st} · 경기 {len(evs) if body else None}")
        if evs and not ev:
            epl = [e for e in evs if (e.get("tournament") or {}).get("uniqueTournament", {}).get("id") == 17
                   and (e.get("status") or {}).get("type") == "finished"]
            ev = (host, (epl or [e for e in evs if (e.get("status") or {}).get("type") == "finished"] or [None])[0])
    if not ev or not ev[1]:
        return
    host, e = ev
    body, st = jget(f"{host}/api/v1/event/{e['id']}/lineups", headers={"Referer": "https://www.sofascore.com/"})
    if not body:
        verdict(f"라인업 HTTP {st}")
        return
    home = (body.get("home") or {}).get("players") or []
    starters = [p for p in home if not p.get("substitute")]
    bench = [p for p in home if p.get("substitute")]
    stat_keys = set()
    for p in home:
        stat_keys |= set((p.get("statistics") or {}).keys())
    verdict(f"소파스코어 {(e.get('homeTeam') or {}).get('name')} vs {(e.get('awayTeam') or {}).get('name')} (HTTP {st})",
            len(starters) >= 10, len(bench) > 0, None, "goalAssist" in stat_keys, "minutesPlayed" in stat_keys,
            f"통계 항목 {sorted(stat_keys)[:10]}")
    inc, st2 = jget(f"{host}/api/v1/event/{e['id']}/incidents", headers={"Referer": "https://www.sofascore.com/"})
    subs = [i for i in (inc or {}).get("incidents", []) if i.get("incidentType") == "substitution"]
    print(f"     교체 기록 HTTP {st2} · {len(subs)}건 · 예: {json.dumps(subs[:1], ensure_ascii=False)[:200]}")


# ---------------------------------------------------------------- 풋몹
def fotmob():
    head("5) 풋몹")
    d = CLUB_DAY.strftime("%Y%m%d")
    mid = None
    for path in ("/api/data/matches", "/api/matches"):
        body, st = jget(f"https://www.fotmob.com{path}", {"date": d}, headers={"Referer": "https://www.fotmob.com/"})
        leagues = (body or {}).get("leagues") or []
        n = sum(len(l.get("matches") or []) for l in leagues)
        print(f"  {'O' if body else 'X'}  {path} HTTP {st} · 리그 {len(leagues)} · 경기 {n if body else None}")
        for l in leagues:
            if l.get("primaryId") == 47 and not mid:
                fin = [m for m in (l.get("matches") or []) if (m.get("status") or {}).get("finished")]
                if fin:
                    mid = fin[0].get("id")
    if not mid:
        return
    for path in ("/api/data/matchDetails", "/api/matchDetails"):
        body, st = jget(f"https://www.fotmob.com{path}", {"matchId": mid}, headers={"Referer": "https://www.fotmob.com/"})
        if not body:
            print(f"     {path} HTTP {st}")
            continue
        lu = ((body.get("content") or {}).get("lineup") or {})
        ht = lu.get("homeTeam") or {}
        verdict(f"풋몹 경기 상세 {path} (HTTP {st})", len(ht.get("starters") or []) >= 10, len(ht.get("subs") or []) > 0,
                None, None, None, f"라인업 키 {list(ht.keys())[:10]}")
        break


# ---------------------------------------------------------------- 네이버 eventMap
def naver_eventmap():
    head("6) 네이버 유럽 경기 eventMap (교체·골 정보가 들어가는지)")
    api = "https://api-gw.sports.naver.com"
    h = {"Referer": "https://m.sports.naver.com/", "Origin": "https://m.sports.naver.com"}
    body, st = jget(f"{api}/schedule/games", {"fields": "basic", "upperCategoryId": "wfootball", "size": 1000,
                                              "fromDate": (TODAY - timedelta(days=10)).isoformat(), "toDate": TODAY.isoformat()}, h)
    gs = [g for g in ((body or {}).get("result") or {}).get("games") or [] if g.get("categoryId") in ("epl", "primera", "bundesliga", "seria")
          and g.get("statusCode") == "RESULT"][:6]
    shown = 0
    for g in gs:
        b, _ = jget(f"{api}/schedule/games/{g['gameId']}/lineup", headers=h)
        home = ((((b or {}).get("result") or {}).get("lineUpData") or {}).get("lineup") or {}).get("home") or {}
        rows = (home.get("players") or {}).get("lineup") or []
        flat = [p for row in rows for p in row] if isinstance(rows, list) else []
        subs_flag = [p for p in flat if str(p.get("substitute")) not in ("0", "None", "")]
        with_ev = [p for p in flat if p.get("eventMap")]
        other_keys = [k for k in (home.get("players") or {}).keys() if k != "lineup"]
        print(f"  {g['categoryId']} {g.get('homeTeamName')}-{g.get('awayTeamName')}: 선발 {len(flat)} · substitute≠0 {len(subs_flag)} · "
              f"eventMap 있음 {len(with_ev)} · players 다른 키 {other_keys}")
        for p in with_ev[:2]:
            if shown < 4:
                print(f"     예: {p.get('name')} eventMap={json.dumps(p.get('eventMap'), ensure_ascii=False)[:250]}")
                shown += 1


def safe(fn):
    try:
        fn()
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")


def main():
    print(f"(기준일: 오늘 {TODAY} · 클럽 경기 확인일 {CLUB_DAY})")
    for fn in (espn, uefa, fifa, sofascore, fotmob, naver_eventmap):
        safe(fn)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
