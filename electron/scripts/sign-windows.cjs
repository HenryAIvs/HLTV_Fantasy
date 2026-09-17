// electron-builder signing hook (win.signtoolOptions.sign): signs each Windows
// executable it is handed (the app exe, the NSIS helper elevate.exe, the
// uninstaller and the installer: four signings per release)
// with the Certum SimplySign cloud certificate via `ssign`
// (https://github.com/Le-Syl21/ssign), which talks to Certum over HTTPS and
// needs no SimplySign Desktop.
//
// Credentials come from the environment only, never from arguments:
//   CERTUM_EMAIL  the SimplySign login (Certum store account e-mail)
//   CERTUM_OTP    the TOTP seed from the SimplySign QR code (or CERTUM_TOKEN,
//                 a current 6-digit code, for a one-off signing by hand)
//   SSIGN_PATH    optional path to the ssign executable (default: on PATH)
//
// Without credentials the build stays unsigned and says so, so local builds
// keep working. With credentials, any signing failure fails the build: a
// release that was meant to be signed must never ship unsigned.
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");

const say = (message, extra) =>
  console.log(`  • sign-windows: ${message}${extra ? " " + JSON.stringify(extra) : ""}`);

module.exports = async function sign(configuration) {
  const file = configuration.path;
  // electron-builder calls the hook once per hash algorithm; the build config
  // asks for sha256 only, and a repeat call (isNest) would double-sign.
  if (configuration.isNest) return;

  const email = process.env.CERTUM_EMAIL || "";
  const haveSecret = Boolean(process.env.CERTUM_OTP || process.env.CERTUM_TOKEN);
  if (!email || !haveSecret) {
    say("no Certum credentials in the environment; leaving unsigned", { file: path.basename(file) });
    return;
  }

  const tool = process.env.SSIGN_PATH || "ssign";
  const args = ["--email", email, "--name", configuration.name || "CS Fantasy Toolkit"];
  if (configuration.site) args.push("--url", configuration.site);
  args.push(file);

  // A .js/.cjs SSIGN_PATH is run with node (used by the build test); otherwise
  // the executable is run directly, with no shell, so paths with spaces are safe.
  const viaNode = /\.c?js$/i.test(tool);
  say("signing", { file: path.basename(file), tool: path.basename(tool) });
  const result = spawnSync(viaNode ? process.execPath : tool, viaNode ? [tool, ...args] : args, {
    stdio: "inherit",
    env: process.env,
  });
  if (result.error) throw new Error(`sign-windows: could not run ${tool}: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`sign-windows: ${tool} exited with ${result.status} for ${file}`);
};
