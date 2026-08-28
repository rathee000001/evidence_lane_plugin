"""Native Hook stage 1: validate, redact, and bound the host signal."""

from subhook_pipeline import run

if __name__ == "__main__":
    raise SystemExit(run("VALIDATE"))
