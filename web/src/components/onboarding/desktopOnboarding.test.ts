import assert from "node:assert/strict";
import test from "node:test";

import {
  DESKTOP_ONBOARDING_COMPLETE_VALUE,
  DESKTOP_ONBOARDING_PENDING_VALUE,
} from "./firstRunOnboardingModel";
import {
  completeDesktopOnboarding,
  ensureDesktopOnboardingStatus,
  markDesktopOnboardingComplete,
} from "./desktopOnboarding";

test("a desktop upgrade without state is marked pending", async () => {
  const calls: Array<{ command: string; status?: unknown }> = [];
  const status = await ensureDesktopOnboardingStatus(async (command, args) => {
    calls.push({ command, status: args?.status });
    return null;
  });

  assert.equal(status, DESKTOP_ONBOARDING_PENDING_VALUE);
  assert.deepEqual(calls, [
    { command: "get_desktop_onboarding_status", status: undefined },
    { command: "set_desktop_onboarding_status", status: DESKTOP_ONBOARDING_PENDING_VALUE },
  ]);
});

test("a completed desktop install does not overwrite its state", async () => {
  const calls: string[] = [];
  const status = await ensureDesktopOnboardingStatus(async (command) => {
    calls.push(command);
    return DESKTOP_ONBOARDING_COMPLETE_VALUE;
  });

  assert.equal(status, DESKTOP_ONBOARDING_COMPLETE_VALUE);
  assert.deepEqual(calls, ["get_desktop_onboarding_status"]);
});

test("completion writes the durable desktop state", async () => {
  const calls: Array<{ command: string; status?: unknown }> = [];
  await markDesktopOnboardingComplete(async (command, args) => {
    calls.push({ command, status: args?.status });
    return undefined;
  });

  assert.deepEqual(calls, [
    { command: "set_desktop_onboarding_status", status: DESKTOP_ONBOARDING_COMPLETE_VALUE },
  ]);
});

test("the app is not shown when the desktop completion marker cannot be saved", async () => {
  let appShown = false;

  await assert.rejects(
    completeDesktopOnboarding({
      desktopRuntime: true,
      previewRequested: false,
      showApp: () => {
        appShown = true;
      },
      invoke: async () => {
        throw new Error("state directory is read-only");
      },
    }),
    /state directory is read-only/,
  );

  assert.equal(appShown, false);
});

test("the app is shown only after the desktop completion marker is saved", async () => {
  const events: string[] = [];

  await completeDesktopOnboarding({
    desktopRuntime: true,
    previewRequested: false,
    showApp: () => events.push("show-app"),
    invoke: async () => {
      events.push("save-complete");
    },
  });

  assert.deepEqual(events, ["save-complete", "show-app"]);
});
