#!/usr/bin/env python3
"""4차 소스 점검 (읽기만 함).

1) 아시안게임: 'AG일반' 탭(upperCategoryId=general) 안의 종목 이름 찾기, 축구 경기·라인업 확인
2) 유럽 리그(EPL·라리가·분데스·챔스): 라인업 원본 모양, 선수 기록·출전시간 유무
3) A매치: 6~9월 대표팀 경기(월드컵·친선) 라인업·선수 기록 유무, 1000경기 제한을 피하는 기간
로그를 그대로 복사해서 보내주면 된다.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta

import requests

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
API = "https://api-gw.sports.naver.com"
H = {"User-Agent": CHROME, "Referer": "https://m.sports.naver.com/", "Origin": "https://m.sports.naver.com",
     "Accept": "application/json, text/plain, */*"}


def get(url, params=None, headers=None):
    try:
        return requests.get(url, params=params, headers=headers or H, timeout=20)
    except Exception as exc:
        print(f"      ! 접속 실패: {type(exc).__name__} {str(exc)[:100]}")
        return None


def jget(url, params=None):
    r = get(url, params)
    if r is None or r.status_code != 200:
        return None, (r.status_code if r is not None else "실패")
    try:
        return r.json(), 200
    except ValueError:
        return None, "JSON 아님"


def games(params):
    body, st = jget(f"{API}/schedule/games", dict({"fields": "basic,superCategoryId,categoryName", "size": 1000}, **params))
    if body is None:
        return None, st
    return (body.get("result") or {}).get("games") or [], st


def dump(obj, n=900):
    return json.dumps(obj, ensure_ascii=False)[:n]


def section(t):
    print()
    print("=" * 74)
    print(t)
    print("=" * 74)


def detail(g, raw=True):
    gid = g["gameId"]
    print(f"\n  ▶ {g.get('categoryId')} {g.get('homeTeamName')} {g.get('homeTeamScore')}:{g.get('awayTeamScore')} "
          f"{g.get('awayTeamName')} ({g.get('gameDate')}) gameId={gid}")
    body, st = jget(f"{API}/schedule/games/{gid}/lineup")
    lu = ((body or {}).get("result") or {}).get("lineUpData") or {}
    if not lu:
        print(f"     라인업: 없음 (HTTP {st})")
    else:
        home = (lu.get("lineup") or {}).get("home")
        print(f"     라인업 키: {list(lu.keys())}")
        print(f"     lineup.home 자료형: {type(home).__name__} · 키: {list(home.keys()) if isinstance(home, dict) else '-'}")
        if raw:
            print(f"     lineup.home 원본: {dump(home, 1200)}")
        for k in ("substitution", "changedPlayer"):
            v = (lu.get(k) or {}).get("home")
            print(f"     {k}.home: {type(v).__name__} {len(v) if hasattr(v, '__len__') else ''} · {dump(v, 300)}")
    body, st = jget(f"{API}/schedule/games/{gid}/record")
    rec = ((body or {}).get("result") or {}).get("recordData") or {}
    ps = rec.get("homePlayerStats") or []
    if ps:
        wt = [p.get("workTime") for p in ps if isinstance(p, dict)]
        print(f"     선수 기록: 홈 {len(ps)}명 · 출전시간 예 {wt[:6]} · 항목 {list(ps[0].keys()) if isinstance(ps[0], dict) else '-'}")
    else:
        print(f"     선수 기록: 없음 (HTTP {st}, 키 {list(rec.keys())})")
    body, st = jget(f"{API}/schedule/games/{gid}/relay")
    rows = ((((body or {}).get("result") or {}).get("textRelayData")) or {}).get("textRelays") or []
    print(f"     문자중계: {len(rows)}건")


# ---------------------------------------------------------------- 1) 아시안게임
def ag():
    section("1) 아시안게임 — 'AG일반'(general) 탭 안의 종목 이름")
    found = set()
    for url in ("https://m.sports.naver.com/general/index", "https://m.sports.naver.com/general/schedule/index"):
        r = get(url, headers={"User-Agent": CHROME})
        if r is None:
            continue
        t = r.text
        cats = set(re.findall(r'category[=:"\\s]+([a-zA-Z0-9_]{2,30})', t))
        found |= cats
        js = re.findall(r'src="([^"]+\.js)"', t)
        print(f"  {url} HTTP {r.status_code} · {len(t)}바이트 · 스크립트 {len(js)}개 · 종목 후보 {sorted(cats)[:30]}")
        for s in js[:6]:
            src = s if s.startswith("http") else "https://m.sports.naver.com" + s
            rj = get(src, headers={"User-Agent": CHROME})
            if rj is None or rj.status_code != 200:
                continue
            hits = set(re.findall(r'["\'](ag[a-z0-9_]{2,25})["\']', rj.text))
            if hits:
                print(f"     {src[-60:]} 안의 ag로 시작하는 이름: {sorted(hits)[:40]}")
                found |= hits
    cands = ["agfootball", "agsoccer", "agwfootball", "agfootballw", "agmfootball", "agsoccerw", "football", "soccer",
             "ag", "agetc", "aggeneral", "general", "asiangames", "agbasketball", "agvolleyball", "agbaseball"]
    cands = [c for c in sorted(found) if c.lower().startswith("ag")] + cands
    for extra in ({"superCategoryId": "football"}, {"categoryIds": "agfootball"}, {}):
        gs, st = games(dict({"upperCategoryId": "general", "fromDate": "2026-09-10", "toDate": "2026-10-05"}, **extra))
        cats = sorted({(g.get("categoryId"), g.get("categoryName")) for g in (gs or [])})
        print(f"  {'O' if gs else '-'}  general + {extra or '조건 없음'}: HTTP {st} · 경기 {len(gs) if gs is not None else None} · {cats[:10]}")
    tried = set()
    for c in cands:
        if c in tried:
            continue
        tried.add(c)
        gs, st = games({"upperCategoryId": "general", "categoryId": c, "fromDate": "2026-09-10", "toDate": "2026-10-05"})
        if gs is None:
            print(f"     general/{c:14} HTTP {st}")
            continue
        names = sorted({g.get('categoryName') for g in gs})
        print(f"  O  general/{c:14} 경기 {len(gs)} · {names[:5]}")
        foot = next((g for g in gs if "축구" in str(g.get("categoryName")) or "football" in str(g.get("superCategoryId"))), None)
        if foot:
            print(f"     축구 예시: {dump(foot, 500)}")
            done = next((g for g in gs if g.get("statusCode") == "RESULT" and ("축구" in str(g.get("categoryName")) or "football" in str(g.get("superCategoryId")))), None)
            if done:
                detail(done, raw=False)


# ---------------------------------------------------------------- 2) 유럽 리그
def europe():
    section("2) 유럽 리그 — 라인업 원본 모양·선수 기록")
    today = date.today()
    gs, st = games({"upperCategoryId": "wfootball", "fromDate": (today - timedelta(days=14)).isoformat(),
                    "toDate": today.isoformat()})
    if gs is None:
        print(f"  X  목록 HTTP {st}")
        return
    print(f"  (14일 구간: {len(gs)}경기 — 1000 미만이면 이 간격으로 끊어 받으면 됨)")
    for cat in ("epl", "primera", "bundesliga", "champs"):
        g = next((x for x in reversed(gs) if x.get("categoryId") == cat and x.get("statusCode") == "RESULT"), None)
        if g:
            detail(g, raw=(cat == "epl"))
        else:
            print(f"\n  - {cat}: 최근 14일 끝난 경기 없음")


# ---------------------------------------------------------------- 3) A매치
def amatch():
    section("3) A매치 — 6~9월 대표팀 경기")
    start = date(2026, 6, 1)
    allg = []
    while start < date.today():
        end = min(start + timedelta(days=13), date.today())
        for upper in ("kfootball", "wfootball"):
            gs, st = games({"upperCategoryId": upper, "fromDate": start.isoformat(), "toDate": end.isoformat()})
            if gs is None:
                print(f"  X  {upper} {start}~{end} HTTP {st}")
                continue
            if len(gs) >= 1000:
                print(f"  ! {upper} {start}~{end} 1000경기 꽉 참 (더 잘게 끊어야 함)")
            allg += [g for g in gs if g.get("categoryId") in ("amatch", "amatchfriendly", "worldcup", "fifaworldcup", "wc")
                     or "월드컵" in str(g.get("categoryName")) or "국가대표" in str(g.get("categoryName"))]
        start = end + timedelta(days=1)
    uniq = {}
    for g in allg:
        uniq[g.get("gameId")] = g
    allg = sorted(uniq.values(), key=lambda g: g.get("gameDate") or "")
    cats = {}
    for g in allg:
        cats[(g.get("categoryId"), g.get("categoryName"))] = cats.get((g.get("categoryId"), g.get("categoryName")), 0) + 1
    print("  대표팀 대회: " + ", ".join(f"{a}({b}) {n}" for (a, b), n in cats.items()))
    kor = [g for g in allg if "대한민국" in (str(g.get("homeTeamName")) + str(g.get("awayTeamName")))]
    print(f"  대한민국 경기 {len(kor)}개: " + ", ".join(f"{g.get('gameDate')} {g.get('homeTeamName')}-{g.get('awayTeamName')}({g.get('categoryId')})" for g in kor[-8:]))
    pick = next((g for g in reversed(kor) if g.get("statusCode") == "RESULT"), None) or \
        next((g for g in reversed(allg) if g.get("statusCode") == "RESULT"), None)
    if pick:
        detail(pick, raw=False)
    other = next((g for g in reversed(allg) if g.get("statusCode") == "RESULT" and g is not pick and g.get("categoryId") != (pick or {}).get("categoryId")), None)
    if other:
        detail(other, raw=False)


def safe(fn):
    try:
        fn()
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")


def main():
    safe(ag)
    safe(europe)
    safe(amatch)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
