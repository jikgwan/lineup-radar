#!/usr/bin/env python3
"""18차 소스 점검 (읽기만 함): 대표팀 Elo — 어느 나라가 자료에서 빠지는지.

1) KBO: 끝난 경기·오늘 경기에서 라인업·타순·선발투수·선수 기록이 어디서 어떤 모양으로 오는지
2) 풋몹: 결장자(unavailable) 항목 모양, 라인업 발표 전에도 결장자 명단이 있는지, 팀 이름 표기
로그를 그대로 복사해서 보내주면 된다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import requests

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
H = {"User-Agent": CHROME, "Accept": "application/json, text/plain, */*"}
NAVER = "https://api-gw.sports.naver.com"
NH = {"Referer": "https://m.sports.naver.com/", "Origin": "https://m.sports.naver.com"}
FH = {"Referer": "https://www.fotmob.com/"}
TODAY = date.today()


def jget(url, params=None, headers=None):
    try:
        r = requests.get(url, params=params, headers=dict(H, **(headers or {})), timeout=20)
        return (r.json(), 200) if r.status_code == 200 else (None, r.status_code)
    except Exception as exc:
        return None, type(exc).__name__


def head(t):
    print()
    print("=" * 74)
    print(t)
    print("=" * 74)


def shape(obj, depth=0, max_depth=4, width=14):
    pad = "     " + "  " * depth
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        ks = list(obj.keys())
        print(f"{pad}{{{', '.join(ks[:width])}{' …' if len(ks) > width else ''}}}")
        for k in ks[:width]:
            v = obj[k]
            if isinstance(v, (dict, list)) and v:
                print(f"{pad}  [{k}]")
                shape(v, depth + 1, max_depth, width)
    elif isinstance(obj, list):
        print(f"{pad}(목록 {len(obj)}개) 첫 항목: {json.dumps(obj[0], ensure_ascii=False)[:220]}")
        if obj and isinstance(obj[0], (dict, list)):
            shape(obj[0], depth + 1, max_depth, width)


# ---------------------------------------------------------------- KBO
def kbo():
    head("1) KBO (네이버) — 끝난 경기와 오늘 경기")
    body, st = jget(f"{NAVER}/schedule/games", {"fields": "basic,superCategoryId,categoryName", "upperCategoryId": "kbaseball",
                                                "fromDate": (TODAY - timedelta(days=3)).isoformat(), "toDate": (TODAY + timedelta(days=1)).isoformat(),
                                                "size": 500}, NH)
    games = [g for g in ((body or {}).get("result") or {}).get("games") or [] if g.get("categoryId") == "kbo"]
    print(f"  목록 HTTP {st} · KBO {len(games)}경기")
    if games:
        print(f"  목록 한 줄: {json.dumps(games[0], ensure_ascii=False)[:500]}")
    done = next((g for g in reversed(games) if g.get("statusCode") == "RESULT"), None)
    soon = next((g for g in games if g.get("statusCode") in ("BEFORE", "READY", "STARTED")), None)
    for label, g in (("끝난 경기", done), ("오늘·예정 경기", soon)):
        if not g:
            print(f"\n  - {label} 없음")
            continue
        gid = g["gameId"]
        print(f"\n  ▶ {label}: {g.get('awayTeamName')} @ {g.get('homeTeamName')} ({g.get('gameDateTime')}) 상태 {g.get('statusCode')} gameId={gid}")
        for path in ("", "/preview", "/lineup", "/record", "/relay"):
            b, s2 = jget(f"{NAVER}/schedule/games/{gid}{path}", headers=NH)
            res = (b or {}).get("result") or {}
            size = len(json.dumps(res)) if res else 0
            print(f"     {'O' if res else 'X'}  /schedule/games/{{id}}{path:9} HTTP {s2} · {size}자")
            if res and size > 200 and path:
                shape(res, max_depth=3)


# ---------------------------------------------------------------- 풋몹
ELO_BASE = "https://www.eloratings.net"
CHECK = ["South Korea", "Korea Republic", "Ecuador", "Japan", "Uruguay", "China PR", "Maldives",
         "Palestine", "New Zealand", "Papua New Guinea", "Solomon Islands", "Vanuatu", "New Caledonia",
         "Namibia", "Congo", "Congo DR"]


def elo_check():
    head("대표팀 Elo — 자료가 받아지는지, 어느 나라가 빠지는지")
    import parse as P
    for path, key in ((f"{ELO_BASE}/World.tsv", "레이팅"), (f"{ELO_BASE}/en.teams.tsv", "이름표")):
        try:
            r = requests.get(path, headers=H, timeout=20)
            print(f"  {key} HTTP {r.status_code} · {len(r.text)}자 · 첫 줄: {r.text.splitlines()[0][:90] if r.text else ''}")
            if key == "레이팅":
                world = r.text
            else:
                namestxt = r.text
        except Exception as exc:
            print(f"  {key} 실패: {type(exc).__name__}")
            return
    ratings = P.parse_elo_world(world)
    names = P.parse_elo_names(namestxt)
    print(f"  읽은 레이팅 {len(ratings)}개 · 이름표 {len(names)}개")
    print("\n  [나라별 확인]")
    for n in CHECK:
        rating, code = P.elo_for(n, ratings, names)
        print(f"   {n:20} → 키 {P.nation_key(n):20} 코드 {code or '-':5} 점수 {rating or '없음'}")
    miss = [n for n in CHECK if P.elo_for(n, ratings, names)[0] is None]
    print(f"\n  못 찾은 나라 {len(miss)}개: {miss}")
    if names:
        sample = list(names.items())[:8]
        print(f"  이름표 예시: {sample}")


def safe(fn):
    try:
        fn()
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")


def main():
    safe(elo_check)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
