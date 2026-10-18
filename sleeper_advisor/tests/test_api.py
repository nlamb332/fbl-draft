import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sleeper_advisor import api
from sleeper_advisor.api import SleeperClient, parse_draft_id
from sleeper_advisor.tests.fakes import DRAFT_ID, fake_fetch, fixture_client


class ParseDraftIdTests(unittest.TestCase):
    def test_accepts_urls_and_bare_ids(self):
        for text in (
            "https://sleeper.com/draft/nba/123456789012345678",
            "https://sleeper.com/draft/nba/123456789012345678?ref=x#y",
            "http://www.sleeper.com/draft/nba/123456789012345678/",
            "sleeper.app/draft/nba/123456789012345678",
            "https://sleeper.app/draft/nba/123456789012345678",
            "  123456789012345678 ",
        ):
            self.assertEqual(parse_draft_id(text), "123456789012345678", text)

    def test_rejects_garbage(self):
        for text in ("", "abc", "https://example.com/draft/nba/123", "https://sleeper.com/league/123", None):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_draft_id(text)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_live_endpoints(self):
        client = fixture_client(self.tmp, "mid")
        self.assertEqual(client.draft(DRAFT_ID)["draft_id"], DRAFT_ID)
        self.assertEqual(len(client.picks(DRAFT_ID)), 14)
        self.assertEqual(client.traded_picks(DRAFT_ID), [])
        self.assertEqual(client.league("800000000000000001")["total_rosters"], 6)
        self.assertEqual(client.user("user_a")["username"], "user_a")
        self.assertIn(f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks", client.fetch_json.calls)

    def test_players_cache_ttl_24h(self):
        clock = [1000.0]
        client = fixture_client(self.tmp, "mid", now=lambda: clock[0])

        def fetches():
            return sum("players/nba" in c for c in client.fetch_json.calls)

        client.players()
        client.players()
        self.assertEqual(fetches(), 1)
        clock[0] += 24 * 3600 - 1
        client.players()
        self.assertEqual(fetches(), 1)
        clock[0] += 2
        client.players()
        self.assertEqual(fetches(), 2)
        self.assertTrue((self.tmp / "sleeper" / "players_nba.json").exists())

    def test_projections_cache_ttl_6h_and_url(self):
        clock = [0.0]
        client = fixture_client(self.tmp, "mid", now=lambda: clock[0])
        rows = client.projections(2026)
        self.assertEqual(len(rows), 56)
        client.projections("2026")
        urls = [c for c in client.fetch_json.calls if "projections" in c]
        self.assertEqual(len(urls), 1)
        self.assertIn("season_type=regular", urls[0])
        self.assertIn("position[]=PG", urls[0])
        self.assertIn("position[]=C", urls[0])
        clock[0] = 6 * 3600 + 1
        client.projections("2026")
        self.assertEqual(len([c for c in client.fetch_json.calls if "projections" in c]), 2)
        self.assertTrue((self.tmp / "sleeper" / "projections_2026.json").exists())

    def test_cache_persists_across_clients_and_keeps_utf8(self):
        routes = {api.API_ROOT + "/players/nba": {"1": {"full_name": "Marko Đurić"}}}
        SleeperClient(fake_fetch(routes), cache_dir=self.tmp, now=lambda: 5.0).players()
        raw = (self.tmp / "players_nba.json").read_text(encoding="utf-8")
        self.assertIn("Đurić", raw)
        fetch = fake_fetch({})
        second = SleeperClient(fetch, cache_dir=self.tmp, now=lambda: 6.0)
        self.assertEqual(second.players()["1"]["full_name"], "Marko Đurić")
        self.assertEqual(fetch.calls, [])

    def test_stale_cache_used_when_refresh_fails(self):
        routes = {api.API_ROOT + "/players/nba": {"1": {}}}
        SleeperClient(fake_fetch(routes), cache_dir=self.tmp, now=lambda: 0.0).players()
        failing = SleeperClient(
            fake_fetch({api.API_ROOT + "/players/nba": OSError("offline")}),
            cache_dir=self.tmp,
            now=lambda: 10 * 24 * 3600.0,
        )
        self.assertEqual(failing.players(), {"1": {}})

    def test_failure_without_cache_raises(self):
        client = SleeperClient(
            fake_fetch({api.API_ROOT + "/players/nba": OSError("offline")}), cache_dir=self.tmp
        )
        with self.assertRaises(OSError):
            client.players()

    def test_corrupt_cache_is_refetched(self):
        (self.tmp / "players_nba.json").write_text("{not json", encoding="utf-8")
        client = SleeperClient(fake_fetch({api.API_ROOT + "/players/nba": {"2": {}}}), cache_dir=self.tmp)
        self.assertEqual(client.players(), {"2": {}})

    def test_user_lookup_url_is_quoted(self):
        fetch = fake_fetch({api.API_ROOT + "/user/": {"user_id": "1"}})
        SleeperClient(fetch, cache_dir=self.tmp).user("a b/c")
        self.assertEqual(fetch.calls, [api.API_ROOT + "/user/a%20b%2Fc"])


class DefaultFetcherTests(unittest.TestCase):
    def test_decodes_utf8_and_sends_user_agent(self):
        body = json.dumps({"name": "Marko Đurić"}, ensure_ascii=False).encode("utf-8")

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Resp(body)) as urlopen:
            data = api.default_fetch_json("https://api.sleeper.app/v1/x")
        self.assertEqual(data["name"], "Marko Đurić")
        request = urlopen.call_args[0][0]
        self.assertTrue(request.get_header("User-agent"))
        self.assertEqual(urlopen.call_args[1]["timeout"], 15)

    def test_default_client_uses_default_fetcher(self):
        self.assertIs(SleeperClient().fetch_json, api.default_fetch_json)


if __name__ == "__main__":
    unittest.main()
