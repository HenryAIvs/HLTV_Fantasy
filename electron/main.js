import { app, BrowserWindow, ipcMain, shell } from "electron";
import path from "path";
import { fileURLToPath } from "url";
import { spawn } from "child_process";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let backendProcess = null;

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

app.whenReady().then(() => {
  ipcMain.handle("open-external", async (_event, url) => {
    await shell.openExternal(String(url || ""));
    return { status: "ok" };
  });

  startBackend();
  createWindow();
});

app.on("will-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
