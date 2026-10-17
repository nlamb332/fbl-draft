import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .__main__ import main
from .contracts import LeagueConfig


class PublicConfigurationTests(unittest.TestCase):
    def test_public_defaults_are_examples(self):
        config = LeagueConfig()
        self.assertEqual(config.league_id, "1000000000")
        self.assertEqual(config.our_team, "Example Team")

    def test_private_config_overrides_examples_without_changing_source(self):
        root = Path(__file__).resolve().parents[1]
        (root / "runtime").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root / "runtime") as temporary:
            config_path = Path(temporary) / "league.json"
            config_path.write_text(json.dumps({"league_id": "2000000000", "season_id": "2027", "team_id": "7", "our_team": "Configured Example"}), encoding="utf-8-sig")
            with patch("draft_assistant.__main__.Coordinator") as coordinator, patch("draft_assistant.__main__.serve"):
                self.assertEqual(main(["--league-config", str(config_path), "serve"]), 0)
                configured = coordinator.call_args.kwargs["config"]
                self.assertEqual(configured.league_id, "2000000000")
                self.assertEqual(configured.our_team, "Configured Example")
            self.assertEqual(LeagueConfig().our_team, "Example Team")
