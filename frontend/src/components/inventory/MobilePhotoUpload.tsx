import { useEffect, useMemo, useRef, useState } from "react";
import QRCode from "qrcode";

import { api, apiBaseUrl } from "../../api/client";
import type { AssetUploadResponse } from "../../api/types";
import {
  buildPhoneUploadUrl,
  discoverLanIpv4s,
  isPhoneReachableLanIp,
  pageOriginWithPort,
  rankLanIp,
} from "../../utils/lanDiscovery";

/** Schlanke Kamera-Upload-Seite fürs Handy (#/mobile-upload). */
export function MobilePhotoUploadPage() {
  const cameraRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<AssetUploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [apiOk, setApiOk] = useState<boolean | null>(null);

  useEffect(() => {
    document.title = "Werkstatt-Brain · Foto";
    void api
      .get<{ status: string }>("/health")
      .then(() => setApiOk(true))
      .catch(() => setApiOk(false));
  }, []);

  const handleFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setError(null);
    setResult(null);
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file, file.name || `handy-foto-${Date.now()}.jpg`);
      if (title.trim()) form.append("title", title.trim());
      // Sofort speichern; Vision im Hintergrund – sonst Timeout bei großen Handy-Fotos
      form.append("defer_process", "true");
      form.append("auto_process", "true");
      const data = await api.postForm<AssetUploadResponse>("/api/v1/inventory/upload", form);
      setResult(data);
      setTitle("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload fehlgeschlagen";
      setError(
        `${msg} (API: ${apiBaseUrl() || window.location.origin}). Seite neu laden und erneut versuchen.`,
      );
    } finally {
      setUploading(false);
      if (cameraRef.current) cameraRef.current.value = "";
    }
  };

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col gap-4 bg-workshop-bg p-4 text-workshop-text">
      <header>
        <h1 className="text-lg font-semibold text-workshop-accent">Inventar-Foto</h1>
        <p className="mt-1 text-sm text-workshop-muted">
          Foto aufnehmen – landet direkt in der Inventory-DB (Vision-Scan folgt automatisch).
        </p>
        <p className="mt-2 font-mono text-[11px] text-workshop-muted">
          API: {apiBaseUrl() || window.location.origin}{" "}
          {apiOk === true ? "· erreichbar" : apiOk === false ? "· nicht erreichbar" : "· prüfe…"}
        </p>
        {apiOk === false && (
          <p className="mt-1 text-xs text-workshop-warning">
            Backend nicht erreichbar. QR neu scannen (URL mit LAN-IP, Port 5173) und Seite hart neu laden.
          </p>
        )}
      </header>

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-workshop-muted">Titel (optional)</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="z. B. Fräser 6mm / Reststück Buche"
          className="rounded-md border border-workshop-border bg-workshop-panel px-3 py-2"
        />
      </label>

      <button
        type="button"
        disabled={uploading}
        onClick={() => cameraRef.current?.click()}
        className="rounded-lg bg-workshop-accent px-4 py-4 text-base font-semibold text-workshop-bg disabled:opacity-40"
      >
        {uploading ? "Lädt hoch…" : "Kamera öffnen & hochladen"}
      </button>
      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => void handleFiles(e.target.files)}
      />

      <label className="block">
        <span className="mb-1 block text-sm text-workshop-muted">Oder aus Galerie wählen</span>
        <input
          type="file"
          accept="image/*"
          disabled={uploading}
          onChange={(e) => void handleFiles(e.target.files)}
          className="w-full text-sm text-workshop-muted file:mr-3 file:rounded-md file:border-0 file:bg-workshop-panel file:px-3 file:py-2 file:text-workshop-text"
        />
      </label>

      {result && (
        <div className="rounded-md border border-workshop-success/40 bg-workshop-success/10 p-3 text-sm">
          {result.duplicate ? "Duplikat – schon in der DB" : "In Inventar-DB gespeichert"} – Status:{" "}
          {result.status}
          <div className="mt-1 text-[11px] text-workshop-muted">ID: {result.asset_id}</div>
        </div>
      )}
      {error && (
        <div className="rounded-md border border-workshop-danger/40 bg-workshop-danger/10 p-3 text-sm text-workshop-danger">
          {error}
        </div>
      )}

      <a href="#/" className="mt-auto text-center text-sm text-workshop-muted underline">
        Zurück zum Dashboard
      </a>
    </div>
  );
}

/** QR-Karte: kompakter Handy-Upload (LAN-IP, nicht localhost). */
export function InventoryMobileQr() {
  const page = useMemo(() => pageOriginWithPort(), []);
  const [lanIps, setLanIps] = useState<string[]>([]);
  const [detecting, setDetecting] = useState(true);
  const [selectedIp, setSelectedIp] = useState<string | null>(null);
  const [hostOverride, setHostOverride] = useState("");
  const [copied, setCopied] = useState(false);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [apiReachable, setApiReachable] = useState<boolean | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setDetecting(true);
      const fromBrowser = await discoverLanIpv4s();
      let fromApi: string[] = [];
      try {
        const hint = await api.get<{ ips: string[] }>("/api/v1/network/lan-hint");
        fromApi = (hint.ips || []).filter((ip) => /^\d{1,3}(\.\d{1,3}){3}$/.test(ip));
      } catch {
        /* ignore */
      }
      if (cancelled) return;
      const merged = [...fromBrowser];
      for (const ip of fromApi) {
        if (!merged.includes(ip)) merged.push(ip);
      }
      const phoneIps = merged
        .filter(isPhoneReachableLanIp)
        .sort((a, b) => rankLanIp(a) - rankLanIp(b) || a.localeCompare(b));
      const fallback = merged.sort((a, b) => rankLanIp(a) - rankLanIp(b) || a.localeCompare(b));
      const usable = phoneIps.length > 0 ? phoneIps : fallback.slice(0, 1);
      setLanIps(usable);
      const pageIsLan = isPhoneReachableLanIp(page.hostname);
      if (pageIsLan) setSelectedIp(page.hostname);
      else if (usable[0]) setSelectedIp(usable[0]);
      setDetecting(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [page.hostname]);

  const phoneUrl = useMemo(() => {
    if (hostOverride.trim()) return buildPhoneUploadUrl(hostOverride.trim());
    if (selectedIp) {
      const portPart = page.port && page.port !== "80" && page.port !== "443" ? `:${page.port}` : "";
      return buildPhoneUploadUrl(`http://${selectedIp}${portPart}`);
    }
    return buildPhoneUploadUrl(page.origin);
  }, [hostOverride, selectedIp, page.origin, page.port]);

  useEffect(() => {
    let cancelled = false;
    void QRCode.toDataURL(phoneUrl, {
      width: 140,
      margin: 1,
      color: { dark: "#111111", light: "#ffffff" },
    }).then((url) => {
      if (!cancelled) setQrDataUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [phoneUrl]);

  useEffect(() => {
    if (!selectedIp && !hostOverride.trim()) {
      setApiReachable(null);
      return;
    }
    const host = hostOverride.trim()
      ? (() => {
          try {
            return new URL(hostOverride.trim()).hostname;
          } catch {
            return selectedIp;
          }
        })()
      : selectedIp;
    if (!host) return;
    const ctrl = new AbortController();
    void fetch(`http://${host}:${page.port || "5173"}/health`, { signal: ctrl.signal })
      .then((r) => setApiReachable(r.ok))
      .catch(() => setApiReachable(false));
    return () => ctrl.abort();
  }, [selectedIp, hostOverride, page.port]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(phoneUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const statusOk = apiReachable === true;
  const statusBad = apiReachable === false || (!detecting && !selectedIp && !hostOverride.trim());

  return (
    <div className="rounded-md border border-workshop-border bg-workshop-bg/40 p-2.5">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-[10px] font-mono uppercase tracking-wider text-workshop-muted">
          Foto per Handy
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          {detecting && <span className="text-workshop-muted">Suche WLAN…</span>}
          {!detecting && statusOk && <span className="text-workshop-success">bereit</span>}
          {!detecting && statusBad && <span className="text-workshop-warning">prüfen</span>}
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="text-workshop-muted underline-offset-2 hover:text-workshop-text hover:underline"
          >
            {showAdvanced ? "Weniger" : "Mehr"}
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3">
        {qrDataUrl ? (
          <img
            src={qrDataUrl}
            alt="QR-Code Handy-Upload"
            width={112}
            height={112}
            className="shrink-0 rounded border border-workshop-border bg-white p-0.5"
          />
        ) : (
          <div className="flex h-28 w-28 shrink-0 items-center justify-center rounded border border-workshop-border text-[11px] text-workshop-muted">
            QR…
          </div>
        )}
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-xs text-workshop-muted">Gleich WLAN · QR scannen · Foto hochladen</p>
          <code className="block truncate rounded border border-workshop-border bg-black/30 px-2 py-1.5 font-mono text-[11px] text-workshop-accent" title={phoneUrl}>
            {phoneUrl}
          </code>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void copy()}
              className="rounded-md border border-workshop-border px-2.5 py-1 text-xs font-semibold text-workshop-text hover:border-workshop-accent"
            >
              {copied ? "Kopiert" : "Kopieren"}
            </button>
            <a
              href={phoneUrl}
              className="rounded-md bg-workshop-accent px-2.5 py-1 text-xs font-semibold text-workshop-bg"
            >
              Öffnen
            </a>
          </div>
        </div>
      </div>

      {showAdvanced && (
        <div className="mt-3 space-y-2 border-t border-workshop-border pt-3 text-xs">
          {lanIps.length > 1 && (
            <div className="flex flex-wrap gap-1.5">
              {lanIps.map((ip) => (
                <button
                  key={ip}
                  type="button"
                  onClick={() => {
                    setSelectedIp(ip);
                    setHostOverride("");
                  }}
                  className={`rounded border px-2 py-1 font-mono text-[11px] ${
                    selectedIp === ip && !hostOverride.trim()
                      ? "border-workshop-accent bg-workshop-accent/15 text-workshop-accent"
                      : "border-workshop-border text-workshop-text hover:border-workshop-accent"
                  }`}
                >
                  {ip}
                </button>
              ))}
            </div>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-workshop-muted">Andere Basis-URL</span>
            <input
              value={hostOverride}
              onChange={(e) => setHostOverride(e.target.value)}
              placeholder={`http://192.168.x.x:${page.port || "5173"}`}
              className="rounded-md border border-workshop-border bg-workshop-bg px-2 py-1.5"
            />
          </label>
          {apiReachable === false && (
            <p className="text-workshop-warning">
              Nicht erreichbar – Firewall für Port {page.port || "5173"} prüfen.
            </p>
          )}
          {!detecting && lanIps.length === 0 && (
            <p className="text-workshop-warning">Keine WLAN-IP gefunden – URL oben manuell setzen.</p>
          )}
        </div>
      )}
    </div>
  );
}
