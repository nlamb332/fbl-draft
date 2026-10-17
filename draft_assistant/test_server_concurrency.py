"""Ensure concurrent panel requests serialize shared coordinator access."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.request import Request, urlopen

from .coordinator import Coordinator
from .server import create_server


class ProbeCoordinator(Coordinator):
    """Record overlapping status calls while one request is deliberately held."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.status_entered = threading.Event()
        self.release_status = threading.Event()
        self._probe_lock = threading.Lock()
        self._probe_local = threading.local()
        self._active_status = 0
        self.max_active_status = 0
        self.block_status = False

    def status(self, session_id=None):
        depth = getattr(self._probe_local, "depth", 0)
        root_call = depth == 0
        self._probe_local.depth = depth + 1
        if root_call:
            with self._probe_lock:
                self._active_status += 1
                self.max_active_status = max(self.max_active_status, self._active_status)
            self.status_entered.set()
        try:
            if root_call and self.block_status:
                if not self.release_status.wait(5):
                    raise RuntimeError("test status release timed out")
            return super().status(session_id)
        finally:
            self._probe_local.depth = depth
            if root_call:
                with self._probe_lock:
                    self._active_status -= 1


class ServerConcurrencyTests(unittest.TestCase):
    def test_status_and_operator_requests_do_not_overlap_coordinator_access(self) -> None:
        Path("runtime").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir="runtime") as directory:
            coordinator = ProbeCoordinator(f"{directory}/control.sqlite3", recover=False)
            connected = coordinator.connect(
                "https://fantasy.espn.com/basketball/draft?leagueId=1000000000&seasonId=2027&teamId=15"
            )
            sid = connected["session_id"]
            coordinator.status_entered.clear()
            server = create_server(coordinator, port=0)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            coordinator.block_status = True
            post_done = threading.Event()
            post_result: list[tuple[int, dict]] = []

            def get_status() -> None:
                with urlopen(base + "/api/status") as response:
                    self.assertEqual(response.status, HTTPStatus.OK)
                    json.load(response)

            def post_status() -> None:
                request = Request(
                    base + "/api/operator",
                    json.dumps({"operation": "status", "session_id": sid, "request_id": "concurrent-status"}).encode(),
                    {"Content-Type": "application/json", "Origin": base},
                )
                try:
                    with urlopen(request) as response:
                        post_result.append((response.status, json.load(response)))
                finally:
                    post_done.set()

            try:
                status_thread = threading.Thread(target=get_status)
                status_thread.start()
                self.assertTrue(coordinator.status_entered.wait(2), "GET /api/status did not reach coordinator")

                operator_thread = threading.Thread(target=post_status)
                operator_thread.start()
                # While GET status is held, POST status must be waiting on the
                # server lock rather than entering the shared Coordinator.
                time.sleep(0.1)
                self.assertFalse(post_done.is_set())
                self.assertEqual(coordinator.max_active_status, 1)

                coordinator.release_status.set()
                status_thread.join(2)
                operator_thread.join(2)
                self.assertFalse(status_thread.is_alive())
                self.assertFalse(operator_thread.is_alive())
                self.assertEqual(post_result[0][0], HTTPStatus.OK)
                self.assertEqual(post_result[0][1]["request_id"], "concurrent-status")
                self.assertEqual(coordinator.max_active_status, 1)
            finally:
                coordinator.release_status.set()
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)
                coordinator.close()


if __name__ == "__main__":
    unittest.main()
