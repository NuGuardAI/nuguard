#!/usr/bin/env node
/**
 * Thin wrapper that launches nuguard-mcp via uvx (uv's tool runner).
 * uv/uvx must be installed: https://docs.astral.sh/uv/getting-started/installation/
 */
const { spawn } = require("child_process");

const args = ["--from", "nuguard[mcp]", "nuguard-mcp", ...process.argv.slice(2)];

const child = spawn("uvx", args, { stdio: "inherit" });

child.on("error", (err) => {
  if (err.code === "ENOENT") {
    console.error(
      "Error: 'uvx' not found. Install uv first:\n" +
      "  curl -LsSf https://astral.sh/uv/install.sh | sh\n" +
      "  # or: pip install uv"
    );
  } else {
    console.error(`Failed to start nuguard-mcp: ${err.message}`);
  }
  process.exit(1);
});

child.on("exit", (code) => process.exit(code ?? 0));
