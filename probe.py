#!/usr/bin/env python3
"""2차 소스 점검.

1) ESPN 403이 '깃허브 서버 자체 차단'인지 '프로그램 요청이라 차단'인지: 요청 모양을 바꿔 시도
2) 네이버 스포츠: 어떤 대회가 있는지(해외축구·K리그·A매치·아시안게임), 경기 상세·라인업·선수 기록이
   어느 주소에서 어떤 모양으로 오는지 실제 경기로 찍어본다
3) 대표팀 Elo 확인

로그를 그대로 복사해서 보내주면 된다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
TIMEOUT = 20
CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def get(url, params=None, headers=None):
    try:
        return requests.get(url, params=params, headers=headers or {}, timeout=TIMEOUT)
    except Exception as exc:
        print(f"      ! 접속 실패: {type(exc).__name__} {str(exc)[:120]}")
        return None


def shape(obj, depth=0, max_depth=3, width=12):
    """JSON 구조를 짧게 요약 (키 이름과 자료형)."""
    pad = "        " + "  " * depth
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        keys = list(obj.keys())
        print(f"{pad}{{{', '.join(keys[:width])}{' …' if len(keys) > width else ''}}}")
        for k in keys[:width]:
            v = obj[k]
            if isinstance(v, (dict, list)) and v:
                print(f"{pad}  [{k}]")
                shape(v, depth + 1, max_depth, width)
    elif isinstance(obj, list):
        print(f"{pad}(목록 {len(obj)}개)")
        if obj:
            shape(obj[0], depth + 1, max_depth, width)


# ------------------------------------------------------------------ 1) ESPN
def espn_check():
    print("=" * 74)
    print("1) ESPN — 요청 모양을 바꾸면 열리는지")
    print("=" * 74)
    variants = {
        "기본(프로그램)": {"User-Agent": "lineup-radar/1.0"},
        "크롬 흉내": {"User-Agent": CHROME, "Accept": "application/json, text/plain, */*"},
        "크롬+언어+출처": {"User-Agent": CHROME, "Accept": "application/json, text/plain, */*",
                          "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.espn.com/",
                          "Origin": "https://www.espn.com"},
    }
    urls = [
        "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
        "https://site.web.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    ]
    for label, h in variants.items():
        codes = []
        for u in urls:
            r = get(u, headers=h)
            codes.append(str(r.status_code) if r is not None else "실패")
        print(f"  {label:14} → site.api 축구 {codes[0]} / site.web 축구 {codes[1]} / site.api 야구 {codes[2]}")


# ------------------------------------------------------------------ 2) 네이버
NAVER = "https://api-gw.sports.naver.com"
NH = {"User-Agent": CHROME, "Referer": "https://m.sports.naver.com/", "Origin": "https://m.sports.naver.com",
      "Accept": "application/json, text/plain, */*"}


def naver_games(upper, frm, to):
    r = get(f"{NAVER}/schedule/games", params={
        "fields": "basic,superCategoryId,categoryName,stadium,statusNum,title,roundCode",
        "upperCategoryId": upper, "fromDate": frm, "toDate": to, "size": 500}, headers=NH)
    if r is None:
        return None, "접속 실패"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return (r.json().get("result") or {}).get("games") or [], "OK"
    except ValueError:
        return None, "JSON 아님"


def naver_check():
    print()
    print("=" * 74)
    print("2) 네이버 스포츠 — 대회 목록 (지난 3일 ~ 앞으로 10일)")
    print("=" * 74)
    today = datetime.now(KST)
    frm = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    to = (today + timedelta(days=10)).strftime("%Y-%m-%d")
    samples = {}
    for upper in ["wfootball", "kfootball", "kbaseball", "wbaseball", "volleyball", "basketball",
                  "asiangames", "general", "etc"]:
        games, st = naver_games(upper, frm, to)
        if games is None:
            print(f"  X  {upper:11} {st}")
            continue
        cats = {}
        for g in games:
            c = g.get("categoryId") or "?"
            cats.setdefault(c, [0, g.get("categoryName") or ""])
            cats[c][0] += 1
        summary = ", ".join(f"{c}({n[1]}){n[0]}" for c, n in sorted(cats.items(), key=lambda x: -x[1][0])[:14])
        print(f"  O  {upper:11} 경기 {len(games):3}개 · {summary[:300]}")
        for g in games:
            key = (upper, g.get("statusCode") or g.get("statusInfo") or "")
            if key not in samples:
                samples[key] = g
    if samples:
        first = next(iter(samples.values()))
        print("\n     경기 목록 한 줄의 모양:")
        print("       " + json.dumps(first, ensure_ascii=False)[:700])
    return samples


def naver_detail_check(samples):
    print()
    print("=" * 74)
    print("3) 네이버 경기 상세 — 라인업·선수 기록이 어디서 오는지")
    print("=" * 74)
    picks = [(u, s, g) for (u, s), g in samples.items() if u in ("wfootball", "kfootball") and g.get("gameId")]
    if not picks:   # 축구 경기가 없으면 다른 종목으로 주소 구조만 확인
        picks = [(u, s, g) for (u, s), g in samples.items() if g.get("gameId")][:2]
    seen = set()
    for upper, status, g in picks[:4]:
        gid = g["gameId"]
        if gid in seen:
            continue
        seen.add(gid)
        print(f"\n  ▶ {upper} {g.get('categoryId')} {g.get('homeTeamName')} vs {g.get('awayTeamName')} "
              f"· 상태 {status} · {g.get('gameDateTime')} · gameId={gid}")
        for path in ["", "/preview", "/lineup", "/record", "/relay", "/playerRecord", "/boxscore"]:
            r = get(f"{NAVER}/schedule/games/{gid}{path}", headers=NH)
            if r is None:
                continue
            ok = r.status_code == 200
            try:
                body = r.json() if ok else None
            except ValueError:
                body = None
            size = len(r.content)
            print(f"     {('O' if ok else 'X')}  /schedule/games/{{id}}{path:14} HTTP {r.status_code} · {size}바이트")
            if ok and isinstance(body, dict) and size > 200:
                shape(body.get("result", body), depth=0, max_depth=3)


def elo_check():
    print()
    print("=" * 74)
    print("4) 대표팀 Elo")
    print("=" * 74)
    r = get("https://www.eloratings.net/World.tsv")
    if r is not None:
        n = len([l for l in r.text.splitlines() if l.strip()])
        print(f"  {'O' if r.status_code == 200 and n else 'X'}  World.tsv HTTP {r.status_code} / {n}줄")


WEB = "https://site.web.api.espn.com/apis/site/v2/sports"


def espn_web_detail():
    """site.web 주소에서 라인업(summary)·팀 일정(schedule)·대회별 목록이 오는지."""
    print()
    print("=" * 74)
    print("1-2) ESPN site.web — 라인업·팀 일정·대회별 경기 수")
    print("=" * 74)
    today = datetime.now(KST)
    rng = f"{(today - timedelta(days=7)).strftime('%Y%m%d')}-{today.strftime('%Y%m%d')}"
    ev = None
    for slug in ("eng.1", "esp.1", "ita.1", "ger.1"):
        r = get(f"{WEB}/soccer/{slug}/scoreboard", params={"dates": rng}, headers={"User-Agent": CHROME})
        if r is None or r.status_code != 200:
            print(f"  X  {slug} 지난 7일 목록 HTTP {getattr(r, 'status_code', '실패')}")
            continue
        events = r.json().get("events") or []
        done = [e for e in events if ((e.get("competitions") or [{}])[0].get("status") or {}).get("type", {}).get("completed")]
        print(f"  O  {slug} 지난 7일 경기 {len(events)}개 (끝난 경기 {len(done)})")
        if done and not ev:
            ev = (slug, done[0])
    if ev:
        slug, e = ev
        comp = e["competitions"][0]
        teams = [c.get("team", {}) for c in comp.get("competitors", [])]
        r = get(f"{WEB}/soccer/{slug}/summary", params={"event": e["id"]}, headers={"User-Agent": CHROME})
        if r is not None and r.status_code == 200:
            body = r.json()
            ros = body.get("rosters") or []
            st = sum(1 for x in ros for p in (x.get("roster") or []) if p.get("starter"))
            ke = len(body.get("keyEvents") or [])
            print(f"  O  summary {e.get('name', '')[:40]} → 명단 {len(ros)}팀 · 선발 {st}명 · 주요 이벤트 {ke}개")
        else:
            print(f"  X  summary HTTP {getattr(r, 'status_code', '실패')}")
        if teams:
            tid = teams[0].get("id")
            r = get(f"{WEB}/soccer/{slug}/teams/{tid}/schedule", headers={"User-Agent": CHROME})
            n = len((r.json().get("events") or [])) if (r is not None and r.status_code == 200) else None
            print(f"  {'O' if n else 'X'}  팀 일정({teams[0].get('displayName')}) HTTP {getattr(r, 'status_code', '실패')} · 경기 {n}")
    nxt = f"{today.strftime('%Y%m%d')}-{(today + timedelta(days=10)).strftime('%Y%m%d')}"
    for slug in ("uefa.nations", "fifa.friendly", "kor.1", "fifa.worldq.afc", "afc.asian.cup"):
        r = get(f"{WEB}/soccer/{slug}/scoreboard", params={"dates": nxt}, headers={"User-Agent": CHROME})
        if r is None:
            continue
        n = len(r.json().get("events") or []) if r.status_code == 200 else None
        print(f"  {'O' if r.status_code == 200 else 'X'}  {slug:16} 앞으로 10일 HTTP {r.status_code} · 경기 {n}")


def naver_ag_search():
    """아시안게임 축구가 네이버 어디에 있는지."""
    print()
    print("=" * 74)
    print("2-2) 네이버 — 아시안게임 축구 찾기 (9/10 ~ 10/5)")
    print("=" * 74)
    for upper in ("kfootball", "wfootball"):
        r = get(f"{NAVER}/schedule/games", params={"fields": "basic", "upperCategoryId": upper,
                "fromDate": "2026-09-10", "toDate": "2026-10-05", "size": 1000}, headers=NH)
        if r is None or r.status_code != 200:
            print(f"  X  {upper} HTTP {getattr(r, 'status_code', '실패')}")
            continue
        games = (r.json().get("result") or {}).get("games") or []
        cats = sorted({(g.get("categoryId"), g.get("categoryName")) for g in games})
        print(f"  O  {upper} 대회: " + ", ".join(f"{c}({n})" for c, n in cats)[:400])
        for g in games:
            if "AG" in (g.get("categoryName") or "") or "아시안" in (g.get("categoryName") or "") or \
                    str(g.get("categoryId", "")).startswith("ag"):
                print(f"     → {g.get('categoryId')} {g.get('categoryName')} {g.get('gameDateTime')} "
                      f"{g.get('homeTeamName')} vs {g.get('awayTeamName')} gameId={g.get('gameId')}")
                break
    for cat in ("agfootball", "agwfootball", "agsoccer"):
        r = get(f"{NAVER}/schedule/games", params={"fields": "basic", "categoryId": cat,
                "fromDate": "2026-09-10", "toDate": "2026-10-05", "size": 200}, headers=NH)
        if r is None:
            continue
        n = len((r.json().get("result") or {}).get("games") or []) if r.status_code == 200 else None
        print(f"  {'O' if n else '-'}  categoryId={cat:12} HTTP {r.status_code} · 경기 {n}")


def safe(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:   # 한 칸이 실패해도 나머지 점검은 계속
        print(f"  ! {fn.__name__} 중 오류: {type(exc).__name__} {str(exc)[:150]}")
        return None


def main():
    safe(espn_check)
    safe(espn_web_detail)
    samples = safe(naver_check) or {}
    safe(naver_ag_search)
    safe(naver_detail_check, samples)
    safe(elo_check)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
