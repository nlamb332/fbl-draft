"""Checks for the generic, recommendation-only ESPN page loader."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from .page_bundle import build_bundle, recommendation_loader_snippet


MODULE_DIR = Path(__file__).resolve().parent


class RecommendationLoaderTests(unittest.TestCase):
    def test_loader_is_room_agnostic_and_sends_only_visible_pick_order(self) -> None:
        bundle = build_bundle(MODULE_DIR)
        snippet = recommendation_loader_snippet(
            "http://127.0.0.1:8765",
            csrf_token="test-csrf",
            module_dir=MODULE_DIR,
        )
        self.assertIn(bundle.bundle_hash, snippet)
        self.assertIn('operation: "connect-advisor"', snippet)
        self.assertIn("pick_order", snippet)
        self.assertIn("safeRoom", snippet)
        self.assertNotIn("memberId", snippet)
        self.assertIn("visible_heading", snippet)
        self.assertIn("practice,", snippet)
        self.assertIn("mode: \"recommendation\"", snippet)
        self.assertIn("controls.dock?.(\"left\")", snippet)
        self.assertIn("controls.collapse?.()", snippet)

    def test_two_practice_rooms_connect_with_fresh_server_seat_and_compact_mount(self) -> None:
        bundle = build_bundle(MODULE_DIR)
        snippet = recommendation_loader_snippet(
            "http://127.0.0.1:8765",
            csrf_token="test-csrf",
            module_dir=MODULE_DIR,
        )
        script = self._harness(snippet, bundle.bundle_hash)
        result = self._run_node(script)

        self.assertEqual([case["result"]["status"] for case in result], ["connected", "connected"])
        self.assertEqual([case["result"]["expected_seat"] for case in result], [4, 11])
        self.assertEqual([case["attach"]["expectedSeat"] for case in result], [4, 11])
        self.assertEqual([case["attach"]["mode"] for case in result], ["recommendation", "recommendation"])
        self.assertTrue(all(call["url"] == "http://127.0.0.1:8765/api/operator" for case in result for call in case["posts"]))
        for case in result:
            self.assertEqual(len(case["posts"]), 1)
            body = case["posts"][0]["body"]
            self.assertEqual(body["operation"], "connect-advisor")
            self.assertEqual(len(body["pick_order"]), 14)
            self.assertEqual(len({name.casefold() for name in body["pick_order"]}), 14)
            self.assertNotIn("memberId", body["url"])
            self.assertEqual(case["mount"]["dock"], ["left"])
            self.assertEqual(case["mount"]["collapse"], 1)

    def test_real_room_loader_connects_without_practice_classification(self) -> None:
        bundle = build_bundle(MODULE_DIR)
        snippet = recommendation_loader_snippet(
            "http://127.0.0.1:8765",
            csrf_token="test-csrf",
            module_dir=MODULE_DIR,
        )
        script = self._harness(snippet, bundle.bundle_hash, real_room=True)
        result = self._run_node(script)
        result = result[0]

        self.assertEqual(result["result"]["status"], "connected")
        body = result["posts"][0]["body"]
        self.assertEqual(body["practice"], False)
        self.assertIn("ESPN Fantasy Basketball Draft", body["visible_heading"])
        self.assertNotIn("Practice Draft", body["visible_heading"])

    def test_same_room_reuses_attached_runner_without_connect_or_replace(self) -> None:
        bundle = build_bundle(MODULE_DIR)
        snippet = recommendation_loader_snippet(
            "http://127.0.0.1:8765",
            csrf_token="test-csrf",
            module_dir=MODULE_DIR,
        )
        script = self._harness(snippet, bundle.bundle_hash, same_room=True)
        result = self._run_node(script)

        self.assertEqual(result["result"]["status"], "reused")
        self.assertEqual(result["result"]["session_id"], "existing-session")
        self.assertEqual(result["posts"], [])
        self.assertEqual(result["attach"], None)
        self.assertEqual(result["mount"]["dock"], ["left"])
        self.assertEqual(result["mount"]["collapse"], 1)

    def test_wrong_trial_room_rejects_before_fetch(self) -> None:
        bundle = build_bundle(MODULE_DIR)
        snippet = recommendation_loader_snippet(
            "http://127.0.0.1:8765",
            csrf_token="test-csrf",
            module_dir=MODULE_DIR,
        )
        script = self._harness(snippet, bundle.bundle_hash, wrong_room=True)
        result = self._run_node(script)
        self.assertTrue(result["error"])
        self.assertIn("not an ESPN basketball draft room", result["error"])
        self.assertEqual(result["posts"], [])

    @staticmethod
    def _run_node(script: str):
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=MODULE_DIR.parent,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode:
            raise AssertionError(f"node harness failed ({completed.returncode}): {completed.stderr}\n{completed.stdout}")
        output = completed.stdout.strip().splitlines()
        if not output:
            raise AssertionError("node harness returned no result")
        return json.loads(output[-1])

    @staticmethod
    def _harness(snippet: str, bundle_hash: str, *, same_room: bool = False, wrong_room: bool = False, real_room: bool = False) -> str:
        snippet_json = json.dumps(snippet)
        hash_json = json.dumps(bundle_hash)
        mode_call = "runWrong()" if wrong_room else "runReuse()" if same_room else "runFresh()"
        real_room_json = "true" if real_room else "false"
        return f"""
const vm = require("vm");
const snippet = {snippet_json};
const expectedHash = {hash_json};
const realRoom = {real_room_json};

function makeCase(index) {{
  const teams = Array.from({{length: 14}}, (_, seat) => `Visible Team ${{index}}-${{seat + 1}}`);
  const ownSeat = index === 1 ? 4 : 11;
  const ownTeam = teams[ownSeat - 1];
  const heading = {{textContent: realRoom ? `ESPN Fantasy Basketball Draft - Live Draft ${{index}}` : `ESPN Fantasy Basketball Draft - Practice Draft for Weekend Hoops League ${{index}}`, getAttribute() {{ return null; }}}};
  const options = teams.map((text, seat) => ({{
    textContent: text,
    getAttribute(name) {{ return name === "data-seat" ? String(seat + 1) : null; }},
    dataset: {{seat: String(seat + 1)}},
  }}));
  const select = {{options, getAttribute() {{ return null; }}}};
  const body = {{innerText: teams.join("\\n"), textContent: teams.join("\\n")}};
  const mounts = {{dock: [], collapse: 0}};
  const posts = [];
  const attached = [];
  let currentRunner = null;
  const runner = {{
    summary() {{ return {{recommendation: {{running: false}}, mode: "recommendation"}}; }},
  }};
  const document = {{
    body,
    head: {{appendChild() {{}}}},
    querySelectorAll(selector) {{
      if (selector === "h1,h2,h3,[role='heading']") return [heading];
      if (selector === "select,[role='combobox']") return [select];
      return [];
    }},
    getElementById() {{ return null; }},
    createElement() {{ return {{setAttribute() {{}}, appendChild() {{}}, style: {{}}}}; }},
  }};
  const FBL = {{
    bundle_hash: expectedHash,
    bundle: "fbl-page-runner-v1",
    modules: {{"page_controls.js": {{
      mountPageControls(options) {{
        mounts.last = {{runner: Boolean(options.runner)}};
        return {{
          dock(side) {{ mounts.dock.push(side); }},
          collapse() {{ mounts.collapse += 1; }},
          dispose() {{}},
        }};
      }},
    }}}},
    configure() {{}},
    runner() {{
      if (!currentRunner) throw new Error("not attached");
      return currentRunner;
    }},
    attach(options) {{
      attached.push(options);
      currentRunner = runner;
    }},
  }};
  const pageWindow = {{
    FBL,
    getComputedStyle() {{ return {{display: "block", visibility: "visible"}}; }},
    async fetch(url, options) {{
      posts.push({{url, body: JSON.parse(options.body)}});
      return {{ok: true, async json() {{ return {{session_id: `session-${{index}}`, observer_token: `observer-${{index}}`, own_team: ownTeam, expected_seat: ownSeat}}; }}}};
    }},
  }};
  const context = {{window: pageWindow, document, location: {{href: realRoom ? `https://fantasy.espn.com/basketball/draft?leagueId=1000000000&seasonId=2027&teamId=15` : `https://fantasy.espn.com/basketball/draft?leagueId=1000000014${{index}}&seasonId=2027&teamId=15`}}, URL, console}};
  context.window.location = context.location;
  return {{context, posts, attached, mounts, runner}};
}}

async function runFresh() {{
  const first = makeCase(1);
  const second = makeCase(2);
  const execute = async (entry) => {{
    vm.createContext(entry.context);
    entry.value = await vm.runInContext(`(async () => {{ return ${{snippet}}; }})()`, entry.context);
    return {{result: entry.value, posts: entry.posts, attach: entry.attached[0], mount: entry.mounts}};
  }};
  return [await execute(first), await execute(second)];
}}

async function runReuse() {{
  const entry = makeCase(1);
  const existing = {{
    mode: "recommendation",
    inputs: {{captured: true}},
    session_id: "existing-session",
    batch: {{running: false}},
  }};
  entry.runner.summary = () => existing;
  entry.runner.coordinatorStatus = async () => ({{room: {{practice: true, league_id: "10000000141", season_id: "2027", team_id: "15"}}}});
  entry.context.window.FBL.runner = () => entry.runner;
  vm.createContext(entry.context);
  const value = await vm.runInContext(`(async () => {{ return ${{snippet}}; }})()`, entry.context);
  return {{result: value, posts: entry.posts, attach: entry.attached[0] || null, mount: entry.mounts}};
}}

async function runWrong() {{
  const entry = makeCase(1);
  entry.context.location.href = "https://example.invalid/basketball/draft?leagueId=1&seasonId=2027&teamId=15";
  entry.context.window.location = entry.context.location;
  vm.createContext(entry.context);
  try {{
    await vm.runInContext(`(async () => {{ return ${{snippet}}; }})()`, entry.context);
    return {{error: "loader unexpectedly resolved", posts: entry.posts}};
  }} catch (error) {{
    return {{error: String(error && error.message ? error.message : error), posts: entry.posts}};
  }}
}}

(async () => {{
  const value = await {mode_call};
  console.log(JSON.stringify(value));
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""


if __name__ == "__main__":
    unittest.main()
