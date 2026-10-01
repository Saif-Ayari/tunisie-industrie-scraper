from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from models import CompanyCandidate, CompanyRecord, SearchScope


STATE_VERSION = 1


class StateError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def source_sort_key(source_id: str) -> tuple[int, int | str]:
    return (0, int(source_id)) if source_id.isdigit() else (1, source_id)


class CheckpointStore:
    """Durable discovery/checkpoint state plus append-only successful records."""

    def __init__(self, state_dir: Path, resume: bool = False, reset: bool = False):
        self.state_dir = Path(state_dir)
        self.checkpoint_path = self.state_dir / "checkpoint.json"
        self.records_path = self.state_dir / "records.jsonl"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if reset:
            self.checkpoint_path.unlink(missing_ok=True)
            self.records_path.unlink(missing_ok=True)
        if resume:
            if not self.checkpoint_path.exists():
                raise StateError(f"Cannot resume: checkpoint does not exist at {self.checkpoint_path}")
            self.data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if self.data.get("version") != STATE_VERSION:
                raise StateError("Unsupported checkpoint version")
        else:
            if self.checkpoint_path.exists() or self.records_path.exists():
                raise StateError(
                    f"State already exists under {self.state_dir}; use --resume or --reset-state explicitly"
                )
            self.data = {
                "version": STATE_VERSION,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "records_path": str(self.records_path),
                "scopes": [],
                "discovery": {
                    "scope_progress": {},
                    "pages_processed": 0,
                    "duplicate_discoveries_removed": 0,
                },
                "candidates": {},
                "completed_ids": [],
                "failures": {},
            }
        self.records = self._load_records()
        for key in self.records:
            if key not in self.data["completed_ids"]:
                self.data["completed_ids"].append(key)
        self.data["completed_ids"] = sorted(set(self.data["completed_ids"]), key=source_sort_key)
        self.save()

    def _load_records(self) -> dict[str, CompanyRecord]:
        records: dict[str, CompanyRecord] = {}
        if not self.records_path.exists():
            return records
        for line in self.records_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = CompanyRecord(**payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                # A partial final JSONL line can result from hard interruption;
                # the checkpoint remains usable and the record will be retried.
                continue
            key = record.source_id or record.source_url
            if key:
                records[key] = record
        return records

    def save(self) -> None:
        self.data["updated_at"] = _utc_now()
        temporary = self.checkpoint_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.checkpoint_path)

    def set_scopes(self, scopes: Iterable[SearchScope]) -> None:
        self.data["scopes"] = [{"code": scope.code, "label": scope.label} for scope in scopes]
        self.save()

    def add_candidate(self, candidate: CompanyCandidate, scope_code: str) -> bool:
        key = candidate.identity
        existing = self.data["candidates"].get(key)
        if existing is not None:
            scopes = set(existing.get("scopes", []))
            scopes.add(scope_code)
            existing["scopes"] = sorted(scopes, key=source_sort_key)
            self.data["discovery"]["duplicate_discoveries_removed"] += 1
            return False
        payload = asdict(candidate)
        payload["scopes"] = [scope_code]
        self.data["candidates"][key] = payload
        return True

    def note_page(
        self,
        scope: SearchScope,
        page_number: int,
        result_count: int | None,
        total_pages: int | None,
        page_size: int,
        complete: bool,
    ) -> None:
        progress = self.data["discovery"]["scope_progress"].setdefault(
            scope.code,
            {"label": scope.label, "last_page": 0, "complete": False, "result_count": None, "total_pages": None},
        )
        progress.update(
            {
                "label": scope.label,
                "last_page": page_number,
                "complete": complete,
                "result_count": result_count,
                "total_pages": total_pages,
                "page_size": page_size,
                "updated_at": _utc_now(),
            }
        )
        self.data["discovery"]["pages_processed"] += 1
        self.save()

    def scope_progress(self, code: str) -> dict[str, Any]:
        return self.data["discovery"]["scope_progress"].get(code, {})

    def candidate_keys(self) -> list[str]:
        return sorted(self.data["candidates"], key=source_sort_key)

    def candidate_keys_for_scopes(self, scope_codes: Iterable[str]) -> list[str]:
        selected = set(scope_codes)
        keys = [
            key
            for key, payload in self.data["candidates"].items()
            if selected.intersection(payload.get("scopes", []))
        ]
        return sorted(keys, key=source_sort_key)

    def candidate(self, key: str) -> CompanyCandidate:
        payload = dict(self.data["candidates"][key])
        payload["scopes"] = tuple(payload.get("scopes", ()))
        return CompanyCandidate(**payload)

    def has_record(self, key: str) -> bool:
        return key in self.records

    def save_record(self, record: CompanyRecord) -> bool:
        key = record.source_id or record.source_url
        if not key or key in self.records:
            return False
        line = json.dumps(asdict(record), ensure_ascii=False) + "\n"
        with self.records_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self.records[key] = record
        self.data["completed_ids"] = sorted(set(self.data["completed_ids"]) | {key}, key=source_sort_key)
        self.data["failures"].pop(key, None)
        self.save()
        return True

    def record_failure(self, candidate: CompanyCandidate, error: Exception | str) -> None:
        key = candidate.identity
        previous = self.data["failures"].get(key, {})
        self.data["failures"][key] = {
            "source_id": candidate.source_id,
            "url": candidate.detail_url,
            "error": str(error),
            "attempts": int(previous.get("attempts", 0)) + 1,
            "last_failed_at": _utc_now(),
        }
        self.save()

    def records_sorted(self) -> list[CompanyRecord]:
        return [self.records[key] for key in sorted(self.records, key=source_sort_key)]

    @property
    def duplicate_discoveries_removed(self) -> int:
        return int(self.data["discovery"].get("duplicate_discoveries_removed", 0))

    @property
    def pages_processed(self) -> int:
        return int(self.data["discovery"].get("pages_processed", 0))

    @property
    def failures(self) -> dict[str, dict[str, Any]]:
        return self.data["failures"]
