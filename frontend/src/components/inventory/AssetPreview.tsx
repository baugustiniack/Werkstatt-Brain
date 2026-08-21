import { Component, Suspense, useMemo, type ReactNode } from "react";
import { Canvas, useLoader } from "@react-three/fiber";
import { Bounds, OrbitControls } from "@react-three/drei";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

import { apiBaseUrl } from "../../api/client";
import type { AssetFileType } from "../../api/types";

class PreviewErrorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { error: boolean }> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  render() {
    if (this.state.error) return this.props.fallback;
    return this.props.children;
  }
}

function StlMesh({ url }: { url: string }) {
  const geometry = useLoader(STLLoader, url);

  useMemo(() => {
    geometry.computeBoundingBox();
    geometry.center();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry]);

  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial color="#89b4fa" metalness={0.15} roughness={0.55} />
    </mesh>
  );
}

function StlPreview({ url, heightClass = "h-56" }: { url: string; heightClass?: string }) {
  return (
    <PreviewErrorBoundary
      fallback={
        <div
          className={`flex w-full items-center justify-center rounded-md border border-dashed border-workshop-border bg-black/20 text-xs text-workshop-muted ${heightClass}`}
        >
          STL konnte nicht geladen werden
        </div>
      }
    >
      <div className={`w-full overflow-hidden rounded-md border border-workshop-border bg-black/40 ${heightClass}`}>
        <Canvas frameloop="demand" dpr={[1, 1]} camera={{ position: [80, 80, 80], fov: 40 }}>
          <ambientLight intensity={0.7} />
          <directionalLight position={[60, 100, 40]} intensity={1.1} />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.35}>
              <StlMesh key={url} url={url} />
            </Bounds>
          </Suspense>
          <OrbitControls makeDefault enablePan={false} />
        </Canvas>
      </div>
    </PreviewErrorBoundary>
  );
}

function PdfPreview({ url, heightClass = "h-72" }: { url: string; heightClass?: string }) {
  return (
    <div
      className={`flex w-full flex-col overflow-hidden rounded-md border border-workshop-border bg-black/30 ${heightClass}`}
    >
      <iframe title="PDF-Voransicht" src={`${url}#toolbar=0&navpanes=0&scrollbar=0`} className="h-full w-full bg-white" />
    </div>
  );
}

function ImagePreview({ url, className }: { url: string; className?: string }) {
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      decoding="async"
      className={
        className ??
        "max-h-56 w-full rounded-md border border-workshop-border object-contain bg-black/20"
      }
    />
  );
}

function TypeBadge({ label }: { label: string }) {
  return (
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded border border-workshop-border bg-workshop-panel text-[9px] font-bold uppercase tracking-wide text-workshop-muted">
      {label}
    </div>
  );
}

export type PreviewKind = "image" | "pdf" | "stl" | "other";

export function resolvePreviewKind(fileType: string, fileName?: string | null): PreviewKind {
  const lower = (fileName ?? "").toLowerCase();
  if (fileType === "image" || /\.(png|jpe?g|webp|gif|bmp|tiff?|heic|heif)$/i.test(lower)) return "image";
  if (fileType === "pdf" || lower.endsWith(".pdf")) return "pdf";
  if (fileType === "stl" || lower.endsWith(".stl")) return "stl";
  return "other";
}

/** Kompakte Listen-Vorschau ohne Datei-Download (verhindert Massen-Loads / Host-Freeze). */
export function AssetListIcon({
  fileType,
  fileName,
}: {
  fileType: AssetFileType;
  fileName?: string | null;
}) {
  const kind = resolvePreviewKind(fileType, fileName);
  if (kind === "image") return <TypeBadge label="IMG" />;
  if (kind === "pdf") return <TypeBadge label="PDF" />;
  if (kind === "stl") return <TypeBadge label="STL" />;
  const lower = (fileName ?? "").toLowerCase();
  if (fileType === "step" || lower.endsWith(".step") || lower.endsWith(".stp")) {
    return <TypeBadge label="STEP" />;
  }
  if (fileType === "f3d") return <TypeBadge label="F3D" />;
  if (fileType === "manual") return <TypeBadge label="TXT" />;
  return <TypeBadge label="FILE" />;
}

/** Kompakte Listen-Vorschau: echte Bild-Thumbnails, sonst Typ-Badge. */
export function AssetListThumbnail({
  itemId,
  fileType,
  fileName,
}: {
  itemId: string;
  fileType: AssetFileType | string;
  fileName?: string | null;
}) {
  const kind = resolvePreviewKind(fileType, fileName);
  if (kind === "image") {
    return (
      <img
        src={assetThumbUrl(itemId)}
        alt=""
        loading="lazy"
        decoding="async"
        className="h-10 w-10 shrink-0 rounded border border-workshop-border object-cover bg-black/20"
      />
    );
  }
  return <AssetListIcon fileType={fileType as AssetFileType} fileName={fileName} />;
}

export function assetFileUrl(itemId: string): string {
  return `${apiBaseUrl()}/api/v1/inventory/items/${itemId}/file?inline=1`;
}

export function assetThumbUrl(itemId: string): string {
  return `${apiBaseUrl()}/api/v1/inventory/items/${itemId}/file?inline=1&size=thumb`;
}

/**
 * Flexible Voransicht (Bild / PDF / STL).
 * `eager3d`: schwere Medien (PDF-iframe / STL-WebGL) nur laden wenn true –
 * sonst friert das Raster bei vielen Einträgen ein.
 */
export function MediaFilePreview({
  itemId,
  fileType,
  fileName,
  heightClass = "h-56",
  eager3d = true,
}: {
  itemId: string;
  fileType: AssetFileType | string;
  fileName?: string | null;
  heightClass?: string;
  eager3d?: boolean;
}) {
  const url = assetFileUrl(itemId);
  const kind = resolvePreviewKind(fileType, fileName);

  if (kind === "image") {
    return (
      <ImagePreview
        url={url}
        className={`w-full rounded-md border border-workshop-border object-contain bg-black/20 ${heightClass}`}
      />
    );
  }
  if (kind === "pdf") {
    if (!eager3d) {
      return (
        <div className={`flex w-full flex-col items-center justify-center gap-1 bg-black/35 ${heightClass}`}>
          <span className="text-xs font-semibold uppercase text-workshop-accent">PDF</span>
          <span className="max-w-[90%] truncate px-2 text-[11px] text-workshop-text">
            {fileName || "Dokument"}
          </span>
          <span className="text-[10px] text-workshop-muted">Tippen → Vorschau</span>
        </div>
      );
    }
    return <PdfPreview url={url} heightClass={heightClass} />;
  }
  if (kind === "stl") {
    if (!eager3d) {
      return (
        <div className={`flex w-full flex-col items-center justify-center gap-1 bg-black/35 ${heightClass}`}>
          <span className="text-xs font-semibold uppercase text-workshop-accent">STL</span>
          <span className="max-w-[90%] truncate px-2 text-[11px] text-workshop-text">
            {fileName || "3D-Modell"}
          </span>
          <span className="text-[10px] text-workshop-muted">Tippen → 3D-Vorschau</span>
        </div>
      );
    }
    return <StlPreview url={url} heightClass={heightClass} />;
  }

  return (
    <div className={`flex w-full flex-col items-center justify-center gap-1 bg-workshop-panel ${heightClass}`}>
      <span className="text-xs font-semibold uppercase text-workshop-muted">{fileType}</span>
      <span className="max-w-[90%] truncate px-2 text-[11px] text-workshop-text">{fileName || "Datei"}</span>
    </div>
  );
}

/** Voransicht für Inventar-Anhänge: Bild, PDF (Browser), STL (WebGL). */
export function AssetPreview({
  itemId,
  fileType,
  fileName,
  compact = false,
}: {
  itemId: string;
  fileType: AssetFileType;
  fileName?: string | null;
  compact?: boolean;
}) {
  if (compact) {
    return <AssetListThumbnail itemId={itemId} fileType={fileType} fileName={fileName} />;
  }
  return <MediaFilePreview itemId={itemId} fileType={fileType} fileName={fileName} />;
}
