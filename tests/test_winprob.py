# -*- coding: utf-8 -*-
"""전력 → 실제 승률 보정 (종목별)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect  # noqa: E402
import grade  # noqa: E402

MODEL = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model.json"), encoding="utf-8"))


def test_모델에_종목별_보정값이_있다():
    c = MODEL["win_calib"]
    assert set(c) >= {"축구", "농구", "야구"}
    assert 0 < c["야구"]["lam"]["KBO"] < 1          # 피타고리안은 줄여야 맞는다
    assert 0 < c["야구"]["lam"]["MLB"] < 1
    assert c["농구"]["elo"]["k"] > 0


def test_농구_전력은_승률과_같은_숫자다():
    pw = grade.basketball_power(MODEL, {"gd": 6.0, "elo": 1600}, {"gd": -3.0, "elo": 1450}, "A", "B")
    assert pw["home"] + pw["away"] == 100
    assert pw["home"] == round(pw["win_home"] * 100)
    assert pw["mode"] == "nba"


def test_농구_전력차가_벌어지면_승률도_올라간다():
    weak = grade.basketball_power(MODEL, {"gd": 0.0, "elo": 1500}, {"gd": 0.0, "elo": 1500}, "A", "B")
    strong = grade.basketball_power(MODEL, {"gd": 8.0, "elo": 1650}, {"gd": -6.0, "elo": 1380}, "A", "B")
    assert 0.5 < weak["win_home"] < 0.62          # 홈 이점만
    assert strong["win_home"] > 0.80


def test_야구는_피타고리안보다_덜_치우친다():
    raw = 5.6 ** grade.PYTH_EXP / (5.6 ** grade.PYTH_EXP + 3.4 ** grade.PYTH_EXP)
    cal = grade.baseball_win_prob(5.6, 3.4, "KBO", MODEL)
    assert cal is not None
    assert 0.5 < cal < raw                        # 같은 방향이지만 과장을 덜어낸다
    assert grade.baseball_win_prob(5.6, 3.4, "MLB", MODEL) > cal   # KBO가 더 많이 줄인다


def test_야구_전력과_승률이_따로_나온다():
    side = lambda era, off: {"off_rpg": off, "sp": {"era": era, "fip": era, "ip_per_start": 5.5},
                             "pen_era": 4.5, "pen_3d": 2, "core_in": 9, "size": 9}
    pw = grade.baseball_power(side(2.9, 5.4), side(5.4, 4.2), "한화", "롯데", rpg=5.0, league="KBO", model=MODEL)
    assert pw["home"] > 60                        # 전력 비율은 크게 벌어지고
    assert pw["win_home"] < pw["home"] / 100      # 실제 승률은 그보다 낮다


def test_win_probs는_축구_승무패를_그대로_쓴다():
    pw = {"probs": [0.22, 0.26, 0.52]}            # [원정, 무, 홈]
    assert grade.win_probs(pw) == [0.52, 0.26, 0.22]


def test_win_probs는_승률만_있으면_무승부_0():
    assert grade.win_probs({"win_home": 0.64}) == [0.64, 0.0, 0.36]
    assert grade.win_probs({}) is None
    assert grade.win_probs(None) is None


def test_등급과_뒤집힐_확률():
    g = grade.win_grade([0.84, 0.0, 0.16], MODEL)
    assert g["label"] == "강력우세" and g["flip"] == 0.16 and g["fav"] == "home"
    g = grade.win_grade([0.46, 0.27, 0.27], MODEL)
    assert g["label"] == "초접전" and g["flip"] == 0.54      # 무승부도 '뒤집힘'으로 센다
    g = grade.win_grade([0.30, 0.25, 0.45], MODEL)
    assert g["fav"] == "away"


def test_attach_win이_power에_붙는다():
    pw = grade.attach_win({"home": 58, "away": 42, "win_home": 0.57}, MODEL)
    assert pw["win"] == [0.57, 0.0, 0.43]
    assert pw["grade_win"]["label"] == "약우세"
    assert grade.attach_win(None, MODEL) is None


def test_win_row는_퍼센트_정수로_내보낸다():
    pw = grade.attach_win({"home": 58, "away": 42, "win_home": 0.6199}, MODEL)
    row = collect.win_row(pw)
    assert row["win"] == [62, 0, 38]
    assert row["wgrade"] == "약우세" and row["flip"] == 38
    assert collect.win_row({}) == {}


def test_농구_Elo_상수는_축구와_다르다():
    assert collect._elo_params("basketball")["k"] == MODEL["win_calib"]["농구"]["elo"]["k"]
    assert collect._elo_params("basketball") != collect._elo_params("soccer")


# ---------------------------------------------------------------- 챔스·유로파 전용 모델

def test_대항전_모델이_model에_있다():
    c = MODEL["cup_model"]
    assert len(c["beta"]) == 2 and c["cut1"] < c["cut2"]
    assert c["fit"]["matches"] > 500


def test_대항전_모델은_확률_세_개를_낸다():
    p = grade.cup_probs(MODEL, {"gd": 1.2, "elo": 1800}, {"gd": 0.9, "elo": 1700})
    assert len(p) == 3
    assert abs(sum(p) - 1.0) < 1e-9
    assert all(0 < x < 1 for x in p)
    assert p[2] > p[0]                                  # 강한 홈팀이 더 유리


def test_대항전_모델은_무승부를_리그보다_낮게_본다():
    even = {"gd": 1.0, "elo": 1600}
    cup = grade.cup_probs(MODEL, even, dict(even))
    lg = grade.model_probs(MODEL, "eng.1", dict(even, gf_pg=1.5, ga_pg=1.2), dict(even, gf_pg=1.5, ga_pg=1.2))
    assert cup[1] < lg[1]                               # 실측 대항전 무승부율 18.8% vs 리그 약 25%


def test_대항전_모델은_리그_모델보다_덜_소심하다():
    h = {"gd": 1.5, "gf_pg": 2.2, "ga_pg": 0.9, "elo": 1880}
    a = {"gd": 0.9, "gf_pg": 1.9, "ga_pg": 1.1, "elo": 1700}
    lg = grade.model_power(MODEL, "uefa.champions", h, a, "A", "B")
    cup = grade.model_power(MODEL, "uefa.champions", h, a, "A", "B", cup=True)
    assert cup["model_used"] == "cup" and lg["model_used"] == "league"
    assert max(cup["probs"]) > max(lg["probs"])         # 같은 전력 차를 더 또렷하게 본다


def test_대항전_모델이_없으면_리그_모델로_돌아간다():
    m = dict(MODEL); m.pop("cup_model", None)
    h = {"gd": 1.0, "gf_pg": 1.8, "ga_pg": 1.0, "elo": 1700}
    a = {"gd": 0.5, "gf_pg": 1.6, "ga_pg": 1.2, "elo": 1600}
    pw = grade.model_power(m, "uefa.champions", h, a, "A", "B", cup=True)
    assert pw["model_used"] == "league"
    assert grade.cup_probs(m, h, a) is None


def test_유럽_통합_Elo는_전용_눈금을_쓴다():
    assert collect._elo_params("euro")["k"] == MODEL["euro_elo_rule"]["k"]
    assert collect._elo_params("euro") != collect._elo_params("soccer")
    assert MODEL["elo"]["k"] == 20                      # 리그별 Elo는 예전 눈금 유지 (beta·zstats가 거기 맞춰져 있다)


def test_통합_Elo_표에_독주_보정이_들어있다():
    """보정은 '리그 평균 쪽으로 당기기'다. 대항전 경험이 적을수록 세게 당겨진다."""
    import json as _j, os as _os
    p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "euro_elo.json")
    d = _j.load(open(p, encoding="utf-8"))
    assert 0 < d["dominance_shrink"]["lam"] < 1         # 0이면 리그 안 순서가 뭉개진다
    t, mean = d["teams"], d["league_elo"]
    moved = [v for v in t.values() if v["league"] in mean and abs(v["elo_raw"] - mean[v["league"]]) > 30]
    assert len(moved) > 50
    for v in moved:                                     # 항상 평균 쪽으로만 움직인다
        mu = mean[v["league"]]
        assert min(mu, v["elo_raw"]) - 0.5 <= v["elo"] <= max(mu, v["elo_raw"]) + 0.5
    # 리그 안에서 격차가 큰 팀일수록 많이 당겨진다
    por = [v for v in t.values() if v["league"] == "por.1"]
    top = max(por, key=lambda v: v["elo_raw"])
    assert top["elo"] < top["elo_raw"]                  # 포르투갈 독주팀은 눌린다
    assert abs(top["bonus"]) > 50


# ---------------------------------------------------------------- 보관 폴더가 ARCHIVE_DIR을 따라가나

def test_상세_보관은_ARCHIVE_DIR을_따라간다():
    """테스트가 ARCHIVE_DIR만 임시 폴더로 바꿔도 진짜 저장소에 쓰면 안 된다."""
    import tempfile
    real = collect.ARCHIVE_DIR
    try:
        tmp = tempfile.mkdtemp()
        collect.ARCHIVE_DIR = os.path.join(tmp, "archive")
        assert collect.detail_archive() == os.path.join(tmp, "archive", "details")
        game = {"key": "축구:esp.1:9", "sport": "축구", "league": "라리가", "league_slug": "esp.1",
                "start_kst": "2026-09-23T22:38:00+09:00", "state": "post", "national": False,
                "home": {"name": "레알", "score": 2}, "away": {"name": "헤타페", "score": 1}}
        assert collect.keep_detail(game, {"lineup_ready": True, "teams": {}}) is True
        assert os.path.exists(os.path.join(tmp, "archive", "details", "2026-09",
                                           collect.game_filename(game["key"])))
    finally:
        collect.ARCHIVE_DIR = real
    assert collect.detail_archive() == os.path.join(real, "details")
