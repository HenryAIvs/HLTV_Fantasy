import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import path from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";
import { spawn } from "child_process";
import http from "http";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const require = createRequire(import.meta.url);

const repoRoot = path.join(__dirname, "..");
// The backend picks a free port at startup (memorized one first, else the next
// free one) and memorizes it here together with its pid — see
// backend/services/backend_port.py. Nothing in the app assumes 8000.
const PORT_FILE = path.join(repoRoot, ".runtime", "backend-port.json");
const DEFAULT_PORT = 8000;
const APP_ID = "hltv-fantasy";

// ---------------------------------------------------------------------------
// Public build: the installed app distributed from the website. It never runs
// a backend of its own; it talks to the operator's hosted backend, whose URL
// comes from the website's api.json (so the server can move without a new
// release), falling back to the URL bundled in public-config.json. Set
// HLTV_PUBLIC=1 to run the repo checkout in public mode, HLTV_API_BASE to
// point it at any backend.
const publicBuild = app.isPackaged || process.env.HLTV_PUBLIC === "1";
const readPublicConfig = () => {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, "public-config.json"), "utf8"));
  } catch {
    return {};
  }
};
const PUBLIC_CONFIG = readPublicConfig();

let autoUpdater = null;
try {
  autoUpdater = require("electron-updater").autoUpdater;
} catch {
  autoUpdater = null; // dev checkout without the dependency installed
}

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
  if (fs.existsSync(venvPython)) return venvPython;
  return "python";
};

const readPortFile = () => {
  try {
    const raw = fs.readFileSync(PORT_FILE, "utf8");
    const info = JSON.parse(raw);
    const port = Number(info.port);
    return Number.isFinite(port) && port > 0 ? port : null;
  } catch {
    return null;
  }
};

const probeHealth = (port) =>
  new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/health", timeout: 1500 }, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => {
        try {
          const data = JSON.parse(body);
          resolve(res.statusCode === 200 && data.app === APP_ID);
        } catch {
          resolve(false);
        }
      });
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });

const findRunningBackend = async () => {
  const candidates = [];
  const memorized = readPortFile();
  if (memorized) candidates.push(memorized);
  for (let p = DEFAULT_PORT; p < DEFAULT_PORT + 100; p++) {
    if (!candidates.includes(p)) candidates.push(p);
  }
  for (const port of candidates) {
    if (await probeHealth(port)) return port;
  }
  return null;
};

// After spawning, the backend writes the port it actually bound to the port
// file; poll that (and /health) until it answers.
const waitForBackend = async () => {
  for (let i = 0; i < 100; i++) {
    const port = readPortFile();
    if (port && (await probeHealth(port))) return port;
    await sleep(300);
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

const fetchJsonWithTimeout = async (url, ms) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    const res = await fetch(url, { signal: controller.signal, cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
};

const resolvePublicApiBase = async () => {
  const override = String(process.env.HLTV_API_BASE || "").trim();
  if (override) return override.replace(/\/+$/, "");
  const configUrl = String(PUBLIC_CONFIG.configUrl || "").trim();
  if (configUrl) {
    const remote = await fetchJsonWithTimeout(configUrl, 6000);
    const base = String(remote?.apiBase || "").trim();
    if (base) return base.replace(/\/+$/, "");
  }
  return String(PUBLIC_CONFIG.apiBase || `http://127.0.0.1:${DEFAULT_PORT}`).replace(/\/+$/, "");
};

let mainWindow = null;

// Updater log: %APPDATA%/CS Fantasy Toolkit/updater.log. electron-updater's
// own messages plus our lifecycle events, so "it restarted on its own" can be
// traced after the fact.
const updaterLogPath = () => path.join(app.getPath("userData"), "updater.log");
const ulog = (level, ...parts) => {
  const line = `${new Date().toISOString()} ${level.padEnd(5)} ${parts.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ")}\n`;
  try {
    fs.appendFileSync(updaterLogPath(), line);
  } catch {
    /* logging must never break the app */
  }
};
const updaterLogger = {
  info: (...a) => ulog("info", ...a),
  warn: (...a) => ulog("warn", ...a),
  error: (...a) => ulog("error", ...a),
  debug: (...a) => ulog("debug", ...a),
};

const sendUpdateStatus = (status) => {
  ulog("info", "status", status);
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("update-status", status);
};

// The title-bar banner is easy to miss; when the download is ready ask once
// with a native dialog. "Later" leaves the banner in place, and the update
// still installs on the next quit.
let askedToRestart = false;
const offerRestart = async (version) => {
  if (askedToRestart || !mainWindow || mainWindow.isDestroyed()) return;
  askedToRestart = true;
  const { response } = await dialog.showMessageBox(mainWindow, {
    type: "info",
    title: "Update ready",
    message: `CS Fantasy Toolkit ${version ? `v${version} ` : ""}has been downloaded.`,
    detail: "Restart now to install it, or keep working and it installs when you next close the app.",
    buttons: ["Restart now", "Later"],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  ulog("info", "restart dialog answered", response === 0 ? "restart now" : "later");
  if (response === 0 && autoUpdater) autoUpdater.quitAndInstall();
};

// electron-updater against the GitHub release feed (package.json "publish").
// Downloads in the background; the renderer shows a "restart to update"
// prompt once the new version is ready.
const setupUpdater = () => {
  if (!app.isPackaged || !autoUpdater) return;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.logger = updaterLogger;
  ulog("info", "app start", { version: app.getVersion(), packaged: app.isPackaged });
  autoUpdater.on("checking-for-update", () => sendUpdateStatus({ status: "checking" }));
  autoUpdater.on("update-available", (info) => sendUpdateStatus({ status: "available", version: info?.version }));
  autoUpdater.on("update-not-available", () => sendUpdateStatus({ status: "none" }));
  autoUpdater.on("download-progress", (p) => sendUpdateStatus({ status: "downloading", percent: Math.round(p?.percent || 0) }));
  autoUpdater.on("update-downloaded", (info) => {
    sendUpdateStatus({ status: "downloaded", version: info?.version });
    offerRestart(info?.version);
  });
  autoUpdater.on("error", (err) => sendUpdateStatus({ status: "error", message: String(err?.message || err) }));
  // Check shortly after launch, then every 30 minutes, and whenever the window
  // regains focus (at most once every 10 minutes) so a release published while
  // the app sits open is picked up within minutes rather than hours.
  let lastCheck = 0;
  const check = () => {
    lastCheck = Date.now();
    autoUpdater.checkForUpdates().catch(() => {});
  };
  setTimeout(check, 10 * 1000);
  setInterval(check, 30 * 60 * 1000);
  app.on("browser-window-focus", () => {
    if (Date.now() - lastCheck > 10 * 60 * 1000) check();
  });
};

const createWindow = () => {
  // Minimum sized so the player/team modals (fixed-height cards) always fit
  // without needing an internal scrollbar.
  const win = new BrowserWindow({
    width: 1400,
    height: 1000,
    minWidth: 1280,
    minHeight: 980,
    // Window chrome in the app's palette: the native title bar is hidden and
    // replaced by the renderer's .titlebar strip (the drag region), with the
    // native minimise / maximise / close controls drawn dark by the overlay.
    // The menu bar stays out of sight (Alt reveals it; its shortcuts still work).
    backgroundColor: "#0a0c10",
    // Dev/unpacked runs only; the installed app carries the icon in the exe.
    icon: app.isPackaged ? undefined : path.join(__dirname, "build", "icon.ico"),
    titleBarStyle: "hidden",
    titleBarOverlay: { color: "#0a0c10", symbolColor: "#c6d0dc", height: 36 },
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
    },
  });

  const dev = process.env.VITE_DEV_SERVER === "true";
  if (dev) {
    win.loadURL("http://localhost:5173/");
  } else {
    win.loadFile(path.join(__dirname, "dist", "index.html"));
  }
  mainWindow = win;
  return win;
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
  ipcMain.on("app-info", (event) => {
    event.returnValue = {
      publicBuild,
      version: app.getVersion(),
      siteUrl: String(PUBLIC_CONFIG.siteUrl || ""),
      packaged: app.isPackaged,
    };
  });
  ipcMain.handle("install-update", () => {
    if (autoUpdater) autoUpdater.quitAndInstall();
    return { status: "ok" };
  });
  ipcMain.handle("check-updates", async () => {
    if (!app.isPackaged || !autoUpdater) return { status: "unavailable" };
    try {
      await autoUpdater.checkForUpdates();
      return { status: "ok" };
    } catch (e) {
      return { status: "error", message: String(e?.message || e) };
    }
  });

  if (publicBuild) {
    apiBase = await resolvePublicApiBase();
  } else {
    await ensureBackend();
  }
  createWindow();
  setupUpdater();
});

app.on("window-all-closed", () => {
  ulog("info", "window-all-closed -> quit");
  app.quit();
});

app.on("before-quit", () => ulog("info", "before-quit"));
process.on("uncaughtException", (err) => {
  ulog("error", "uncaughtException", String(err?.stack || err));
});

app.on("will-quit", () => {
  // Leave an already-running (auto-started) backend alone; only stop one we
  // spawned ourselves.
  if (backendProcess && weStartedBackend) {
    backendProcess.kill();
  }
});
