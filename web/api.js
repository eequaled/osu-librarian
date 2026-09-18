/* API client: fetch wrappers with ETag support. No DOM here. */

let libraryVersion = null;

export async function status() {
  const r = await fetch("/api/status");
  if (!r.ok) throw new Error(`status ${r.status}`);
  return r.json();
}

/** Returns {version, maps} or {notModified:true} when ETag matches. */
export async function library() {
  const headers = libraryVersion ? { "If-None-Match": `"${libraryVersion}"` } : {};
  const r = await fetch("/api/library", { headers });
  if (r.status === 304) return { notModified: true };
  if (!r.ok) throw new Error(`library ${r.status}`);
  const data = await r.json();
  libraryVersion = data.version;
  return data;
}

export function cachedVersion() {
  return libraryVersion;
}

export async function detectInstalls() {
  const r = await fetch("/api/detect");
  if (!r.ok) throw new Error(`detect ${r.status}`);
  return (await r.json()).installs || [];
}

export async function useInstall(kind, path) {
  const r = await fetch("/api/use-install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, path }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `use-install ${r.status}`);
  libraryVersion = null;
  return data;
}

export async function setMode(mode) {  const r = await fetch("/api/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!r.ok) throw new Error(`mode ${r.status}`);
  libraryVersion = null; // different mode = different library version
  return r.json();
}

export async function startScan(mode, fresh = false) {  const r = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, fresh }),
  });
  if (!r.ok) throw new Error((await r.json()).error || `scan ${r.status}`);
  return (await r.json()).job_id;
}

export async function job(id) {
  const r = await fetch(`/api/jobs/${id}`);
  if (!r.ok) throw new Error(`job ${r.status}`);
  return r.json();
}

/** Poll until done/error; onProgress(job) each tick. Resolves the final job. */
export function pollJob(id, onProgress, interval = 300) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const j = await job(id);
        onProgress?.(j);
        if (j.state === "done") return resolve(j);
        if (j.state === "error") return reject(new Error(j.error || "job failed"));
        setTimeout(tick, interval);
      } catch (e) {
        reject(e);
      }
    };
    tick();
  });
}

export async function authUrl() {
  const r = await fetch("/api/auth/url");
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `auth ${r.status}`);
  return data.url;
}

export async function submitCode(code) {
  const r = await fetch("/api/auth/code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `auth ${r.status}`);
  return data;
}

export async function startOnlineCheck() {
  const r = await fetch("/api/online-check", { method: "POST" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `online-check ${r.status}`);
  return data.job_id;
}

/** Error message for a failed export: server detail when present, else the HTTP status. */
export function exportFailureMessage(statusCode, body) {
  return (body && body.error) || `export ${statusCode}`;
}

/** Triggers a file download for the export endpoint. */
export async function downloadExport(ids, format) {
  const r = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, format }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(exportFailureMessage(r.status, data));
  const blob = await r.blob();
  const name = (r.headers.get("Content-Disposition") || "").match(/filename="(.+?)"/)?.[1]
    || `selection.${format === "collection" ? "db" : format}`;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
