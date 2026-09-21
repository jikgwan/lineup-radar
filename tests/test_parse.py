import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parse  # noqa: E402
from grade import analyze_lineup  # noqa: E402

SCOREBOARD = {
    "events": [
        {
            "id": "401879285",
            "date": "2026-09-18T14:00Z",
            "competitions": [
                {
                    "status": {"type": {"state": "pre"}},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {
                                "id": "349",
                                "displayName": "AFC Bournemouth",
                                "shortDisplayName": "Bournemouth",
                                "logo": "https://x/349.png",
                            },
                        },
                        {
                            "homeAway": "away",
                            "team": {"id": "337", "displayName": "Brentford", "shortDisplayName": "Brentford"},
                        },
                    ],
                }
            ],
        },
        {"id": "broken", "competitions": []},
    ]
}

SUMMARY = {
    "rosters": [
        {
            "homeAway": "home",
            "formation": "4-2-3-1",
            "team": {"id": "349", "displayName": "AFC Bournemouth"},
            "roster": [
                {"starter": True, "jersey": "1", "athlete": {"id": "1", "displayName": "GK One"},
                 "position": {"abbreviation": "G"}},
                {"starter": True, "jersey": "5", "athlete": {"id": "2", "displayName": "DF Two"},
                 "position": {"abbreviation": "D"}},
                {"starter": False, "subbedIn": True, "athlete": {"id": "3", "displayName": "Sub Three"},
                 "position": {"abbreviation": "M"}},
                {"starter": False, "subbedIn": False, "athlete": {"id": "4", "displayName": "Bench Four"}},
            ],
        },
        {
            "homeAway": "away",
            "team": {"id": "337", "displayName": "Brentford"},
            "roster": [
                {"starter": True, "athlete": {"id": "11", "displayName": "Away One"},
                 "position": {"abbreviation": "G"}}
            ],
        },
    ]
}


def test_scoreboard_parsing_and_kst():
    games = parse.parse_espn_scoreboard(SCOREBOARD, "축구", "eng.1", "EPL")
    assert len(games) == 1  # 형식이 깨진 경기는 건너뛴다
    g = games[0]
    assert g["key"] == "축구:eng.1:401879285"
    assert g["home"]["name"] == "AFC Bournemouth"
    assert g["away"]["short"] == "Brentford"
    assert g["state"] == "pre"
    assert g["start_kst"].startswith("2026-09-18T23:00:00")  # 14:00Z -> 23:00 KST


def test_soccer_lineup_only_starters():
    sides = parse.parse_soccer_lineup(SUMMARY)
    assert [p["id"] for p in sides["home"]["lineup"]] == ["1", "2"]
    assert sides["home"]["formation"] == "4-2-3-1"
    assert sides["home"]["lineup"][0]["pos"] == "G"
    assert len(sides["away"]["lineup"]) == 1


def test_soccer_history_counts_subs_as_appearance():
    rec = parse.parse_soccer_match_for_team(SUMMARY, "349")
    assert rec["starters"] == ["1", "2"]
    assert sorted(rec["played"]) == ["1", "2", "3"]  # 미출전 벤치(4)는 제외
    assert rec["names"]["3"] == "Sub Three"


def test_soccer_history_unknown_team_returns_none():
    assert parse.parse_soccer_match_for_team(SUMMARY, "999") is None


def test_subbed_in_as_object():
    summary = {
        "rosters": [
            {
                "homeAway": "home",
                "team": {"id": "1"},
                "roster": [
                    {"starter": False, "subbedIn": {"didSub": True}, "athlete": {"id": "9", "displayName": "X"}},
                    {"starter": False, "subbedIn": {"didSub": False}, "athlete": {"id": "8", "displayName": "Y"}},
                    {"starter": False, "appearances": 1, "athlete": {"id": "7", "displayName": "Z"}},
                ],
            }
        ]
    }
    rec = parse.parse_soccer_match_for_team(summary, "1")
    assert sorted(rec["played"]) == ["7", "9"]


def test_empty_or_broken_payloads():
    assert parse.parse_espn_scoreboard({}, "축구", "eng.1", "EPL") == []
    assert parse.parse_espn_scoreboard(None, "축구", "eng.1", "EPL") == []
    assert parse.parse_soccer_lineup({}) == {}
    assert parse.parse_soccer_lineup({"rosters": [{}]})["home"]["lineup"] == []
    assert parse.parse_soccer_match_for_team({}, "1") is None
    assert parse.to_kst("") is None and parse.to_kst("쓰레기") is None


BOX = {
    "header": {
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"id": "13", "displayName": "Lakers"}},
                    {"homeAway": "away", "team": {"id": "2", "displayName": "Celtics"}},
                ]
            }
        ]
    },
    "boxscore": {
        "players": [
            {
                "team": {"id": "13"},
                "statistics": [
                    {
                        "athletes": [
                            {"starter": True, "athlete": {"id": "a1", "displayName": "Star A"}},
                            {"starter": True, "athlete": {"id": "a2", "displayName": "Star B"}},
                            {"starter": False, "athlete": {"id": "a3", "displayName": "Bench C"}},
                            {"starter": False, "didNotPlay": True, "athlete": {"id": "a4", "displayName": "DNP D"}},
                        ]
                    }
                ],
            },
            {
                "team": {"id": "2"},
                "statistics": [{"athletes": [{"starter": True, "athlete": {"id": "b1", "displayName": "Away A"}}]}],
            },
        ]
    },
}


def test_basketball_box_and_lineup():
    rec = parse.parse_basketball_box_for_team(BOX, "13")
    assert rec["starters"] == ["a1", "a2"]
    assert rec["played"] == ["a1", "a2", "a3"]  # 결장(DNP)은 빠진다
    sides = parse.parse_basketball_lineup(BOX)
    assert sides["home"]["team_name"] == "Lakers"
    assert [p["name"] for p in sides["home"]["lineup"]] == ["Star A", "Star B"]
    assert sides["away"]["lineup"][0]["id"] == "b1"


MLB_SCHEDULE = {
    "dates": [
        {
            "games": [
                {
                    "gamePk": 778123,
                    "gameDate": "2026-09-18T00:05:00Z",
                    "status": {"abstractGameState": "Preview"},
                    "teams": {
                        "home": {
                            "team": {"id": 147, "name": "New York Yankees", "teamName": "Yankees"},
                            "probablePitcher": {"id": 543037, "fullName": "Gerrit Cole"},
                        },
                        "away": {"team": {"id": 111, "name": "Boston Red Sox", "teamName": "Red Sox"}},
                    },
                    "lineups": {
                        "homePlayers": [
                            {"id": 1, "fullName": "Batter One", "primaryPosition": {"abbreviation": "CF"}},
                            {"id": 2, "fullName": "Batter Two", "primaryPosition": {"abbreviation": "SS"}},
                        ],
                        "awayPlayers": [],
                    },
                }
            ]
        }
    ]
}


def test_mlb_schedule_parsing():
    games = parse.parse_mlb_schedule(MLB_SCHEDULE)
    g = games[0]
    assert g["sport"] == "야구" and g["league"] == "MLB"
    assert g["home"]["name"] == "New York Yankees"
    assert g["start_kst"].startswith("2026-09-18T09:05:00")  # 00:05Z -> 09:05 KST
    assert g["state"] == "pre"
    assert g["probables"]["home"]["name"] == "Gerrit Cole"
    assert g["probables"]["away"] is None
    assert g["lineups"]["home"][0]["pos"] == "CF"
    assert g["lineups"]["away"] == []


PEOPLE = {
    "people": [
        {
            "id": 1,
            "fullName": "Regular Guy",
            "stats": [
                {
                    "group": {"displayName": "hitting"},
                    "type": {"displayName": "season"},
                    "splits": [{"stat": {"plateAppearances": 500, "gamesPlayed": 120}}],
                }
            ],
        },
        {
            "id": 2,
            "fullName": "Backup Guy",
            "stats": [
                {
                    "group": {"displayName": "hitting"},
                    "type": {"displayName": "season"},
                    "splits": [{"stat": {"plateAppearances": 90, "gamesPlayed": 40}}],
                }
            ],
        },
        {"id": 3, "fullName": "No Stats"},
    ]
}


def test_mlb_regular_detection():
    info = parse.mlb_history_from_stats(PEOPLE["people"], team_games=140)
    assert info["regulars"] == ["1"]  # 500/140 = 3.6 타석 >= 2.2
    assert info["names"]["2"] == "Backup Guy"
    assert "3" not in info["regulars"]


def test_mlb_flows_into_grade():
    info = parse.mlb_history_from_stats(PEOPLE["people"], team_games=140)
    regulars = info["regulars"] + [f"r{i}" for i in range(8)]
    names = dict(info["names"], **{f"r{i}": f"R{i}" for i in range(8)})
    hist = parse.synth_history(regulars, names, matches=10, extra_ids=["2"])
    lineup = [{"id": pid, "name": names.get(pid, "")} for pid in regulars]
    out = analyze_lineup(lineup, hist, 9)
    assert out["grade"] == "1군" and out["core_in"] == 9


def test_mlb_season_stat_handles_missing():
    assert parse.mlb_season_stat({}) == {}
    assert parse.mlb_season_stat({"stats": [{"splits": []}]}) == {}


if __name__ == "__main__":
    count = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            count += 1
            print(f"  ok  {name}")
    print(f"\n{count} passed")
