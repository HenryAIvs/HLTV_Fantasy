// Some third-party packages ship their own yarn.lock / package-lock.json.
// npm never reads them, but vulnerability scanners do, and they pin ancient
// versions. Drop them from node_modules after every install.
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..", "node_modules");
const stray = new Set(["yarn.lock", "package-lock.json"]);
let removed = 0;

function walk(dir) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== ".bin") walk(full);
    } else if (dir !== root && stray.has(entry.name)) {
      fs.rmSync(full);
      removed += 1;
    }
  }
}

walk(root);
console.log(`prune-lockfiles: removed ${removed} stray lockfile(s)`);
