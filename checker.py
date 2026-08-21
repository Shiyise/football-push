# -*- coding: utf-8 -*-
"""GitHub Actions 定时检查器：拉取英超/德甲赛程，生成静态赛程页，并把变化推到手机。"""
import asyncio
import html
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import sources

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT = DATA_DIR / "snapshot.json"
SITE = BASE_DIR / "index.html"
CONFIG = BASE_DIR / "config.json"
HK = timezone(timedelta(hours=8))
REMINDER_HOURS = 12


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def env_bool(name, default=False):
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val not in ("0", "false", "no", "off")


def split_env(name):
    raw = os.environ.get(name, "") or ""
    out = []
    for part in raw.replace(",", " ").split():
        part = part.strip()
        if part:
            out.append(part)
    return out


def _fmt_hk(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(HK)
    except ValueError:
        return iso
    return f"{dt.month}月{dt.day}日 {dt.strftime('%H:%M')}"


def _line(match):
    return f"{match['home']['name']} vs {match['away']['name']}"


def _score_line(match):
    return f"{match['home']['name']} {match['home_score']}:{match['away_score']} {match['away']['name']}"


def _followed_names(match, followed):
    names = []
    for key in ("home", "away"):
        team = match[key]
        if team["id"] in followed:
            names.append(team["name"])
    return names


def _reminder_event(match, followed):
    if match.get("status") != "scheduled" or not _followed_names(match, followed):
        return None
    try:
        dt = datetime.fromisoformat(match["time"].replace("Z", "+00:00")).astimezone(HK)
    except (ValueError, TypeError):
        return None
    remaining = dt - datetime.now(HK)
    if remaining <= timedelta(0) or remaining > timedelta(hours=REMINDER_HOURS):
        return None
    body = f"{_line(match)}\n{match['league_name']} · {_fmt_hk(match['time'])}"
    note = " · ".join(x for x in (match.get("venue"), match.get("broadcast")) if x)
    if note:
        body += f"\n{note}"
    key = f"{match['league']}|{match['id']}"
    return ("reminder", f"开赛提醒 {match['league_short']}", body, {"match": match, "key": key})


def _already_reminded(notifications, key):
    return any(n.get("kind") == "reminder" and n.get("key") == key for n in notifications)


def _result_note(match, followed):
    names = _followed_names(match, followed)
    if len(names) != 1:
        return ""
    team_id = match["home"]["id"] if names[0] == match["home"]["name"] else match["away"]["id"]
    hs, as_ = match["home_score"], match["away_score"]
    if hs == as_:
        word = "平"
    elif (team_id == match["home"]["id"] and hs > as_) or (team_id == match["away"]["id"] and as_ > hs):
        word = "胜"
    else:
        word = "负"
    return f"\n关注球队 {names[0]}：{word}"


def _events_for(old, new, followed):
    """返回 (kind, title, body, payload) 列表；只看关注球队。"""
    if not _followed_names(new, followed):
        return []
    score_line = _score_line(new)
    if old["status"] == "scheduled" and new["status"] == "live":
        minute = f"第{new['minute']}分钟" if new.get("minute") else "已开赛"
        return [("live", f"比赛开始 {new['league_short']}", f"{score_line}\n{minute}", {"match": new})]
    if old["status"] == "live" and new["status"] == "live":
        if old["home_score"] != new["home_score"] or old["away_score"] != new["away_score"]:
            minute = f"第{new['minute']}分钟" if new.get("minute") else "比分更新"
            return [("score", f"比分变化 {new['league_short']}", f"{score_line}\n{minute}", {"match": new})]
    if old["status"] != "finished" and new["status"] == "finished":
        return [("result", f"完场 {new['league_short']}", score_line + _result_note(new, followed), {"match": new})]
    if old["status"] == "live" and new["status"] == "scheduled":
        return [("update", f"比赛延期 {new['league_short']}", f"{_line(new)}\n赛程状态有变化，等待官方确认", {"match": new})]
    if old["status"] == "scheduled" and new["status"] == "scheduled" and old["time"] != new["time"]:
        old_time = _fmt_hk(old["time"])
        return [("time", f"开球时间调整 {new['league_short']}", f"{_line(new)}\n{old_time} → {_fmt_hk(new['time'])}", {"match": new})]
    return []


def _new_match_events(new, followed):
    if not _followed_names(new, followed):
        return []
    try:
        dt = datetime.fromisoformat(new["time"].replace("Z", "+00:00")).astimezone(HK)
        soon = dt - datetime.now(HK) <= timedelta(hours=24)
    except (ValueError, TypeError):
        soon = False
    if not soon:
        return []
    body = f"{new['league_name']} · {_fmt_hk(new['time'])}"
    note = " · ".join(x for x in (new.get("venue"), new.get("broadcast")) if x)
    if note:
        body += f"\n{note}"
    if new["status"] == "live":
        return [("live", f"比赛开始 {new['league_short']}", f"{_score_line(new)}\n已开赛", {"match": new})]
    if new["status"] == "finished":
        return [("result", f"完场 {new['league_short']}", _score_line(new) + _result_note(new, followed), {"match": new})]
    return [("new", f"新赛程 {new['league_short']}", f"{_line(new)}\n{body}", {"match": new})]


async def _send_ntfy(client, topic, title, body):
    url = topic if topic.startswith("http://") or topic.startswith("https://") else f"https://ntfy.sh/{topic}"
    try:
        resp = await client.post(url, content=body, headers={"Title": title, "Tags": "soccer"})
        return {"ok": resp.status_code < 300, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def _send_bark(client, key, title, body):
    from urllib.parse import quote

    base = key if key.startswith("http://") or key.startswith("https://") else f"https://api.day.app/{key}"
    url = f"{base.rstrip('/')}/{quote(title)}/{quote(body)}"
    try:
        resp = await client.get(url, params={"group": "football-push"})
        return {"ok": resp.status_code < 400, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def _send_webhook(client, url, title, body, event_id):
    try:
        resp = await client.post(url, json={"id": event_id, "title": title, "body": body})
        return {"ok": resp.status_code < 400, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def send_pushes(events, ntfy_topics, bark_keys, webhook_urls):
    results = []
    async with httpx.AsyncClient(timeout=20) as client:
        for kind, title, body, payload in events:
            event_id = f"gh-{int(time.time() * 1000)}-{kind}"
            for topic in ntfy_topics:
                results.append({"event": title, "channel": "ntfy", **await _send_ntfy(client, topic, title, body)})
            for key in bark_keys:
                results.append({"event": title, "channel": "Bark", **await _send_bark(client, key, title, body)})
            for url in webhook_urls:
                results.append({"event": title, "channel": "Webhook", **await _send_webhook(client, url, title, body, event_id)})
    return results


def _site_html(matches, teams, followed, notifications):
    payload = {
        "matches": matches,
        "teams": teams,
        "followed": followed,
        "notifications": notifications,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    inline = html.escape(json.dumps(payload, ensure_ascii=False), quote=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>球讯哨 · 英超德甲赛程</title>
<style>
:root {{ color-scheme: dark; --bg:#121714; --panel:#1b221e; --line:#2b352f; --text:#e8efe9; --muted:#93a39a; --epl:#3d8f5f; --bl:#ff7a3c; --live:#ff5d5d; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
header {{ padding:18px 16px 10px; border-bottom:1px solid var(--line); background:#151b18; display:flex; justify-content:space-between; align-items:center; gap:12px; }}
h1 {{ font-size:18px; margin:0; }}
header p {{ margin:2px 0 0; font-size:12px; color:var(--muted); }}
.tabs {{ display:flex; gap:8px; padding:12px 16px; overflow-x:auto; }}
.tabs button {{ flex:0 0 auto; border:1px solid var(--line); background:var(--panel); color:var(--text); padding:7px 14px; border-radius:8px; font-size:13px; cursor:pointer; }}
.tabs button.active {{ background:#34543f; border-color:#4c7a5b; }}
main {{ max-width:760px; margin:0 auto; padding:6px 12px 40px; }}
.match {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:10px 0; }}
.top {{ display:flex; justify-content:space-between; align-items:center; gap:8px; font-size:12px; color:var(--muted); }}
.badge {{ border-radius:999px; padding:2px 8px; font-size:11px; }}
.badge.epl {{ background:#1d3a2a; color:#7fd6a2; }}
.badge.bl {{ background:#3a2418; color:#ffb07a; }}
.status {{ padding:2px 8px; border-radius:999px; }}
.status.live {{ background:#3a1a1a; color:var(--live); }}
.status.done {{ background:#202723; color:var(--muted); }}
.status.scheduled {{ background:#222a25; color:#9fc2ad; }}
.teams {{ display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:8px; margin:10px 0 4px; }}
.team {{ font-size:15px; font-weight:600; overflow-wrap:anywhere; }}
.team.home {{ text-align:right; }}
.team.away {{ text-align:left; }}
.score {{ font-weight:800; font-size:17px; text-align:center; min-width:44px; }}
.meta {{ font-size:12px; color:var(--muted); }}
.slot {{ display:block; color:#88998f; }}
.notify {{ display:inline-block; background:#23351f; color:#b8e2a8; border:1px solid #3a5a34; border-radius:999px; padding:2px 8px; font-size:11px; }}
.news {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 14px; margin:14px 0; }}
.news h2 {{ font-size:14px; margin:0 0 8px; }}
.news li {{ font-size:13px; color:var(--muted); margin:6px 0; list-style:none; }}
footer {{ text-align:center; color:#5e6d64; font-size:12px; padding:20px; }}
</style>
</head>
<body>
<header><div><h1>球讯哨 · 英超德甲赛程</h1><p>最后更新 <span id="updated"></span> · 数据来自 ESPN 免费接口</p></div></header>
<div class="tabs" id="tabs"></div>
<main id="list"></main>
<div class="news"><h2>最近推送</h2><ul id="news"></ul></div>
<footer>关注球队会推送到手机通知栏；本页在 GitHub 上每 30 分钟自动刷新</footer>
<script id="payload" type="application/json">{inline}</script>
<script>
const data = JSON.parse(document.getElementById("payload").textContent);
const followed = new Set(data.followed);
const order = {{live:0, scheduled:1, finished:2}};
document.getElementById("updated").textContent = data.updated;
let filter = "all";
const tabs = [["all","全部"],["EPL","英超"],["BL1","德甲"],["followed","我关注的"]];
const tabBox = document.getElementById("tabs");
for (const [key,label] of tabs) {{
  const b = document.createElement("button");
  b.textContent = label;
  b.onclick = () => {{ filter = key; render(); }};
  tabBox.appendChild(b);
}}
function esc(s) {{ return String(s ?? "").replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c])); }}
function render() {{
  const matches = data.matches
    .filter(m => filter === "all" || (filter === "followed" ? (followed.has(m.home.id) || followed.has(m.away.id)) : m.league === filter))
    .sort((a,b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || String(a.time).localeCompare(String(b.time)));
  const box = document.getElementById("list");
  box.innerHTML = matches.map(m => {{
    const isFollowed = followed.has(m.home.id) || followed.has(m.away.id);
    const score = m.status === "scheduled" ? "vs" : esc(m.home_score) + ":" + esc(m.away_score);
    const statusText = m.status === "live" ? "进行中" : m.status === "finished" ? "完场" : "未开赛";
    return `<div class="match"><div class="top"><span class="badge ${{m.league.toLowerCase()}}">${{esc(m.league_short)}}</span><span class="status ${{m.status}}">${{statusText}}</span><span>${{esc(m.detail || "")}}</span></div><div class="teams"><div class="team home">${{esc(m.home.name)}}</div><div class="score">${{score}}</div><div class="team away">${{esc(m.away.name)}}</div></div><div class="meta"><span class="slot">${{esc(m.venue || "")}}</span>${{m.broadcast ? " · " + esc(m.broadcast) : ""}}${{isFollowed ? ' <span class="notify">已关注 推送中</span>' : ""}}</div></div>`;
  }}).join("") || '<div class="match"><div class="meta">暂无比赛</div></div>';
}}
function renderNews() {{
  const box = document.getElementById("news");
  box.innerHTML = data.notifications.slice(0, 12).map(n => `<li>${{esc(n.time)}} · ${{esc(n.title)}}：${{esc(n.body)}}</li>`).join("") || "<li>还没有推送记录</li>";
}}
document.querySelectorAll(".tabs button")[0].classList.add("active");
render(); renderNews();
</script>
</body>
</html>
"""


def build_site(matches, teams, followed, notifications, site_file):
    site_file.write_text(_site_html(matches, teams, followed, notifications), encoding="utf-8")


async def run(source="espn", snapshots=None, send=False, site_file=None, config_file=None):
    config = load_json(config_file or CONFIG, {})
    followed_defs = config.get("followed_teams", [])
    followed_ids = {str(t.get("id", "")) for t in followed_defs if t.get("id")}
    ntfy_topics = split_env("NTFY_TOPICS") or [t for t in config.get("ntfy_topics", []) if t]
    bark_keys = split_env("BARK_KEYS") or [k for k in config.get("bark_keys", []) if k]
    webhook_urls = split_env("WEBHOOK_URLS") or [u for u in config.get("webhook_urls", []) if u]

    matches, teams = await sources.fetch_all(source)
    by_key = {}
    for m in matches:
        by_key[f"{m['league']}|{m['id']}"] = m

    old = {k: v for k, v in ((snapshots or {}).get("matches") or {}).items()}
    notifications = list((snapshots or {}).get("notifications", []))
    events = []
    if old and not env_bool("FIRST_RUN", False):
        for key, new in by_key.items():
            event = _reminder_event(new, followed_ids)
            if event and not _already_reminded(notifications, key):
                events.append(event)
    else:
        old = {}

    new_notifications = []
    for kind, title, body, payload in events:
        key = payload.get("key", "") if payload and kind == "reminder" else ""
        new_notifications.append({"time": _fmt_hk(datetime.now(timezone.utc).isoformat()), "kind": kind, "title": title, "body": body, "key": key})
    notifications = new_notifications + notifications
    notifications = notifications[:50]

    results = []
    if send and events:
        results = await send_pushes(events, ntfy_topics, bark_keys, webhook_urls)
    elif events:
        for kind, title, body, _ in events:
            results.append({"event": title, "channel": "dry-run (未发送)", "ok": True})

    next_snapshot = {
        "matches": by_key,
        "teams": teams,
        "followed": sorted(followed_ids),
        "notifications": notifications,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    if site_file is not None:
        site_file.parent.mkdir(parents=True, exist_ok=True)
        build_site(matches, teams, sorted(followed_ids), notifications, site_file)
    if snapshots_path := os.environ.get("SNAPSHOT_FILE"):
        Path(snapshots_path).parent.mkdir(parents=True, exist_ok=True)
        Path(snapshots_path).write_text(json.dumps(next_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    elif snapshots is not None:
        for key in list(snapshots.keys()):
            snapshots.pop(key, None)
        snapshots.update(next_snapshot)

    return {
        "matches": len(matches),
        "teams": len(teams),
        "events": len(events),
        "results": results,
        "notifications": len(notifications),
    }


async def main():
    source = os.environ.get("SOURCE", "espn")
    send = env_bool("SEND_PUSH", False)
    snapshots = load_json(SNAPSHOT, {})
    site_file = SITE
    result = await run(source=source, snapshots=snapshots, send=send, site_file=site_file)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps({"matches": snapshots.get("matches", {}), "teams": snapshots.get("teams", []), "followed": snapshots.get("followed", []), "notifications": snapshots.get("notifications", []), "updated": snapshots.get("updated", "")}, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_lines = [
        f"比赛 {result['matches']} 场，变更通知 {result['events']} 条",
    ]
    for r in result["results"]:
        summary_lines.append(f"- {r.get('event', '')} [{r.get('channel', '')}] ok={r.get('ok')}")
    summary = "\n".join(summary_lines)
    print(summary)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        delimiter = "FUTBALL_ACTIONS_EOF"
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"summary<<{delimiter}\n{summary}\n{delimiter}\n")
    return result


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"检查失败：{exc}", file=sys.stderr)
        raise
