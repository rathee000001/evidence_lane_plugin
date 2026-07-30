"""Read and resolve secret-redacted visible prompt-entry index records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes

_PROMPT_REFERENCE = re.compile(
    r"^PROMPT(?:\s*[:#]\s*|\s+)(?P<index>[1-9][0-9]*)$",
    flags=re.IGNORECASE,
)
_TURN_REFERENCE = re.compile(
    r"^TURN(?:\s*[:#]\s*|\s+)(?P<turn>[A-Za-z0-9._:-]{1,256})$",
    flags=re.IGNORECASE,
)


def is_prompt_reference(value: str) -> bool:
    exact = value.strip()
    return bool(_PROMPT_REFERENCE.fullmatch(exact) or _TURN_REFERENCE.fullmatch(exact))


class PromptIndex:
    """Verify hook records that retain only the secret-redacted visible prompt."""

    def __init__(self, store_root: str | Path) -> None:
        self.root = Path(store_root).resolve() / "prompt-index"

    @staticmethod
    def _host_key(host_session_id: str) -> str:
        exact = host_session_id.strip()
        require(
            bool(exact) and len(exact) <= 256,
            "HOST_SESSION_ID_INVALID",
            "A prompt reference requires the current Codex host session ID.",
            status="BLOCKED",
        )
        return f"host-{sha256_bytes(exact.encode('utf-8'))[:40].lower()}"

    def _all_records(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(
            self.root.glob("host-*/*.json"),
            key=lambda item: (item.parent.name, item.name),
        ):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise EvidenceLaneError(
                    "PROMPT_INDEX_RECORD_UNREADABLE",
                    "A prompt-index record cannot be verified.",
                    status="MISMATCH",
                    details={"path": str(path), "type": type(exc).__name__},
                ) from exc
            claimed = str(payload.pop("record_sha256", ""))
            actual = sha256_bytes(canonical_json_bytes(payload))
            host_session_id = str(payload.get("host_session_id", ""))
            require(
                payload.get("schema") == "evidence-lane.prompt-index.v1"
                and path.parent.name == self._host_key(host_session_id)
                and claimed == actual,
                "PROMPT_INDEX_RECORD_MISMATCH",
                "A prompt-index record failed its schema, session, or SHA-256 check.",
                status="MISMATCH",
                path=str(path),
            )
            payload["record_sha256"] = claimed
            records.append(payload)
        ordered = sorted(
            records,
            key=lambda row: (
                str(row.get("evidence_session_id", "")),
                int(row.get("prompt_index", 0)),
            ),
        )
        prior_by_session: dict[tuple[str, str], tuple[int, str]] = {}
        for row in ordered:
            key = (
                str(row.get("project_id", "")),
                str(row.get("evidence_session_id", "")),
            )
            prior = prior_by_session.get(key)
            expected_index = 1 if prior is None else prior[0] + 1
            expected_prior_hash = None if prior is None else prior[1]
            require(
                int(row.get("prompt_index", 0)) == expected_index
                and row.get("prior_record_sha256") == expected_prior_hash,
                "PROMPT_INDEX_CHAIN_MISMATCH",
                "The governed-session prompt index is not a contiguous SHA-256 chain.",
                status="MISMATCH",
                project_id=key[0],
                evidence_session_id=key[1],
                expected_prompt_index=expected_index,
                actual_prompt_index=row.get("prompt_index"),
            )
            prior_by_session[key] = (
                int(row["prompt_index"]),
                str(row["record_sha256"]),
            )
        return ordered

    def _records(self, host_session_id: str) -> list[dict[str, Any]]:
        exact = host_session_id.strip()
        self._host_key(exact)
        return [
            row for row in self._all_records() if row.get("host_session_id") == exact
        ]

    @staticmethod
    def _bounded(
        records: list[dict[str, Any]],
        *,
        project_id: str,
        evidence_session_id: str,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in records
            if row.get("project_id") == project_id
            and row.get("evidence_session_id") == evidence_session_id
            and row.get("entry_pv")
        ]

    def resolve(
        self,
        *,
        host_session_id: str,
        project_id: str,
        evidence_session_id: str,
        reference: str,
    ) -> dict[str, Any]:
        self._host_key(host_session_id)
        exact = reference.strip()
        prompt_match = _PROMPT_REFERENCE.fullmatch(exact)
        turn_match = _TURN_REFERENCE.fullmatch(exact)
        require(
            bool(prompt_match or turn_match),
            "PROMPT_REFERENCE_INVALID",
            "Use PROMPT <index> or TURN <turn-id> for indexed rollback.",
            status="BLOCKED",
            reference=reference,
        )
        records = self._bounded(
            self._all_records(),
            project_id=project_id,
            evidence_session_id=evidence_session_id,
        )
        if prompt_match:
            prompt_index = int(prompt_match.group("index"))
            matches = [
                row
                for row in records
                if int(row.get("prompt_index", 0)) == prompt_index
            ]
        else:
            if turn_match is None:
                raise EvidenceLaneError(
                    "PROMPT_REFERENCE_INVALID",
                    "Use PROMPT <index> or TURN <turn-id> for indexed rollback.",
                    status="BLOCKED",
                    details={"reference": reference},
                )
            turn_id = str(turn_match.group("turn"))
            matches = [row for row in records if row.get("turn_id") == turn_id]
        require(
            len(matches) == 1,
            "PROMPT_REFERENCE_NOT_RESOLVED",
            "The indexed prompt reference does not resolve to exactly one entry PV "
            "in this project and governed session.",
            status="BLOCKED",
            reference=reference,
            matching_records=len(matches),
            available_prompt_indexes=[
                int(row["prompt_index"]) for row in records[-50:]
            ],
        )
        row = matches[0]
        return {
            "kind": "PROMPT_INDEX",
            "reference": exact,
            "prompt_index": int(row["prompt_index"]),
            "turn_id": row["turn_id"],
            "entry_pv": row["entry_pv"],
            "pointer_generation": row.get("pointer_generation"),
            "record_sha256": row["record_sha256"],
        }

    def latest_entry(
        self,
        *,
        host_session_id: str,
        project_id: str,
        evidence_session_id: str,
    ) -> dict[str, Any] | None:
        records = self._bounded(
            self._records(host_session_id),
            project_id=project_id,
            evidence_session_id=evidence_session_id,
        )
        if not records:
            return None
        row = records[-1]
        return {
            "kind": "CURRENT_PROMPT_ENTRY",
            "reference": f"PROMPT {row['prompt_index']}",
            "prompt_index": int(row["prompt_index"]),
            "turn_id": row["turn_id"],
            "entry_pv": row["entry_pv"],
            "pointer_generation": row.get("pointer_generation"),
            "record_sha256": row["record_sha256"],
        }

    def status(
        self,
        *,
        host_session_id: str,
        project_id: str,
        evidence_session_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        self._host_key(host_session_id)
        bounded_limit = max(1, min(int(limit), 100))
        records = self._bounded(
            self._all_records(),
            project_id=project_id,
            evidence_session_id=evidence_session_id,
        )
        visible = [
            {
                "prompt_index": int(row["prompt_index"]),
                "turn_id": row["turn_id"],
                "entry_pv": row["entry_pv"],
                "pointer_generation": row.get("pointer_generation"),
                "record_sha256": row["record_sha256"],
                "recorded_at": row["recorded_at"],
            }
            for row in records[-bounded_limit:]
        ]
        return {
            "status": "PASS",
            "schema": "evidence-lane.prompt-index-status.v1",
            "project_id": project_id,
            "evidence_session_id": evidence_session_id,
            "raw_prompt_stored": False,
            "redacted_visible_prompt_stored": True,
            "returned": len(visible),
            "total_resolvable": len(records),
            "records": visible,
        }
