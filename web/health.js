/* Health badge: relay reachability + token state at a glance.
 *
 * Probe strategy: single-flight fetch of /api/auth/status (cheap, local)
 * and a HEAD-like GET of the relay /health only when a relay URL exists.
 * Never toasts — the badge is the surface. Pure logic lives in
 * deriveHealthState so it can be unit-tested without a browser. */
const $ = (id) => document.getElementById(id);

/* Pure: decide badge state from probe results.
 * - "relink"       token expired/dead (relay fine) — user must link again
 * - "ok"           linked (or linkable) and relay answers
 * - "waking"       relay probe still in flight / cold start suspected
 * - "rate_limited" relay answered 429
 * - "unreachable"  relay probe failed
 * - "nolink"       nothing linked but relay fine (normal unlinked state) */
export function deriveHealthState({ relayProbe, tokenExpired, linked }) {
  if (tokenExpired) return "relink";
  if (relayProbe === "pending") return "waking";
  if (relayProbe === 429) return "rate_limited";
  if (relayProbe === "error") return "unreachable";
  if (relayProbe === "ok") return linked ? "ok" : "nolink";
  return "nolink";
}

export const HEALTH_LABELS = {
  ok: "relay ok",
  nolink: "relay ok · not linked",
  waking: "relay waking…",
  unreachable: "relay unreachable",
  rate_limited: "relay rate-limited",
  relink: "relink needed",
};

const POLL_OK_MS = 60_000;
const POLL_BACKOFF_MS = 5 * 60_000;

export function initHealth() {
  const badge = $("health-badge");
  if (!badge) return;
  let timer = 0;
  let inFlight = false;
  let failures = 0;

  function paint(state) {
    badge.textContent = HEALTH_LABELS[state] || state;
    badge.dataset.state = state;
  }

  async function probe() {
    if (inFlight) return;
    inFlight = true;
    let relayProbe = "pending";
    let linked = false;
    let tokenExpired = false;
    try {
      const r = await fetch("/api/auth/status");
      const st = await r.json();
      linked = !!st.linked;
      const relayUrl = st?.relay?.url;
      if (relayUrl && st?.relay?.available) {
        try {
          const hr = await fetch(`${String(relayUrl).replace(/\/+$/, "")}/health`,
                                 { mode: "cors" });
          relayProbe = hr.status === 429 ? 429 : (hr.ok ? "ok" : "error");
        } catch {
          relayProbe = "error";
        }
      } else {
        relayProbe = "ok"; // no relay configured: nothing to probe
      }
    } catch {
      // local server unreachable — badge reflects relay as unknown/unreachable
      relayProbe = "error";
    }
    paint(deriveHealthState({ relayProbe, tokenExpired, linked }));
    inFlight = false;
    failures = relayProbe === "error" ? failures + 1 : 0;
    clearTimeout(timer);
    timer = setTimeout(probe, failures >= 3 ? POLL_BACKOFF_MS : POLL_OK_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") probe();
  });
  probe();
}
