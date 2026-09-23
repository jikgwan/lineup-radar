#!/usr/bin/env python3
"""14차 소스 점검 (읽기만 함): 일왕배 라인업을 풋몹 말고 어디서 더 빨리 받을 수 있나 (네이버·소파스코어·JFA 공식).

1) KBO: 끝난 경기·오늘 경기에서 라인업·타순·선발투수·선수 기록이 어디서 어떤 모양으로 오는지
2) 풋몹: 결장자(unavailable) 항목 모양, 라인업 발표 전에도 결장자 명단이 있는지, 팀 이름 표기
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
ESPN_WEB = "https://site.web.api.espn.com/apis/site/v2/sports/soccer"
ESPN_PARAMS = {"region": "us", "lang": "en", "contentorigin": "espn"}


def emperor_espn():
    head("1) ESPN — 일본 대회 코드 찾기 (일왕배가 있는지)")
    body, st = jget("https://sports.core.api.espn.com/v2/sports/soccer/leagues", {"limit": 1000})
    refs = [(i or {}).get("$ref", "") for i in ((body or {}).get("items") or [])]
    slugs = sorted({r.split("/leagues/")[1].split("?")[0] for r in refs if "/leagues/" in r})
    jp = [x for x in slugs if "jpn" in x or "japan" in x]
    print(f"  리그 목록 HTTP {st} · 전체 {len(slugs)}개 · 일본 관련: {jp}")
    cands = jp + ["jpn.emperors_cup", "jpn.emperor_cup", "jpn.cup", "jpn.emperors.cup", "jpn.league_cup"]
    for slug in dict.fromkeys(cands):
        for back in (0, 1, -1):
            d = (TODAY + timedelta(days=back)).strftime("%Y%m%d")
            b2, s2 = jget(f"{ESPN_WEB}/{slug}/scoreboard", dict(ESPN_PARAMS, dates=d))
            evs = (b2 or {}).get("events") or []
            if s2 == 200 and evs:
                lg = ((b2.get("leagues") or [{}])[0]).get("name")
                print(f"  O {slug} {d}: {lg} · {len(evs)}경기 · 예: {evs[0].get('name')}")
                ev = evs[0]
                b3, s3 = jget(f"{ESPN_WEB}/{slug}/summary", dict(ESPN_PARAMS, event=ev.get("id")))
                ro = (b3 or {}).get("rosters") or []
                print(f"     summary HTTP {s3} · 라인업 팀 {len(ro)} · 선수 {[len(r.get('roster') or []) for r in ro]}")
                break
        else:
            print(f"  X {slug}: 경기 없음 또는 없는 코드")


def naver_cup():
    head("1) 네이버 — 오늘·내일 축구 대회 목록에 일왕배가 있나")
    for upper in ("kfootball", "wfootball"):
        cats = {}
        for back in (0, 1):
            d = (TODAY + timedelta(days=back)).isoformat()
            body, st = jget(f"{NAVER}/schedule/games", {"fields": "basic,categoryName", "upperCategoryId": upper,
                                                        "fromDate": d, "toDate": d, "size": 500}, NH)
            for g in ((body or {}).get("result") or {}).get("games") or []:
                cats.setdefault((g.get("categoryId"), g.get("categoryName")), 0)
                cats[(g.get("categoryId"), g.get("categoryName"))] += 1
        print(f"  {upper} HTTP {st} · 대회: {sorted(cats.items(), key=lambda x: -x[1])[:14]}")


def sofascore_cup():
    head("2) 소파스코어 — 접속 가능한지 · 일왕배 라인업이 있는지")
    d = TODAY.isoformat()
    body, st = jget(f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{d}",
                    headers={"Referer": "https://www.sofascore.com/"})
    evs = (body or {}).get("events") or []
    print(f"  일정 HTTP {st} · 경기 {len(evs)}개")
    cup = [e for e in evs if "emperor" in str(((e.get("tournament") or {}).get("name") or "")).lower()
           or "天皇" in str(((e.get("tournament") or {}).get("name") or ""))]
    print(f"  일왕배로 보이는 경기 {len(cup)}개")
    for e in cup[:3]:
        eid = e.get("id")
        print(f"   - {((e.get('homeTeam') or {}).get('name'))} vs {((e.get('awayTeam') or {}).get('name'))} (id {eid}) · 상태 {((e.get('status') or {}).get('description'))}")
        b2, s2 = jget(f"https://api.sofascore.com/api/v1/event/{eid}/lineups", headers={"Referer": "https://www.sofascore.com/"})
        if s2 == 200 and b2:
            hp = ((b2.get("home") or {}).get("players") or [])
            print(f"     라인업 HTTP {s2} · 확정 {b2.get('confirmed')} · 홈 선수 {len(hp)} · 예: {json.dumps(hp[0], ensure_ascii=False)[:260] if hp else '-'}")
        else:
            print(f"     라인업 HTTP {s2}")


def jfa_cup():
    head("3) JFA 공식 — 경기 페이지에 선발 명단이 있나")
    url = "https://www.jfa.jp/match/emperorscup_2026/schedule_result/"
    try:
        r = requests.get(url, headers=H, timeout=20)
        html = r.text if r.status_code == 200 else ""
        print(f"  일정 페이지 HTTP {r.status_code} · {len(html)}자")
    except Exception as exc:
        print(f"  일정 페이지 실패: {type(exc).__name__}")
        return
    links = sorted(set(re.findall(r'href="(/match/emperorscup_2026/[^"]*?(?:match|result)[^"]*?)"', html)))
    print(f"  경기 링크 {len(links)}개 · 예: {links[:5]}")
    for link in links[:3]:
        try:
            r2 = requests.get("https://www.jfa.jp" + link, headers=H, timeout=20)
            t = r2.text if r2.status_code == 200 else ""
        except Exception as exc:
            print(f"   - {link}: 실패 {type(exc).__name__}")
            continue
        has = [k for k in ("スターティングメンバー", "先発", "出場選手", "メンバー", "控え") if k in t]
        print(f"   - {link} HTTP {r2.status_code} · {len(t)}자 · 라인업 관련 표시: {has}")
        if "スターティングメンバー" in t:
            i = t.index("スターティングメンバー")
            print("     주변:", re.sub(r"<[^>]+>", " ", t[i:i + 400]).split())


def safe(fn):
    try:
        fn()
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")


def main():
    safe(naver_cup)
    safe(sofascore_cup)
    safe(jfa_cup)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
