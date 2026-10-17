"""Heartbeat endpoint: CORS / Private Network Access preflight and origin limits."""

from __future__ import annotations

import http.client
import json
import shutil
import threading
import unittest
from dataclasses import replace

from .contracts import DraftObservation, LivePlayer, PickEvent, RoomIdentity, utc_now
from .coordinator import Coordinator
from .server import _canonical_page_origin, create_server
from .test_heartbeat_freshness import ORDER, OUR_TEAM, POOL, ROOM
from mock_draft.fixture import SCORING_WEIGHTS

ESPN = "https://fantasy.espn.com"


def frame(sid: str, pick: int = 2) -> DraftObservation:
    team = lambda n: ORDER[(n - 1) % 14]  # noqa: E731 - round one only
    history = tuple(PickEvent(n, team(n), f"p{n}", f"Player {n}") for n in range(1, pick))
    taken = {e.player_id for e in history}
    return DraftObservation(
        session_id=sid, room=ROOM, observed_at=utc_now(), revision=0, phase="draft", current_pick=pick, on_clock_team=team(pick),
        seconds_remaining=60, pick_order=ORDER, history=history, available_players=tuple(p for p in POOL if p.player_id not in taken),
        autopick=False, complete=True, history_complete=True,
        rules={"teams": 14, "rounds": 10, "starting_slots": ["C", "G", "G", "F", "F", "UTIL"], "bench_slots": 4, "ir_slots": 3, "scoring": dict(SCORING_WEIGHTS)},
    )


class HeartbeatServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = Coordinator(":memory:", recover=False)
        self.sid = self.coordinator.connect(ROOM.safe_url())["session_id"]
        self.start_server(page_dispatch_enabled=False)

    def start_server(self, **kwargs) -> None:
        if hasattr(self, "server"):
            self.stop_server()
        self.server = create_server(self.coordinator, port=0, **kwargs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        if getattr(self, "server", None) is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None

    def tearDown(self) -> None:
        self.coordinator.close()

    def call(self, method: str, path: str, headers: dict | None = None, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        raw = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=raw, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = (response.status, {k.lower(): v for k, v in response.getheaders()}, data)
        connection.close()
        return result

    def preflight(self, origin: str, *, private_network: bool = True, headers: str = "content-type, x-csrf-token", path: str = "/api/heartbeat"):
        request = {"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": headers}
        if private_network:
            request["Access-Control-Request-Private-Network"] = "true"
        return self.call("OPTIONS", path, request)

    def post(self, path: str, body: dict, *, origin: str | None = ESPN, token: str | None = "default"):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        if token == "default":
            token = self.coordinator.csrf_token
        if token:
            headers["X-CSRF-Token"] = token
        return self.call("POST", path, headers, body)

    # --- preflight ---------------------------------------------------------

    def test_pna_preflight_allows_the_espn_origin(self) -> None:
        status, headers, _ = self.preflight(ESPN)
        self.assertEqual(status, 204)
        self.assertEqual(headers["access-control-allow-origin"], ESPN)
        self.assertEqual(headers["access-control-allow-private-network"], "true")
        self.assertIn("x-csrf-token", headers["access-control-allow-headers"].lower())
        self.assertEqual(headers["access-control-allow-methods"], "POST, OPTIONS")
        self.assertEqual(headers["vary"], "Origin")

    def test_private_network_allowance_is_only_sent_when_requested(self) -> None:
        status, headers, _ = self.preflight(ESPN, private_network=False)
        self.assertEqual(status, 204)
        self.assertNotIn("access-control-allow-private-network", headers)

    def test_preflight_denies_every_other_origin_without_allow_headers(self) -> None:
        for origin in ("https://evil.example", "http://fantasy.espn.com", "https://espn.com.evil.example", "https://fantasy.espn.com.evil.example", "null", "http://127.0.0.1:1", "https://example.com"):
            with self.subTest(origin=origin):
                status, headers, _ = self.preflight(origin)
                self.assertEqual(status, 403)
                self.assertFalse([h for h in headers if h.startswith("access-control-allow")])

    def test_origin_matching_returns_only_a_clean_configured_value(self) -> None:
        self.assertEqual(_canonical_page_origin(ESPN, frozenset({ESPN})), ESPN)
        injected = ESPN + "\r\nX-Evil: reflected"
        self.assertIsNone(_canonical_page_origin(injected, frozenset({ESPN, injected})))
        self.assertIsNone(_canonical_page_origin(ESPN, frozenset({injected})))
        self.assertIsNone(_canonical_page_origin("https://fantasy.espn.com/évil", frozenset({ESPN})))

    def test_rejected_origin_is_never_reflected_into_cors_headers(self) -> None:
        body = {"observation": frame(self.sid).to_dict()}
        status, headers, _ = self.post("/api/heartbeat", body, origin="https://fantasy.espn.com.evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertNotIn("vary", headers)

    def test_preflight_rejects_other_headers_methods_paths_and_hosts(self) -> None:
        self.assertEqual(self.preflight(ESPN, headers="content-type, x-csrf-token, authorization")[0], 403)
        self.assertEqual(self.preflight(ESPN, path="/api/connect")[0], 403)
        self.assertEqual(self.preflight(ESPN, path="/api/control")[0], 403)
        status, _, _ = self.call("OPTIONS", "/api/heartbeat", {"Origin": ESPN, "Access-Control-Request-Method": "DELETE"})
        self.assertEqual(status, 403)
        status, _, _ = self.call("OPTIONS", "/api/heartbeat", {"Origin": ESPN, "Host": "evil.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(status, 403)

    def test_operator_preflight_needs_the_page_dispatch_flag_at_post_time(self) -> None:
        self.assertEqual(self.preflight(ESPN, path="/api/operator")[0], 204)

    # --- heartbeat posts ---------------------------------------------------

    def test_espn_origin_can_post_a_heartbeat_with_the_csrf_token(self) -> None:
        status, headers, data = self.post("/api/heartbeat", {"observation": frame(self.sid).to_dict(), "visibility_state": "visible"})
        self.assertEqual(status, 200, data)
        self.assertEqual(headers["access-control-allow-origin"], ESPN)
        result = json.loads(data)
        self.assertEqual((result["status"], result["revision"]), ("ingested", 1))
        status, _, data = self.post("/api/heartbeat", {"observation": replace(frame(self.sid), observed_at=utc_now()).to_dict(), "visibility_state": "hidden"})
        self.assertEqual(json.loads(data)["status"], "touched")
        telemetry = self.coordinator.gap_telemetry(self.sid)
        self.assertEqual((telemetry["frames"], telemetry["hidden_tab_frames"]), (2, 1))

    def test_espn_origin_without_a_valid_token_is_rejected(self) -> None:
        body = {"observation": frame(self.sid).to_dict()}
        self.assertEqual(self.post("/api/heartbeat", body, token=None)[0], 403)
        self.assertEqual(self.post("/api/heartbeat", body, token="wrong")[0], 403)
        self.assertIsNone(self.coordinator._latest_observation(self.sid))

    def test_other_cross_origins_cannot_post_even_with_the_token(self) -> None:
        body = {"observation": frame(self.sid).to_dict()}
        for origin in ("https://evil.example", "http://fantasy.espn.com"):
            self.assertEqual(self.post("/api/heartbeat", body, origin=origin)[0], 403)

    def test_espn_origin_is_limited_to_the_heartbeat_endpoint(self) -> None:
        for path in ("/api/connect", "/api/control", "/api/observation", "/api/export-session", "/api/queue-claim"):
            self.assertEqual(self.post(path, {"session_id": self.sid, "url": "x"})[0], 403, path)
        self.assertEqual(self.post("/api/operator", {"operation": "prepare", "session_id": self.sid})[0], 403)
        self.assertEqual(self.post("/api/operator", {"operation": "resume", "session_id": self.sid})[0], 403)

    def test_page_dispatch_flag_opens_only_prepare_ack_and_reconcile(self) -> None:
        self.start_server(page_dispatch_enabled=True)
        for operation in ("prepare", "ack", "reconcile"):
            status, _, _ = self.post("/api/operator", {"operation": operation, "session_id": self.sid, "action_id": "x", "status": "uncertain"})
            self.assertNotEqual(status, 403, operation)  # reaches the coordinator, which refuses without a session state
        for operation in ("publish", "start", "resume", "queue-claim", "queue-prepare"):
            status, _, _ = self.post("/api/operator", {"operation": operation, "session_id": self.sid})
            self.assertEqual(status, 403, operation)

    def test_csrf_token_is_never_readable_cross_origin(self) -> None:
        for path in ("/api/csrf", "/api/status"):
            status, headers, _ = self.call("GET", path, {"Origin": ESPN})
            self.assertEqual(status, 200)
            self.assertNotIn("access-control-allow-origin", headers)

    def test_local_origin_heartbeat_and_export_work(self) -> None:
        origin = f"http://127.0.0.1:{self.port}"
        status, _, data = self.post("/api/heartbeat", {"observation": frame(self.sid).to_dict()}, origin=origin, token=None)
        self.assertEqual(status, 200, data)
        status, _, data = self.post("/api/export-session", {"session_id": self.sid}, origin=origin, token=None)
        self.assertEqual(status, 200)
        exported = json.loads(data)
        self.assertEqual(exported["publish_gaps"]["frames"], 1)
        self.assertEqual(exported["status"]["session_id"], self.sid)

    @unittest.skipUnless(shutil.which("node"), "Node is required for Python/JS interoperability")
    def test_js_publisher_posts_to_the_real_server_across_origins(self) -> None:
        script = r"""
const { createHeartbeatPublisher, createFetchPost } = require(process.argv[1]);
const [port, token, first, second] = process.argv.slice(2);
const frames = [JSON.parse(first), JSON.parse(first), JSON.parse(second)];
let index = 0;
const acks = [];
const post = createFetchPost({
  baseUrl: `http://127.0.0.1:${port}`, csrfToken: token,
  fetchFn: (url, init) => fetch(url, { ...init, headers: { ...init.headers, Origin: "https://fantasy.espn.com" } }),
});
const publisher = createHeartbeatPublisher({
  capture: async () => ({ ...frames[Math.min(index++, 2)], observed_at: new Date().toISOString() }),
  post: async (body) => { const ack = await post(body); acks.push(ack); return ack; },
  getVisibility: () => "visible",
});
(async () => {
  for (let i = 0; i < 3; i += 1) await publisher.tick("interval");
  console.log(JSON.stringify({ acks, stats: publisher.stats() }));
})();
"""
        import subprocess
        from pathlib import Path

        module = str(Path(__file__).with_name("heartbeat_publisher.js"))
        run = subprocess.run(
            ["node", "-e", script, module, str(self.port), self.coordinator.csrf_token, json.dumps(frame(self.sid, 2).to_dict()), json.dumps(frame(self.sid, 3).to_dict())],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual([ack["status"] for ack in result["acks"]], ["ingested", "touched", "ingested"])
        self.assertEqual([ack["revision"] for ack in result["acks"]], [1, 1, 2])
        self.assertEqual(result["stats"]["post_errors"], 0)
        self.assertEqual(self.coordinator.gap_telemetry(self.sid)["frames"], 3)

    def test_incomplete_heartbeat_is_acknowledged_but_ignored(self) -> None:
        status, _, data = self.post("/api/heartbeat", {"observation": replace(frame(self.sid), complete=False).to_dict()})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["status"], "ignored")
        self.assertIsNone(self.coordinator._latest_observation(self.sid))


if __name__ == "__main__":
    unittest.main()
