"""Native Hook stage 3: execute the event transport implementation once."""

from subhook_pipeline import run

if __name__ == "__main__":
    raise SystemExit(run("TRANSPORT"))
