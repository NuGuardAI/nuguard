import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const uiDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(uiDir, "..");
const pythonBackendDir = path.join(repoRoot, "python-backend");

const isWin = process.platform === "win32";
const venvPython = path.join(
  pythonBackendDir,
  ".venv",
  isWin ? "Scripts" : "bin",
  isWin ? "python.exe" : "python"
);

const python = existsSync(venvPython) ? venvPython : "python";

if (!existsSync(pythonBackendDir)) {
  console.error(`Could not find python-backend directory at: ${pythonBackendDir}`);
  process.exit(1);
}

if (python !== "python" && !existsSync(python)) {
  console.error(`Could not find venv python at: ${python}`);
  process.exit(1);
}

const port = process.env.BACKEND_PORT || "8250";

const child = spawn(
  python,
  [
    "-m",
    "uvicorn",
    "api:app",
    "--reload",
    "--host",
    "127.0.0.1",
    "--port",
    port,
  ],
  {
    cwd: pythonBackendDir,
    stdio: "inherit",
    env: process.env,
  }
);

child.on("exit", (code) => {
  process.exit(code ?? 1);
});
