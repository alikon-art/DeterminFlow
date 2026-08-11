import {
  DESKTOP_ONBOARDING_COMPLETE_VALUE,
  DESKTOP_ONBOARDING_PENDING_VALUE,
} from "./firstRunOnboardingModel";

type InvokeDesktop = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

async function invokeDesktop(
  command: string,
  args?: Record<string, unknown>,
): Promise<unknown> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke(command, args);
}

export async function ensureDesktopOnboardingStatus(
  invoke: InvokeDesktop = invokeDesktop,
): Promise<string> {
  const status = await invoke("get_desktop_onboarding_status");
  if (status === DESKTOP_ONBOARDING_COMPLETE_VALUE) return status;
  await invoke("set_desktop_onboarding_status", {
    status: DESKTOP_ONBOARDING_PENDING_VALUE,
  });
  return DESKTOP_ONBOARDING_PENDING_VALUE;
}

export async function markDesktopOnboardingComplete(
  invoke: InvokeDesktop = invokeDesktop,
): Promise<void> {
  await invoke("set_desktop_onboarding_status", {
    status: DESKTOP_ONBOARDING_COMPLETE_VALUE,
  });
}

export async function completeDesktopOnboarding({
  desktopRuntime,
  previewRequested,
  showApp,
  invoke = invokeDesktop,
}: {
  desktopRuntime: boolean;
  previewRequested: boolean;
  showApp: () => void;
  invoke?: InvokeDesktop;
}): Promise<void> {
  if (desktopRuntime && !previewRequested) {
    await markDesktopOnboardingComplete(invoke);
  }
  showApp();
}
