import json
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .__main__ import main
from .contracts import LeagueConfig


class PublicConfigurationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'), 'PowerShell is required')
    def test_windows_first_launch_parses_url_and_keeps_config_local(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'scripts/local-panel.ps1').read_text(encoding='utf-8')
        setup = source.split('if (-not (Test-Path -LiteralPath $leagueConfigPath)) {', 1)[1]
        setup = 'if (-not (Test-Path -LiteralPath $leagueConfigPath)) {' + setup.split('Set-Location -LiteralPath $repoRoot', 1)[0]
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        with tempfile.TemporaryDirectory(dir=root / 'runtime') as temporary:
            for url, expected_code in [('https://fantasy.espn.com/basketball/team?leagueId=2000000000&teamId=7&seasonId=2028', 0), ('https://example.com/basketball/team?leagueId=2000000000&teamId=7&seasonId=2028', 1)]:
                config_path = Path(temporary) / f'league-{expected_code}.json'
                script = Path(temporary) / f'setup-{expected_code}.ps1'
                script.write_text('\n'.join([
                    "$ErrorActionPreference = 'Stop'",
                    f'$repoRoot = {quote(root)}',
                    f'$leagueConfigPath = {quote(config_path)}',
                    f'$pythonCommand = @{{ Source = {quote(sys.executable)} }}',
                    '$pythonPrefix = @()',
                    '$answers = [System.Collections.Generic.Queue[string]]::new()',
                    f'$answers.Enqueue({quote(url)})',
                    "$answers.Enqueue('Configured Example')",
                    'function Read-Host { param($Prompt) $answers.Dequeue() }',
                    setup,
                ]), encoding='utf-8-sig')
                result = subprocess.run([shutil.which('pwsh') or shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, expected_code, result.stderr)
                if expected_code:
                    self.assertFalse(config_path.exists())
                else:
                    self.assertEqual(json.loads(config_path.read_text(encoding='utf-8-sig')), {'league_id': '2000000000', 'season_id': '2028', 'team_id': '7', 'our_team': 'Configured Example'})

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
