/** Ermittelt LAN-IPv4-Adressen im Browser (WebRTC), damit der QR nicht auf localhost zeigt. */

function isPrivateLanIp(ip: string): boolean {
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) return false;
  const [a, b] = ip.split(".").map(Number);
  if (a === 10) return true;
  if (a === 192 && b === 168) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  return false;
}

/** Bevorzugt Heim-WLAN (192.168.*) vor Docker/WSL-Bridges (oft 172.x). */
export function rankLanIp(ip: string): number {
  if (ip.startsWith("192.168.") && !ip.startsWith("192.168.65.")) return 0; // 65.x = Docker Desktop
  if (ip.startsWith("10.")) return 1;
  if (ip.startsWith("192.168.65.")) return 8;
  if (ip.startsWith("172.")) return 9;
  return 10;
}

/** Typische WLAN-/LAN-Adressen fürs Handy (ohne Docker-/WSL-Bridges). */
export function isPhoneReachableLanIp(ip: string): boolean {
  if (!isPrivateLanIp(ip) || ip.startsWith("127.")) return false;
  if (ip.startsWith("172.")) return false; // Docker/Compose-Netze
  if (ip.startsWith("192.168.65.")) return false; // Docker Desktop Host
  return true;
}

export async function discoverLanIpv4s(timeoutMs = 1500): Promise<string[]> {
  if (typeof window === "undefined" || typeof RTCPeerConnection === "undefined") {
    return [];
  }

  const found = new Set<string>();

  const collect = (text: string) => {
    const matches = text.matchAll(/(?:^|[\s.])((?:\d{1,3}\.){3}\d{1,3})(?:\s|$)/g);
    for (const m of matches) {
      const ip = m[1];
      if (ip && isPrivateLanIp(ip) && !ip.startsWith("127.")) found.add(ip);
    }
    // Chrome mDNS: candidate line often has IP before typ
    const cand = text.match(/candidate:.*? ((?:\d{1,3}\.){3}\d{1,3}) /);
    if (cand?.[1] && isPrivateLanIp(cand[1])) found.add(cand[1]);
  };

  try {
    const pc = new RTCPeerConnection({ iceServers: [] });
    pc.createDataChannel("lan-detect");
    pc.onicecandidate = (ev) => {
      if (ev.candidate?.candidate) collect(ev.candidate.candidate);
    };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise<void>((resolve) => {
      window.setTimeout(() => resolve(), timeoutMs);
    });
    pc.close();
  } catch {
    /* WebRTC ggf. blockiert */
  }

  return [...found].sort((a, b) => rankLanIp(a) - rankLanIp(b) || a.localeCompare(b));
}

export function buildPhoneUploadUrl(baseOrigin: string): string {
  return `${baseOrigin.replace(/\/$/, "")}/#/mobile-upload`;
}

export function pageOriginWithPort(): { origin: string; port: string; hostname: string } {
  const { protocol, hostname, port } = window.location;
  const effectivePort = port || (protocol === "https:" ? "443" : "80");
  return {
    hostname,
    port: effectivePort,
    origin: `${protocol}//${hostname}${port ? `:${port}` : ""}`,
  };
}
