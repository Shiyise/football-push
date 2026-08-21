# -*- coding: utf-8 -*-
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

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

    def test_second_run_records_notification(self):
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
        new_match["minute"] = 81
        snapshots["matches"]["EPL|m2"] = new_match
        result = asyncio.run(
            checker.run(
                source="mock",
                snapshots=snapshots,
                send=False,
                site_file=self.tmp / "b.html",
                config_file=Path(__file__).parent / "config.json",
            )
        )
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["notifications"], 1)
        self.assertEqual(snapshots["notifications"][0]["kind"], "score")


if __name__ == "__main__":
    unittest.main()
