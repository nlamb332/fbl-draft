import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .__main__ import main
from .coordinator import Coordinator
from .server import DraftRequestHandler


class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = Coordinator(":memory:", recover=False)
        runtime_dir = Path(__file__).resolve().parents[1] / "runtime"
        runtime_dir.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime_dir)
        self.config_path = Path(self.temp.name) / "league.json"
        self.server = SimpleNamespace(
            coordinator=self.coordinator,
            setup_config_path=self.config_path,
            setup_complete=False,
            setup_summary=None,
            setup_candidates=None,
            setup_pending_source=None,
            advisor_tokens={},
            advisor_nonces={},
            page_runner_enabled=True,
            page_origins=frozenset({"https://fantasy.espn.com"}),
        )
        self.handler = object.__new__(DraftRequestHandler)
        self.handler.server = self.server
        self.handler._cors_response = True
        self.handler._page_origin = lambda: "https://fantasy.espn.com"

    def tearDown(self):
        self.coordinator.close()
        self.temp.cleanup()

    def test_first_run_source_observation_selection_save_and_reload(self):
        source = self.handler._dispatch_post("/api/setup/source", {
            "url": "https://fantasy.espn.com/basketball/team?leagueId=2000000000&seasonId=2028&teamId=7"
        })
        self.assertEqual(source, {"source_saved": True, "league_id": "2000000000", "season_id": "2028", "team_id": "7"})
        names = ["My Team"] + [f"Other {index}" for index in range(13)]
        result = self.handler._dispatch_post("/api/operator", {
            "operation": "connect-advisor",
            "current_room_url": "https://fantasy.espn.com/basketball/draft?leagueId=2000000088&seasonId=2028&teamId=7",
            "visible_heading": "ESPN Fantasy Basketball Draft - Practice Draft for Example League",
            "practice": True,
            "pick_order": names,
        })
        self.assertTrue(result["setup_discovery"])
        with self.assertRaisesRegex(Exception, "Finish league setup"):
            self.handler._dispatch_post("/api/operator", {"operation": "start"})
        self.assertEqual(result["team_options"], names)
        self.assertEqual(self.coordinator.status()["sessions"], [], "discovery cannot create a session or grant an observer token")
        with self.assertRaisesRegex(ValueError, "Select one"):
            self.handler._dispatch_post("/api/setup", {"our_team": "Example Team"})
        saved = self.handler._dispatch_post("/api/setup", {"our_team": "My Team"})
        self.assertEqual(saved["team"]["league_id"], "2000000000", "practice league ID must not replace the source league")
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["our_team"], "My Team")
        self.assertTrue(self.server.setup_complete)

    def test_get_setup_reports_checklist_fields(self):
        sent = []
        self.handler._send = lambda status, payload, **kw: sent.append(payload)
        self.handler._host_ok = lambda: True
        self.handler.path = "/api/setup"
        self.server.bookmarklet_seen = False
        self.handler.do_GET()
        first = sent.pop()
        self.assertEqual((first["configured"], first["pending_source"], first["team_options_ready"], first["bookmarklet_seen"]), (False, None, False, False))
        self.handler._dispatch_post("/api/setup/source", {
            "url": "https://fantasy.espn.com/basketball/team?leagueId=2000000000&seasonId=2028&teamId=7"
        })
        names = ["My Team"] + [f"Other {index}" for index in range(13)]
        self.handler._dispatch_post("/api/operator", {
            "operation": "connect-advisor",
            "current_room_url": "https://fantasy.espn.com/basketball/draft?leagueId=2000000088&seasonId=2028&teamId=7",
            "visible_heading": "ESPN Fantasy Basketball Draft - Practice Draft for Example League",
            "practice": True,
            "pick_order": names,
        })
        self.handler.do_GET()
        second = sent.pop()
        self.assertEqual(second["pending_source"], {"league_id": "2000000000", "season_id": "2028", "team_id": "7"})
        self.assertTrue(second["team_options_ready"])
        self.assertTrue(second["bookmarklet_seen"])

    def test_mismatched_observed_team_identity_is_rejected(self):
        self.handler._dispatch_post("/api/setup/source", {
            "url": "https://fantasy.espn.com/basketball/team?leagueId=2000000000&seasonId=2028&teamId=7"
        })
        names = ["My Team"] + [f"Other {index}" for index in range(13)]
        with self.assertRaisesRegex(Exception, "does not match the saved source team ID"):
            self.handler._connect_advisor({
                "current_room_url": "https://fantasy.espn.com/basketball/draft?leagueId=2000000088&seasonId=2028&teamId=9",
                "visible_heading": "ESPN Fantasy Basketball Draft - Practice Draft for Example League",
                "practice": True,
                "pick_order": names,
            })

    def test_restart_keeps_unfinished_edit_inactive_then_loads_saved_selection(self):
        self.handler._dispatch_post("/api/setup/source", {
            "url": "https://fantasy.espn.com/basketball/team?leagueId=2000000000&seasonId=2028&teamId=7"
        })
        args = ["--db", ":memory:", "--league-config", str(self.config_path), "serve"]
        with patch("draft_assistant.__main__.serve") as serve_mock:
            self.assertEqual(main(args), 0)
            call = serve_mock.call_args.kwargs
            self.assertFalse(call["setup_complete"])
            self.assertEqual(call["setup_pending_source"]["league_id"], "2000000000")
            call["coordinator"].close()
        self.server.setup_candidates = {
            **self.server.setup_pending_source,
            "team_options": ["Renamed Team"],
        }
        self.handler._dispatch_post("/api/setup", {"our_team": "Renamed Team"})
        with patch("draft_assistant.__main__.serve") as serve_mock:
            self.assertEqual(main(args), 0)
            call = serve_mock.call_args.kwargs
            self.assertTrue(call["setup_complete"])
            self.assertIsNone(call["setup_pending_source"])
            self.assertEqual(call["coordinator"].config.our_team, "Renamed Team")
            call["coordinator"].close()

    def test_explicit_missing_config_opens_inactive_setup(self):
        with patch("draft_assistant.__main__.serve") as serve_mock:
            self.assertEqual(main(["--db", ":memory:", "--league-config", str(self.config_path), "serve"]), 0)
            call = serve_mock.call_args.kwargs
            self.assertFalse(call["setup_complete"])
            call["coordinator"].close()

    def test_edit_setup_disconnects_old_sessions_and_persists_pending_source(self):
        connected = self.coordinator.connect("https://fantasy.espn.com/basketball/draft?leagueId=2000000000&seasonId=2028&teamId=7")
        self.server.setup_complete = True
        self.server.setup_summary = {"league_id": "2000000000", "season_id": "2028", "team_id": "7", "our_team": "Old Name"}
        self.coordinator.ownership.claim(connected["session_id"])
        self.handler._dispatch_post("/api/setup/begin", {})
        status = self.coordinator.status(connected["session_id"])
        self.assertEqual(status["state"], "disconnected")
        self.assertIsNone(status["owner"])
        source = self.handler._dispatch_post("/api/setup/source", {
            "url": "https://fantasy.espn.com/basketball/team?leagueId=2000000000&seasonId=2028&teamId=7"
        })
        self.assertTrue(source["source_saved"])
        self.assertEqual(json.loads(self.config_path.with_name("setup-pending.json").read_text(encoding="utf-8"))["league_id"], "2000000000")

    SOURCE_URL = "https://fantasy.espn.com/basketball/team?leagueId=2000000000&seasonId=2028&teamId=7"
    ROOM_URL = "https://fantasy.espn.com/basketball/draft?leagueId=2000000088&seasonId=2028&teamId=7"
    HEADING = "ESPN Fantasy Basketball Draft - Practice Draft for Example League"

    def _discover(self):
        self.handler._dispatch_post("/api/setup/source", {"url": self.SOURCE_URL})
        names = ["My Team"] + [f"Other {index}" for index in range(13)]
        result = self.handler._dispatch_post("/api/operator", {
            "operation": "connect-advisor",
            "current_room_url": self.ROOM_URL,
            "visible_heading": self.HEADING,
            "practice": True,
            "pick_order": names,
        })
        return names, result

    def test_select_team_in_room_matches_panel_save_and_then_connects(self):
        names, discovery = self._discover()
        nonce = discovery["setup_nonce"]
        self.assertTrue(nonce)
        saved = self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "My Team", "setup_nonce": nonce})
        self.assertEqual(saved["status"], "setup_saved")
        in_room_file = self.config_path.read_text(encoding="utf-8")
        self.assertTrue(self.server.setup_complete)
        self.assertIsNone(self.server.setup_candidates)
        self.assertFalse(self.config_path.with_name("setup-pending.json").exists())
        self.assertEqual(self.server.setup_summary["our_team"], "My Team")
        self.assertFalse(self.config_path.with_suffix(".json.tmp").exists())

        # The panel path writes byte-identical configuration for the same pick.
        self.handler._dispatch_post("/api/setup/begin", {})
        self._discover()
        self.handler._dispatch_post("/api/setup", {"our_team": "My Team"})
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), in_room_file)

        connected = self.handler._dispatch_post("/api/operator", {
            "operation": "connect-advisor",
            "current_room_url": self.ROOM_URL,
            "visible_heading": self.HEADING,
            "practice": True,
            "pick_order": names,
        })
        self.assertNotIn("setup_discovery", connected)
        self.assertEqual(connected["own_team"], "My Team")
        self.assertEqual(connected["expected_seat"], 1)
        self.assertTrue(connected["observer_token"])

    def test_select_team_rejects_unknown_name_and_bad_nonce(self):
        _, discovery = self._discover()
        nonce = discovery["setup_nonce"]
        with self.assertRaisesRegex(ValueError, "Select one"):
            self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "Not Listed", "setup_nonce": nonce})
        for bad in ({}, {"setup_nonce": "wrong"}, {"setup_nonce": 5}):
            with self.assertRaisesRegex(Exception, "discovery nonce"):
                self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "My Team", **bad})
        self.assertFalse(self.server.setup_complete)
        self.assertFalse(self.config_path.exists())

    def test_select_team_requires_page_origin_discovery_and_incomplete_setup(self):
        with self.assertRaisesRegex(Exception, "waiting for a team choice"):
            self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "My Team", "setup_nonce": "x"})
        _, discovery = self._discover()
        self.handler._cors_response = False
        with self.assertRaisesRegex(Exception, "allowed ESPN page origin"):
            self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "My Team", "setup_nonce": discovery["setup_nonce"]})
        self.handler._cors_response = True
        self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "My Team", "setup_nonce": discovery["setup_nonce"]})
        # Replaying the nonce after completion is refused.
        with self.assertRaisesRegex(Exception, "waiting for a team choice"):
            self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "Other 1", "setup_nonce": discovery["setup_nonce"]})
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["our_team"], "My Team")

    def test_new_discovery_invalidates_previous_nonce(self):
        _, first = self._discover()
        _, second = self._discover()
        self.assertNotEqual(first["setup_nonce"], second["setup_nonce"])
        with self.assertRaisesRegex(Exception, "discovery nonce"):
            self.handler._dispatch_post("/api/operator", {"operation": "setup-select-team", "our_team": "My Team", "setup_nonce": first["setup_nonce"]})

    def test_missing_source_message_is_actionable(self):
        with self.assertRaisesRegex(Exception, r"source ESPN team URL.*http://127\.0\.0\.1:\d+/"):
            self.handler._dispatch_post("/api/operator", {
                "operation": "connect-advisor",
                "current_room_url": self.ROOM_URL,
                "visible_heading": self.HEADING,
                "practice": True,
                "pick_order": ["My Team"] + [f"Other {index}" for index in range(13)],
            })


if __name__ == "__main__":
    unittest.main()
