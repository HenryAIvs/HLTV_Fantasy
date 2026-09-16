// CommonJS on purpose: the renderer is sandboxed (Electron default), and a
// sandboxed preload cannot use ESM imports - with the old .js/ESM file the
// bridge silently failed to load and the renderer fell back to fetching
// 127.0.0.1:8000 directly. package.json is "type": "module", so .cjs.
const { contextBridge, ipcRenderer } = require("electron");

// The main process resolved which backend to talk to: the local one it found
// or spawned (operator checkout) or the hosted one from the website's api.json
// (public build); ask it once, sync, so the base URL is baked in before the
// renderer's first request.
const API_BASE = ipcRenderer.sendSync("api-base") || "http://127.0.0.1:8000";
const APP_INFO = ipcRenderer.sendSync("app-info") || { publicBuild: false, version: "dev", siteUrl: "", packaged: false };

const parseJsonSafe = async (res) => {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
};

const requestJson = async (path, init = {}, timeoutMs = 60000) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  const headers = { ...(init.headers || {}) };
  // The public app is treated as public by the backend even on the operator's
  // own machine (backend/services/public_access.py).
  if (APP_INFO.publicBuild) headers["X-Public-Client"] = "1";
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers, signal: controller.signal });
  } catch (e) {
    if (e?.name === "AbortError") {
      throw new Error(
        APP_INFO.publicBuild
          ? "The server did not respond in time. Please try again in a moment."
          : "Request timed out - the backend may still be working. Wait a moment and retry."
      );
    }
    if (APP_INFO.publicBuild) {
      throw new Error("Could not reach the server. Check your connection and try again.");
    }
    throw e;
  } finally {
    clearTimeout(timeout);
  }
  const data = await parseJsonSafe(res);
  if (!res.ok) {
    const detail = data?.detail || `HTTP ${res.status}`;
    throw new Error(String(detail));
  }
  return data;
};

contextBridge.exposeInMainWorld("api", {
  baseUrl: API_BASE,
  appInfo: APP_INFO,
  get: (path, timeoutMs) => requestJson(path, {}, timeoutMs),
  post: (path, body, timeoutMs) =>
    requestJson(
      path,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      timeoutMs
    ),
  delete: (path) =>
    requestJson(path, {
      method: "DELETE",
    }),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  onUpdateStatus: (callback) => {
    ipcRenderer.on("update-status", (_event, status) => callback(status));
  },
  installUpdate: () => ipcRenderer.invoke("install-update"),
  checkForUpdates: () => ipcRenderer.invoke("check-updates"),
});
