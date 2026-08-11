import assert from "node:assert/strict";
import test from "node:test";

import { canDeleteMainSession } from "./SessionsPanel";
import type { Session } from "../types";

const historicalRunningMain = {
  session_id: "main-history",
  type: "main",
  status: "running",
} as Session;

test("every Main conversation remains deletable, including the active one", () => {
  assert.equal(canDeleteMainSession(historicalRunningMain, "main-current"), true);
  assert.equal(canDeleteMainSession(historicalRunningMain, "main-history"), true);
});
