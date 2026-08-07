import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const adapterSource = readFileSync(new URL("./desktop-adapter.js", import.meta.url), "utf8");

class FakeElement {
  constructor() {
    this.hidden = false;
    this.textContent = "";
    this.children = [];
    this.listeners = new Map();
  }

  appendChild(child) { this.children.push(child); return child; }
  setAttribute() {}
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  querySelector(selector) {
    if (!this.childrenBySelector) this.childrenBySelector = new Map();
    if (!this.childrenBySelector.has(selector)) {
      this.childrenBySelector.set(selector, new FakeElement());
    }
    return this.childrenBySelector.get(selector);
  }
  focus() { this.focused = true; }
}

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.listeners = new Map();
  }

  addEventListener(name, handler) { this.listeners.set(name, handler); }
  emit(data) { this.listeners.get("message")?.({ data }); }
}

function response({ ok = true, status = 200, payload = {} } = {}) {
  return {
    ok,
    status,
    async json() { return payload; },
    clone() { return response({ ok, status, payload }); },
  };
}

function loadAdapter(nativeFetch, { search = "" } = {}) {
  const document = {
    head: new FakeElement(),
    body: new FakeElement(),
    createElement() { return new FakeElement(); },
  };
  const window = {
    location: {
      origin: "http://127.0.0.1:4321",
      href: `http://127.0.0.1:4321/${search}`,
      search,
    },
    WebSocket: FakeWebSocket,
    fetch: nativeFetch,
  };
  const context = { window, document, URL, URLSearchParams, console };
  vm.runInNewContext(adapterSource, context, { filename: "desktop-adapter.js" });
  return { context, window, document };
}

test("valid API key is probed before provider update", async () => {
  const calls = [];
  const { window } = loadAdapter(async (input, init = {}) => {
    calls.push({ input, init });
    if (input.endsWith("/models/discover")) return response();
    return response();
  });

  await window.fetch("/api/model-providers/openai", {
    method: "PUT",
    body: JSON.stringify({ base_url: "https://example.test/v1", api_key: "new-key" }),
  });

  assert.deepEqual(calls.map(({ input }) => input), [
    "/api/model-providers/models/discover",
    "/api/model-providers/openai",
  ]);
});

test("failed API key probe blocks the provider update and opens a dialog", async () => {
  let saveCalled = false;
  const { window, document } = loadAdapter(async (input) => {
    if (input.endsWith("/models/discover")) {
      return response({ ok: false, status: 400, payload: { detail: "API Key 无效" } });
    }
    saveCalled = true;
    return response();
  });

  await assert.rejects(
    window.fetch("/api/model-providers/openai", {
      method: "PUT",
      body: JSON.stringify({ base_url: "https://example.test/v1", api_key: "bad-key" }),
    }),
    { message: "API Key 无效" },
  );
  assert.equal(saveCalled, false);
  const dialog = document.body.children.find((child) => child.id === "determinflow-desktop-dialog");
  assert.equal(dialog.hidden, false);
  assert.equal(dialog.querySelector("#df-dialog-title").textContent, "连接检测失败");
  assert.equal(dialog.querySelector("#df-dialog-message").textContent, "API Key 无效");
});

test("terminal WebSocket error opens the reusable dialog", () => {
  const { window, document } = loadAdapter(async () => response());
  const socket = new window.WebSocket("ws://127.0.0.1:4321/ws/chat");
  socket.emit(JSON.stringify({
    type: "error",
    session_id: "main-session",
    message: "provider authentication failed",
    terminal: true,
  }));

  const dialog = document.body.children.find((child) => child.id === "determinflow-desktop-dialog");
  assert.equal(dialog.hidden, false);
  assert.equal(dialog.querySelector("#df-dialog-message").textContent, "provider authentication failed");
});
