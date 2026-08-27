import { app, BrowserWindow, ipcMain, shell } from "electron";
import path from "path";
import { fileURLToPath } from "url";
import { spawn } from "child_process";
import http from "http";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let backendProcess = null;
// Only true when THIS app started the backend. When the always-on backend
// (auto-started at logon) is already running we just connect to it and must
// NOT kill it on quit — closing the window should leave the scheduler running.
let weStartedBackend = false;

const resolvePython = () => {
  // Prefer repo-local venv python if it exists; fall back to system python.
  const venvPython = path.join(__dirname, "..", ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python";
};

const isBackendUp = () =>
  new Promise((resolve) => {
    const req = http.get("http://127.0.0.1:8000/health", { timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });

const waitForBackend = async (attempts = 40) => {
  for (let i = 0; i < attempts; i += 1) {
    if (await isBackendUp()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
};

const ensureBackend = async () => {
  // Connect to an already-running (auto-started) backend if present; otherwise
  // spawn our own and remember that we own its lifetime.
  if (await isBackendUp()) {
    weStartedBackend = false;
    return;
  }
  const repoRoot = path.join(__dirname, "..");
  // Run as a module so package imports work regardless of cwd.
  backendProcess = spawn(resolvePython(), ["-m", "backend.main"], {
    stdio: "inherit",
    cwd: repoRoot,
  });
  weStartedBackend = true;
  await waitForBackend();
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
