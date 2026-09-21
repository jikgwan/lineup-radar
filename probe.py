#!/usr/bin/env python3
"""3차 소스 점검: 네이버만으로 우리 기능에 필요한 정보가 전부 오는지.

A) 지난 경기 기록을 얼마나 멀리까지 받을 수 있는지 (시즌 기록·최근 6경기·체급 계산용)
B) 끝난 경기에서 라인업·교체·선수 기록 항목 전체 (리그별: EPL·라리가·챔스·K리그·A매치)
C) 경기 전 경기에서 라인업이 언제부터 나오는지 (예정 경기 확인)
D) 'AG일반' 탭 찾기 (아시안게임 축구)
로그를 그대로 복사해서 보내주면 된다.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
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


def one_line(obj, n=600):
    return json.dumps(obj, ensure_ascii=False)[:n]


# ---------------------------------------------------------------- A
def history_depth():
    print("=" * 74)
    print("A) 지난 경기를 얼마나 멀리까지 받을 수 있나 (EPL·K리그, 날짜 구간별)")
    print("=" * 74)
    today = datetime.now(KST).date()
    for upper, cat in (("wfootball", "epl"), ("kfootball", "kleague")):
        for days in (30, 90, 200):
            frm = (today - timedelta(days=days)).isoformat()
            gs, st = games({"upperCategoryId": upper, "fromDate": frm, "toDate": today.isoformat()})
            if gs is None:
                print(f"  X  {upper}/{cat} 지난 {days}일: HTTP {st}")
                continue
            mine = [g for g in gs if g.get("categoryId") == cat]
            done = [g for g in mine if g.get("statusCode") == "RESULT"]
            first = min((g.get("gameDate") for g in mine), default="-")
            print(f"  O  {upper}/{cat} 지난 {days:3}일: 전체 {len(gs)}경기 중 {cat} {len(mine)}경기(끝남 {len(done)}) · 가장 이른 날짜 {first}")
    # 팀으로 거르는 옵션이 있는지
    gs, _ = games({"upperCategoryId": "wfootball", "fromDate": (today - timedelta(days=60)).isoformat(),
                   "toDate": today.isoformat()})
    sample = next((g for g in (gs or []) if g.get("categoryId") == "epl"), None)
    if sample:
        code = sample.get("homeTeamCode")
        for key in ("teamCode", "teamId", "homeTeamCode"):
            g2, st = games({"upperCategoryId": "wfootball", "fromDate": (today - timedelta(days=60)).isoformat(),
                            "toDate": today.isoformat(), key: code})
            n = len(g2) if g2 is not None else None
            print(f"     팀 거르기 {key}={code}({sample.get('homeTeamName')}): HTTP {st} · {n}경기 (전체 {len(gs)})")


# ---------------------------------------------------------------- B
def pick_games():
    today = datetime.now(KST).date()
    want = {"epl": None, "primera": None, "champs": None, "kleague": None, "amatchfriendly": None, "amatch": None, "unl": None}
    upcoming = {}
    for upper in ("wfootball", "kfootball"):
        gs, _ = games({"upperCategoryId": upper, "fromDate": (today - timedelta(days=40)).isoformat(),
                       "toDate": (today + timedelta(days=5)).isoformat()})
        for g in reversed(gs or []):
            c = g.get("categoryId")
            if c in want and want[c] is None and g.get("statusCode") == "RESULT":
                want[c] = g
            if c in want and g.get("statusCode") == "BEFORE" and c not in upcoming:
                upcoming[c] = g
    return want, upcoming


def keys_of(rows):
    ks = []
    for r in rows or []:
        if isinstance(r, dict):
            for k in r:
                if k not in ks:
                    ks.append(k)
    return ks


def detail(g):
    gid = g["gameId"]
    print(f"\n  ▶ {g.get('categoryId')} {g.get('homeTeamName')} {g.get('homeTeamScore')}:{g.get('awayTeamScore')} "
          f"{g.get('awayTeamName')} ({g.get('gameDate')}) gameId={gid}")
    body, st = jget(f"{API}/schedule/games/{gid}/lineup")
    lu = ((body or {}).get("result") or {}).get("lineUpData") or {}
    if not lu:
        print(f"     라인업: 없음 (HTTP {st})")
    else:
        home = (lu.get("lineup") or {}).get("home") or {}
        players = home.get("players") or []
        print(f"     라인업: 홈 선발 {len(players)}명 · 포메이션 {home.get('formation')} · 줄(row) {str(home.get('row'))[:80]}")
        print(f"       선수 항목: {keys_of(players)}")
        if players:
            print(f"       선수 예시: {one_line(players[0], 300)}")
        sub = (lu.get("substitution") or {}).get("home") or []
        chg = (lu.get("changedPlayer") or {}).get("home") or []
        print(f"     교체명단(substitution): {len(sub)}명 · 항목 {keys_of(sub)}")
        if sub:
            print(f"       예시: {one_line(sub[0], 250)}")
        print(f"     교체기록(changedPlayer): {len(chg)}건 · 항목 {keys_of(chg)}")
        if chg:
            print(f"       예시: {one_line(chg[0], 250)}")
    body, st = jget(f"{API}/schedule/games/{gid}/record")
    rec = ((body or {}).get("result") or {}).get("recordData") or {}
    ps = rec.get("homePlayerStats") or []
    if not ps:
        print(f"     선수 기록(record): 없음 (HTTP {st})")
    else:
        print(f"     선수 기록: 홈 {len(ps)}명 · 항목 {keys_of(ps)}")
        print(f"       예시: {one_line(ps[0], 350)}")
        tl = rec.get("timeline") or []
        types = sorted({t.get("eventType") for t in tl if isinstance(t, dict)})
        print(f"     타임라인: {len(tl)}건 · 종류 {types}")
    body, st = jget(f"{API}/schedule/games/{gid}/relay")
    rel = ((body or {}).get("result") or {}).get("textRelayData") or {}
    rows = rel.get("textRelays") or []
    types = sorted({t.get("eventType") for t in rows if isinstance(t, dict)})
    print(f"     문자중계(relay): {len(rows)}건 · 종류 {types}")
    body, st = jget(f"{API}/schedule/games/{gid}/preview")
    pv = ((body or {}).get("result") or {}).get("previewData") or {}
    print(f"     프리뷰(preview): {'있음 ' + str(list(pv.keys())) if pv else '없음'}")


def finished_check():
    print()
    print("=" * 74)
    print("B) 끝난 경기에서 라인업·교체·선수 기록 (리그별)")
    print("=" * 74)
    want, upcoming = pick_games()
    for c, g in want.items():
        if g is None:
            print(f"\n  - {c}: 최근 40일 안에 끝난 경기 없음")
        else:
            try:
                detail(g)
            except Exception as exc:
                print(f"     ! 오류 {type(exc).__name__} {exc}")
    return upcoming


# ---------------------------------------------------------------- C
def upcoming_check(upcoming):
    print()
    print("=" * 74)
    print("C) 경기 전: 라인업이 벌써 있는지 (가장 가까운 예정 경기)")
    print("=" * 74)
    for c, g in upcoming.items():
        body, _ = jget(f"{API}/schedule/games/{g['gameId']}/lineup")
        lu = ((body or {}).get("result") or {}).get("lineUpData") or {}
        n = len(((lu.get("lineup") or {}).get("home") or {}).get("players") or [])
        print(f"  {c:15} {g.get('gameDateTime')} {g.get('homeTeamName')} vs {g.get('awayTeamName')} → 선발 {n}명")


# ---------------------------------------------------------------- D
def ag_search():
    print()
    print("=" * 74)
    print("D) 'AG일반' 탭 찾기 (네이버 스포츠 메뉴에서)")
    print("=" * 74)
    found = set()
    for url in ("https://m.sports.naver.com/", "https://sports.naver.com/", "https://m.sports.naver.com/ag/index",
                "https://m.sports.naver.com/asiangames/index"):
        r = get(url, headers={"User-Agent": CHROME})
        if r is None:
            continue
        text = r.text
        ids = set(re.findall(r'sports\.naver\.com/([a-zA-Z0-9]+)/', text)) | set(re.findall(r'"upperCategoryId"\s*:\s*"([a-zA-Z0-9]+)"', text))
        ag_ctx = re.findall(r'.{0,80}AG\s?일반.{0,80}', text)
        print(f"  {url} HTTP {r.status_code} · 메뉴 이름 {len(ids)}개")
        if ag_ctx:
            for ctx in ag_ctx[:3]:
                print(f"     'AG일반' 주변: {ctx.strip()[:200]}")
        found |= ids
    cands = sorted(i for i in found if len(i) < 25)
    print(f"  발견한 메뉴 이름: {cands[:80]}")
    guesses = ["ag", "agetc", "aggeneral", "aggen", "asiangame", "asiangames", "ag2026", "general", "etc",
               "olympic", "multisports"]
    tried = []
    for up in [c for c in cands if c.lower().startswith(("ag", "asia", "gen", "etc", "multi"))] + guesses:
        if up in tried:
            continue
        tried.append(up)
        gs, st = games({"upperCategoryId": up, "fromDate": "2026-09-10", "toDate": "2026-10-05"})
        if gs is None:
            print(f"     {up:14} HTTP {st}")
            continue
        cats = {}
        for g in gs:
            cats.setdefault((g.get("categoryId"), g.get("categoryName")), 0)
            cats[(g.get("categoryId"), g.get("categoryName"))] += 1
        print(f"  O  {up:14} 경기 {len(gs)} · " + ", ".join(f"{a}({b}){n}" for (a, b), n in cats.items())[:350])
        foot = next((g for g in gs if "축구" in (g.get("categoryName") or "") or "football" in str(g.get("categoryId"))), None)
        if foot:
            print(f"     축구 예시: {one_line(foot, 400)}")
            try:
                detail(foot)
            except Exception as exc:
                print(f"     ! 오류 {exc}")


def safe(fn, *a):
    try:
        return fn(*a)
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")
        return None


def main():
    safe(history_depth)
    upcoming = safe(finished_check) or {}
    safe(upcoming_check, upcoming)
    safe(ag_search)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
