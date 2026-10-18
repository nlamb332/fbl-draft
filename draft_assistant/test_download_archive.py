"""The user download must run independently of research and test artifacts."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SMOKE = """
import json
import threading
import urllib.request
from sleeper_advisor.advisor import recommend
from sleeper_advisor.model import DraftState, SleeperLeague, SleeperPlayer
from sleeper_advisor.cli import build_snapshot
from sleeper_advisor.panel import PAGE
league = SleeperLeague(draft_id='1',league_id=None,season='2026',draft_type='snake',
    mock_type=None,status='drafting',teams=6,rounds=14,
    starting_slots=('PG','SG','G','SF','PF','F','C','UTIL','UTIL'),bench_slots=5,
    reversal_round=None,scoring={'pts':0.5})
player = SleeperPlayer(player_id='example',name='Example Player',positions=('PG',),
    nba_team=None,injury_status=None,stats={'pts':40},adp=1,has_projection=True)
state = DraftState(league=league,our_slot=1,current_pick=1,on_clock_slot=1,picks=(),
    drafted_ids=frozenset(),our_roster=(),available=(player,),our_next_picks=(1,12),warnings=())
recs = recommend(state)
assert recs and recs[0].components['lock_in_weekly_points'] > 0
snapshot = build_snapshot(state,recs)
assert snapshot['strategy']['mode'] == 'lock_in'
assert 'Lock-In/wk' in PAGE
from draft_assistant.coordinator import Coordinator
from draft_assistant.marginal_lineup import DEFAULT_PROMOTION_PATH
from draft_assistant.server import create_server

coordinator = Coordinator(':memory:', recover=False, promotion_path=DEFAULT_PROMOTION_PATH)
server = create_server(coordinator, port=0, page_runner_enabled=True)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    base = f'http://127.0.0.1:{server.server_port}'
    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as response:
            return response.read().decode('utf-8')
    assert 'Copy bookmarklet URL' in get('/')
    info = json.loads(get('/api/runtime-info'))
    assert info['policy']['promotion_valid'] is True
    assert info['policy']['name'] == 'marginal-lineup-v1'
    assert info['startup_source']['git_revision'] is None
    assert 'Close advisor' in get('/page-runner.js')
    assert 'bookmarklet bootstrap' in get('/advisor-bookmarklet.js')
    request = urllib.request.Request(
        base + '/api/operator',
        data=json.dumps({
            'operation': 'connect-advisor',
            'url': 'https://fantasy.espn.com/basketball/draft?leagueId=2000000000&seasonId=2027&teamId=15',
            'practice': True,
            'visible_heading': 'ESPN Fantasy Basketball Draft - Practice Draft for Weekend Hoops League',
            'expected_seat': 1,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Origin': 'https://fantasy.espn.com', 'X-CSRF-Token': coordinator.csrf_token},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        connected = json.load(response)
    assert connected['room']['practice'] is True
    assert connected['practice_attestation']['heading'].endswith('Weekend Hoops League')
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    coordinator.close()
"""


@unittest.skipUnless(shutil.which("git"), "Git is required to build an archive")
class DownloadArchiveTests(unittest.TestCase):
    def test_archive_excludes_research_and_runs_with_required_policy(self):
        (ROOT / "runtime").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as temporary:
            folder = Path(temporary)
            archive = folder / "download.zip"
            subprocess.run(
                ["git", "archive", "--worktree-attributes", "--format=zip", f"--output={archive}", "HEAD"],
                cwd=ROOT, check=True, capture_output=True, timeout=60,
            )
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                for required in (
                    "Launch Advisor.cmd", "scripts/local-panel.ps1",
                    "Launch Sleeper Advisor.cmd", "sleeper_advisor/lock_in.py",
                    "docs/SLEEPER_ADVISOR.md",
                    "draft_assistant/validation/marginal-lineup-v1/promotion_artifact.json",
                    "draft_assistant/validation/marginal-lineup-v1/frozen_params.json",
                    "docs/DRAFT_ADVISOR_BOOKMARKLET.md",
                ):
                    self.assertIn(required, names)
                self.assertFalse(any(name.startswith(("studies/", "skills/", "mock_draft/results/", "draft_assistant/fixtures/")) for name in names))
                self.assertFalse(any(Path(name).name.startswith("test_") for name in names))
                policy_files = {name for name in names if name.startswith("draft_assistant/validation/") and not name.endswith("/")}
                self.assertEqual(len(policy_files), 2)
                bundle.extractall(folder / "extracted")
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment["GIT_CEILING_DIRECTORIES"] = str(folder)
            result = subprocess.run(
                [sys.executable, "-c", SMOKE], cwd=folder / "extracted",
                env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
