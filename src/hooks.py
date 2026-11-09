"""Lifecycle hook handlers for PostToolUse, PreInvocation, and Stop events."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .integrity import resolve_state_dir, resolve_workspace_root
from .locking import file_lock
from .models import BOOTSTRAP_AMNESIA_RE
from .recovery import build_recovery_capsule
from .state import load_state, save_state_atomic
from .transcript import sync_state_from_transcript


def handle_post_tool(payload: dict[str, Any]) -> dict[str, Any]:
    state_dir = resolve_state_dir(payload)
    tpath = str(payload.get("transcriptPath") or "")
    w_root = resolve_workspace_root(payload)
    if state_dir is not None and tpath:
        state_file = state_dir / "compaction_guard_state.json"
        lock_file = state_file.with_suffix(".lock")
        with file_lock(lock_file):
            state = load_state(state_file, transcript_path=tpath, workspace_root=w_root)
            state = sync_state_from_transcript(state, tpath, workspace_root=w_root)
            save_state_atomic(state_file, state)
    return {}


def handle_pre_invocation(payload: dict[str, Any]) -> dict[str, Any]:
    state_dir = resolve_state_dir(payload)
    tpath = str(payload.get("transcriptPath") or "")
    w_root = resolve_workspace_root(payload)
    if state_dir is None or not tpath:
        return {}

    state_file = state_dir / "compaction_guard_state.json"
    lock_file = state_file.with_suffix(".lock")
    with file_lock(lock_file):
        state = load_state(state_file, transcript_path=tpath, workspace_root=w_root)
        state = sync_state_from_transcript(state, tpath, workspace_root=w_root)

        checkpoints: list[int] = state.get("checkpoints_seen", [])
        last_injected = int(state.get("last_injected_checkpoint_step", -1))

        if not checkpoints or max(checkpoints) <= last_injected:
            save_state_atomic(state_file, state)
            return {}

        latest_cp = max(checkpoints)
        capsule = build_recovery_capsule(state, latest_cp, tpath, workspace_root=w_root)
        state["last_injected_checkpoint_step"] = latest_cp
        save_state_atomic(state_file, state)

    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": capsule,
                    "metadata": {
                        "user_markdown": (
                            f"**Compaction Guard Active (Step {latest_cp}):** "
                            "Session evidence, dual-digest baseline hashes, and working context restored deterministically."
                        )
                    },
                }
            }
        ]
    }


def handle_stop(payload: dict[str, Any]) -> dict[str, Any]:
    term_reason = str(payload.get("terminationReason") or "")
    if term_reason != "NO_TOOL_CALL":
        return {"decision": "allow"}

    state_dir = resolve_state_dir(payload)
    tpath = str(payload.get("transcriptPath") or "")
    w_root = resolve_workspace_root(payload)
    if state_dir is None or not tpath:
        return {"decision": "allow"}

    state_file = state_dir / "compaction_guard_state.json"
    lock_file = state_file.with_suffix(".lock")
    with file_lock(lock_file):
        state = load_state(state_file, transcript_path=tpath, workspace_root=w_root)
        state = sync_state_from_transcript(state, tpath, workspace_root=w_root)

        checkpoints: list[int] = state.get("checkpoints_seen", [])
        last_resp = state.get("last_planner_response")

        if not checkpoints or not isinstance(last_resp, dict):
            save_state_atomic(state_file, state)
            return {"decision": "allow"}

        latest_cp = max(checkpoints)
        cp_key = str(latest_cp)
        blocks_map: dict[str, int] = state.setdefault("stop_blocks_by_checkpoint", {})
        already_blocked = int(blocks_map.get(cp_key, 0))

        resp_content = str(last_resp.get("content") or "")
        resp_step = int(last_resp.get("step_index", -1))
        has_tools = bool(last_resp.get("has_tool_calls", False))

        if (
            already_blocked < 1
            and not has_tools
            and latest_cp < resp_step <= latest_cp + 4
            and BOOTSTRAP_AMNESIA_RE.search(resp_content)
        ):
            blocks_map[cp_key] = already_blocked + 1
            save_state_atomic(state_file, state)
            return {
                "decision": "continue",
                "reason": (
                    "[COMPACTION GUARD ANTI-AMNESIA INTERCEPTOR] Context window compaction occurred mid-session; "
                    "a new-session bootstrap greeting or role query was emitted in error. "
                    "This is an ACTIVE CONTINUATION of the ongoing session. Consult the Compaction Guard recovery "
                    "capsule above, verify current disk lines with read_file, and resume active work."
                ),
            }

        save_state_atomic(state_file, state)
    return {"decision": "allow"}
