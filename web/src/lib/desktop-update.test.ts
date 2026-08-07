import assert from "node:assert/strict";
import test from "node:test";

import {
  DESKTOP_UPDATE_INTERVAL_MS,
  calculateDownloadProgress,
  describeUpdateError,
  downloadUpdateWithFallback,
  isDesktopRuntime,
  shouldAutoCheckForUpdate,
} from "./desktop-update";

test("desktop runtime detection only enables the updater inside Tauri", () => {
  assert.equal(isDesktopRuntime({}), false);
  assert.equal(isDesktopRuntime({ __TAURI_INTERNALS__: {} }), true);
});

test("automatic checks are throttled to once per day", () => {
  const now = 1_800_000_000_000;
  assert.equal(shouldAutoCheckForUpdate(null, now), true);
  assert.equal(shouldAutoCheckForUpdate(String(now - 1000), now), false);
  assert.equal(
    shouldAutoCheckForUpdate(String(now - DESKTOP_UPDATE_INTERVAL_MS), now),
    true,
  );
  assert.equal(shouldAutoCheckForUpdate("invalid", now), true);
});

test("download progress stays within zero and one hundred percent", () => {
  assert.equal(calculateDownloadProgress(25, 100), 25);
  assert.equal(calculateDownloadProgress(120, 100), 100);
  assert.equal(calculateDownloadProgress(-10, 100), 0);
  assert.equal(calculateDownloadProgress(20), null);
});

test("update download uses the primary source when it succeeds", async () => {
  const attempts: string[] = [];
  const selected = await downloadUpdateWithFallback(
    "gitee",
    "github",
    async (source, attempt) => {
      attempts.push(`${attempt}:${source}`);
    },
  );

  assert.equal(selected, "gitee");
  assert.deepEqual(attempts, ["primary:gitee"]);
});

test("update download falls back once when the primary source fails", async () => {
  const attempts: string[] = [];
  const selected = await downloadUpdateWithFallback(
    "gitee",
    "github",
    async (source, attempt) => {
      attempts.push(`${attempt}:${source}`);
      if (source === "gitee") throw new Error("offline");
    },
  );

  assert.equal(selected, "github");
  assert.deepEqual(attempts, ["primary:gitee", "fallback:github"]);
});

test("update download reports the fallback failure", async () => {
  await assert.rejects(
    downloadUpdateWithFallback(
      "gitee",
      "github",
      async (source) => {
        throw new Error(`${source} offline`);
      },
    ),
    /github offline/,
  );
});

test("update errors keep technical details out of the user interface", () => {
  assert.match(describeUpdateError(new Error("404 Not Found")), /尚未发布/);
  assert.match(describeUpdateError(new Error("request timeout")), /超时/);
  assert.match(describeUpdateError(new Error("socket closed")), /暂时无法/);
});
