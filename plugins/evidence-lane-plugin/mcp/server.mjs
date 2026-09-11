#!/usr/bin/env node
/** Thin Codex-to-Python MCP bridge. It owns transport startup, not actions. */

import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const mcpDir = path.dirname(fileURLToPath(import.meta.url));
const pluginRoot = path.resolve(mcpDir, "..");
const server = path.join(pluginRoot, "scripts", "run_mcp.py");
const studioRoot = process.env.EVIDENCE_LANE_STUDIO_ROOT || "C:\\Apps\\EvidenceLaneStudio";
const stablePython = path.join(studioRoot, "engine", "venv", "Scripts", "python.exe");

const candidates = [];
if (process.env.EVIDENCE_LANE_PYTHON) candidates.push(process.env.EVIDENCE_LANE_PYTHON);
if (process.platform === "win32" && existsSync(stablePython)) candidates.push(stablePython);
if (process.platform === "win32") candidates.push("python");
else candidates.push("python3", "python");

const unique = [...new Set(candidates)];
let index = 0;
let child;

function startNext(lastError) {
  if (index >= unique.length) {
    const detail = lastError?.code ? ` (${lastError.code})` : "";
    process.stderr.write(`Evidence Lane could not find a Python runtime${detail}.\n`);
    process.exitCode = 127;
    return;
  }
  const command = unique[index++];
  child = spawn(command, ["-I", "-B", server, ...process.argv.slice(2)], {
    cwd: pluginRoot,
    env: { ...process.env, EVIDENCE_LANE_PLUGIN_ROOT: pluginRoot },
    shell: false,
    stdio: "inherit",
    windowsHide: true,
  });
  let spawned = false;
  child.once("spawn", () => { spawned = true; });
  child.once("error", (error) => {
    if (!spawned && error.code === "ENOENT") startNext(error);
    else {
      process.stderr.write(`Evidence Lane MCP bridge failed: ${error.code || error.message}.\n`);
      process.exitCode = 1;
    }
  });
  child.once("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exitCode = code ?? 1;
  });
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (child && !child.killed) child.kill(signal);
  });
}

startNext();
