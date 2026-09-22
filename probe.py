#!/usr/bin/env python3
"""12차 소스 점검 (읽기만 함): 일왕배(풋몹 9011) — 경기 목록, 라인업 모양, 팀의 지난 경기 목록.

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


def emperor_fotmob():
    head("일왕배 (풋몹 대회 번호 9011) — 경기 목록 · 라인업 모양 · 팀 정보")
    found = []
    for back in (0, 1, -1):
        d = (TODAY + timedelta(days=back)).strftime("%Y%m%d")
        body, st = jget("https://www.fotmob.com/api/data/matches", {"date": d}, FH)
        for lg in (body or {}).get("leagues") or []:
            if lg.get("primaryId") == 9011 or lg.get("id") == 9011:
                print(f"  {d} {lg.get('name')} · {len(lg.get('matches') or [])}경기 · 리그 키: {list(lg.keys())[:10]}")
                for m in lg.get("matches") or []:
                    found.append(m)
    if found:
        print(f"  경기 한 줄: {json.dumps(found[0], ensure_ascii=False)[:500]}")
    for m in found[:8]:
        st = m.get("status") or {}
        print(f"   - {(m.get('home') or {}).get('name')} (id {(m.get('home') or {}).get('id')}) vs {(m.get('away') or {}).get('name')} · {st.get('utcTime')} · 시작 {st.get('started')} · 끝 {st.get('finished')}")
    shown = 0
    for m in found:
        body, st = jget("https://www.fotmob.com/api/data/matchDetails", {"matchId": m.get("id")}, FH)
        lu = ((body or {}).get("content") or {}).get("lineup") or {}
        ht = lu.get("homeTeam") or {}
        print(f"\n  ▶ {(m.get('home') or {}).get('name')} vs {(m.get('away') or {}).get('name')} · 상세 HTTP {st} · 라인업 유형 {lu.get('lineupType')} · 선발 {len(ht.get('starters') or [])} · 교체 {len(ht.get('subs') or [])} · 결장 {len(ht.get('unavailable') or [])}")
        if ht.get("starters"):
            print(f"     팀 키: {list(ht.keys())[:16]}")
            for p in (ht.get("starters") or [])[:2]:
                print(f"     선발 예: {json.dumps(p, ensure_ascii=False)[:450]}")
            for p in (ht.get("subs") or [])[:1]:
                print(f"     교체 예: {json.dumps(p, ensure_ascii=False)[:300]}")
            shown += 1
        if shown >= 2:
            break
    if not shown:
        print("  - 아직 라인업이 나온 경기가 없어요 (경기 1시간 전쯤 발표)")
    # 팀 정보 (최근 경기 목록이 있는지: 몇 군 판정용)
    if found:
        tid = (found[0].get("home") or {}).get("id")
        body, st = jget("https://www.fotmob.com/api/data/teams", {"id": tid}, FH)
        print(f"\n  팀 정보 HTTP {st} · 키: {list((body or {}).keys())[:14]}")
        fx = (body or {}).get("fixtures") or {}
        print(f"  fixtures 키: {list(fx.keys())[:10] if isinstance(fx, dict) else type(fx).__name__}")
        allf = (((fx.get("allFixtures") or {}) if isinstance(fx, dict) else {}).get("fixtures")) or []
        done = [f for f in allf if (f.get("status") or {}).get("finished")]
        print(f"  지난 경기 {len(done)}개 · 예: {json.dumps(done[-1], ensure_ascii=False)[:300] if done else '-'}")


def safe(fn):
    try:
        fn()
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")


def main():
    safe(emperor_fotmob)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
