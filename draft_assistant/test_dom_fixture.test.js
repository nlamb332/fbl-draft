"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const { parseHtml } = require("./dom_fixture.js");

test("fixture HTML parser preserves quoted attributes, comments, and void tags", () => {
  const body = parseHtml(
    '<div data-value="1 > 0" title="quoted">Text<!-- ignored -->'
      + '<input disabled/><br /></div><button></button>',
  );
  const div = body.querySelector("div");
  assert.deepEqual(div.attrs, { "data-value": "1 > 0", title: "quoted" });
  assert.equal(div.textContent, "Text");
  assert.equal(body.querySelectorAll("input").length, 1);
  assert.equal(body.querySelectorAll("br").length, 1);
  assert.equal(body.querySelectorAll("button").length, 1);
});

test("malformed quoted attributes are bounded in a subprocess", () => {
  const hostile = "<A !=" + '"" !='.repeat(24);
  const script = [
    "const { parseHtml } = require(process.argv[1]);",
    "const input = process.argv[2];",
    "const body = parseHtml(input);",
    "process.stdout.write(String(body.textContent.length));",
  ].join(" ");
  const result = spawnSync(
    process.execPath,
    ["-e", script, path.join(__dirname, "dom_fixture.js"), hostile],
    { encoding: "utf8", timeout: 2000 },
  );
  assert.equal(result.error, undefined, result.error && result.error.message);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(Number(result.stdout), hostile.length);
});
