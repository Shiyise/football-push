import json
from datetime import datetime, timedelta, timezone

import httpx


ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

LEAGUES = {
    "EPL": {
        "espn": "eng.1",
        "name": "英格兰足球超级联赛",
        "short": "英超",
        "color": "#3d8f5f",
    },
    "BL1": {
        "espn": "ger.1",
        "name": "德国足球甲级联赛",
        "short": "德甲",
        "color": "#ff7a3c",
    },
}


def _team_from_espn(team, league_code):
    tid = str(team.get("id", ""))
    logo = team.get("logo") or f"https://a.espncdn.com/i/teamlogos/soccer/500/{tid}.png"
    return {
        "id": tid,
        "name": team.get("displayName") or team.get("name") or "",
        "short": team.get("shortDisplayName") or team.get("abbreviation") or team.get("name") or "",
        "logo": logo,
        "league": league_code,
    }


def _competitor(competition, home_away):
    competitors = competition.get("competitors") or []
    for comp in competitors:
        if comp.get("homeAway") == home_away:
            return comp
    if competitors:
        return competitors[0 if home_away == "home" else -1]
    return {}


def _score(competitor):
    value = competitor.get("score")
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def match_from_event(event, league_code):
    league = LEAGUES[league_code]
    competition = (event.get("competitions") or [{}])[0]
    status_type = competition.get("status", {}).get("type", {})
    state = status_type.get("state", "pre")
    if state == "post":
        status = "finished"
    elif state == "in":
        status = "live"
    else:
        status = "scheduled"

    home_c = _competitor(competition, "home")
    away_c = _competitor(competition, "away")
    home_t = home_c.get("team") or {}
    away_t = away_c.get("team") or {}
    if not home_t or not away_t:
        return None

    minute = None
    if status == "live":
        try:
            minute = int(float(competition.get("clock") or 0))
        except (TypeError, ValueError):
            minute = None

    broadcasts = competition.get("broadcasts") or []
    broadcast = ""
    if broadcasts:
        names = broadcasts[0].get("names") or []
        if names:
            broadcast = names[0]

    links = event.get("links") or []
    link = links[0].get("href", "") if links else ""
    detail = status_type.get("detail") or status_type.get("shortDetail") or ""
    if status == "live" and minute is not None:
        detail = f"进行中 {minute}'"

    return {
        "id": str(event.get("id", "")),
        "league": league_code,
        "league_name": league["name"],
        "league_short": league["short"],
        "home": _team_from_espn(home_t, league_code),
        "away": _team_from_espn(away_t, league_code),
        "time": event.get("date", ""),
        "status": status,
        "detail": detail,
        "minute": minute if status == "live" else None,
        "home_score": _score(home_c),
        "away_score": _score(away_c),
        "venue": competition.get("venue", {}).get("fullName", ""),
        "broadcast": broadcast,
        "link": link,
    }


async def fetch_espn_matches(client, league_code, start, end):
    league = LEAGUES[league_code]
    params = {"dates": f"{start:%Y%m%d}-{end:%Y%m%d}"}
    resp = await client.get(f"{ESPN_BASE}/{league['espn']}/scoreboard", params=params)
    resp.raise_for_status()
    data = resp.json()
    out = []
    for event in data.get("events") or []:
        match = match_from_event(event, league_code)
        if match:
            out.append(match)
    return out


async def fetch_espn_teams(client, league_code):
    league = LEAGUES[league_code]
    resp = await client.get(f"{ESPN_BASE}/{league['espn']}/teams")
    resp.raise_for_status()
    data = resp.json()
    items = data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams") or []
    return [_team_from_espn(item.get("team") or {}, league_code) for item in items if item.get("team")]


async def fetch_all(source, client=None):
    if source == "mock":
        return mock_matches(), mock_teams()

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=2)
    end = now + timedelta(days=13)
    if client is None:
        client = httpx.AsyncClient(headers={"User-Agent": "football-push/1.0"}, timeout=30)
        should_close = True
    else:
        should_close = False

    try:
        matches = []
        teams = []
        for code in LEAGUES:
            matches.extend(await fetch_espn_matches(client, code, start, end))
            try:
                teams.extend(await fetch_espn_teams(client, code))
            except Exception:
                # 球队名单拿不到时先用赛程里出现的球队顶住
                seen = set()
                for m in matches:
                    if m["league"] != code:
                        continue
                    for side in ("home", "away"):
                        t = m[side]
                        if t["id"] not in seen:
                            seen.add(t["id"])
                            teams.append(t)
        return matches, teams
    finally:
        if should_close:
            await client.aclose()


MOCK_TEAMS = [
    {"id": "359", "name": "Arsenal", "short": "ARS", "league": "EPL"},
    {"id": "364", "name": "Liverpool", "short": "LIV", "league": "EPL"},
    {"id": "382", "name": "Manchester City", "short": "MCI", "league": "EPL"},
    {"id": "367", "name": "Tottenham Hotspur", "short": "TOT", "league": "EPL"},
    {"id": "363", "name": "Chelsea", "short": "CHE", "league": "EPL"},
    {"id": "2235", "name": "Coventry City", "short": "COV", "league": "EPL"},
    {"id": "132", "name": "Bayern Munich", "short": "MUN", "league": "BL1"},
    {"id": "124", "name": "Borussia Dortmund", "short": "DOR", "league": "BL1"},
    {"id": "131", "name": "Bayer Leverkusen", "short": "B04", "league": "BL1"},
    {"id": "133", "name": "RB Leipzig", "short": "RBL", "league": "BL1"},
    {"id": "598", "name": "1. FC Union Berlin", "short": "FCU", "league": "BL1"},
    {"id": "167", "name": "VfB Stuttgart", "short": "STU", "league": "BL1"},
]


def mock_teams():
    out = []
    for t in MOCK_TEAMS:
        item = dict(t)
        item["logo"] = f"https://a.espncdn.com/i/teamlogos/soccer/500/{t['id']}.png"
        out.append(item)
    return out


def mock_matches():
    now = datetime.now(timezone.utc)

    def at(days, hour):
        return (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

    def match(mid, code, home, away, days, hour, status, hs=0, as_=0, minute=None, venue="", broadcast=""):
        league = LEAGUES[code]
        teams = {t["id"]: t for t in mock_teams()}
        detail = {
            "scheduled": "未开赛",
            "live": f"进行中 {minute}'",
            "finished": "已完赛",
        }[status]
        return {
            "id": mid,
            "league": code,
            "league_name": league["name"],
            "league_short": league["short"],
            "home": teams[home],
            "away": teams[away],
            "time": at(days, hour),
            "status": status,
            "detail": detail,
            "minute": minute if status == "live" else None,
            "home_score": hs,
            "away_score": as_,
            "venue": venue,
            "broadcast": broadcast,
            "link": "",
        }

    return [
        match("m1", "EPL", "359", "367", -1, 18, "finished", 2, 1, venue="Emirates Stadium", broadcast="Sky Sports"),
        match("m2", "EPL", "364", "382", 0, 20, "live", 1, 1, minute=67, venue="Anfield", broadcast="TNT Sports"),
        match("m3", "EPL", "363", "2235", 2, 14, "scheduled", venue="Stamford Bridge", broadcast="Sky Sports"),
        match("m4", "EPL", "359", "364", 5, 19, "scheduled", venue="Emirates Stadium", broadcast="BBC"),
        match("m5", "BL1", "132", "131", 3, 18, "scheduled", venue="Allianz Arena", broadcast="DAZN"),
        match("m6", "BL1", "124", "133", 4, 16, "scheduled", venue="Signal Iduna Park", broadcast="Sky Sports DE"),
        match("m7", "BL1", "598", "167", 6, 18, "scheduled", venue="Stadion An der Alten Försterei", broadcast="DAZN"),
    ]


def dump_mock(path):
    payload = {"matches": mock_matches(), "teams": mock_teams()}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
