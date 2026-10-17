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
