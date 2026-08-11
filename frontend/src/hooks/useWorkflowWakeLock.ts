import { useEffect } from "react";

/**
 * Hält den Bildschirm wach, solange ein CAD-Workflow aktiv ist (Browser Wake Lock API).
 * System-Ruhemodus wird zusätzlich serverseitig (Windows) unterbunden.
 */
export function useWorkflowWakeLock(active: boolean): void {
  useEffect(() => {
    if (!active || typeof navigator === "undefined") return;

    const nav = navigator as Navigator & {
      wakeLock?: {
        request: (type: "screen") => Promise<{ release: () => Promise<void>; released: boolean }>;
      };
    };
    if (!nav.wakeLock?.request) return;

    let released = false;
    let lock: { release: () => Promise<void>; released: boolean } | null = null;

    const request = async () => {
      try {
        lock = await nav.wakeLock!.request("screen");
      } catch {
        // Permission / Battery / unsupported – Backend-Sperre bleibt maßgeblich
      }
    };

    void request();

    const onVisible = () => {
      if (document.visibilityState === "visible" && !released) {
        void request();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      released = true;
      document.removeEventListener("visibilitychange", onVisible);
      if (lock && !lock.released) {
        void lock.release();
      }
    };
  }, [active]);
}
