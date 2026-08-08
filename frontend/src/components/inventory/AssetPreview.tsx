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

function StlPreview({ url }: { url: string }) {
  return (
    <PreviewErrorBoundary
      fallback={
        <div className="flex h-56 w-full items-center justify-center rounded-md border border-dashed border-workshop-border bg-black/20 text-xs text-workshop-muted">
          STL konnte nicht geladen werden
        </div>
      }
    >
      <div className="h-56 w-full overflow-hidden rounded-md border border-workshop-border bg-black/40">
        <Canvas frameloop="demand" dpr={[1, 1.5]} camera={{ position: [80, 80, 80], fov: 40 }}>
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

function PdfPreview({ url }: { url: string }) {
  return (
    <div className="flex h-72 w-full flex-col overflow-hidden rounded-md border border-workshop-border bg-black/30">
      <iframe title="PDF-Voransicht" src={`${url}#toolbar=1&navpanes=0`} className="h-full w-full bg-white" />
    </div>
  );
}

function ImagePreview({ url, compact }: { url: string; compact?: boolean }) {
  if (compact) {
    return <img src={url} alt="" className="h-10 w-10 shrink-0 rounded object-cover" />;
  }
  return (
    <img
      src={url}
      alt=""
      className="max-h-56 w-full rounded-md border border-workshop-border object-contain bg-black/20"
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

export function assetFileUrl(itemId: string): string {
  return `${apiBaseUrl()}/api/v1/inventory/items/${itemId}/file?inline=1`;
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
  const url = assetFileUrl(itemId);
  const lower = (fileName ?? "").toLowerCase();
  const isPdf = fileType === "pdf" || lower.endsWith(".pdf");
  const isStl = fileType === "stl" || lower.endsWith(".stl");
  const isImage = fileType === "image" || /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i.test(lower);

  if (isImage) return <ImagePreview url={url} compact={compact} />;
  if (isPdf) {
    if (compact) return <TypeBadge label="PDF" />;
    return <PdfPreview url={url} />;
  }
  if (isStl) {
    if (compact) return <TypeBadge label="STL" />;
    return <StlPreview url={url} />;
  }

  if (compact) {
    const label =
      fileType === "step" ? "STEP" : fileType === "f3d" ? "F3D" : fileType === "manual" ? "TXT" : "FILE";
    return <TypeBadge label={label} />;
  }

  return (
    <div className="flex h-24 items-center justify-center rounded-md border border-dashed border-workshop-border text-xs text-workshop-muted">
      Keine Voransicht für {fileType}
    </div>
  );
}
