import { app, BrowserWindow, ipcMain, shell } from "electron";
import path from "path";
import { fileURLToPath } from "url";
import { spawn } from "child_process";
import http from "http";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const repoRoot = path.join(__dirname, "..");
// The backend picks a free port at startup (memorized one first, else the next
// free one) and memorizes it here together with its pid — see
// backend/services/backend_port.py. Nothing in the app assumes 8000.
const PORT_FILE = path.join(repoRoot, ".runtime", "backend-port.json");
const DEFAULT_PORT = 8000;
const APP_ID = "hltv-fantasy";

let backendProcess = null;
// Only true when THIS app started the backend. When the always-on backend
// (auto-started at logon) is already running we just connect to it and must
// NOT kill it on quit — closing the window should leave the scheduler running.
let weStartedBackend = false;
// Resolved base URL handed to the preload/renderer via the "api-base" IPC.
let apiBase = `http://127.0.0.1:${DEFAULT_PORT}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const resolvePython = () => {
  // Prefer repo-local venv python if it exists; fall back to system python.
  const venvPython = path.join(repoRoot, ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python";
};

const readPortInfo = () => {
  try {
    const info = JSON.parse(fs.readFileSync(PORT_FILE, "utf8"));
    if (info && Number.isInteger(info.port)) return info;
  } catch {
    // missing or half-written file: treat as "no memorized port"
  }
  return null;
};

// GET /health and only accept an answer that identifies as OUR backend, so a
// stranger owning the port is never mistaken for a running backend.
const probeBackend = (port) =>
  new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/health`, { timeout: 1500 }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        try {
          const data = JSON.parse(body);
          resolve(res.statusCode === 200 && data?.app === APP_ID ? data : null);
        } catch {
          resolve(null);
        }
      });
    });
    req.on("error", () => resolve(null));
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
  });

const findRunningBackend = async () => {
  const info = readPortInfo();
  const ports = [...new Set([info?.port, DEFAULT_PORT].filter(Number.isInteger))];
  for (const port of ports) {
    if (await probeBackend(port)) return port;
  }
  return null;
};

// After spawning, the backend writes the port it actually bound to the port
// file; wait for that entry to answer /health.
const waitForBackend = async (attempts = 60) => {
  for (let i = 0; i < attempts; i += 1) {
    const info = readPortInfo();
    if (info && (await probeBackend(info.port))) return info.port;
    await sleep(500);
  }
  return null;
};

const ensureBackend = async () => {
  // Connect to an already-running (auto-started) backend if present; otherwise
  // spawn our own and remember that we own its lifetime.
  const running = await findRunningBackend();
  if (running) {
    apiBase = `http://127.0.0.1:${running}`;
    weStartedBackend = false;
    return;
  }
  // Run as a module so package imports work regardless of cwd.
  backendProcess = spawn(resolvePython(), ["-m", "backend.main"], {
    stdio: "inherit",
    cwd: repoRoot,
  });
  backendProcess.on("exit", () => {
    backendProcess = null;
  });
  const port = await waitForBackend();
  if (port) {
    apiBase = `http://127.0.0.1:${port}`;
  }
  // If the child found a sibling already running it exits by itself and leaves
  // the port file pointing at that sibling, so "our child is still alive" is
  // the ownership test. (Pids are useless here: the venv's python.exe is a
  // launcher that runs the real interpreter as a child process.)
  weStartedBackend = backendProcess !== null;
};

const createWindow = () => {
  // Minimum sized so the player/team modals (fixed-height cards) always fit
  // without needing an internal scrollbar.
  const win = new BrowserWindow({
    width: 1400,
    height: 1000,
    minWidth: 1280,
    minHeight: 980,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
    },
  });

  const dev = process.env.VITE_DEV_SERVER === "true";
  if (dev) {
    win.loadURL("http://localhost:5173/");
  } else {
    win.loadFile(path.join(__dirname, "dist", "index.html"));
  }
};

app.whenReady().then(async () => {
  ipcMain.handle("open-external", async (_event, url) => {
    await shell.openExternal(String(url || ""));
    return { status: "ok" };
  });
  // Synchronous so the preload can bake the base URL in before the renderer
  // makes its first request.
  ipcMain.on("api-base", (event) => {
    event.returnValue = apiBase;
  });

  await ensureBackend();
  createWindow();
});

app.on("will-quit", () => {
  // Leave an already-running (auto-started) backend alone; only stop one we
  // spawned ourselves.
  if (backendProcess && weStartedBackend) {
    backendProcess.kill();
  }
});
