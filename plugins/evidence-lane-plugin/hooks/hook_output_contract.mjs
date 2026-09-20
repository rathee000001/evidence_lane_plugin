/** Validate the bounded, non-controlling output a Hook may return to Codex. */

const CONTEXT_EVENTS = new Set(["SessionStart", "UserPromptSubmit"]);
const MAX_SYSTEM_MESSAGE_BYTES = 4_096;
const MAX_ADDITIONAL_CONTEXT_BYTES = 12_000;

function exactKeys(value, expected) {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

export function validatedHookOutput(chunks, byteCount, selectedEvent) {
  if (byteCount <= 0 || byteCount > 65_536) return null;
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)).trim();
    if (!text) return null;
    const parsed = JSON.parse(text);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    if (Object.keys(parsed).length === 0) return "{}\n";
    if (exactKeys(parsed, ["systemMessage"])) {
      if (
        typeof parsed.systemMessage !== "string"
        || Buffer.byteLength(parsed.systemMessage, "utf8") > MAX_SYSTEM_MESSAGE_BYTES
      ) return null;
      return JSON.stringify(parsed) + "\n";
    }
    if (!exactKeys(parsed, ["hookSpecificOutput"]) || !CONTEXT_EVENTS.has(selectedEvent)) return null;
    const output = parsed.hookSpecificOutput;
    if (
      output === null
      || typeof output !== "object"
      || Array.isArray(output)
      || !exactKeys(output, ["additionalContext", "hookEventName"])
      || output.hookEventName !== selectedEvent
      || typeof output.additionalContext !== "string"
      || Buffer.byteLength(output.additionalContext, "utf8") > MAX_ADDITIONAL_CONTEXT_BYTES
    ) return null;
    return JSON.stringify(parsed) + "\n";
  } catch {
    return null;
  }
}
