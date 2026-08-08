import { Suspense, useMemo, useState } from "react";
import { Canvas, useLoader } from "@react-three/fiber";
import { Bounds, Grid, OrbitControls } from "@react-three/drei";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import * as THREE from "three";

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

interface ModelViewerProps {
  /** Einzelnes Modell (Legacy-/Single-Part-Fall). Wird ignoriert, sobald `parts` gesetzt ist. */
  stlUrl: string | null;
  /** Mehrere abgeschlossene Teile (Mehrteil-Ausarbeitung) – zeigt einen
   * einfachen Teil-Umschalter statt einer kombinierten Szene (bewusst außerhalb
   * des aktuellen Scopes, siehe Plan "Out of scope"). */
  parts?: ModelViewerPart[];
  selectedPartIndex?: number;
  onSelectPartIndex?: (index: number) => void;
}

/** Interaktiver WebGL-Viewer für das exportierte .stl-Modell (SPEC Kap. 5.2 Panel 3). */
export function ModelViewer({ stlUrl, parts, selectedPartIndex = 0, onSelectPartIndex }: ModelViewerProps) {
  const [wireframe, setWireframe] = useState(false);
  const [boundingBox, setBoundingBox] = useState<THREE.Box3 | null>(null);

  const hasMultipleParts = (parts?.length ?? 0) > 1;
  const activeUrl = hasMultipleParts ? parts![Math.min(selectedPartIndex, parts!.length - 1)].stlUrl : stlUrl;

  if (!activeUrl) {
    return (
      <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-2 rounded-md border border-dashed border-workshop-border text-sm text-workshop-muted">
        <span>Noch kein Modell generiert.</span>
        <span className="text-xs">Starte einen Workflow im Command Center.</span>
      </div>
    );
  }

  const size = boundingBox ? boundingBox.getSize(new THREE.Vector3()) : null;

  return (
    <div className="flex h-full min-h-[280px] flex-col gap-2">
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
