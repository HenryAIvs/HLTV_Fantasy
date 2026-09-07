import { contextBridge, ipcRenderer } from "electron";

// The main process resolved which port the backend actually bound (it picks a
// free one and memorizes it in .runtime/backend-port.json); ask it once, sync.
const API_BASE = ipcRenderer.sendSync("api-base") || "http://127.0.0.1:8000";
const parseJsonSafe = async (res) => {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
};

const requestJson = async (path, init = {}, timeoutMs = 30000) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal });
  } catch (e) {
    if (e?.name === "AbortError") {
      throw new Error("Backend did not respond in time. Restart FastAPI and try again.");
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
});
