# Prompt Studio local retrieval artifact

This directory carries the inspectable, committed retrieval authority used by
the public Prompt Studio. It is generated locally from an explicit public-safe
corpus and never reads `.env`, runtime state, secrets, accepted project brains,
private source packages, or untracked host material.

Build it with the pinned optional dependency:

```powershell
python -m pip install -e ".[rag]"
python plugins/evidence-lane-plugin/scripts/build_prompt_studio_index.py
```

The builder uses `llama-index-core==0.14.23` `SentenceSplitter` for offline
chunk construction, SQLite FTS5/BM25 for lexical retrieval, explicit
materialized TF-IDF using `idf=ln((1+N)/(1+df))+1`, and reciprocal-rank fusion
with `k=60`. The SQLite database is the forensic authority; the adjacent JSON
is the deterministic browser projection committed into the Next.js build.

The Git-history corpus ends at the parent `HEAD` from which the artifact is
built. The same release commit carries the resulting SQLite/JSON blobs and all
current source hashes, avoiding the impossible self-reference of embedding a
commit's own final SHA inside that commit.
