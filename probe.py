#!/usr/bin/env python3
"""소스 점검기.

여기서 확인하는 것
  1) ESPN에 K리그·아시안게임·대표팀 대회가 있는지 (slug가 살아 있는지)
  2) 그 리그가 '선발 라인업'까지 주는지 (일정만 주는 리그가 많다)
  3) 국내 리그(K리그·KBO·KBL·KOVO)를 네이버에서 받아올 수 있는지
  4) KOVO 공식 사이트가 응답하는지

깃허브 액션에서 돌리면 실제 수집이 일어날 환경(깃허브 IP)에서 그대로 찍힌다.
로그를 복사해서 주면 되는 용도.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

import requests

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports"
UA = {"User-Agent": "Mozilla/5.0 (compatible; lineup-radar probe)", "Accept": "*/*"}
TIMEOUT = 20

# 확인해 볼 축구 slug 후보 (아시안게임·대표팀 대회 포함)
SOCCER_SLUGS = [
    ("kor.1", "K리그1"),
    ("kor.2", "K리그2"),
    ("kor.league_cup", "코리아컵"),
    ("afc.champions", "ACL 엘리트"),
    ("afc.cup", "ACL2"),
    ("afc.asian.cup", "아시안컵"),
    ("fifa.worldq.afc", "월드컵 예선(아시아)"),
    ("fifa.friendly", "국가대표 친선"),
    ("fifa.olympics", "올림픽 남자축구"),
    ("asian.games", "아시안게임(후보 A)"),
    ("afc.asiad", "아시안게임(후보 B)"),
    ("fifa.asian_games", "아시안게임(후보 C)"),
    ("jpn.1", "J리그"),
    ("chn.1", "중국 슈퍼리그"),
    ("fifa.world", "월드컵"),
    ("fifa.worldq.uefa", "월드컵 예선(유럽)"),
    ("uefa.nations", "네이션스리그"),
    ("uefa.euroq", "유로 예선"),
    ("conmebol.america", "코파 아메리카"),
    ("fifa.wwc", "여자 월드컵"),
    ("fifa.friendly.w", "여자 A매치 친선"),
]

NAVER_TESTS = [
    ("K리그 일정", "https://api-gw.sports.naver.com/schedule/games",
     {"fields": "basic,superCategoryId,categoryName,stadium",
      "upperCategoryId": "kfootball", "fromDate": None, "toDate": None, "size": 30}),
    ("KBO 일정", "https://api-gw.sports.naver.com/schedule/games",
     {"fields": "basic", "upperCategoryId": "kbaseball", "fromDate": None, "toDate": None, "size": 30}),
    ("배구 일정", "https://api-gw.sports.naver.com/schedule/games",
     {"fields": "basic", "upperCategoryId": "volleyball", "fromDate": None, "toDate": None, "size": 30}),
    ("농구 일정", "https://api-gw.sports.naver.com/schedule/games",
     {"fields": "basic", "upperCategoryId": "basketball", "fromDate": None, "toDate": None, "size": 30}),
]

OTHER_TESTS = [
    ("KOVO 모바일 일정", "https://m.kovo.co.kr/11101_schedule_list.asp", None),
    ("KOVO PC", "https://kovo.co.kr/KOVO/game/v-league", {"gender": "male"}),
    ("KBL 공식", "https://www.kbl.or.kr/game/schedule", None),
]


def get(url, params=None):
    try:
        res = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        return res
    except Exception as exc:
        print(f"      ! 접속 실패: {type(exc).__name__} {exc}")
        return None


def espn_league(slug, label):
    """최근 ±7일 경기 수와, 가장 최근 끝난 경기의 라인업 제공 여부."""
    today = datetime.utcnow()
    events = []
    for offset in range(-7, 8):
        date = (today + timedelta(days=offset)).strftime("%Y%m%d")
        res = get(f"{ESPN_SITE}/soccer/{slug}/scoreboard", {"dates": date})
        if res is None:
            return
        if res.status_code != 200:
            print(f"  X  {slug:22} {label:18} HTTP {res.status_code}")
            return
        try:
            data = res.json()
        except ValueError:
            print(f"  X  {slug:22} {label:18} JSON 아님")
            return
        for ev in data.get("events") or []:
            events.append(ev)

    if not events:
        print(f"  △  {slug:22} {label:18} 응답은 되지만 ±7일 경기 0개")
        return

    done = []
    for ev in events:
        comps = ev.get("competitions") or []
        if comps and ((comps[0].get("status") or {}).get("type") or {}).get("completed"):
            done.append(ev)

    line = f"  O  {slug:22} {label:18} ±7일 경기 {len(events)}개"
    if not done:
        print(line + " / 끝난 경기가 없어 라인업 확인 보류")
        return
    res = get(f"{ESPN_SITE}/soccer/{slug}/summary", {"event": done[-1].get("id")})
    if res is None or res.status_code != 200:
        print(line + " / 라인업 확인 실패")
        return
    try:
        summary = res.json()
    except ValueError:
        print(line + " / 라인업 응답이 JSON 아님")
        return
    rosters = summary.get("rosters") or []
    starters = 0
    for r in rosters:
        starters += sum(1 for p in (r.get("roster") or []) if p.get("starter"))
    if starters >= 10:
        print(line + f" / 라인업 있음 (선발 {starters}명 확인) ★")
    elif rosters:
        print(line + f" / 명단은 있으나 선발 표시 {starters}명뿐")
    else:
        print(line + " / 라인업 없음 (일정·점수만)")


def naver_check():
    today = datetime.utcnow() + timedelta(hours=9)
    frm = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    for label, url, params in NAVER_TESTS:
        params = dict(params or {})
        if "fromDate" in params:
            params["fromDate"] = frm
            params["toDate"] = to
        res = get(url, params)
        if res is None:
            continue
        body = res.text[:160].replace("\n", " ")
        if res.status_code == 200:
            try:
                data = res.json()
                result = data.get("result") or {}
                games = result.get("games") or []
                cats = sorted({g.get("categoryName") or g.get("categoryId") for g in games})
                print(f"  O  네이버 {label:12} 경기 {len(games)}개 / 대회: {', '.join(str(c) for c in cats)[:70]}")
                if games:
                    keys = sorted(games[0].keys())
                    print(f"       첫 경기 키: {', '.join(keys)[:150]}")
            except ValueError:
                print(f"  △  네이버 {label:12} 200이지만 JSON 아님: {body[:80]}")
        else:
            print(f"  X  네이버 {label:12} HTTP {res.status_code} {body[:80]}")


def other_check():
    for label, url, params in OTHER_TESTS:
        res = get(url, params)
        if res is None:
            continue
        kind = res.headers.get("content-type", "")[:40]
        print(f"  {'O' if res.status_code == 200 else 'X'}  {label:16} HTTP {res.status_code} / {kind} / {len(res.content)}바이트")


def elo_check():
    """대표팀 Elo(eloratings.net) 파일이 받아지는지."""
    for label, url in (("World.tsv", "https://www.eloratings.net/World.tsv"), ("en.teams.tsv", "https://www.eloratings.net/en.teams.tsv")):
        res = get(url)
        if res is None:
            continue
        lines = [l for l in res.text.splitlines() if l.strip()]
        sample = lines[0][:60].replace("\t", " | ") if lines else ""
        print(f"  {'O' if res.status_code == 200 and lines else 'X'}  Elo {label:13} HTTP {res.status_code} / {len(lines)}줄 / 첫 줄: {sample}")


def main():
    print("=" * 74)
    print("1) ESPN 축구 리그 — K리그·아시안게임·대표팀 대회 확인")
    print("=" * 74)
    for slug, label in SOCCER_SLUGS:
        espn_league(slug, label)

    print()
    print("=" * 74)
    print("2) 네이버 스포츠 — 국내 리그(K리그·KBO·배구·농구) 접근 가능 여부")
    print("=" * 74)
    naver_check()

    print()
    print("=" * 74)
    print("3) KOVO·KBL 공식 사이트 응답 확인")
    print("=" * 74)
    other_check()

    print()
    print("=" * 74)
    print("4) 대표팀 Elo (A매치 전력 비교용)")
    print("=" * 74)
    elo_check()

    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
