export const DESKTOP_UPDATE_INTERVAL_MS = 24 * 60 * 60 * 1000;
export const DESKTOP_UPDATE_LAST_CHECK_KEY = "determinflow.desktopUpdate.lastCheck";

interface TauriRuntimeScope {
  __TAURI_INTERNALS__?: unknown;
}

export function isDesktopRuntime(scope: unknown = globalThis): boolean {
  if (!scope || typeof scope !== "object") return false;
  return "__TAURI_INTERNALS__" in (scope as TauriRuntimeScope);
}

export function shouldAutoCheckForUpdate(
  lastChecked: string | null,
  now = Date.now(),
): boolean {
  if (!lastChecked) return true;
  const timestamp = Number(lastChecked);
  return !Number.isFinite(timestamp) || now - timestamp >= DESKTOP_UPDATE_INTERVAL_MS;
}

export function calculateDownloadProgress(
  downloadedBytes: number,
  totalBytes?: number,
): number | null {
  if (!totalBytes || totalBytes <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((downloadedBytes / totalBytes) * 100)));
}

export async function downloadUpdateWithFallback<T>(
  primary: T,
  fallback: T | null,
  download: (resource: T, attempt: "primary" | "fallback") => Promise<void>,
): Promise<T> {
  try {
    await download(primary, "primary");
    return primary;
  } catch (primaryError) {
    if (!fallback) throw primaryError;
    await download(fallback, "fallback");
    return fallback;
  }
}

export function describeUpdateError(error: unknown): string {
  const detail = error instanceof Error ? error.message : String(error ?? "");
  if (/404|not found/i.test(detail)) {
    return "更新服务尚未发布桌面版本，请稍后再试";
  }
  if (/timed?\s*out|timeout/i.test(detail)) {
    return "连接更新服务超时，请检查网络后重试";
  }
  return "暂时无法连接更新服务，请稍后重试";
}
