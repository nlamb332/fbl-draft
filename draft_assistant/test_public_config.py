import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .__main__ import main
from .contracts import LeagueConfig


class PublicConfigurationTests(unittest.TestCase):
    def test_windows_launcher_opens_browser_setup_without_console_questions(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'scripts/local-panel.ps1').read_text(encoding='utf-8')
        self.assertNotIn('Read-Host', source)
        self.assertIn("$configArgs = @()", source)
        self.assertIn("if (Test-Path -LiteralPath $leagueConfigPath)", source)
        self.assertIn("-OpenPanel", (root / 'Launch Advisor.cmd').read_text(encoding='utf-8'))

    def test_public_defaults_are_examples(self):
        config = LeagueConfig()
        self.assertEqual(config.league_id, "1000000000")
        self.assertEqual(config.our_team, "Example Team")

    def test_serve_without_saved_config_starts_in_inert_browser_setup(self):
        with patch("draft_assistant.__main__.Coordinator") as coordinator, patch("draft_assistant.__main__.serve") as serve:
            self.assertEqual(main(["serve"]), 0)
            self.assertIsNone(coordinator.call_args.kwargs["config"])
            self.assertFalse(serve.call_args.kwargs["setup_complete"])
            self.assertEqual(serve.call_args.kwargs["setup_config_path"], Path("runtime/league.json"))

    def test_missing_explicit_serve_config_falls_back_to_browser_setup(self):
        runtime_dir = Path(__file__).resolve().parents[1] / "runtime"
        runtime_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime_dir) as temporary:
            missing = Path(temporary) / "not-created.json"
            with patch("draft_assistant.__main__.Coordinator") as coordinator, patch("draft_assistant.__main__.serve") as serve:
                self.assertEqual(main(["--league-config", str(missing), "serve"]), 0)
                self.assertIsNone(coordinator.call_args.kwargs["config"])
                self.assertFalse(serve.call_args.kwargs["setup_complete"])

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
