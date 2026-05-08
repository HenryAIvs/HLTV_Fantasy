import { app, BrowserWindow, ipcMain, shell } from "electron";
import path from "path";
import { fileURLToPath } from "url";
import { spawn } from "child_process";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let backendProcess = null;
let hltvWindow = null;
let hltvSessionConfigured = false;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const getReadablePageText = async (win) => {
  return win.webContents.executeJavaScript(
    `(() => {
      const body = document && document.body;
      if (!body) return "";
      const text = String(body.innerText || "").trim();
      if (text.length >= 120) return text;
      const root = document.querySelector("#app") || document.documentElement;
      return String((root && root.innerText) || text || "").trim();
    })()`,
    true
  );
};

const resolvePython = () => {
  // Prefer repo-local venv python if it exists; fall back to system python.
  const venvPython = path.join(__dirname, "..", ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python";
};

const startBackend = () => {
  const repoRoot = path.join(__dirname, "..");
  // Run as a module so package imports work regardless of cwd.
  backendProcess = spawn(resolvePython(), ["-m", "backend.main"], {
    stdio: "inherit",
    cwd: repoRoot,
  });
};

const createWindow = () => {
  const win = new BrowserWindow({
    width: 1300,
    height: 850,
    minWidth: 1000,
    minHeight: 700,
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

const ensureHltvWindow = () => {
  if (hltvWindow && !hltvWindow.isDestroyed()) {
    return hltvWindow;
  }
  hltvWindow = new BrowserWindow({
    width: 1260,
    height: 900,
    minWidth: 960,
    minHeight: 700,
    autoHideMenuBar: true,
    webPreferences: {
      // Dedicated persistent session partition for HLTV so challenge cookies can survive.
      partition: "persist:hltv",
    },
  });

  if (!hltvSessionConfigured) {
    const blockedHosts = [
      "sync.inmobi.com",
      "cpmstar.com",
      "googlesyndication.com",
      "doubleclick.net",
      "adnxs.com",
      "criteo.com",
      "taboola.com",
      "outbrain.com",
    ];
    hltvWindow.webContents.session.webRequest.onBeforeRequest((details, callback) => {
      try {
        const host = new URL(details.url).hostname.toLowerCase();
        const shouldBlock = blockedHosts.some(
          (blocked) => host === blocked || host.endsWith(`.${blocked}`)
        );
        callback({ cancel: shouldBlock });
      } catch {
        callback({ cancel: false });
      }
    });
    hltvSessionConfigured = true;
  }

  hltvWindow.on("closed", () => {
    hltvWindow = null;
  });
  return hltvWindow;
};

app.whenReady().then(() => {
  ipcMain.handle("open-external", async (_event, url) => {
    await shell.openExternal(String(url || ""));
    return { status: "ok" };
  });

  ipcMain.handle("open-hltv-page", async (_event, rawUrl) => {
    const url = String(rawUrl || "").trim();
    if (!/^https:\/\/www\.hltv\.org\//i.test(url)) {
      throw new Error("Invalid HLTV URL");
    }
    const win = ensureHltvWindow();
    await win.loadURL(url);
    win.show();
    win.focus();
    return { status: "ok", url };
  });

  ipcMain.handle("read-opened-hltv-page-text", async () => {
    if (!hltvWindow || hltvWindow.isDestroyed()) {
      throw new Error("No HLTV page is currently open in the app.");
    }
    const currentUrl = String(hltvWindow.webContents.getURL() || "");
    if (!/^https:\/\/www\.hltv\.org\//i.test(currentUrl)) {
      throw new Error("Opened window is not on an HLTV page.");
    }
    // Wait a short period for async client-side rendering before reading text.
    let text = "";
    for (let attempt = 0; attempt < 8; attempt += 1) {
      // Ensure any in-flight navigations settle between attempts.
      try {
        if (hltvWindow.webContents.isLoading()) {
          await hltvWindow.webContents.executeJavaScript("void 0", true);
        }
      } catch {
        // Ignore transient execution errors during navigation.
      }
      text = String((await getReadablePageText(hltvWindow)) || "").trim();
      if (text.length >= 120) {
        break;
      }
      await sleep(500);
    }
    if (text.length < 120) {
      throw new Error("Opened HLTV page has no readable text yet. Wait for it to fully load and retry.");
    }
    return {
      status: "ok",
      url: currentUrl,
      text,
    };
  });

  startBackend();
  createWindow();
});

app.on("will-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
