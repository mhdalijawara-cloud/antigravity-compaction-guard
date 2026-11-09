"""State persistence, quarantine, and error logging."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from typing import Any

from .models import STATE_SCHEMA_VERSION, default_state
from .transcript import sync_state_from_transcript


def log_hook_error(state_dir: Path | None, mode: str, exc: BaseException) -> str:
    summary = f"{type(exc).__name__}: {exc}"
    if state_dir is not None:
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            err_file = state_dir / "compaction_guard_error.log"
            stamp = dt.datetime.now(dt.timezone.utc).isoformat()
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            with err_file.open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] mode={mode} {summary}\n{tb}\n")
        except OSError as io_err:
            sys.stderr.write(f"[CompactionGuard] Log write failure ({mode}): {io_err}\n")
    return summary


def load_state(
    state_file: Path,
    transcript_path: str | Path | None = None,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """Load JSON state with automatic schema migration, corruption quarantine, and self-healing reconstruction (P1-01)."""
    if not state_file.exists():
        bak_file = state_file.with_suffix(".bak")
        if bak_file.exists():
            try:
                bak_data = json.loads(bak_file.read_text(encoding="utf-8"))
                if isinstance(bak_data, dict) and bak_data.get("schema_version") in (1, STATE_SCHEMA_VERSION):
                    merged = default_state()
                    merged.update(bak_data)
                    merged["schema_version"] = STATE_SCHEMA_VERSION
                    return merged
            except (OSError, ValueError, TypeError):
                pass
        return default_state()

    try:
        raw_text = state_file.read_text(encoding="utf-8")
        if not raw_text.strip():
            raise ValueError("State file is empty")
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            raise TypeError("State payload is not a JSON object")
        version = data.get("schema_version")
        if version not in (1, STATE_SCHEMA_VERSION):
            raise ValueError(f"Unsupported schema version: {version}")
        merged = default_state()
        merged.update(data)
        merged["schema_version"] = STATE_SCHEMA_VERSION
        if "pending_mutations" not in merged or not isinstance(merged["pending_mutations"], list):
            merged["pending_mutations"] = []
        return merged
    except (OSError, ValueError, TypeError) as exc:
        # P1-01: Quarantine corrupted state file instead of silent reset
        stamp = int(time.time())
        corrupt_file = state_file.with_name(f"{state_file.stem}.corrupt.{stamp}.json")
        try:
            shutil.copy2(state_file, corrupt_file)
        except OSError:
            pass
        log_hook_error(
            state_file.parent,
            "state_load_corruption",
            ValueError(f"State file {state_file.name} corrupted ({exc}); quarantined to {corrupt_file.name}"),
        )

        # 1. Attempt fallback to backup (.bak)
        bak_file = state_file.with_suffix(".bak")
        if bak_file.exists():
            try:
                bak_data = json.loads(bak_file.read_text(encoding="utf-8"))
                if isinstance(bak_data, dict) and bak_data.get("schema_version") in (1, STATE_SCHEMA_VERSION):
                    merged = default_state()
                    merged.update(bak_data)
                    merged["schema_version"] = STATE_SCHEMA_VERSION
                    save_state_atomic(state_file, merged)
                    return merged
            except (OSError, ValueError, TypeError):
                pass

        # 2. Attempt self-healing reconstruction from transcript
        if transcript_path:
            try:
                tp = Path(transcript_path)
                if tp.exists():
                    reconstructed = sync_state_from_transcript(default_state(), str(tp), workspace_root=workspace_root)
                    save_state_atomic(state_file, reconstructed)
                    return reconstructed
            except Exception as replay_err:
                log_hook_error(state_file.parent, "state_self_healing_replay", replay_err)

        return default_state()


def save_state_atomic(state_file: Path, state: dict[str, Any]) -> None:
    """Persist JSON state atomically via temporary file, atomic replace, and backup copy (P1-01)."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    tmp_file = state_file.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if state_file.exists():
            bak_file = state_file.with_suffix(".bak")
            try:
                if state_file.stat().st_size > 5:
                    shutil.copy2(state_file, bak_file)
            except OSError:
                pass
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                os.replace(tmp_file, state_file)
                last_err = None
                break
            except (PermissionError, OSError) as e:
                last_err = e
                time.sleep(0.025 * (2**attempt))
        if last_err is not None:
            raise last_err
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass
