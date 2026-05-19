#!/usr/bin/env node
/**
 * Launcher for nuguard-mcp. Tries runners in order of least friction:
 *   1. nuguard-mcp already on PATH (pip-installed globally or in active venv)
 *   2. python/python3 -m nuguard.mcp (nuguard installed but scripts not on PATH)
 *   3. uvx --from nuguard[mcp] nuguard-mcp  (uv available)
 *   4. pip install nuguard[mcp] then python -m nuguard.mcp  (one-time setup)
 */
"use strict";

const { spawn, spawnSync } = require("child_process");

function which(cmd) {
  const isWin = process.platform === "win32";
  const r = spawnSync(isWin ? "where" : "which", [cmd], { encoding: "utf8", stdio: "pipe" });
  return r.status === 0 && r.stdout.trim().length > 0;
}

function pyHasNuguardMcp(python) {
  const r = spawnSync(python, ["-c", "import nuguard.mcp"], { encoding: "utf8", stdio: "pipe" });
  return r.status === 0;
}

function run(command, args) {
  const child = spawn(command, [...args], { stdio: "inherit" });
  child.on("error", (err) => {
    process.stderr.write(`nuguard-mcp: failed to start '${command}': ${err.message}\n`);
    process.exit(1);
  });
  child.on("exit", (code) => process.exit(code ?? 0));
}

const extra = process.argv.slice(2);

// 1. Already pip-installed and on PATH
if (which("nuguard-mcp")) {
  return run("nuguard-mcp", extra);
}

// 2. Python with nuguard already installed
const python = which("python3") ? "python3" : which("python") ? "python" : null;
if (python && pyHasNuguardMcp(python)) {
  return run(python, ["-m", "nuguard.mcp", ...extra]);
}

// 3. uvx
if (which("uvx")) {
  return run("uvx", ["--from", "nuguard[mcp]", "nuguard-mcp", ...extra]);
}

// 4. pip install then run
if (python) {
  process.stderr.write("nuguard-mcp: installing via pip (one-time setup)...\n");
  const install = spawnSync(
    python, ["-m", "pip", "install", "--quiet", "nuguard[mcp]"],
    { stdio: "inherit" }
  );
  if (install.status === 0) {
    return run(python, ["-m", "nuguard.mcp", ...extra]);
  }
  process.stderr.write("nuguard-mcp: pip install failed — try: pip install nuguard[mcp]\n");
  process.exit(1);
}

process.stderr.write(
  "nuguard-mcp: no suitable Python runtime found.\n" +
  "Install via one of:\n" +
  "  pip install nuguard[mcp]\n" +
  "  uvx --from nuguard[mcp] nuguard-mcp\n" +
  "See https://github.com/NuGuardAI/nuguard for details.\n"
);
process.exit(1);
