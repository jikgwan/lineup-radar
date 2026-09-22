#!/usr/bin/env python3
"""6차 소스 점검 (읽기만 함): J리그를 어디서 가져오는 게 가장 좋은지 + K리그2 재확인.

비교 항목(끝난 경기 하나 기준): 선발 · 벤치 · 교체 시각 · 도움 · 출전시간 · 평점 · 이름 언어
후보: 네이버(jleague) · ESPN(jpn.1, site.web + 옵션) · 풋몹(J1)
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
NAVER = "https://api-gw.sports.naver.com"
NH = {"Referer": "https://m.sports.naver.com/", "Origin": "https://m.sports.naver.com"}
TODAY = date.today()


def jget(url, params=None, headers=None):
    try:
        r = requests.get(url, params=params, headers=dict(BASE_H, **(headers or {})), timeout=20)
    except Exception as exc:
        return None, f"실패 {type(exc).__name__}"
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


def row(label, **k):
    def m(x):
        return "?" if x is None else ("O" if x else "X")
    order = ["선발", "벤치", "교체시각", "도움", "출전시간", "평점"]
    print(f"  ▶ {label}")
    print("     " + " · ".join(f"{name} {m(k.get(name))}" for name in order) + (f"   {k.get('note', '')}" if k.get("note") else ""))


# ---------------------------------------------------------------- 네이버
def naver_finished(cat, days=21):
    body, st = jget(f"{NAVER}/schedule/games", {"fields": "basic", "upperCategoryId": "kfootball", "size": 1000,
                                                "fromDate": (TODAY - timedelta(days=days)).isoformat(), "toDate": TODAY.isoformat()}, NH)
    gs = [g for g in ((body or {}).get("result") or {}).get("games") or [] if g.get("categoryId") == cat and g.get("statusCode") == "RESULT"]
    return gs, st


def naver_check(cat, label):
    head(f"네이버 {label} ({cat})")
    gs, st = naver_finished(cat)
    print(f"  목록 HTTP {st} · 최근 3주 끝난 경기 {len(gs)}개")
    for g in list(reversed(gs))[:2]:
        gid = g["gameId"]
        lu, _ = jget(f"{NAVER}/schedule/games/{gid}/lineup", headers=NH)
        d = (((lu or {}).get("result") or {}).get("lineUpData")) or {}
        home = (d.get("lineup") or {}).get("home") or {}
        players = home.get("players")
        if isinstance(players, dict):
            players = players.get("lineup")
        flat = [p for r in (players or []) for p in (r if isinstance(r, list) else [r])]
        sub = (d.get("substitution") or {}).get("home") or []
        chg = (d.get("changedPlayer") or {}).get("home") or []
        rec, _ = jget(f"{NAVER}/schedule/games/{gid}/record", headers=NH)
        ps = ((((rec or {}).get("result") or {}).get("recordData")) or {}).get("homePlayerStats") or []
        keys = set(ps[0].keys()) if ps and isinstance(ps[0], dict) else set()
        row(f"{g.get('homeTeamName')} {g.get('homeTeamScore')}:{g.get('awayTeamScore')} {g.get('awayTeamName')} ({g.get('gameDate')})",
            선발=len(flat) >= 10, 벤치=len(sub) > 0, 교체시각=bool(chg and chg[0].get("time")),
            도움="assists" in keys, 출전시간="workTime" in keys, 평점="playerPoint" in keys,
            note=f"선발 {len(flat)} · 벤치 {len(sub)} · 교체 {len(chg)} · 기록 {len(ps)}명 · 이름 예: {flat[0].get('name') if flat else '-'}")


# ---------------------------------------------------------------- ESPN
def espn_check():
    head("ESPN J리그 (jpn.1, site.web + 옵션)")
    base = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/jpn.1"
    opt = {"region": "us", "lang": "en", "contentorigin": "espn"}
    ev = None
    for back in range(0, 10):
        d = (TODAY - timedelta(days=back)).strftime("%Y%m%d")
        body, st = jget(f"{base}/scoreboard", dict(opt, dates=d))
        evs = (body or {}).get("events") or []
        done = [e for e in evs if ((e.get("competitions") or [{}])[0].get("status") or {}).get("type", {}).get("completed")]
        if back < 3 or done:
            print(f"  {d} 목록 HTTP {st} · 경기 {len(evs)} (끝남 {len(done)})")
        if done:
            ev = done[0]
            break
    if not ev:
        print("  - 최근 10일 끝난 경기를 못 찾음")
        return
    body, st = jget(f"{base}/summary", dict(opt, event=ev["id"]))
    if not body:
        row(f"summary HTTP {st}")
        return
    ros = body.get("rosters") or []
    r0 = (ros[0].get("roster") or []) if ros else []
    stats = {s.get("name") for p in r0 for s in (p.get("stats") or []) if isinstance(s, dict)}
    subs = [k for k in (body.get("keyEvents") or []) if "ubstitution" in str((k.get("type") or {}).get("text"))]
    row(f"ESPN {ev.get('name', '')[:50]}", 선발=sum(1 for p in r0 if p.get("starter")) >= 10,
        벤치=any(not p.get("starter") for p in r0), 교체시각=bool(subs), 도움="goalAssists" in stats, 출전시간=None, 평점=False,
        note=f"교체 이벤트 {len(subs)} · 영어 이름 예: {((r0[0].get('athlete') or {}).get('displayName')) if r0 else '-'}")


# ---------------------------------------------------------------- 풋몹
def fotmob_check():
    head("풋몹 J1리그")
    mid = None
    for back in range(0, 10):
        d = (TODAY - timedelta(days=back)).strftime("%Y%m%d")
        body, st = jget("https://www.fotmob.com/api/data/matches", {"date": d}, {"Referer": "https://www.fotmob.com/"})
        for lg in (body or {}).get("leagues") or []:
            if lg.get("primaryId") == 223 or "J. League" in str(lg.get("name")) or "J1" in str(lg.get("name")):
                fin = [m for m in (lg.get("matches") or []) if (m.get("status") or {}).get("finished")]
                if fin and not mid:
                    mid = fin[0].get("id")
                    print(f"  {d} {lg.get('name')} 끝난 경기 {len(fin)}개 (HTTP {st})")
        if mid:
            break
    if not mid:
        print("  - 최근 10일 J1 끝난 경기를 못 찾음")
        return
    body, st = jget("https://www.fotmob.com/api/data/matchDetails", {"matchId": mid}, {"Referer": "https://www.fotmob.com/"})
    if not body:
        row(f"상세 HTTP {st}")
        return
    lu = ((body.get("content") or {}).get("lineup") or {})
    ht = lu.get("homeTeam") or {}
    starters = ht.get("starters") or []
    subs = ht.get("subs") or []
    txt = json.dumps(body)
    p0 = starters[0] if starters else {}
    row(f"풋몹 {ht.get('name')} (경기 {mid})", 선발=len(starters) >= 10, 벤치=len(subs) > 0,
        교체시각=("substitution" in txt.lower() or "subbedOut" in txt), 도움=("assist" in txt.lower()),
        출전시간=("minutesPlayed" in txt or "minutes_played" in txt), 평점=bool((p0.get("performance") or {}).get("rating") or p0.get("rating")),
        note=f"선수 항목 {list(p0.keys())[:10]} · 결장 {len(ht.get('unavailable') or [])}명")


def safe(fn, *a):
    try:
        fn(*a)
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")


def main():
    print(f"(기준일 {TODAY}) 표시: O 있음 · X 없음 · ? 확인 못 함")
    safe(naver_check, "jleague", "J1리그")
    safe(naver_check, "kleague2", "K리그2")
    safe(espn_check)
    safe(fotmob_check)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
