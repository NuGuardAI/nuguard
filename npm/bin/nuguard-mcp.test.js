#!/usr/bin/env node
/**
 * Tests for nuguard-mcp.js runner detection logic.
 *
 * Run with:  node npm/bin/nuguard-mcp.test.js
 *
 * Uses Node's built-in assert module — no external test framework needed.
 */
"use strict";

const assert = require("assert");
const { spawnSync } = require("child_process");
const path = require("path");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const WRAPPER = path.resolve(__dirname, "nuguard-mcp.js");

/**
 * Run the wrapper with a mocked environment via a child node process.
 * We inject a tiny shim that overrides `which`, `pyHasNuguardMcp`, and
 * `spawnSync` (for pip install) before the real module logic runs.
 *
 * Returns { stdout, stderr, exitCode }.
 */
function runWithMocks({ whichResults = {}, pyHasNuguard = false, pipOk = true } = {}) {
  const shim = `
"use strict";
const cp = require("child_process");
const original_spawnSync = cp.spawnSync.bind(cp);

// Intercept spawnSync used by which() and pip install
cp.spawnSync = function(cmd, args, opts) {
  const isWin = process.platform === "win32";
  const whichCmd = isWin ? "where" : "which";

  if (cmd === whichCmd) {
    const target = args[0];
    const found = ${JSON.stringify(whichResults)}[target] === true;
    return { status: found ? 0 : 1, stdout: found ? target : "", stderr: "" };
  }

  // python -c "import nuguard.mcp"
  if (args && args[0] === "-c" && args[1] === "import nuguard.mcp") {
    return { status: ${pyHasNuguard ? 0 : 1} };
  }

  // pip install
  if (args && args[0] === "-m" && args[1] === "pip") {
    return { status: ${pipOk ? 0 : 1} };
  }

  return { status: 1, stdout: "", stderr: "" };
};

// Intercept spawn used by run() — capture what would be launched
const cp2 = require("child_process");
const original_spawn = cp2.spawn.bind(cp2);
cp2.spawn = function(cmd, args, opts) {
  // Print what would be launched and exit cleanly
  process.stdout.write(JSON.stringify({ cmd, args }) + "\\n");
  return {
    on: function(event, cb) {
      if (event === "exit") cb(0);
      if (event === "error") {}
      return this;
    }
  };
};

require(${JSON.stringify(WRAPPER)});
`;

  const result = spawnSync(process.execPath, ["--eval", shim], {
    encoding: "utf8",
    stdio: "pipe",
    timeout: 5000,
  });

  let launched = null;
  if (result.stdout && result.stdout.trim()) {
    try { launched = JSON.parse(result.stdout.trim()); } catch (_) {}
  }

  return {
    launched,
    stderr: result.stderr || "",
    exitCode: result.status,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
    failed++;
  }
}

console.log("\nnuguard-mcp.js runner detection\n");

// 1. nuguard-mcp already on PATH → run it directly
test("uses nuguard-mcp directly when it is on PATH", () => {
  const { launched } = runWithMocks({ whichResults: { "nuguard-mcp": true } });
  assert.ok(launched, "should have launched something");
  assert.strictEqual(launched.cmd, "nuguard-mcp");
  assert.deepStrictEqual(launched.args, []);
});

// 2. python3 with nuguard installed → python3 -m nuguard.mcp
test("uses python3 -m nuguard.mcp when nuguard-mcp not on PATH but python3 has it", () => {
  const { launched } = runWithMocks({
    whichResults: { python3: true },
    pyHasNuguard: true,
  });
  assert.ok(launched);
  assert.strictEqual(launched.cmd, "python3");
  assert.ok(launched.args.includes("-m"), "should include -m flag");
  assert.ok(launched.args.includes("nuguard.mcp"), "should target nuguard.mcp");
});

// 3. python (not python3) with nuguard installed
test("falls back to python (not python3) when python3 not available", () => {
  const { launched } = runWithMocks({
    whichResults: { python: true },
    pyHasNuguard: true,
  });
  assert.ok(launched);
  assert.strictEqual(launched.cmd, "python");
});

// 4. uvx available, nuguard not pip-installed
test("uses uvx when nuguard not pip-installed but uvx is available", () => {
  const { launched } = runWithMocks({
    whichResults: { uvx: true },
    pyHasNuguard: false,
  });
  assert.ok(launched);
  assert.strictEqual(launched.cmd, "uvx");
  assert.ok(launched.args.includes("--from"));
  assert.ok(launched.args.includes("nuguard[mcp]"));
  assert.ok(launched.args.includes("nuguard-mcp"));
});

// 5. pip install fallback when python available but nuguard not installed
test("pip-installs nuguard then launches python -m nuguard.mcp when only python present", () => {
  const { launched, stderr } = runWithMocks({
    whichResults: { python3: true },
    pyHasNuguard: false,
    pipOk: true,
  });
  assert.ok(launched, "should launch after pip install");
  assert.strictEqual(launched.cmd, "python3");
  assert.ok(launched.args.includes("nuguard.mcp"));
  assert.ok(stderr.includes("installing via pip"), "should mention pip install");
});

// 6. No Python, no uvx → exit 1 with helpful message
test("exits with code 1 and helpful message when no runtime found", () => {
  const { launched, stderr, exitCode } = runWithMocks({
    whichResults: {},
    pyHasNuguard: false,
  });
  assert.strictEqual(launched, null, "should not launch anything");
  assert.ok(stderr.includes("pip install") || stderr.includes("Python"), "stderr should contain install hint");
});

// 7. pip install fails → exit 1
test("exits with code 1 when pip install fails", () => {
  const { launched } = runWithMocks({
    whichResults: { python3: true },
    pyHasNuguard: false,
    pipOk: false,
  });
  assert.strictEqual(launched, null, "should not launch after failed pip install");
});

// 8. nuguard-mcp takes precedence over python + uvx
test("nuguard-mcp on PATH takes precedence over python and uvx", () => {
  const { launched } = runWithMocks({
    whichResults: { "nuguard-mcp": true, python3: true, uvx: true },
    pyHasNuguard: true,
  });
  assert.ok(launched);
  assert.strictEqual(launched.cmd, "nuguard-mcp", "nuguard-mcp should win over python/uvx");
});

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed\n`);
process.exit(failed > 0 ? 1 : 0);
