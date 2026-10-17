"use strict";

/**
 * Regression for the 2026-10-13 live probe of practice room 1000000015, which
 * failed against real ESPN markup: nested <li>/<button> view tabs read as
 * ambiguous, the League Manager region class was unknown, and the pick timer
 * select (random name, data-testid only) was not found.
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const { createFixtureTab, parseHtml } = require("./dom_fixture.js");
const { clickResumeInPage, inspectNativeControls } = require("./native_activation.js");
const { resolveViewTab } = require("./view_switch.js");

const SOURCE = fs.readFileSync(path.join(__dirname, "fixtures", "espn_live_league_manager_2026-10-13.html"), "utf8");

function liveTab(timerSeconds = 30) {
  const html = SOURCE.replace("{{T30}}", timerSeconds === 30 ? " selected" : "").replace("{{T90}}", timerSeconds === 90 ? " selected" : "");
  const body = parseHtml(html);
  return { body, tab: createFixtureTab({ getBody: () => body, url: "https://fantasy.espn.com/basketball/draft?leagueId=1000000015" }) };
}

const evaluate = (tab, fn, arg) => tab.playwright.locator("body").evaluate(fn, arg);

test("nested <li>/<button> view tabs resolve to exactly one tab each", async () => {
  const { tab } = liveTab();
  for (const pattern of ["^players$", "^pick\\s+history$", "^league\\s+manager$"]) {
    const probe = await evaluate(tab, resolveViewTab, { pattern, click: false });
    assert.equal(probe.status, "unique", pattern);
    assert.equal(probe.count, 1, pattern);
  }
});

test("view-tab click lands on the inner button, not the list item", async () => {
  const { body, tab } = liveTab();
  const clicked = [];
  for (const node of body.querySelectorAll('[role="tab"]')) node.onclick = () => clicked.push(node.localName);
  const result = await evaluate(tab, resolveViewTab, { pattern: "^pick\\s+history$", click: true });
  assert.equal(result.clicked, true);
  assert.deepEqual(clicked, ["button"]);
});

test("buttons inside the tab panel are not treated as view tabs", async () => {
  const { tab } = liveTab();
  const probe = await evaluate(tab, resolveViewTab, { pattern: "^undo$", click: false });
  assert.equal(probe.status, "missing");
});

test("League Manager region, Resume and pick timer are read from live markup", async () => {
  for (const seconds of [30, 90]) {
    const { tab } = liveTab(seconds);
    const result = await evaluate(tab, inspectNativeControls);
    assert.equal(result.resume.status, "unique", result.resume.reason);
    assert.equal(result.resume.region_count, 1);
    assert.equal(result.timer_select_count, 1, "only the pickTimeDropdown, not the roster team select");
    assert.equal(result.native_timer_seconds, seconds);
    assert.equal(result.native_timer_label, `${seconds} seconds`);
  }
});

test("League Manager switch waits for the Resume control to render, and reports not-ready without throwing", async () => {
  const { createViewSwitcher } = require("./view_switch.js");
  // Live ESPN showed Resume hidden ~25 ms after the tab click and visible by ~150 ms.
  let reads = 0;
  const hiddenHtml = SOURCE.replace("{{T30}}", " selected").replace("{{T90}}", "").replace('<button class="Button Button--default pause-resume-draft', '<button style="display:none" class="Button Button--default pause-resume-draft');
  const shownBody = liveTab().body;
  const hiddenBody = parseHtml(hiddenHtml);
  const delayed = createFixtureTab({ getBody: () => (reads++ < 3 ? hiddenBody : shownBody), url: "https://fantasy.espn.com/basketball/draft?leagueId=1000000015" });
  const ready = await createViewSwitcher({ tab: delayed, leagueManagerSettleMs: 1 }).showLeagueManager();
  assert.equal(ready.ready, true);
  assert.ok(ready.polls > 1, "it polled past the hidden render");
  const never = createFixtureTab({ getBody: () => hiddenBody, url: "https://fantasy.espn.com/basketball/draft?leagueId=1000000015" });
  const notReady = await createViewSwitcher({ tab: never, leagueManagerPolls: 3, leagueManagerSettleMs: 1 }).showLeagueManager();
  assert.equal(notReady.ready, false);
  assert.equal((await evaluate(never, inspectNativeControls)).resume.status, "hidden", "activation would then refuse without clicking");
});

test("the single Resume click resolves in live markup", async () => {
  const { body, tab } = liveTab();
  let clicks = 0;
  for (const node of body.querySelectorAll("button.pause-resume-draft")) node.onclick = () => { clicks += 1; };
  assert.deepEqual(await evaluate(tab, clickResumeInPage), { clicked: true, usable_count: 1 });
  assert.equal(clicks, 1);
});
