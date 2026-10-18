import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from sleeper_advisor import cli
from sleeper_advisor.api import API_ROOT, SleeperClient
from sleeper_advisor.cli import Poller, PollingClient, main, render_text
from sleeper_advisor.tests.fakes import (
    DRAFT_ID, USERNAME, fake_fetch, fixture_client, fixture_routes, load_fixture)

PICKS = load_fixture("picks_mid")
PICKS_URL = f"{API_ROOT}/draft/{DRAFT_ID}/picks"


def scripted_client(tmp, counts, scenario="drafting"):
    """Client whose picks endpoint returns the first N fixture picks.

    ``counts`` is a mutable list; its first element is the current N. An
    element equal to ``None`` makes the picks endpoint raise.
    """
    def picks(_url):
        n = counts[0]
        if n is None:
            raise OSError("network down")
        return PICKS[:n]

    routes = fixture_routes(scenario)
    routes[PICKS_URL] = picks
    fetch = fake_fetch(routes)
    return SleeperClient(fetch_json=fetch, cache_dir=Path(tmp) / "sleeper"), fetch


def run(argv, client, sleep=lambda s: None):
    out = io.StringIO()
    code = main(argv, client=client, out=out, sleep=sleep)
    return code, out.getvalue()


class OnceTests(unittest.TestCase):
    def test_once_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(["once", DRAFT_ID, "--user", USERNAME, "--top", "5"],
                             fixture_client(tmp, "mid"))
        self.assertEqual(code, 0)
        self.assertIn("league_mock", text)
        self.assertIn("Best lineup", text)
        self.assertIn(cli.LEGEND, text)
        self.assertIn(">>> YOU ARE ON THE CLOCK <<<", text)  # pick 15 is slot 3
        rows = [l for l in text.splitlines() if l[:2].strip().isdigit()]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0].split()[0], "1")
        self.assertRegex(text, r"[+-]\d+\.\d")

    def test_once_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(["once", DRAFT_ID, "--user", USERNAME, "--json", "--top", "4"],
                             fixture_client(tmp, "mid"))
        self.assertEqual(code, 0)
        data = json.loads(text)
        self.assertEqual(data["our_slot"], 3)
        self.assertEqual(data["picks_made"], len(PICKS))
        self.assertTrue(data["on_clock"])
        self.assertEqual(len(data["recommendations"]), 4)
        self.assertEqual(data["recommendations"][0]["rank"], 1)
        self.assertEqual(len(data["lineup"]), 9)
        self.assertEqual(data["draft"]["mock_type"], "league_mock")

    def test_once_pre_draft_slot_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(["once", DRAFT_ID, "--slot", "4", "--json"], fixture_client(tmp, "pre"))
        self.assertEqual(code, 0)
        data = json.loads(text)
        self.assertEqual(data["our_slot"], 4)
        self.assertEqual(data["picks_away"], 3)

    def test_url_accepted_and_bad_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = run(["once", f"https://sleeper.com/draft/nba/{DRAFT_ID}?x=1", "--slot", "1"],
                          fixture_client(tmp, "pre"))
            self.assertEqual(code, 0)
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code, _ = run(["once", "not-a-draft"], fixture_client(tmp, "pre"))
        self.assertEqual(code, 2)
        self.assertIn("error", err.getvalue())

    def test_once_network_failure_exit_code(self):
        client = SleeperClient(fetch_json=fake_fetch({}), cache_dir=tempfile.gettempdir())
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = run(["once", DRAFT_ID], client)
        self.assertEqual(code, 1)

    def test_render_shows_pick_in_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            poller = Poller(fixture_client(tmp, "drafting"), DRAFT_ID, USERNAME)
            poller.poll()
        text = render_text(poller.snapshot)
        self.assertIn("Your pick in 2", text)
        self.assertNotIn("ON THE CLOCK", text)


class WatchTests(unittest.TestCase):
    def test_watch_ticks_and_banners(self):
        counts = [0]
        script = [0, 0, 1, 13, 14]
        with tempfile.TemporaryDirectory() as tmp:
            client, fetch = scripted_client(tmp, counts)
            ticks = iter(script[1:])

            def sleep(_s):
                counts[0] = next(ticks)

            counts[0] = script[0]
            code, text = run(["watch", DRAFT_ID, "--user", USERNAME, "--iterations", "5"],
                             client, sleep=sleep)
        self.assertEqual(code, 0)
        # tables only at ticks 1, 3, 4, 5 (tick 2 sees no change)
        self.assertEqual(text.count(cli.LEGEND), 4)
        self.assertIn("Your pick in 2", text)
        self.assertIn("Your pick in 1", text)
        self.assertEqual(text.count(">>> YOU ARE ON THE CLOCK <<<"), 1)
        self.assertLess(text.index("Your pick in 2"), text.index(">>> YOU ARE ON THE CLOCK <<<"))
        # players loaded once even though the draft was fetched every tick
        draft_calls = [u for u in fetch.calls if u.endswith(f"/draft/{DRAFT_ID}")]
        player_calls = [u for u in fetch.calls if u.endswith("/players/nba")]
        self.assertEqual(len(draft_calls), 5)
        self.assertEqual(len(player_calls), 1)

    def test_watch_survives_network_errors(self):
        counts = [0]
        script = iter([None, None, 14])
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = scripted_client(tmp, counts)

            def sleep(_s):
                counts[0] = next(script)

            code, text = run(["watch", DRAFT_ID, "--user", USERNAME, "--iterations", "4"],
                             client, sleep=sleep)
        self.assertEqual(code, 0)
        self.assertEqual(text.count("warning: update failed"), 2)
        self.assertIn("network down", text)
        self.assertIn(">>> YOU ARE ON THE CLOCK <<<", text)

    def test_watch_first_tick_error_then_recovers(self):
        counts = [None]
        script = iter([0])
        with tempfile.TemporaryDirectory() as tmp:
            client, _ = scripted_client(tmp, counts)

            def sleep(_s):
                counts[0] = next(script)

            code, text = run(["watch", DRAFT_ID, "--slot", "3", "--iterations", "2"], client, sleep=sleep)
        self.assertEqual(code, 0)
        self.assertIn("warning: update failed", text)
        self.assertIn(cli.LEGEND, text)

    def test_watch_stops_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = load_fixture("draft_drafting")
            draft["status"] = "complete"
            routes = fixture_routes("mid")
            routes[f"{API_ROOT}/draft/{DRAFT_ID}"] = draft
            client = SleeperClient(fetch_json=fake_fetch(routes), cache_dir=Path(tmp) / "s")
            sleeps = []
            code, text = run(["watch", DRAFT_ID, "--slot", "3"], client, sleep=sleeps.append)
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [])
        self.assertIn("Draft complete.", text)

    def test_keyboard_interrupt_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            def sleep(_s):
                raise KeyboardInterrupt

            code, text = run(["watch", DRAFT_ID, "--slot", "3"], fixture_client(tmp, "pre"), sleep=sleep)
        self.assertEqual(code, 0)
        self.assertIn("Stopped.", text)


class PollerTests(unittest.TestCase):
    def test_recompute_only_on_change(self):
        counts = [3]
        calls = []

        def fake_recommend(state, top):
            calls.append(len(state.picks))
            return []

        with tempfile.TemporaryDirectory() as tmp:
            client, _ = scripted_client(tmp, counts)
            poller = Poller(client, DRAFT_ID, USERNAME, recommend_fn=fake_recommend)
            self.assertTrue(poller.poll())
            self.assertFalse(poller.poll())
            counts[0] = 4
            self.assertTrue(poller.poll())
            counts[0] = None
            self.assertFalse(poller.poll())
            self.assertIn("network down", poller.error)
            self.assertIn("network down", poller.snapshot["error"])
            counts[0] = 4
            self.assertFalse(poller.poll())
            self.assertIsNone(poller.error)
        self.assertEqual(calls, [3, 4])

    def test_polling_client_memoizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = fixture_client(tmp, "pre")
            wrapped = PollingClient(base)
            self.assertIs(wrapped.players(), wrapped.players())
            wrapped.draft(DRAFT_ID)
            self.assertEqual(wrapped.last_draft["draft_id"], DRAFT_ID)


class ConfigTests(unittest.TestCase):
    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ["--cache-dir", tmp]
            self.assertEqual(run(["config", "get", "username", *args], None)[1].strip(), "")
            self.assertEqual(run(["config", "set", "username", "user_a", *args], None)[0], 0)
            self.assertEqual(run(["config", "set", "draft_url", "https://sleeper.com/draft/nba/1", *args], None)[0], 0)
            self.assertEqual(run(["config", "get", "username", *args], None)[1].strip(), "user_a")
            saved = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["draft_url"], "https://sleeper.com/draft/nba/1")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run(["config", "set", "bogus", "x", *args], None)[0], 2)
                self.assertEqual(run(["config", "set", "draft_url", "bad-url", *args], None)[0], 2)
            self.assertEqual(run(["config", "get", "draft_url", *args], None)[1].strip(),
                             "https://sleeper.com/draft/nba/1")


if __name__ == "__main__":
    unittest.main()
