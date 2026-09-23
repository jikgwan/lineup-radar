#!/usr/bin/env python3
"""16차 소스 점검 (읽기만 함): 위키데이터에 선수 한국어 이름이 얼마나 있나 (리그별 표본 50명).

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
SAMPLES = {
    "유럽 주요 리그": ["Erling Haaland", "Bukayo Saka", "Jude Bellingham", "Lamine Yamal", "Rafael Leao",
                   "Florian Wirtz", "Ousmane Dembele", "Virgil van Dijk", "Alexander Isak", "Pedri"],
    "걸프·중동 대표팀": ["Aymen Hussein", "Ali Jasim", "Zaid Tahseen", "Issam Al-Sabhi", "Abdulaziz Al-Ghanim",
                   "Salem Al-Dawsari", "Sultan Al-Ghannam", "Ali Al-Bulaihi", "Mohanad Ali", "Muhsen Al-Ghassani"],
    "사우디 리그": ["Aleksandar Mitrovic", "Malcom", "Ruben Neves", "Karim Benzema", "Sadio Mane",
                "Riyad Mahrez", "Roberto Firmino", "Ivan Toney", "Franck Kessie", "Moussa Diaby"],
    "중국 슈퍼리그": ["Wu Lei", "Zhang Yuning", "Wei Shihao", "Oscar", "Cesar Aguilar",
                 "Serginho", "Leonardo", "Fernandinho", "Wang Dalei", "Zhu Chenjie"],
    "남미 리그": ["Pedro", "Gabriel Barbosa", "Hulk", "Paulinho", "Miguel Borja",
               "Edinson Cavani", "Angel Di Maria", "Luis Suarez", "Everton Ribeiro", "Marcos Rojo"],
}


def wikidata_names():
    head("위키데이터 — 선수 이름이 한국어로 얼마나 있나 (리그별 표본)")
    api = "https://www.wikidata.org/w/api.php"
    total_ok = total_n = 0
    for group, names in SAMPLES.items():
        ok, lines = 0, []
        for name in names:
            body, st = jget(api, {"action": "wbsearchentities", "search": name, "language": "en",
                                  "uselang": "ko", "type": "item", "limit": 3, "format": "json"})
            hits = (body or {}).get("search") or []
            player = next((h for h in hits if "football" in str(h.get("description") or "").lower()
                           or "축구" in str(h.get("description") or "")), hits[0] if hits else None)
            ko = None
            if player:
                b2, s2 = jget(api, {"action": "wbgetentities", "ids": player.get("id"), "props": "labels",
                                    "languages": "ko", "format": "json"})
                ent = ((b2 or {}).get("entities") or {}).get(player.get("id")) or {}
                ko = ((ent.get("labels") or {}).get("ko") or {}).get("value")
            if ko:
                ok += 1
            lines.append(f"     {name:24} → {ko or '(없음)'}")
        total_ok += ok
        total_n += len(names)
        print(f"\n  ▶ {group}: {ok}/{len(names)} 한국어 이름 있음")
        for line in lines:
            print(line)
    print(f"\n  전체: {total_ok}/{total_n} ({round(100 * total_ok / max(1, total_n))}%)")
    print("  · 절반을 넘으면 붙일 만하고, 낮으면 네이버가 있는 리그만 한국어로 하는 게 나아요.")


def safe(fn):
    try:
        fn()
    except Exception as exc:
        print(f"  ! {fn.__name__} 오류: {type(exc).__name__} {str(exc)[:150]}")


def main():
    safe(wikidata_names)
    print()
    print("점검 끝. 위 내용을 그대로 복사해서 보내주면 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
