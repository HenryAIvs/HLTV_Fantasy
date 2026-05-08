import { contextBridge, ipcRenderer } from "electron";

const API_BASE = "http://127.0.0.1:8000";
const parseJsonSafe = async (res) => {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
};

const requestJson = async (path, init) => {
  const res = await fetch(`${API_BASE}${path}`, init);
  const data = await parseJsonSafe(res);
  if (!res.ok) {
    const detail = data?.detail || `HTTP ${res.status}`;
    throw new Error(String(detail));
  }
  return data;
};

contextBridge.exposeInMainWorld("api", {
  get: (path) => requestJson(path),
  post: (path, body) =>
    requestJson(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  delete: (path) =>
    requestJson(path, {
      method: "DELETE",
    }),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  openHltvPage: (url) => ipcRenderer.invoke("open-hltv-page", url),
  readOpenedHltvPageText: () => ipcRenderer.invoke("read-opened-hltv-page-text"),
});
