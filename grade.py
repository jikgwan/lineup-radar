"""선발 라인업으로 '몇 군'을 판정하는 순수 로직 (네트워크 없음 = 테스트 가능)."""

from __future__ import annotations

import math


def josa(word, with_final, without_final):
    """받침 유무로 조사 선택: josa('레알', '은', '는') -> '레알은'."""
    w = str(word or "")
    ch = w[-1:] if w else ""
    if ch and "가" <= ch <= "힣":
        has = (ord(ch) - 0xAC00) % 28 != 0
    else:
        has = ch.lower() in set("lmnr0136789")   # 영문·숫자는 읽는 소리 기준 대략
    return w + (with_final if has else without_final)


def build_profiles(history):
    """history: [{"starters": [pid...], "played": [pid...], "names": {pid: name}}, ...]
    최근 경기부터 순서는 상관 없음.
    반환: {pid: {"name": str, "starts": int, "apps": int}}
    """
    profiles = {}
    for match in history:
        names = match.get("names") or {}
        played = set(match.get("played") or [])
        starters = set(match.get("starters") or [])
        played |= starters
        for pid in played:
            p = profiles.setdefault(pid, {"name": names.get(pid, ""), "starts": 0, "apps": 0})
            if not p["name"] and names.get(pid):
                p["name"] = names[pid]
            p["apps"] += 1
            if pid in starters:
                p["starts"] += 1
    return profiles


def core_players(profiles, size):
    """최근 선발 횟수 기준 '주전 후보' size명의 pid 목록."""
    ranked = sorted(
        profiles.items(),
        key=lambda kv: (-kv[1]["starts"], -kv[1]["apps"], str(kv[0])),
    )
    return [pid for pid, _ in ranked[:size]]


def role_of(pid, profiles, matches):
    """주전 / 로테이션 / 백업 / 불명"""
    if matches <= 0:
        return "불명"
    p = profiles.get(pid)
    if not p:
        return "신규·복귀"
    ratio = p["starts"] / matches
    if ratio >= 0.6:
        return "주전"
    if ratio >= 0.3:
        return "로테이션"
    return "백업"


# 기준 인원별 (1군 최소 인원, 1.5군 최소 인원)
GRADE_CUTS = {
    11: (8, 6),  # 축구 — 백테스트: 팀들이 평소에도 2~3명씩 바꿈 → 0~3명 빠짐 1군 · 4~5명 1.5군 · 6명+ 2군
    9: (8, 6),   # 야구 타선
    7: (6, 5),   # 배구
    5: (5, 4),   # 농구
}


def grade_label(core_in, size):
    """선발 중 주전 후보가 몇 명인지로 1군/1.5군/2군 판정."""
    if size <= 0:
        return "판단불가"
    first, second = GRADE_CUTS.get(size, (-(-size * 8 // 10), -(-size * 55 // 100)))
    if core_in >= first:
        return "1군"
    if core_in >= second:
        return "1.5군"
    return "2군"


def analyze_lineup(lineup, history, size):
    """lineup: [{"id": pid, "name": str, "pos": str}, ...] (오늘 선발)
    history: build_profiles 입력과 동일
    size: 기준 인원 (축구 11, 야구 9, 농구 5)
    """
    matches = len(history)
    profiles = build_profiles(history)
    core = core_players(profiles, size) if profiles else []
    core_set = set(core)

    players = []
    lineup_ids = set()
    for pl in lineup:
        pid = pl.get("id")
        lineup_ids.add(pid)
        prof = profiles.get(pid, {"starts": 0, "apps": 0})
        players.append(
            {
                "id": pid,
                "name": pl.get("name") or prof.get("name") or "",
                "pos": pl.get("pos") or "",
                "starts": prof["starts"],
                "apps": prof["apps"],
                "role": role_of(pid, profiles, matches),
                "core": pid in core_set,
            }
        )

    core_in = sum(1 for p in players if p["core"])
    missing = []
    for pid in core:
        if pid in lineup_ids:
            continue
        prof = profiles[pid]
        missing.append(
            {"id": pid, "name": prof["name"], "starts": prof["starts"], "apps": prof["apps"]}
        )

    return {
        "matches": matches,
        "size": size,
        "core_in": core_in,
        "grade": grade_label(core_in, size) if (matches >= 3 and lineup) else "판단불가",
        "players": players,
        "missing": missing,
        "key_players": [
            {"name": profiles[pid]["name"], "starts": profiles[pid]["starts"], "in_lineup": pid in lineup_ids}
            for pid in core[: min(4, len(core))]
        ],
    }


# ---------------------------------------------------------------- 화면용 부가 정보

def parse_formation(text):
    """'4-2-3-1' -> [4, 2, 3, 1]. 이상하면 []."""
    parts = []
    for chunk in str(text or "").replace(" ", "").split("-"):
        if not chunk.isdigit():
            return []
        parts.append(int(chunk))
    return parts if all(p > 0 for p in parts) else []


def formation_rows(lineup, formation, shape):
    """선발 명단을 경기장 줄 단위로 배치한다.
    lineup: [{"pos", "place", ...}], shape: pos -> (깊이, 좌우)
    반환: 골키퍼 줄부터 최전방 줄까지, 각 줄은 왼쪽->오른쪽 인덱스 목록.
    """
    if not lineup:
        return []
    info = []
    for i, p in enumerate(lineup):
        depth, side = shape(p.get("pos"))
        place = p.get("place") if isinstance(p.get("place"), int) else 99
        info.append({"i": i, "depth": depth, "side": side, "place": place})

    gk = next((x for x in info if x["depth"] == 0), None)
    if gk is None:
        gk = next((x for x in info if x["place"] == 1), None)
    if gk is None:
        gk = min(info, key=lambda x: (x["depth"] if x["depth"] is not None else 9, x["place"]))
    outfield = [x for x in info if x is not gk]
    outfield.sort(key=lambda x: (x["depth"] if x["depth"] is not None else 3, x["place"]))

    counts = parse_formation(formation)
    rows = []
    if counts and sum(counts) == len(outfield):
        pos = 0
        for c in counts:
            rows.append(outfield[pos:pos + c])
            pos += c
    else:
        groups = {}
        for x in outfield:
            groups.setdefault(x["depth"] if x["depth"] is not None else 3, []).append(x)
        rows = [groups[k] for k in sorted(groups)]

    out = [[gk["i"]]]
    for row in rows:
        row.sort(key=lambda x: (x["side"], x["place"]))
        out.append([x["i"] for x in row])
    return out


def player_log(history, pid):
    """최근 경기별 출전 기록(최신순)."""
    log = []
    for m in history:
        starters = set(m.get("starters") or [])
        played = set(m.get("played") or []) | starters
        mins = (m.get("minutes") or {}).get(pid) if pid in played else 0
        log.append(
            {
                "date": m.get("date", ""),
                "opp": m.get("opp", ""),
                "res": m.get("res", ""),
                "started": pid in starters,
                "played": pid in played,
                "min": mins,
                "g": int((m.get("goals") or {}).get(pid, 0) or 0),
                "a": int((m.get("assists") or {}).get(pid, 0) or 0),
            }
        )
    return log


def minutes_total(log):
    """(합계, 모든 경기의 분을 알았는지)"""
    total, complete = 0, True
    for row in log:
        if not row["played"]:
            continue
        if row["min"] is None:
            complete = False
            continue
        total += row["min"]
    return total, complete


def team_form(history, limit=5):
    out = []
    for m in history[:limit]:
        if not m.get("res"):
            continue
        out.append({k: m.get(k) for k in ("date", "opp", "home", "gf", "ga", "res")})
    return out


def team_leaders(history, key, names=None, top=2):
    """key='goals' 또는 'assists'. 동률이면 최대 top명."""
    tally, label = {}, dict(names or {})
    for m in history:
        for pid, n in (m.get(key) or {}).items():
            if n:
                tally[pid] = tally.get(pid, 0) + int(n)
        for pid, nm in (m.get("names") or {}).items():
            label.setdefault(pid, nm)
    if not tally:
        return []
    best = max(tally.values())
    leaders = sorted([pid for pid, n in tally.items() if n == best], key=lambda p: label.get(p, ""))
    return [{"name": label.get(pid, ""), "n": best} for pid in leaders[:top]]


def enrich(result, lineup, history, season=None, formation="", shape=None):
    """analyze_lineup 결과에 출전시간·최근경기·팀 기록 1위·포메이션 배치를 붙인다."""
    season = season if season is not None else history
    for p, src in zip(result["players"], lineup):
        log = player_log(history, p["id"])
        total, complete = minutes_total(log)
        p["minutes"] = total
        p["minutes_complete"] = complete
        p["log"] = log
        p["short"] = src.get("short") or ""
        p["jersey"] = src.get("jersey") or ""
        g = sum(int((m.get("goals") or {}).get(p["id"], 0) or 0) for m in season)
        a = sum(int((m.get("assists") or {}).get(p["id"], 0) or 0) for m in season)
        p["goals"], p["assists"] = g, a
    for m in result["missing"]:
        log = player_log(history, m["id"])
        m["log"] = log
        m["minutes"], m["minutes_complete"] = minutes_total(log)
        m["goals"] = sum(int((s.get("goals") or {}).get(m["id"], 0) or 0) for s in season)
        m["assists"] = sum(int((s.get("assists") or {}).get(m["id"], 0) or 0) for s in season)
    result["form"] = team_form(history)
    result["leaders"] = {
        "goals": team_leaders(season, "goals"),
        "assists": team_leaders(season, "assists"),
        "matches": len(season),
    }
    result["rows"] = formation_rows(lineup, formation, shape) if shape else []
    return result


def _player_card(p, history, season):
    log = player_log(history, p["id"])
    total, complete = minutes_total(log)
    p["minutes"], p["minutes_complete"], p["log"] = total, complete, log
    p["goals"] = sum(int((m.get("goals") or {}).get(p["id"], 0) or 0) for m in season)
    p["assists"] = sum(int((m.get("assists") or {}).get(p["id"], 0) or 0) for m in season)
    return p


def add_bench(result, bench, history, season=None):
    """교체명단 기록을 붙이고, 빠진 주전이 벤치인지 명단 제외인지 표시한다.
    bench가 비어 있으면(명단 미제공) 구분하지 않는다(None)."""
    season = season if season is not None else history
    matches = len(history)
    profiles = build_profiles(history)
    core_ids = {p["id"] for p in result["players"] if p.get("core")} | {m["id"] for m in result["missing"]}
    cards = []
    for b in bench or []:
        prof = profiles.get(b["id"], {"starts": 0, "apps": 0})
        card = {
            "id": b["id"], "name": b.get("name") or prof.get("name", ""), "short": b.get("short") or "",
            "pos": b.get("pos") or "", "jersey": b.get("jersey") or "",
            "starts": prof["starts"], "apps": prof["apps"],
            "role": role_of(b["id"], profiles, matches), "core": b["id"] in core_ids,
        }
        cards.append(_player_card(card, history, season))
    cards.sort(key=lambda c: (not c["core"], -c["starts"], -c["apps"]))
    result["bench"] = cards
    bench_ids = {c["id"] for c in cards}
    for m in result["missing"]:
        m["status"] = None if not bench else ("벤치" if m["id"] in bench_ids else "명단 제외")
    return result


def strength(result, history, season=None):
    """전력 지표.
    retain: 오늘 선발 11명의 최근 출전시간 합 / 평소 주전 11명의 최근 출전시간 합
    goal_share / assist_share: 오늘 선발이 팀 득점·도움에서 차지하는 비율
    """
    season = season if season is not None else history
    core = [p for p in result["players"] if p.get("core")] + list(result["missing"])
    core_min = 0
    for c in core:
        core_min += minutes_total(player_log(history, c["id"]))[0]
    start_min = sum(minutes_total(player_log(history, p["id"]))[0] for p in result["players"])
    team_goals = sum(sum(int(v or 0) for v in (m.get("goals") or {}).values()) for m in season)
    team_ast = sum(sum(int(v or 0) for v in (m.get("assists") or {}).values()) for m in season)
    ids = {p["id"] for p in result["players"]}
    my_goals = sum(sum(int(v or 0) for k, v in (m.get("goals") or {}).items() if k in ids) for m in season)
    my_ast = sum(sum(int(v or 0) for k, v in (m.get("assists") or {}).items() if k in ids) for m in season)
    pct = lambda a, b: round(100 * a / b) if b else None
    result["strength"] = {
        "retain": min(100, pct(start_min, core_min)) if core_min else None,
        "goal_share": pct(my_goals, team_goals),
        "assist_share": pct(my_ast, team_ast),
        "team_goals": team_goals,
        "team_assists": team_ast,
    }
    return result


# ---------------------------------------------------------------- 한 줄 요약·핵심 포인트

GRADE_RANK = {"1군": 3, "1.5군": 2, "2군": 1}


def _impact_order(missing):
    return sorted(missing, key=lambda m: (-(int(m.get("goals") or 0) * 2 + int(m.get("assists") or 0)),
                                          -int(m.get("starts") or 0), m.get("name", "")))


def _form_text(form):
    w = sum(1 for f in form[:5] if f.get("res") == "W")
    d = sum(1 for f in form[:5] if f.get("res") == "D")
    l = sum(1 for f in form[:5] if f.get("res") == "L")
    parts = [f"{w}승" if w else "", f"{d}무" if d else "", f"{l}패" if l else ""]
    return " ".join(p for p in parts if p)


def summarize(home_name, away_name, teams, sport="축구"):
    """라인업 판정 결과 -> 한 줄 요약(headline), 목록용 한 줄(insight), 핵심 포인트 3줄.
    판정이 없는 팀은 건너뛴다. 데이터에 없는 말은 만들지 않는다."""
    h, a = teams.get("home") or {}, teams.get("away") or {}
    names = {"home": home_name, "away": away_name}
    rh, ra = GRADE_RANK.get(h.get("grade"), 0), GRADE_RANK.get(a.get("grade"), 0)
    if not rh or not ra:
        return {"headline": "", "insight": "", "points": [], "focus": None}

    focus = "home" if rh < ra else "away" if ra < rh else None
    if focus is None:   # 등급이 같으면 주전이 더 많이 빠진 쪽
        mh, ma = len(h.get("missing") or []), len(a.get("missing") or [])
        focus = "home" if mh > ma else "away" if ma > mh else None
    if focus is None and (h.get("missing") or a.get("missing")):
        # 등급·빠진 인원이 같으면 어느 한쪽만 말할 수 없으니 양 팀을 같이 말한다
        mh, ma = len(h.get("missing") or []), len(a.get("missing") or [])
        if h.get("grade") == a.get("grade") and h.get("grade") != "1군":
            headline = f"양 팀 모두 {h.get('grade')}"
        else:
            headline = "양 팀 사실상 베스트" if h.get("grade") == "1군" else f"{home_name} {h.get('grade')} · {away_name} {a.get('grade')}"
        return {"headline": headline, "insight": f"양 팀 주전 {mh}명·{ma}명 빠짐",
                "points": [[f"{home_name} 주전 {mh}명, {away_name} 주전 {ma}명이 선발에서 빠짐", ""]], "focus": None}
    ft = teams[focus] if focus else h
    other = "away" if focus == "home" else "home"
    ot = teams[other] if focus else a
    fname = names[focus] if focus else ""
    oname = names[other] if focus else ""

    missing = _impact_order(ft.get("missing") or [])
    top = [m["name"] for m in missing[:2] if m.get("name")]
    top_txt = "·".join(top)
    grade = ft.get("grade")

    if focus is None and not (h.get("missing") or a.get("missing")):
        headline = "양 팀 모두 베스트 라인업"
        insight = "양 팀 베스트"
    elif grade == "2군":
        headline = f"{fname}, {top_txt} 빼고 2군 가동" if top else f"{fname} 2군 가동"
        insight = f"{fname} 주전 {len(missing)}명 빠짐"
    elif grade == "1.5군":
        headline = f"{fname} 1.5군, {top_txt} 제외" if top else f"{fname} 1.5군"
        insight = f"{fname} 주전 {len(missing)}명 빠짐"
    else:
        headline = f"양 팀 사실상 베스트, {fname} {top_txt} 제외" if top else "양 팀 사실상 베스트"
        insight = f"{fname} {top[0]} 제외" if top else "양 팀 베스트"
    if missing and missing[0].get("status"):
        insight += f" · {missing[0]['name']} {missing[0]['status']}"

    points = []
    if focus and missing:
        size = ft.get("size") or 0
        bench = sum(1 for m in missing if m.get("status") == "벤치")
        out = sum(1 for m in missing if m.get("status") == "명단 제외")
        sub = f"벤치 대기 {bench} · 명단 제외 {out}" if (bench or out) else ", ".join(m["name"] for m in missing[:4])
        points.append([f"{fname} 주전 {size}명 중 {len(missing)}명이 선발에서 빠짐", sub])
        if sport != "야구":
            tg = int((ft.get("strength") or {}).get("team_goals") or 0)
            mg = sum(int(m.get("goals") or 0) for m in missing)
            if tg and mg:
                scorers = " · ".join(f"{m['name']} {m['goals']}골" for m in missing if m.get("goals"))
                points.append([f"{fname} {tg}골 중 {mg}골({round(mg * 100 / tg)}%) 넣은 선수가 선발에 없음", scorers])
    if focus:
        om = len(ot.get("missing") or [])
        line = f"{josa(oname, '은', '는')} 주전 {ot.get('core_in')}명 그대로" if om <= 1 else f"{oname}도 주전 {om}명 빠짐"
        points.append([line, ("최근 5경기 " + _form_text(ot.get("form") or [])) if ot.get("form") else ""])
    return {"headline": headline, "insight": insight, "points": points[:3], "focus": focus}



# ---------------------------------------------------------------- 최근 폼 vs 시즌 전체

def aggregate(history, pid):
    """기간(history) 동안 한 선수의 선발·출전·시간·골·도움."""
    log = player_log(history, pid)
    total, complete = minutes_total(log)
    return {
        "matches": len(history),
        "starts": sum(1 for r in log if r["started"]),
        "apps": sum(1 for r in log if r["played"]),
        "minutes": total,
        "minutes_complete": complete,
        "goals": sum(int((m.get("goals") or {}).get(pid, 0) or 0) for m in history),
        "assists": sum(int((m.get("assists") or {}).get(pid, 0) or 0) for m in history),
    }


def form_trend(recent, season):
    """시즌 선발 비율 대비 최근 선발 비율 변화: 'up' / 'down' / ''"""
    if not recent["matches"] or season["matches"] <= recent["matches"]:
        return ""
    r = recent["starts"] / recent["matches"]
    s = season["starts"] / season["matches"]
    if r - s >= 0.25:
        return "up"
    if s - r >= 0.25:
        return "down"
    return ""


def season_record(season):
    w = sum(1 for m in season if m.get("res") == "W")
    d = sum(1 for m in season if m.get("res") == "D")
    l = sum(1 for m in season if m.get("res") == "L")
    gf = sum(int(m.get("gf") or 0) for m in season if m.get("res"))
    ga = sum(int(m.get("ga") or 0) for m in season if m.get("res"))
    n = w + d + l
    return {"matches": n, "w": w, "d": d, "l": l, "gf": gf, "ga": ga,
            "ppg": round((3 * w + d) / n, 2) if n else None}


def add_periods(result, history, season):
    """선발·교체·빠진 선수 모두에 recent/season 집계와 폼 변화를 붙이고,
    팀에는 시즌 성적과 '시즌 기준 주전 몇 명 선발'을 붙인다."""
    for group in ("players", "bench", "missing"):
        for p in result.get(group) or []:
            rec, sea = aggregate(history, p["id"]), aggregate(season, p["id"])
            p["recent"], p["season"], p["trend"] = rec, sea, form_trend(rec, sea)
    result["season_record"] = season_record(season)
    size = result.get("size") or 0
    sprof = build_profiles(season)
    score = {pid: v["starts"] for pid, v in sprof.items()}
    core = sorted(score, key=lambda k: (-score[k], str(k)))[:size]
    ids = {p["id"] for p in result.get("players") or []}
    result["season_core_in"] = sum(1 for pid in core if pid in ids) if season else None
    result["season_matches"] = len(season)
    return result



# ---------------------------------------------------------------- 에이스

def _form_value(x):
    """한 기간의 기여도: 경기당 (골 + 0.7×도움)×2 + 경기당 출전시간 비율."""
    n = (x or {}).get("matches") or 0
    if not n:
        return 0.0
    ap = ((x.get("goals") or 0) + 0.7 * (x.get("assists") or 0)) / n
    mins = (x.get("minutes") or 0) / (90.0 * n)
    return ap * 2 + mins


def ace_score(p):
    """시즌 50% + 최근 50%."""
    return round(0.5 * _form_value(p.get("season")) + 0.5 * _form_value(p.get("recent")), 3)


def pick_ace(result):
    """선발·교체명단·빠진 선수 전체에서 팀 에이스 1명.
    기록이 없으면 None. 동점이면 시즌 골, 시즌 출전시간 순."""
    cands, seen = [], set()
    for where, group in (("선발", result.get("players")), ("벤치", result.get("bench")), (None, result.get("missing"))):
        for p in group or []:
            if not p.get("season") or p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            status = where or (p.get("status") or "선발 제외")
            cands.append((p, status))
    cands = [c for c in cands if (c[0]["season"].get("apps") or 0) > 0]
    if not cands:
        return None
    p, status = max(cands, key=lambda c: (ace_score(c[0]), c[0]["season"].get("goals") or 0,
                                          c[0]["season"].get("minutes") or 0, c[0].get("name", "")))
    s, r = p["season"], p.get("recent") or {}
    return {
        "id": p["id"], "name": p.get("name", ""), "pos": p.get("pos", ""), "jersey": p.get("jersey", ""),
        "status": status, "score": ace_score(p), "season": s, "recent": r,
        "trend": p.get("trend", ""), "log": p.get("log") or [],
    }



# ---------------------------------------------------------------- 오늘 라인업 절대 전력
# 1군/2군은 "자기 팀 평소 대비"라 두 팀을 직접 비교하지 못한다.
# 여기서는 오늘 선발 11명 자체와 팀 체급으로 두 팀을 같은 잣대에 올린다.

LEAGUE_AVG_PPG = 1.37      # 유럽 1부 리그 평균 경기당 승점(대략)
PRIOR_MATCHES = 4          # 경기 수가 적을 때 평균 쪽으로 당기는 강도
ROTATION_DROP = 0.45       # 완전 2군(라인업 계수 0)일 때 체급에서 빠지는 비율


def _pts(res):
    return 3 if res == "W" else 1 if res == "D" else 0 if res == "L" else None


def on_pitch(season, pid):
    """그 선수가 선발로 나온 경기의 팀 성적."""
    n = pts = ga = 0
    for m in season:
        if pid not in (m.get("starters") or []):
            continue
        p = _pts(m.get("res"))
        if p is None:
            continue
        n += 1
        pts += p
        ga += int(m.get("ga") or 0)
    return {"starts": n, "ppg": round(pts / n, 2) if n else None, "ga_pg": round(ga / n, 2) if n else None, "pts": pts}


def line_of(pos, shape):
    depth, _ = shape(pos) if shape else (None, 0)
    if depth is None:
        return "MF"
    if depth == 0:
        return "GK"
    if depth <= 1.5:
        return "DF"
    if depth >= 5:
        return "FW"
    return "MF"


def team_strength(record):
    """리그 경기당 승점을 경기 수로 보정한 체급 (적은 경기는 리그 평균 쪽으로)."""
    n = (record or {}).get("matches") or 0
    ppg = (record or {}).get("ppg")
    if not n or ppg is None:
        return None
    return round((ppg * n + LEAGUE_AVG_PPG * PRIOR_MATCHES) / (n + PRIOR_MATCHES), 3)


def _top(players, key, limit=2):
    vals = [(p, (p.get("season") or {}).get(key) or 0) for p in players]
    best = max((v for _, v in vals), default=0)
    if best <= 0:
        return []
    return [{"name": p.get("name", ""), "n": v} for p, v in vals if v == best][:limit]


def lineup_power(result, season, shape=None):
    """오늘 선발 11명의 시즌 합산·라인별 기록·선발 시 성적, 팀 체급과 오늘 전력."""
    xi = result.get("players") or []
    everyone = xi + (result.get("bench") or []) + [m for m in (result.get("missing") or [])
                                                  if m.get("id") not in {b.get("id") for b in result.get("bench") or []}]
    tot = {k: sum(int((p.get("season") or {}).get(k) or 0) for p in xi) for k in ("goals", "assists", "minutes", "starts")}

    lines = {}
    for p in xi:
        ln = line_of(p.get("pos"), shape)
        op = on_pitch(season, p["id"])
        p["on_pitch"] = op
        d = lines.setdefault(ln, {"n": 0, "goals": 0, "assists": 0, "minutes": 0, "ga_w": 0.0, "ga_n": 0})
        s = p.get("season") or {}
        d["n"] += 1
        d["goals"] += int(s.get("goals") or 0)
        d["assists"] += int(s.get("assists") or 0)
        d["minutes"] += int(s.get("minutes") or 0)
        if ln in ("DF", "GK") and op["starts"] >= 2:
            d["ga_w"] += op["ga_pg"] * op["starts"]
            d["ga_n"] += op["starts"]
    for p in (result.get("bench") or []) + (result.get("missing") or []):
        p["on_pitch"] = on_pitch(season, p["id"])
    out_lines = {}
    for ln, d in lines.items():
        out_lines[ln] = {"n": d["n"], "goals": d["goals"], "assists": d["assists"], "minutes": d["minutes"],
                         "ga_pg": round(d["ga_w"] / d["ga_n"], 2) if d["ga_n"] else None}
    back = {"n": 0, "ga_w": 0.0, "ga_n": 0}
    for ln in ("DF", "GK"):
        if ln in lines:
            back["ga_w"] += lines[ln]["ga_w"]
            back["ga_n"] += lines[ln]["ga_n"]

    # 선발 11명이 선발로 뛴 경기의 팀 경기당 승점.
    # 선발 3경기 미만 선수는 표본이 작아 빼고(보정해서 채우면 로테이션 멤버가 주전만큼 좋아 보이는 착시가 생김),
    # 몇 명 기준인지 함께 알려준다.
    q_pts = q_starts = q_n = 0
    for p in xi:
        op = p["on_pitch"]
        if op["starts"] >= 3:
            q_pts += op["pts"]
            q_starts += op["starts"]
            q_n += 1
    xi_ppg = round(q_pts / q_starts, 2) if q_starts else None

    st = result.get("strength") or {}
    retain = st.get("retain")
    gs, ast = st.get("goal_share"), st.get("assist_share")
    if retain is None:
        q = None
    else:
        att = ((gs if gs is not None else retain) + (ast if ast is not None else retain)) / 200
        q = round(0.6 * retain / 100 + 0.4 * att, 3)
    T = team_strength(result.get("season_record"))

    result["lineup_power"] = {
        "totals": tot,
        "top_scorer": _top(xi, "goals"),
        "top_assist": _top(xi, "assists"),
        "team_top_scorer": _top(everyone, "goals", 1),
        "lines": out_lines,
        "back_ga_pg": round(back["ga_w"] / back["ga_n"], 2) if back["ga_n"] else None,
        "xi_ppg": xi_ppg,
        "xi_ppg_n": q_n,
        "team_strength": T,
        "q": q,
        "today": round(T * (1 - ROTATION_DROP * (1 - q)), 3) if (T is not None and q is not None) else None,
    }
    return result


def compare_power(home, away, home_name, away_name, factor_home=1.0, factor_away=1.0):
    """두 팀 '오늘 전력'을 100점 나눠 갖기로 비교. 결과 예측(승률)이 아님."""
    lh, la = home.get("lineup_power") or {}, away.get("lineup_power") or {}
    th, ta = lh.get("today"), la.get("today")
    if th is None or ta is None:
        return None
    th, ta = th * factor_home, ta * factor_away
    share = round(100 * th / (th + ta)) if (th + ta) else 50
    gap = share - 50
    if abs(gap) < 4:
        verdict = "비슷한 전력"
        fav = None
    else:
        fav = "home" if gap > 0 else "away"
        verdict = f"{home_name if fav == 'home' else away_name} {'근소 ' if abs(gap) < 10 else ''}우위"
    note = ""
    gh, ga = home.get("grade"), away.get("grade")
    rank = {"1군": 3, "1.5군": 2, "2군": 1}
    if fav and rank.get(gh) and rank.get(ga):
        fg, og = (gh, ga) if fav == "home" else (ga, gh)
        fname, oname = (home_name, away_name) if fav == "home" else (away_name, home_name)
        if rank[fg] < rank[og]:
            note = f"{josa(fname, '은', '는')} {fg}이지만 팀 체급이 높아 {oname} {og}보다 앞섬"
        elif rank[fg] > rank[og]:
            note = f"{oname}의 로테이션이 전력 차로 이어짐"
    elif fav is None and rank.get(gh) and rank.get(ga) and gh != ga:
        low = home_name if rank[gh] < rank[ga] else away_name
        note = f"{josa(low, '은', '는')} 로테이션을 했지만 체급 덕에 비슷한 수준"
    return {"home": share, "away": 100 - share, "verdict": verdict, "fav": fav, "note": note,
            "league_adjusted": factor_home != factor_away}



# ---------------------------------------------------------------- A매치 (국가대표) 전력 비교: Elo 기반

ELO_ROTATION = 200     # 완전 2군(라인업 계수 0)일 때 깎는 Elo 점수
ELO_HOME = 100         # 홈 이점 (eloratings.net 방식)


def compare_elo(home, away, home_name, away_name, elo_home, elo_away, home_adv=0):
    """대표팀 Elo에 오늘 라인업 계수를 반영해 두 팀을 비교. 결과는 Elo 기대 성적(무승부는 절반) 기준 비율."""
    if elo_home is None or elo_away is None:
        return None
    qh = (home.get("lineup_power") or {}).get("q")
    qa = (away.get("lineup_power") or {}).get("q")
    qh = 1.0 if qh is None else qh
    qa = 1.0 if qa is None else qa
    th = elo_home - ELO_ROTATION * (1 - qh)
    ta = elo_away - ELO_ROTATION * (1 - qa)
    dr = th + home_adv - ta
    we = 1 / (10 ** (-dr / 400) + 1)
    share = round(100 * we)
    fake = {"grade": home.get("grade"), "lineup_power": {"today": share}}
    fake_a = {"grade": away.get("grade"), "lineup_power": {"today": 100 - share}}
    res = compare_power(fake, fake_a, home_name, away_name) or {}
    for side, t, e in (("home", home, elo_home), ("away", away, elo_away)):
        lp = t.setdefault("lineup_power", {})
        lp["elo"] = e
        lp["today_elo"] = round(th if side == "home" else ta)
    res.update({"home": share, "away": 100 - share, "mode": "elo", "home_adv": home_adv, "league_adjusted": False})
    return res


# ---------------------------------------------------------------- 야구 전력 (투수 중심)

MLB_RPG = 4.4            # MLB 평균 경기당 득점(대략)
MLB_HOME_EDGE = 0.54     # 홈 승률(대략)
PYTH_EXP = 1.83


def _ra9_starter(sp, rpg=MLB_RPG):
    """선발투수 9이닝당 예상 실점: 시즌 FIP 50% + 시즌 ERA 30% + 최근 3경기 ERA 20% (없는 값은 평균으로)."""
    avg = rpg * 0.95
    fip = sp.get("fip") if sp.get("fip") is not None else avg
    era = sp.get("era") if sp.get("era") is not None else avg
    rec = sp.get("recent_era") if sp.get("recent_era") is not None else era
    return 0.5 * fip + 0.3 * era + 0.2 * rec


def recent_era(starts, n=3):
    s = starts[:n]
    ip = sum(x["ip"] for x in s)
    return round(9 * sum(x["er"] for x in s) / ip, 2) if ip >= 3 else None


def bullpen_fatigue(pitches_3d):
    """최근 3일 불펜 투구수 -> 실점 가산 비율 (평소 3일 약 420구, 많이 던지면 최대 +15%)"""
    return max(0.0, min(0.15, (pitches_3d - 420) / 1000.0))


def baseball_power(home, away, home_name, away_name, rpg=None):
    """야구 오늘 전력: 예상 득점과 비율. home/away = {"off_rpg", "sp", "pen_era", "pen_3d", "core_in", "size"}
    rpg: 리그 평균 경기당 득점 (MLB 약 4.4, KBO 약 5)"""
    rpg = rpg or MLB_RPG

    def allowed(t):
        sp = t.get("sp") or {}
        share = min(0.75, max(0.45, ((sp.get("ip_per_start") or 5.3) / 9.0)))
        pen = (t.get("pen_era") if t.get("pen_era") is not None else rpg * 0.95) * (1 + bullpen_fatigue(t.get("pen_3d") or 0))
        return share * _ra9_starter(sp, rpg) + (1 - share) * pen

    def offense(t):
        base = t.get("off_rpg") if t.get("off_rpg") else rpg
        ratio = (t.get("core_in") or 0) / (t.get("size") or 9)
        return base * (0.9 + 0.1 * ratio)          # 타선 몇 군은 작은 보정만

    exp_h = offense(home) * allowed(away) / rpg
    exp_a = offense(away) * allowed(home) / rpg
    p = exp_h ** PYTH_EXP / (exp_h ** PYTH_EXP + exp_a ** PYTH_EXP) if (exp_h + exp_a) else 0.5
    p = (p * MLB_HOME_EDGE) / (p * MLB_HOME_EDGE + (1 - p) * (1 - MLB_HOME_EDGE))
    share = round(100 * p)
    fake_h = {"grade": home.get("grade"), "lineup_power": {"today": share}}
    fake_a = {"grade": away.get("grade"), "lineup_power": {"today": 100 - share}}
    out = compare_power(fake_h, fake_a, home_name, away_name) or {}
    out.update({"home": share, "away": 100 - share, "mode": "mlb", "league_adjusted": False,
                "exp_home": round(exp_h, 1), "exp_away": round(exp_a, 1), "exp_total": round(exp_h + exp_a, 1),
                "allowed_home": round(allowed(home), 2), "allowed_away": round(allowed(away), 2)})
    out["note"] = ""       # 축구식 '2군이지만…' 문구는 야구에 맞지 않아 뺀다
    return out


# ---------------------------------------------------------------- 경기 맥락: 로테이션 성적 · 비슷한 라인업 · 득실 흐름

def _core_ids(result):
    return {p["id"] for p in result.get("players") or [] if p.get("core")} | {m["id"] for m in result.get("missing") or []}


def rotation_record(result, season):
    """이번 시즌 경기마다 그날 선발이 몇 군이었는지(오늘 기준 주전으로 셈)와 결과."""
    core, size = _core_ids(result), result.get("size") or 11
    out = {g: [0, 0, 0] for g in ("1군", "1.5군", "2군")}
    if not core:
        return out
    for m in season:
        res = m.get("res")
        if res not in ("W", "D", "L"):
            continue
        n = sum(1 for pid in (m.get("starters") or []) if pid in core)
        out[grade_label(n, size)]["WDL".index(res)] += 1
    return out


def similar_record(result, season, need=None):
    """오늘 선발 중 need명 이상(축구 8명, 야구 6명)이 같이 선발로 나온 지난 경기 성적."""
    size = result.get("size") or 11
    need = need or (size - 3)
    ids = {p["id"] for p in result.get("players") or []}
    w = d = l = 0
    for m in season:
        res = m.get("res")
        if res not in ("W", "D", "L"):
            continue
        if len(ids & set(m.get("starters") or [])) >= need:
            if res == "W":
                w += 1
            elif res == "D":
                d += 1
            else:
                l += 1
    return {"w": w, "d": d, "l": l, "n": w + d + l, "need": need}


def goals_flow(season, n=10, over_line=3):
    """최근 n경기 득실 (최신순). over_line: 이 점수 이상이면 '오버' (축구 3 = 2.5골 이상)."""
    rows = [m for m in season if m.get("res") in ("W", "D", "L") and m.get("gf") is not None and m.get("ga") is not None][:n]
    if not rows:
        return None
    gf = [int(m["gf"]) for m in rows]
    ga = [int(m["ga"]) for m in rows]
    k = len(rows)
    return {"n": k, "gf": gf, "ga": ga, "gf_avg": round(sum(gf) / k, 2), "ga_avg": round(sum(ga) / k, 2),
            "total_avg": round((sum(gf) + sum(ga)) / k, 2),
            "over_pct": round(100 * sum(1 for a, b in zip(gf, ga) if a + b >= over_line) / k),
            "clean": sum(1 for b in ga if b == 0), "over_line": over_line}


def add_context(result, season, sport="축구"):
    result["rotation"] = rotation_record(result, season)
    result["similar"] = similar_record(result, season, need=None if sport != "야구" else 6)
    result["goals"] = goals_flow(season, over_line=9 if sport == "야구" else 3)
    return result


BIG_COMPS = ("챔스", "챔피언스", "유로파", "컨퍼런스", "ACL", "컵", "FA", "코파", "Champions", "Europa", "Conference")


def build_signals(names, teams, sport="축구"):
    """신호등: [문구, 종류(bad/warn/good)] — 에이스 결장, 2군, 곧 큰 경기, 풀전력, 불펜 과부하."""
    out = []
    for side in ("home", "away"):
        t = teams.get(side) or {}
        nm = names[side]
        ace = t.get("ace") or {}
        if ace.get("status") and ace["status"] != "선발":
            out.append([f"{nm} 에이스 {'벤치' if ace['status'] == '벤치' else '결장'}", "bad"])
        if t.get("grade") == "2군":
            out.append([f"{nm} 2군", "warn"])
        nx = (t.get("schedule") or {}).get("next") or {}
        if nx.get("in_days") is not None and nx["in_days"] <= 4 and any(k in (nx.get("comp") or "") for k in BIG_COMPS):
            out.append([f"{nm} {nx['in_days']}일 뒤 {nx['comp']}", "warn"])
        pen = (t.get("pitching") or {}).get("pen") or {}
        if (pen.get("pitches_3d") or 0) >= 500 or (pen.get("b2b") or 0) >= 3:
            out.append([f"{nm} 불펜 과부하", "warn"])
        sp = (t.get("pitching") or {}).get("sp") or {}
        if sp.get("recent_era") is not None and sp["recent_era"] >= 6:
            out.append([f"{nm} 선발 최근 부진", "warn"])
    for side in ("home", "away"):
        t = teams.get(side) or {}
        back = t.get("returning") or []
        if back:
            who = back[0]["name"] if len(back) == 1 else f"{back[0]['name']} 등 {len(back)}명"
            out.append([f"{names[side]} {who} 복귀", "good"])
        if t.get("grade") == "1군" and t.get("core_in") == t.get("size"):
            out.append([f"{names[side]} 풀전력", "good"])
    order = {"bad": 0, "warn": 1, "good": 2}
    out.sort(key=lambda s: order[s[1]])
    return out[:4]


# ---------------------------------------------------------------- 라인업 발표 전: 로테이션 가능성

def core_from_history(recent, size):
    """최근 경기 선발 기록만으로 주전(평소 베스트) 목록 — 라인업 발표 전에 쓴다."""
    return analyze_lineup([], recent, size).get("missing") or []


def _situation(gap_days, next_comp, rest_days):
    big = next_comp and any(k in next_comp for k in BIG_COMPS)
    if big and gap_days is not None and gap_days <= 4:
        return "big"
    if (gap_days is not None and gap_days <= 3) or (rest_days is not None and rest_days <= 3):
        return "short"
    return "normal"


SITUATION_TEXT = {"big": "큰 경기 앞", "short": "짧은 휴식", "normal": "평소"}


def rotation_risk(history, events, core, size, today_next, today_rest):
    """history: 이번 시즌 경기(최신순, starters·date·minutes) · events: 팀 전체 일정 [{"date", "comp", "completed"}]
    today_next: {"in_days", "comp"} · today_rest: 지난 경기 후 휴식일"""
    from datetime import datetime as _dt
    core_ids = {m["id"] for m in core}
    names = {m["id"]: m.get("name") for m in core}
    if not core_ids or not history:
        return None
    ev = sorted((e for e in events if e.get("date")), key=lambda e: e["date"])

    def parse(d):
        try:
            return _dt.fromisoformat(str(d).replace("Z", "+00:00"))
        except ValueError:
            return None
    buckets = {"big": [], "short": [], "normal": []}
    for i, m in enumerate(history):
        md = parse(m.get("date"))
        if not md or not m.get("starters"):
            continue
        changes = size - sum(1 for pid in m["starters"] if pid in core_ids)
        nxt = next((e for e in ev if parse(e["date"]) and (parse(e["date"]) - md).total_seconds() > 3600), None)
        gap = (parse(nxt["date"]).date() - md.date()).days if nxt else None
        prev = history[i + 1] if i + 1 < len(history) else None
        rest = (md.date() - parse(prev["date"]).date()).days if prev and parse(prev.get("date")) else None
        buckets[_situation(gap, (nxt or {}).get("comp"), rest)].append(changes)

    today = _situation((today_next or {}).get("in_days"), (today_next or {}).get("comp"), today_rest)
    rows = buckets[today]
    base = buckets["normal"]
    stat = lambda xs: {"n": len(xs), "avg": round(sum(xs) / len(xs), 1) if xs else None,
                       "rot": sum(1 for x in xs if x >= 3)}
    s_today, s_base = stat(rows), stat(base)
    level = None
    if s_today["n"] >= 3:
        if s_today["avg"] >= 3 or s_today["rot"] / s_today["n"] >= 0.5:
            level = "높음"
        elif s_today["avg"] >= 1.5:
            level = "보통"
        else:
            level = "낮음"
    # 최근 3경기에서 거의 풀타임 뛴 주전 (쉬게 할 후보)
    recent3 = history[:3]
    tired = []
    for pid in core_ids:
        mins = [((m.get("minutes") or {}).get(pid) or 0) for m in recent3]
        if len(recent3) == 3 and all(x >= 80 for x in mins):
            tired.append(names.get(pid) or pid)
    reason = ""
    if today_next and today_next.get("in_days") is not None:
        reason = f"다음 경기 {today_next['in_days']}일 뒤 {today_next.get('comp') or ''}".strip()
    if today_rest is not None:
        reason = (reason + " · " if reason else "") + f"지난 경기 후 {today_rest}일 휴식"
    return {"level": level, "situation": today, "situation_text": SITUATION_TEXT[today], "today": s_today,
            "base": s_base, "tired": sorted(tired)[:4], "reason": reason, "size": size}



# ---------------------------------------------------------------- 새 전력 계산 (백테스트 model.json: 득실차 + Elo + 리그별 홈 이점)

LEAGUE_OVER = {"eng.1": 55, "esp.1": 51, "fra.1": 54, "ger.1": 64, "ita.1": 47, "jpn.1": 50, "kleague": 48}   # 2.5골 이상 비율(%)


def _sg(x):
    x = max(-35.0, min(35.0, x))
    return 1 / (1 + math.exp(-x))


def model_probs(model, league, h, a):
    """h, a = {"gd": 경기당 득실차, "elo": Elo} → [원정 승, 무, 홈 승] 확률"""
    pw = model.get("power", 1.0)
    eta = 0.0
    for f in model["features"]:
        key = f.split(":", 1)[1]
        m, sd = model["zstats"][f]
        d = ((h[key] - m) - (a[key] - m)) / (sd or 1)
        d = math.copysign(abs(d) ** pw, d)
        eta += model["beta"][f] * d
    offs = model.get("league_home") or {}
    eta += offs.get(league, sum(offs.values()) / len(offs) if offs else 0.0)
    s1, s2 = _sg(model["cut1"] - eta), _sg(model["cut2"] - eta)
    return [s1, s2 - s1, 1 - s2]


def model_goals(model, league, h, a):
    """예상 골 (양 팀 득점·실점 기록 × 리그 평균)"""
    gp = (model.get("goals") or {})
    g = gp.get(league) or gp.get("_all") or {"mh": 1.45, "ma": 1.2, "L": 1.33}
    k = 6
    att = lambda t: (t["gf_pg"] * 10 + g["L"] * k) / (10 + k) / g["L"]
    dfn = lambda t: (t["ga_pg"] * 10 + g["L"] * k) / (10 + k) / g["L"]
    return g["mh"] * att(h) * dfn(a), g["ma"] * att(a) * dfn(h)


def model_power(model, league, h, a, home_name, away_name, teams=None):
    """전력 비교(A안): 기대 승점 비율 64 vs 36. 확률은 계산에만 쓰고 화면엔 비율로."""
    p = model_probs(model, league, h, a)
    share = max(1, min(99, round(100 * (p[2] + 0.5 * p[1]))))
    gap = share - 50
    fav = "home" if gap > 0 else "away"
    fname = home_name if fav == "home" else away_name
    verdict = "비슷한 전력" if abs(gap) < 5 else f"{fname} {'근소 ' if abs(gap) < 10 else ''}우위"
    note = ""
    if teams:
        rot = [(s, teams[s].get("grade")) for s in ("home", "away") if teams.get(s, {}).get("grade") in ("2군", "1.5군")]
        if rot:
            s, gr = rot[0]
            nm = home_name if s == "home" else away_name
            note = f"{nm} {gr} 출전 · 전력은 팀 체급 기준 (라인업은 아래 정보로)"
    lh, la = model_goals(model, league, h, a)
    offs = model.get("league_home") or {}
    edge = offs.get(league)
    return {"home": share, "away": 100 - share, "verdict": verdict, "fav": fav if abs(gap) >= 5 else None, "note": note,
            "mode": "model", "probs": [round(x, 4) for x in p], "confident": max(p) >= 0.65,
            "pick": ("away", "draw", "home")[max(range(3), key=lambda i: p[i])],
            "exp_goals": round(lh + la, 2), "exp_home": round(lh, 2), "exp_away": round(la, 2),
            "basis": {"gd": [round(h["gd"], 2), round(a["gd"], 2)], "elo": [round(h["elo"]), round(a["elo"])],
                      "home_edge": round(edge, 3) if edge is not None else None},
            "league_over": LEAGUE_OVER.get(league)}


def elo_table(matches, k=20, home=60, regress=1 / 3):
    """matches: [(날짜, 시즌, 홈 id, 원정 id, 홈 골, 원정 골)] → {팀: Elo}. 시즌이 바뀌면 평균 쪽으로 당김."""
    elo, season = {}, None
    for d, s, hid, aid, hg, ag in sorted(matches, key=lambda x: str(x[0])):
        if s != season and season is not None:
            elo = {t: 1500 + (e - 1500) * (1 - regress) for t, e in elo.items()}
        season = s
        eh, ea = elo.get(hid, 1500.0), elo.get(aid, 1500.0)
        exp = 1 / (1 + 10 ** (-(eh + home - ea) / 400))
        sc = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        elo[hid], elo[aid] = eh + k * (sc - exp), ea - k * (sc - exp)
    return elo


# ---------------------------------------------------------------- 장기 결장 후 복귀 선수

RETURN_SEASON_RATE = 0.4     # 시즌 선발 비율이 이만큼 넘고
RETURN_RECENT_MAX = 1        # 최근 경기 선발이 이 이하면 '복귀'로 본다
RETURN_MIN_SEASON = 8        # 시즌 경기가 이만큼은 있어야 판단


def mark_returning(result, recent, season, size):
    """장기 부상·결장으로 최근엔 못 나왔지만 시즌 내내 주전이던 선수가 오늘 선발이면 '복귀'로 보고 주전으로 센다.
    (그대로 두면 팀이 강해졌는데 등급이 내려가는 거꾸로 결과가 나온다)"""
    season = season or recent
    n = len(season or [])
    if n < RETURN_MIN_SEASON:
        return result
    s_starts, r_starts = {}, {}
    for m in season:
        for pid in m.get("starters") or []:
            s_starts[pid] = s_starts.get(pid, 0) + 1
    for m in recent or []:
        for pid in m.get("starters") or []:
            r_starts[pid] = r_starts.get(pid, 0) + 1
    back = []
    for p in result.get("players") or []:
        if p.get("core"):
            continue
        if s_starts.get(p["id"], 0) / n >= RETURN_SEASON_RATE and r_starts.get(p["id"], 0) <= RETURN_RECENT_MAX:
            p["core"] = True
            p["returning"] = True
            p["role"] = "주전"
            p["season_starts"] = s_starts.get(p["id"], 0)
            back.append(p)
    if not back:
        return result
    # 복귀 선수가 들어온 만큼, 최근에만 주전이던 선수(시즌 선발이 가장 적은 쪽)를 '빠진 주전'에서 뺀다
    miss = sorted(result.get("missing") or [], key=lambda m: s_starts.get(m["id"], 0))
    drop = {m["id"] for m in miss[:len(back)]}
    result["missing"] = [m for m in (result.get("missing") or []) if m["id"] not in drop]
    result["core_in"] = sum(1 for p in result["players"] if p.get("core"))
    result["returning"] = [{"id": p["id"], "name": p["name"], "season_starts": p.get("season_starts", 0), "of": n} for p in back]
    if result.get("grade") != "판단불가":
        result["grade"] = grade_label(result["core_in"], size)
    return result
