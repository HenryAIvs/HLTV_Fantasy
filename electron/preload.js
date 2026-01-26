import { contextBridge } from "electron";

const API_BASE = "http://127.0.0.1:8000";
const json = (r) => r.json();

contextBridge.exposeInMainWorld("api", {
  get: (path) => fetch(`${API_BASE}${path}`).then(json),
  post: (path, body) =>
    fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json),
  delete: (path) =>
    fetch(`${API_BASE}${path}`, {
      method: "DELETE",
    }).then(json),
});
