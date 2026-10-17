"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

function makeNode(id = "") {
  return {
    id, value: "", textContent: "", hidden: false, disabled: false, dataset: {}, attributes: {}, children: [], handlers: {},
    addEventListener(name, handler) { this.handlers[name] = handler; },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name] || (name === "href" ? "#disabled" : ""); },
    setSelectionRange() {}, focus() {}, select() {},
    replaceChildren(...children) { this.children = children; this.value = ""; },
    appendChild(child) { this.children.push(child); return child; },
    add(child) { this.children.push(child); },
    querySelector() { return makeNode(); },
    querySelectorAll() { return []; },
  };
}

test("first-run panel saves source, selects only observed teams, saves, and preserves edits while polling", async () => {
  const ids = ["state", "setup-card", "setup-title", "setup-help", "setup-edit", "setup-fields", "setup-url-input", "setup-url", "setup-team", "setup-save", "setup-source", "setup-summary", "room-url", "practice", "connect", "operator-form", "advisor-bookmarklet", "copy-advisor-bookmarklet", "advisor-bookmarklet-url", "advisor-bookmarklet-status"];
  const nodes = new Map(ids.map((id) => [id, makeNode(id)]));
  let options = {};
  let saved = false;
  const calls = [];
  const response = (value, ok = true) => ({ ok, statusText: ok ? "OK" : "Error", json: async () => value });
  const fetch = async (path, request = {}) => {
    if (request.method === "POST") {
      const body = JSON.parse(request.body); calls.push([path, body]);
      if (path === "/api/setup/source") return response({ source_saved: true, league_id: "2000000000", season_id: "2028", team_id: "7" });
      if (path === "/api/setup") { saved = true; return response({ configured: true, team: { league_id: "2000000000", season_id: "2028", team_id: "7", our_team: body.our_team } }); }
      if (path === "/api/setup/begin") { saved = false; return response({ configured: false }); }
    }
    if (path === "/api/setup") return response({ configured: saved, team: saved ? { league_id: "2000000000", season_id: "2028", team_id: "7", our_team: "Observed Team" } : null });
    if (path === "/api/setup/options") return response(options);
    if (path === "/api/status") return response({ sessions: [], setup_required: !saved, configured_team: saved ? { league_id: "2000000000", season_id: "2028", team_id: "7", our_team: "Observed Team" } : null });
    throw new Error(`Unexpected fetch ${path}`);
  };
  let poll;
  const document = { getElementById: (id) => nodes.get(id) || (nodes.set(id, makeNode(id)), nodes.get(id)), createElement: (tag) => makeNode(tag), querySelectorAll: () => [] };
  const context = {
    document, fetch, navigator: {}, Option: function Option(label, value) { return { textContent: label, value }; },
    window: { FBL_CSRF_TOKEN: "csrf", crypto: null },
    setInterval(callback) { poll = callback; }, setTimeout, clearTimeout, console,
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("./static/app.js"), "utf8"), context);
  const flush = async () => { await new Promise((resolve) => setImmediate(resolve)); };
  await flush();
  assert.equal(nodes.get("setup-card").hidden, false);
  nodes.get("setup-url-input").value = "https://fantasy.espn.com/basketball/team?leagueId=2000000000&seasonId=2028&teamId=7";
  await nodes.get("setup-source").handlers.click();
  assert.equal(calls[0][0], "/api/setup/source");
  options = { league_id: "2000000000", season_id: "2028", team_id: "7", team_options: ["Observed Team", "Another Team"] };
  poll(); await flush();
  assert.deepEqual(nodes.get("setup-team").children.map((item) => item.value), ["", "Observed Team", "Another Team"]);
  nodes.get("setup-team").value = "Observed Team";
  await nodes.get("setup-save").handlers.click();
  assert.equal(calls.find(([path]) => path === "/api/setup")[1].our_team, "Observed Team");
  assert.equal(nodes.get("setup-fields").hidden, true);
  await nodes.get("setup-edit").handlers.click();
  assert.match(nodes.get("setup-url-input").value, /leagueId=2000000000/);
  assert.equal(nodes.get("setup-save").disabled, true);
  nodes.get("setup-url-input").value = "typing in progress";
  nodes.get("setup-team").value = "Another Team";
  poll(); await flush();
  assert.equal(nodes.get("setup-url-input").value, "typing in progress");
  assert.equal(nodes.get("setup-team").value, "Another Team");
});
