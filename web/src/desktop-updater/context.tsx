import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { DownloadEvent, Update } from "@tauri-apps/plugin-updater";

import {
  DESKTOP_UPDATE_LAST_CHECK_KEY,
  calculateDownloadProgress,
  describeUpdateError,
  downloadUpdateWithFallback,
  isDesktopRuntime,
  shouldAutoCheckForUpdate,
} from "../lib/desktop-update";
import {
  DesktopUpdateContext,
  type DesktopUpdateContextValue,
  type DesktopUpdateInfo,
  type DesktopUpdatePhase,
} from "./context-value";

interface UpdateMetadata {
  rid: number;
  fallbackRid?: number;
  currentVersion: string;
  version: string;
  date?: string;
  body?: string;
  rawJson: Record<string, unknown>;
}

function readLastCheck(): string | null {
  try {
    return window.localStorage.getItem(DESKTOP_UPDATE_LAST_CHECK_KEY);
  } catch {
    return null;
  }
}

function rememberSuccessfulCheck(): void {
  try {
    window.localStorage.setItem(DESKTOP_UPDATE_LAST_CHECK_KEY, String(Date.now()));
  } catch {
    // A blocked localStorage must not disable updates for the desktop app.
  }
}

export function DesktopUpdateProvider({ children }: { children: ReactNode }) {
  const enabled = isDesktopRuntime();
  const [phase, setPhase] = useState<DesktopUpdatePhase>(enabled ? "idle" : "disabled");
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [update, setUpdate] = useState<DesktopUpdateInfo | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  const updateResource = useRef<Update | null>(null);
  const fallbackUpdateResource = useRef<Update | null>(null);
  const checkInFlight = useRef(false);

  const releaseUpdateResources = useCallback(async () => {
    const primary = updateResource.current;
    const fallback = fallbackUpdateResource.current;
    updateResource.current = null;
    fallbackUpdateResource.current = null;
    if (primary) await primary.close().catch(() => undefined);
    if (fallback) await fallback.close().catch(() => undefined);
  }, []);

  const performCheck = useCallback(async (silent: boolean) => {
    if (!enabled || checkInFlight.current) return;
    checkInFlight.current = true;
    setPhase("checking");
    setError(null);
    setProgress(null);

    try {
      const [{ getVersion }, { Update, check }, { invoke }] = await Promise.all([
        import("@tauri-apps/api/app"),
        import("@tauri-apps/plugin-updater"),
        import("@tauri-apps/api/core"),
      ]);
      const installedVersion = await getVersion();
      setCurrentVersion(installedVersion);
      let availableUpdate: Update | null;
      let fallbackUpdate: Update | null = null;
      try {
        const metadata = await invoke<UpdateMetadata | null>("check_update_sources");
        availableUpdate = metadata ? new Update(metadata) : null;
        if (metadata?.fallbackRid !== undefined) {
          fallbackUpdate = new Update({ ...metadata, rid: metadata.fallbackRid });
        }
      } catch {
        availableUpdate = await check({ timeout: 15_000 });
      }
      rememberSuccessfulCheck();
      await releaseUpdateResources();

      if (!availableUpdate) {
        setUpdate(null);
        setPhase("up-to-date");
        return;
      }

      updateResource.current = availableUpdate;
      fallbackUpdateResource.current = fallbackUpdate;
      setUpdate({
        version: availableUpdate.version,
        date: availableUpdate.date,
        body: availableUpdate.body,
      });
      setNoticeDismissed(false);
      setPhase("available");
    } catch (caught) {
      if (silent) {
        setPhase("idle");
        setError(null);
      } else {
        setPhase("error");
        setError(describeUpdateError(caught));
      }
    } finally {
      checkInFlight.current = false;
    }
  }, [enabled, releaseUpdateResources]);

  const checkForUpdates = useCallback(
    () => performCheck(false),
    [performCheck],
  );

  const installUpdate = useCallback(async () => {
    const resource = updateResource.current;
    const fallback = fallbackUpdateResource.current;
    if (!enabled || !resource) return;

    setPhase("downloading");
    setProgress(0);
    setError(null);
    let downloadedBytes = 0;
    let totalBytes: number | undefined;

    const onDownloadEvent = (event: DownloadEvent) => {
      if (event.event === "Started") {
        totalBytes = event.data.contentLength;
        setProgress(totalBytes ? 0 : null);
      } else if (event.event === "Progress") {
        downloadedBytes += event.data.chunkLength;
        setProgress(calculateDownloadProgress(downloadedBytes, totalBytes));
      } else {
        setProgress(100);
        setPhase("installing");
      }
    };

    try {
      const downloadedResource = await downloadUpdateWithFallback(
        resource,
        fallback,
        async (candidate, attempt) => {
          if (attempt === "fallback") {
            downloadedBytes = 0;
            totalBytes = undefined;
            setPhase("downloading");
            setProgress(0);
          }
          await candidate.download(onDownloadEvent, { timeout: 10 * 60 * 1000 });
        },
      );
      if (downloadedResource === fallback) {
        await resource.close().catch(() => undefined);
        updateResource.current = downloadedResource;
        fallbackUpdateResource.current = null;
      } else if (fallback) {
        await fallback.close().catch(() => undefined);
        fallbackUpdateResource.current = null;
      }
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("prepare_for_update");
      await downloadedResource.install();
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (caught) {
      setPhase("available");
      setProgress(null);
      setError(describeUpdateError(caught));
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    if (shouldAutoCheckForUpdate(readLastCheck())) {
      void performCheck(true);
      return;
    }

    void import("@tauri-apps/api/app")
      .then(({ getVersion }) => getVersion())
      .then(setCurrentVersion)
      .catch(() => undefined);
  }, [enabled, performCheck]);

  useEffect(() => () => {
    void releaseUpdateResources();
  }, [releaseUpdateResources]);

  const value = useMemo<DesktopUpdateContextValue>(() => ({
    enabled,
    phase,
    currentVersion,
    update,
    progress,
    error,
    noticeDismissed,
    checkForUpdates,
    installUpdate,
    dismissNotice: () => setNoticeDismissed(true),
  }), [
    checkForUpdates,
    currentVersion,
    enabled,
    error,
    installUpdate,
    noticeDismissed,
    phase,
    progress,
    update,
  ]);

  return (
    <DesktopUpdateContext.Provider value={value}>
      {children}
    </DesktopUpdateContext.Provider>
  );
}
