import { Suspense, useEffect, useMemo, useState } from "react";
import { Canvas, useLoader } from "@react-three/fiber";
import { Bounds, Grid, OrbitControls } from "@react-three/drei";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import * as THREE from "three";

import { MediaFilePreview } from "../inventory/AssetPreview";

interface StlMeshProps {
  url: string;
  wireframe: boolean;
  onBoundingBox: (box: THREE.Box3 | null) => void;
}

function StlMesh({ url, wireframe, onBoundingBox }: StlMeshProps) {
  const geometry = useLoader(STLLoader, url);

  useMemo(() => {
    geometry.computeBoundingBox();
    onBoundingBox(geometry.boundingBox ?? null);
    geometry.center();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geometry]);

  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial color="#89b4fa" wireframe={wireframe} metalness={0.1} roughness={0.6} />
    </mesh>
  );
}

export interface ModelViewerPart {
  name: string;
  stlUrl: string;
}

/** Inventar-/Referenzvorschau im Model-Viewer-Panel. */
export interface ReferenceMediaPreview {
  url: string;
  label: string;
  kind: "image" | "pdf" | "stl" | "other";
  fileType?: string;
  /** Für PDF/STL-Inline-Vorschau über Asset-API. */
  itemId?: string;
}

interface ModelViewerProps {
  /** Einzelnes Modell (Legacy-/Single-Part-Fall). Wird ignoriert, sobald `parts` gesetzt ist. */
  stlUrl: string | null;
  /** Mehrere abgeschlossene Teile (Mehrteil-Ausarbeitung) – zeigt einen
   * einfachen Teil-Umschalter statt einer kombinierten Szene (bewusst außerhalb
   * des aktuellen Scopes, siehe Plan "Out of scope"). */
  parts?: ModelViewerPart[];
  selectedPartIndex?: number;
  onSelectPartIndex?: (index: number) => void;
  /** Großes Referenzbild (z. B. Inventar-Auswahl) statt/über dem leeren Viewer. */
  referencePreview?: ReferenceMediaPreview | null;
  onClearReferencePreview?: () => void;
}

/** Interaktiver WebGL-Viewer für das exportierte .stl-Modell (SPEC Kap. 5.2 Panel 3). */
export function ModelViewer({
  stlUrl,
  parts,
  selectedPartIndex = 0,
  onSelectPartIndex,
  referencePreview = null,
  onClearReferencePreview,
}: ModelViewerProps) {
  const [wireframe, setWireframe] = useState(false);
  const [boundingBox, setBoundingBox] = useState<THREE.Box3 | null>(null);
  const [showRef, setShowRef] = useState(true);

  const hasMultipleParts = (parts?.length ?? 0) > 1;
  const activeUrl = hasMultipleParts ? parts![Math.min(selectedPartIndex, parts!.length - 1)].stlUrl : stlUrl;
  const hasModel = Boolean(activeUrl);
  const showingReference = Boolean(referencePreview) && (showRef || !hasModel);

  useEffect(() => {
    if (referencePreview) setShowRef(true);
  }, [referencePreview?.url]);

  if (showingReference && referencePreview) {
    return (
      <div className="flex h-full min-h-[280px] flex-col gap-2">
        <div className="flex items-center justify-between gap-2 text-xs">
          <span className="truncate font-semibold text-workshop-accent">Referenz: {referencePreview.label}</span>
          <div className="flex shrink-0 gap-1.5">
            {hasModel && (
              <button
                type="button"
                onClick={() => setShowRef(false)}
                className="rounded-md border border-workshop-border px-2 py-1 text-workshop-text hover:bg-workshop-bg"
              >
                Zum 3D-Modell
              </button>
            )}
            {onClearReferencePreview && (
              <button
                type="button"
                onClick={onClearReferencePreview}
                className="rounded-md border border-workshop-border px-2 py-1 text-workshop-muted hover:text-workshop-text"
              >
                Schließen
              </button>
            )}
          </div>
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-md border border-workshop-border bg-black/40 p-2">
          {referencePreview.itemId &&
          (referencePreview.kind === "pdf" ||
            referencePreview.kind === "stl" ||
            referencePreview.kind === "image") ? (
            <div className="h-full w-full min-h-[260px]">
              <MediaFilePreview
                itemId={referencePreview.itemId}
                fileType={referencePreview.fileType || referencePreview.kind}
                fileName={referencePreview.label}
                heightClass="h-full min-h-[260px]"
                eager3d
              />
            </div>
          ) : referencePreview.kind === "image" ? (
            <img
              src={referencePreview.url}
              alt={referencePreview.label}
              className="max-h-full max-w-full object-contain"
            />
          ) : referencePreview.kind === "pdf" ? (
            <iframe
              title={referencePreview.label}
              src={`${referencePreview.url}#toolbar=1&navpanes=0`}
              className="h-full min-h-[260px] w-full rounded bg-white"
            />
          ) : referencePreview.kind === "stl" ? (
            <div className="h-full min-h-[260px] w-full">
              <Canvas frameloop="demand" dpr={[1, 1.5]} camera={{ position: [120, 120, 120], fov: 45 }}>
                <ambientLight intensity={0.65} />
                <directionalLight position={[100, 150, 100]} intensity={1} />
                <Suspense fallback={null}>
                  <Bounds fit clip observe margin={1.4}>
                    <StlMesh
                      key={referencePreview.url}
                      url={referencePreview.url}
                      wireframe={false}
                      onBoundingBox={() => undefined}
                    />
                  </Bounds>
                </Suspense>
                <OrbitControls makeDefault />
              </Canvas>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 text-sm text-workshop-muted">
              <span className="font-semibold uppercase tracking-wide">
                {referencePreview.fileType || "Datei"}
              </span>
              <span className="max-w-xs truncate text-workshop-text">{referencePreview.label}</span>
              <a
                href={referencePreview.url}
                target="_blank"
                rel="noreferrer"
                className="text-workshop-accent underline"
              >
                Öffnen
              </a>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!activeUrl) {
    return (
      <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-2 rounded-md border border-dashed border-workshop-border text-sm text-workshop-muted">
        <span>Noch kein Modell generiert.</span>
        <span className="text-xs">Inventar-Referenz tippen → Vorschau erscheint hier.</span>
      </div>
    );
  }

  const size = boundingBox ? boundingBox.getSize(new THREE.Vector3()) : null;

  return (
    <div className="flex h-full min-h-[280px] flex-col gap-2">
      {referencePreview && (
        <button
          type="button"
          onClick={() => setShowRef(true)}
          className="self-start rounded-md border border-workshop-accent/50 px-2 py-1 text-xs text-workshop-accent hover:bg-workshop-accent/10"
        >
          Referenz anzeigen
        </button>
      )}
      {hasMultipleParts && (
        <div className="flex flex-wrap gap-1 text-xs">
          {parts!.map((part, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => onSelectPartIndex?.(idx)}
              className={`rounded-md border px-2 py-1 ${
                idx === selectedPartIndex
                  ? "border-workshop-accent bg-workshop-accent/20 text-workshop-accent"
                  : "border-workshop-border text-workshop-muted hover:bg-workshop-bg"
              }`}
            >
              {idx + 1}. {part.name}
            </button>
          ))}
        </div>
      )}
      <div className="flex items-center justify-between text-xs">
        <button
          type="button"
          onClick={() => setWireframe((v) => !v)}
          className="rounded-md border border-workshop-border px-2 py-1 text-workshop-text hover:bg-workshop-bg"
        >
          {wireframe ? "Solid-Ansicht" : "Wireframe-Ansicht"}
        </button>
        {size && (
          <span className="font-mono text-workshop-muted">
            {size.x.toFixed(1)} × {size.y.toFixed(1)} × {size.z.toFixed(1)} mm
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-hidden rounded-md border border-workshop-border bg-black/30">
        <Canvas shadows camera={{ position: [120, 120, 120], fov: 45 }}>
          <ambientLight intensity={0.6} />
          <directionalLight position={[100, 150, 100]} intensity={1} castShadow />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.4}>
              <StlMesh key={activeUrl} url={activeUrl} wireframe={wireframe} onBoundingBox={setBoundingBox} />
            </Bounds>
          </Suspense>
          <Grid infiniteGrid fadeDistance={400} cellColor="#3b3b52" sectionColor="#89b4fa" />
          <OrbitControls makeDefault />
        </Canvas>
      </div>
    </div>
  );
}
