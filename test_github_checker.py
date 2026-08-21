# -*- coding: utf-8 -*-
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import checker


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_first_run_builds_site_without_notifying(self):
        snapshots = {}
        result = asyncio.run(
            checker.run(
                source="mock",
                snapshots=snapshots,
                send=False,
                site_file=self.tmp / "index.html",
                config_file=Path(__file__).parent / "config.json",
            )
        )
        self.assertEqual(result["matches"], 7)
        self.assertEqual(result["events"], 0)
        self.assertTrue((self.tmp / "index.html").exists())
        self.assertIn("比赛", (self.tmp / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(snapshots["matches"]), 7)

    def test_score_change_for_followed_team_triggers_event(self):
        snapshots = {}
        asyncio.run(
            checker.run(
                source="mock",
                snapshots=snapshots,
                site_file=self.tmp / "a.html",
                config_file=Path(__file__).parent / "config.json",
            )
        )
        old_match = snapshots["matches"]["EPL|m2"]
        new_match = json.loads(json.dumps(old_match))
        new_match["home_score"] = 2
        new_match["away_score"] = 1
        new_match["minute"] = 81
        events = checker._events_for(old_match, new_match, {"364", "359", "360", "132", "124"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "score")
        self.assertIn("2:1", events[0][2])

    def test_reminder_fires_once_before_kickoff(self):
        snapshots = {}
        asyncio.run(
            checker.run(
                source="mock",
                snapshots=snapshots,
                send=False,
                site_file=self.tmp / "a.html",
                config_file=Path(__file__).parent / "config.json",
            )
        )
        base = json.loads(json.dumps(snapshots["matches"]["EPL|m4"]))
        upcoming = dict(base)
        upcoming["id"] = "m-reminder"
        upcoming["time"] = (datetime.now(timezone.utc) + timedelta(hours=11)).isoformat()

        async def fake_fetch(source, client=None):
            matches, teams = checker.sources.mock_matches(), checker.sources.mock_teams()
            matches.append(upcoming)
            return matches, teams

        with mock.patch("checker.sources.fetch_all", side_effect=fake_fetch):
            first = asyncio.run(
                checker.run(
                    source="mock",
                    snapshots=snapshots,
                    send=False,
                    site_file=self.tmp / "b.html",
                    config_file=Path(__file__).parent / "config.json",
                )
            )
            self.assertEqual(first["events"], 1)
            self.assertEqual(first["notifications"], 1)
            self.assertEqual(snapshots["notifications"][0]["kind"], "reminder")
            self.assertEqual(snapshots["notifications"][0]["key"], "EPL|m-reminder")

            second = asyncio.run(
                checker.run(
                    source="mock",
                    snapshots=snapshots,
                    send=False,
                    site_file=self.tmp / "c.html",
                    config_file=Path(__file__).parent / "config.json",
                )
            )
            self.assertEqual(second["events"], 0)


if __name__ == "__main__":
    unittest.main()
