"""Native Hook stage 4: verify and emit the stored event result."""

from subhook_pipeline import run

if __name__ == "__main__":
    raise SystemExit(run("EMIT"))
