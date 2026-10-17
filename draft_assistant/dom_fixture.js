"use strict";

/**
 * Minimal offline DOM for fixtures.
 *
 * The canonical observer and activation locator serialize plain functions into
 * the ESPN page (`locator.evaluate(fn)` / `evaluateAll(fn)`).  This module
 * parses fixture HTML into just enough of the browser DOM for those exact
 * functions to run unchanged: element traversal, a small CSS selector engine,
 * attributes, textContent, form state, and visibility.  It is test
 * infrastructure only.  Nothing here touches a network, a browser, or ESPN.
 */

const VOID_ELEMENTS = new Set(["img", "input", "br", "hr", "meta", "link"]);
const ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&apos;": "'", "&nbsp;": " " };

function decode(value) {
  return String(value).replace(/&(?:amp|lt|gt|quot|apos|nbsp|#39);/g, (entity) => ENTITIES[entity]);
}

class FixtureElement {
  constructor(tagName, attributes = {}) {
    this.tagName = String(tagName).toUpperCase();
    this.localName = String(tagName).toLowerCase();
    this.attrs = { ...attributes };
    this.children = [];
    this.parentElement = null;
    this._text = null;
    this._listeners = new Map();
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  get childNodesRaw() { return this.children; }

  get textContent() {
    return this.children.map((child) => (typeof child === "string" ? child : child.textContent)).join("");
  }

  getAttribute(name) {
    const key = String(name).toLowerCase();
    return Object.prototype.hasOwnProperty.call(this.attrs, key) ? this.attrs[key] : null;
  }

  hasAttribute(name) { return this.getAttribute(name) !== null; }

  get disabled() { return this.hasAttribute("disabled"); }

  get checked() {
    if (this.localName !== "input") return undefined;
    return this.hasAttribute("checked");
  }

  set checked(value) {
    if (value) this.attrs.checked = "";
    else delete this.attrs.checked;
  }

  get options() {
    return this.localName === "select" ? this.querySelectorAll("option") : undefined;
  }

  get selectedOptions() {
    const options = this.options || [];
    const selected = options.filter((option) => option.hasAttribute("selected"));
    return selected.length ? selected : options.slice(0, 1);
  }

  get value() {
    if (this.localName === "select") {
      const option = this.selectedOptions[0];
      return option ? (option.hasAttribute("value") ? option.getAttribute("value") : option.textContent) : "";
    }
    return this.getAttribute("value") || "";
  }

  get className() { return this.getAttribute("class") || ""; }

  get id() { return this.getAttribute("id") || ""; }

  getClientRects() { return isHidden(this) ? [] : [{ width: 1, height: 1 }]; }

  getBoundingClientRect() {
    return isHidden(this)
      ? { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }
      : { left: 0, top: 0, width: 800, height: 480, right: 800, bottom: 480 };
  }

  addEventListener(type, listener) {
    if (typeof listener !== "function") return;
    const listeners = this._listeners.get(String(type)) || [];
    listeners.push(listener);
    this._listeners.set(String(type), listeners);
  }

  removeEventListener(type, listener) {
    const key = String(type);
    const listeners = this._listeners.get(key) || [];
    this._listeners.set(key, listeners.filter((candidate) => candidate !== listener));
  }

  dispatchEvent(event) {
    if (!event || !event.type) throw new TypeError("fixture events require a type");
    if (!event.target) event.target = this;
    event.currentTarget = this;
    const handler = this[`on${event.type}`];
    if (typeof handler === "function") handler.call(this, event);
    for (const listener of this._listeners.get(String(event.type)) || []) listener.call(this, event);
    return !event.defaultPrevented;
  }

  matches(selector) {
    return parseSelector(selector).some((complex) => matchesComplex(this, complex));
  }

  querySelectorAll(selector) {
    const parsed = parseSelector(selector);
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (typeof child === "string") continue;
        if (parsed.some((complex) => matchesComplex(child, complex))) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  closest(selector) {
    for (let node = this; node && typeof node.matches === "function"; node = node.parentElement) {
      if (node.matches(selector)) return node;
    }
    return null;
  }

  contains(other) {
    for (let node = other; node; node = node.parentElement) if (node === this) return true;
    return false;
  }

  click() {
    if (typeof this.onclick === "function") this.onclick({ target: this });
  }
}

function isHidden(element) {
  for (let node = element; node; node = node.parentElement) {
    if (node.hasAttribute && node.hasAttribute("hidden")) return true;
    const style = (node.getAttribute && node.getAttribute("style")) || "";
    if (/display\s*:\s*none/i.test(style) || /visibility\s*:\s*hidden/i.test(style)) return true;
  }
  return false;
}

function parseHtml(html) {
  const root = new FixtureElement("body");
  const stack = [root];
  const tokenPattern = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)/g;
  let match;
  while ((match = tokenPattern.exec(html))) {
    if (match[0].startsWith("<!--")) continue;
    if (match[1]) {
      const name = match[1].toLowerCase();
      for (let index = stack.length - 1; index > 0; index -= 1) {
        if (stack[index].localName === name) { stack.length = index; break; }
      }
    } else if (match[2]) {
      const name = match[2].toLowerCase();
      const attributes = {};
      const attrPattern = /([^\s=>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
      let attr;
      while ((attr = attrPattern.exec(match[3] || ""))) {
        const value = attr[2] ?? attr[3] ?? attr[4];
        attributes[attr[1].toLowerCase()] = value === undefined ? "" : decode(value);
      }
      const element = new FixtureElement(name, attributes);
      stack[stack.length - 1].appendChild(element);
      if (!match[4] && !VOID_ELEMENTS.has(name)) stack.push(element);
    } else if (match[5]) {
      const text = decode(match[5]);
      if (text.trim() || stack[stack.length - 1].localName === "button") stack[stack.length - 1].children.push(text);
    }
  }
  return root;
}

// --- CSS selector subset -------------------------------------------------
// comma lists, descendant and child combinators, tag, .class, #id,
// [attr], [attr=v], [attr*=v], [attr~=v] with an optional ` i` flag, and
// :first-child.  That is every construct the canonical modules use.

const selectorCache = new Map();

function parseSelector(selector) {
  if (selectorCache.has(selector)) return selectorCache.get(selector);
  const complexes = splitTop(selector, ",").map((part) => parseComplex(part.trim())).filter(Boolean);
  selectorCache.set(selector, complexes);
  return complexes;
}

function splitTop(value, separator) {
  const parts = [];
  let depth = 0;
  let quote = null;
  let current = "";
  for (const char of value) {
    if (quote) { if (char === quote) quote = null; current += char; continue; }
    if (char === '"' || char === "'") { quote = char; current += char; continue; }
    if (char === "[") depth += 1;
    if (char === "]") depth -= 1;
    if (char === separator && depth === 0) { parts.push(current); current = ""; continue; }
    current += char;
  }
  parts.push(current);
  return parts;
}

function parseComplex(source) {
  // returns [{compound, combinator}] left to right; combinator links to previous
  const tokens = [];
  let depth = 0;
  let quote = null;
  let current = "";
  let pending = " ";
  const flush = () => {
    if (current) { tokens.push({ compound: parseCompound(current), combinator: pending }); current = ""; pending = " "; }
  };
  for (const char of source) {
    if (quote) { if (char === quote) quote = null; current += char; continue; }
    if (char === '"' || char === "'") { quote = char; current += char; continue; }
    if (char === "[") depth += 1;
    if (char === "]") depth -= 1;
    if (depth === 0 && char === ">") { flush(); pending = ">"; continue; }
    if (depth === 0 && /\s/.test(char)) { flush(); continue; }
    current += char;
  }
  flush();
  return tokens.length ? tokens : null;
}

function parseCompound(source) {
  const compound = { tag: null, classes: [], id: null, attrs: [], firstChild: false };
  let rest = source;
  const tag = rest.match(/^[a-zA-Z][\w-]*|^\*/);
  if (tag) { compound.tag = tag[0] === "*" ? null : tag[0].toLowerCase(); rest = rest.slice(tag[0].length); }
  while (rest) {
    let m;
    if ((m = rest.match(/^\.([\w-]+)/))) compound.classes.push(m[1]);
    else if ((m = rest.match(/^#([\w-]+)/))) compound.id = m[1];
    else if ((m = rest.match(/^:first-child/))) compound.firstChild = true;
    else if ((m = rest.match(/^\[\s*([\w-]+)\s*(?:([*~^$]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+))\s*(i)?)?\s*\]/))) {
      compound.attrs.push({ name: m[1].toLowerCase(), op: m[2] || null, value: m[3] ?? m[4] ?? m[5] ?? "", insensitive: m[6] === "i" });
    } else throw new Error(`unsupported selector fragment: ${rest}`);
    rest = rest.slice(m[0].length);
  }
  return compound;
}

function matchesCompound(element, compound) {
  if (compound.tag && element.localName !== compound.tag) return false;
  if (compound.id && element.id !== compound.id) return false;
  const classes = element.className.split(/\s+/);
  if (!compound.classes.every((name) => classes.includes(name))) return false;
  for (const attr of compound.attrs) {
    const raw = element.getAttribute(attr.name);
    if (raw === null) return false;
    if (!attr.op) continue;
    const actual = attr.insensitive ? raw.toLowerCase() : raw;
    const wanted = attr.insensitive ? attr.value.toLowerCase() : attr.value;
    if (attr.op === "=" && actual !== wanted) return false;
    if (attr.op === "*=" && !actual.includes(wanted)) return false;
    if (attr.op === "^=" && !actual.startsWith(wanted)) return false;
    if (attr.op === "$=" && !actual.endsWith(wanted)) return false;
    if (attr.op === "~=" && !actual.split(/\s+/).includes(wanted)) return false;
  }
  if (compound.firstChild) {
    const parent = element.parentElement;
    if (!parent || parent.children.find((child) => typeof child !== "string") !== element) return false;
  }
  return true;
}

function matchesComplex(element, complex) {
  let index = complex.length - 1;
  if (!matchesCompound(element, complex[index].compound)) return false;
  const walk = (node, position) => {
    if (position < 0) return true;
    const { compound } = complex[position];
    const link = complex[position + 1].combinator;
    if (link === ">") {
      const parent = node.parentElement;
      return Boolean(parent) && matchesCompound(parent, compound) && walk(parent, position - 1);
    }
    for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (matchesCompound(ancestor, compound) && walk(ancestor, position - 1)) return true;
    }
    return false;
  };
  return walk(element, index - 1);
}

// --- Page / tab adapters --------------------------------------------------

/**
 * Wrap a mutable `getBody()` into the Playwright-like tab surface used by the
 * canonical modules.  Serialized in-page functions get a `window` whose
 * getComputedStyle reflects fixture visibility.
 */
function createFixtureTab({ getBody, url, onClick = null, calls = [] }) {
  const windowShim = {
    getComputedStyle(element) {
      const hidden = isHidden(element);
      return { display: hidden ? "none" : "block", visibility: hidden ? "hidden" : "visible" };
    },
  };

  function withWindow(callback) {
    const previous = global.window;
    global.window = windowShim;
    try { return callback(); } finally { global.window = previous; }
  }

  function locatorFor(selector) {
    const resolve = () => getBody().querySelectorAll(selector);
    return {
      selector,
      async evaluateAll(fn, arg) { calls.push(`evaluateAll:${selector}`); return withWindow(() => fn(resolve(), arg)); },
      async evaluate(fn, arg) {
        calls.push(`evaluate:${selector}`);
        const body = getBody();
        const target = selector === "body" ? body : resolve()[0];
        if (!target) throw new Error(`locator ${selector} resolved no element`);
        return withWindow(() => fn(target, arg));
      },
      async click() {
        calls.push(`click:${selector}`);
        const targets = resolve().filter((element) => !isHidden(element) && !element.disabled);
        if (targets.length !== 1) throw new Error(`click locator ${selector} resolved ${targets.length} elements`);
        if (onClick) await onClick(targets[0], selector);
        targets[0].click();
      },
      async innerText() { const first = resolve()[0]; return first ? first.textContent : ""; },
    };
  }

  return {
    calls,
    async url() { return typeof url === "function" ? url() : url; },
    playwright: {
      locator: locatorFor,
      getByRole(role) { return locatorFor(role === "grid" ? '[role="grid"]' : `[role="${role}"]`); },
    },
  };
}

module.exports = { FixtureElement, createFixtureTab, isHidden, parseHtml };
