#!/usr/bin/env node
/** Run one Host-visible Hook stage through the sealed Studio Python when available. */

import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { validatedHookOutput } from "./hook_output_contract.mjs";

const events = new Set([
  "Interrupt",
  "PermissionRequest",
  "PostCompact",
  "PostToolUse",
  "PreCompact",
  "PreToolUse",
  "SessionEnd",
  "SessionStart",
  "Stop",
  "SubagentStart",
  "SubagentStop",
  "UserPromptSubmit",
]);
const selectedEvent = process.argv[2];
const selectedStage = process.argv[3];
const hooksRoot = path.dirname(fileURLToPath(import.meta.url));
const pluginRoot = path.resolve(hooksRoot, "..");
const handler = path.join(hooksRoot, "stage_runner.py");
const stages = new Set(["VALIDATE", "SEAL", "TRANSPORT", "EMIT"]);
const terminalEvents = new Set(["Interrupt", "SessionEnd"]);
const terminalOwnerStage = "VALIDATE";
const studioRoot = process.env.EVIDENCE_LANE_STUDIO_ROOT || "C:\\Apps\\EvidenceLaneStudio";
const stablePython = path.join(studioRoot, "engine", "venv", "Scripts", "python.exe");
const checkoutPython = path.resolve(pluginRoot, "..", "..", ".venv", "Scripts", "python.exe");

function unavailable(code) {
  process.stdout.write(JSON.stringify({ systemMessage: `Evidence Lane capture unavailable: ${code}` }) + "\n");
  process.exitCode = 0;
}

if (!events.has(selectedEvent) || !stages.has(selectedStage) || !existsSync(handler)) {
  unavailable("HOOK_EVENT_UNSUPPORTED");
} else if (terminalEvents.has(selectedEvent) && selectedStage !== terminalOwnerStage) {
  // Codex permits at most three seconds for Interrupt and SessionEnd.  Keep
  // all four Host-visible rows, but avoid four concurrent cold interpreters:
  // Hook 1 owns the complete terminal pipeline and the other rows are bounded
  // compatibility/presentation members.
  process.stdout.write("{}\n");
  process.exitCode = 0;
} else {
  const candidates = [];
  if (process.env.EVIDENCE_LANE_PYTHON) candidates.push(process.env.EVIDENCE_LANE_PYTHON);
  if (process.platform === "win32" && existsSync(stablePython)) candidates.push(stablePython);
  if (process.platform === "win32" && existsSync(checkoutPython)) candidates.push(checkoutPython);
  if (process.platform === "win32") candidates.push("python");
  else candidates.push("python3", "python");

  const unique = [...new Set(candidates)];
  let index = 0;

  function startNext(lastError) {
    if (index >= unique.length) {
      unavailable(lastError?.code || "HOOK_RUNTIME_UNAVAILABLE");
      return;
    }
    const command = unique[index++];
    const executionStage = terminalEvents.has(selectedEvent) ? "EMIT" : selectedStage;
    const child = spawn(command, ["-I", "-B", handler, selectedEvent, executionStage], {
      cwd: pluginRoot,
      env: { ...process.env, EVIDENCE_LANE_PLUGIN_ROOT: pluginRoot },
      shell: false,
      stdio: ["inherit", "pipe", "pipe"],
      windowsHide: true,
    });
    const stdout = [];
    let stdoutBytes = 0;
    let spawned = false;
    let settled = false;
    child.once("spawn", () => { spawned = true; });
    child.stdout.on("data", (chunk) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes <= 65_536) stdout.push(chunk);
    });
    // Always drain child stderr, but never forward raw diagnostics into the
    // shared Host Hook channel where request paths or values may be exposed.
    child.stderr.on("data", () => {});
    child.once("error", (error) => {
      if (!spawned && error.code === "ENOENT") startNext(error);
      else if (!settled) {
        settled = true;
        unavailable(error.code || "HOOK_RUNTIME_UNAVAILABLE");
      }
    });
    child.once("exit", (code) => {
      if (settled) return;
      settled = true;
      const output = code === 0 ? validatedHookOutput(stdout, stdoutBytes, selectedEvent) : null;
      if (output !== null) {
        process.stdout.write(output);
        process.exitCode = 0;
        return;
      }
      unavailable(code === 0 ? "HOOK_OUTPUT_INVALID" : "HOOK_HANDLER_FAILED");
    });
  }

  startNext();
}
