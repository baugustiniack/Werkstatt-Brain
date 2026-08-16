/** Client-side Bildkompression vor Chat-/Inventar-Upload (Host-Schutz). */

const DEFAULT_MAX_SIDE = 1280;
const DEFAULT_QUALITY = 0.72;
const DEFAULT_MAX_BYTES = 900_000;

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Bild konnte nicht geladen werden"));
    };
    img.src = url;
  });
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("Kompression fehlgeschlagen"))),
      type,
      quality,
    );
  });
}

/**
 * Verkleinert Fotos vor dem Upload (max. Kante / Zielgröße).
 * Nicht-Bilder und HEIC (Browser oft ohne Decode) unverändert zurück.
 */
export async function compressImageForUpload(
  file: File,
  options?: { maxSide?: number; quality?: number; maxBytes?: number },
): Promise<File> {
  const type = (file.type || "").toLowerCase();
  if (!type.startsWith("image/") || type.includes("heic") || type.includes("heif") || type === "image/svg+xml") {
    return file;
  }

  const maxSide = options?.maxSide ?? DEFAULT_MAX_SIDE;
  const maxBytes = options?.maxBytes ?? DEFAULT_MAX_BYTES;
  let quality = options?.quality ?? DEFAULT_QUALITY;

  try {
    const img = await loadImage(file);
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth || img.width, img.naturalHeight || img.height, 1));
    const w = Math.max(1, Math.round((img.naturalWidth || img.width) * scale));
    const h = Math.max(1, Math.round((img.naturalHeight || img.height) * scale));

    // Schon klein genug und unter Limit → Original behalten
    if (scale >= 1 && file.size <= maxBytes) {
      return file;
    }

    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.drawImage(img, 0, 0, w, h);

    let blob = await canvasToBlob(canvas, "image/jpeg", quality);
    while (blob.size > maxBytes && quality > 0.45) {
      quality -= 0.08;
      blob = await canvasToBlob(canvas, "image/jpeg", quality);
    }

    const base = file.name.replace(/\.[^.]+$/, "") || "upload";
    return new File([blob], `${base}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
  } catch {
    return file;
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
