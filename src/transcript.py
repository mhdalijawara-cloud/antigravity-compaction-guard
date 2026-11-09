"""Incremental JSONL transcript reader, cursor tracking, and action extractor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from .integrity import (
    canonicalize_path,
    compute_file_probe,
    is_path_within_workspace,
)
from .models import (
    ANSWER_LINE_RE,
    DISALLOWED_SYMBOLS,
    MAX_MUTATED_FILES,
    MAX_PRE_CP_ACTIONS,
    MAX_QA_DECISIONS,
    MAX_RECENT_ACTIONS,
    MAX_RECENT_USER_REQUESTS,
    MUTATING_DIRECT_TOOLS,
    MUTATING_MCP_TOOLS,
    RUNWAY_CRITICAL_BYTES,
    RUNWAY_WARNING_BYTES,
    SYMBOL_RE,
    USER_REQ_RE,
    default_state,
)


def extract_user_request(raw_content: str) -> str:
    if not raw_content:
        return ""
    match = USER_REQ_RE.search(raw_content)
    if match:
        return match.group(1).strip()
    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw_content, flags=re.DOTALL)
    cleaned = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<system_notification>.*?</system_notification>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<environment_details>.*?</environment_details>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def truncate_text(text: str, max_len: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def extract_code_symbols(text: str) -> list[str]:
    """Extract structural class, function, interface, or struct definitions from code edits."""
    if not text or not isinstance(text, str) or len(text) < 5:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in SYMBOL_RE.finditer(text[:65536]):
        for g in m.groups():
            if g and g not in seen and g not in DISALLOWED_SYMBOLS:
                seen.add(g)
                found.append(g)
                if len(found) >= 12:
                    return found
    return found


def unwrap_json_arg(value: Any, max_depth: int = 2) -> Any:
    """Decode stringified JSON tool arguments used in Antigravity transcripts."""
    current = value
    for _ in range(max_depth):
        if not isinstance(current, str):
            break
        stripped = current.strip()
        if not stripped or stripped[0] not in ('"', "[", "{"):
            break
        try:
            current = json.loads(stripped)
        except (ValueError, TypeError):
            break
    return current


def summarize_tool_call(tc: dict[str, Any]) -> tuple[str, str, str | None, list[str]]:
    """Return (tool_name, concise_target_summary, mutated_file_path_or_none, extracted_symbols)."""
    name = str(tc.get("name") or "unknown")
    raw_args = tc.get("args")
    args = unwrap_json_arg(raw_args) if isinstance(raw_args, (dict, str)) else {}
    if not isinstance(args, dict):
        args = {}

    mutated_path: str | None = None
    symbols: list[str] = []
    if name in MUTATING_DIRECT_TOOLS:
        p = unwrap_json_arg(args.get("Path") or args.get("path") or args.get("destination") or "")
        if isinstance(p, str) and p.strip():
            mutated_path = p.strip()
        code_body = str(
            args.get("Replacement")
            or args.get("replacement")
            or args.get("Content")
            or args.get("content")
            or args.get("Target")
            or args.get("target")
            or ""
        )
        symbols = extract_code_symbols(code_body)
        return name, f"path={mutated_path or '?'}", mutated_path, symbols

    if name == "call_mcp_tool":
        srv = unwrap_json_arg(args.get("ServerName") or "")
        tname = unwrap_json_arg(args.get("ToolName") or "")
        inner = unwrap_json_arg(args.get("Arguments") or {})
        if not isinstance(inner, dict):
            inner = {}
        if srv == "filesystem" and tname in MUTATING_MCP_TOOLS:
            p = unwrap_json_arg(inner.get("path") or inner.get("Path") or inner.get("destination") or "")
            if isinstance(p, str) and p.strip():
                mutated_path = p.strip()
            code_body = str(
                inner.get("replacement")
                or inner.get("Replacement")
                or inner.get("content")
                or inner.get("Content")
                or inner.get("target")
                or inner.get("Target")
                or ""
            )
            symbols = extract_code_symbols(code_body)
            return f"mcp_filesystem_{tname}", f"path={mutated_path or '?'}", mutated_path, symbols

        keys = list(inner.keys())
        first_val = inner.get(keys[0]) if keys else ""
        short_val = truncate_text(str(first_val), 40) if first_val else ""
        summary = f"{srv}:{tname}({keys[0]}={short_val})" if keys else f"{srv}:{tname}"
        return f"mcp_{srv}_{tname}", summary, None, []

    if name in ("read_file", "mcp_filesystem_read_file"):
        p = unwrap_json_arg(args.get("Path") or args.get("path") or "")
        return name, f"path={p or '?'}", None, []

    if name == "run_command":
        tsum = unwrap_json_arg(args.get("toolSummary") or "")
        cmd = unwrap_json_arg(args.get("CommandLine") or "")
        desc = f"{tsum} ({truncate_text(str(cmd), 70)})" if tsum else truncate_text(str(cmd), 90)
        return name, desc, None, []

    if name == "ask_question":
        qs = unwrap_json_arg(args.get("questions") or [])
        q_count = len(qs) if isinstance(qs, list) else 1
        return name, f"{q_count} question(s)", None, []

    return name, truncate_text(json.dumps(args, ensure_ascii=False), 80), None, []


def resolve_transcript_pair(transcript_path_raw: str) -> tuple[Path | None, Path | None]:
    """Return (primary_transcript, fallback_compact_transcript)."""
    if not transcript_path_raw:
        return None, None
    tp = Path(transcript_path_raw)
    full_tp = tp.with_name("transcript_full.jsonl")
    if full_tp.exists() and full_tp.is_file():
        return full_tp, (tp if tp.exists() else None)
    if tp.exists() and tp.is_file():
        return tp, None
    return None, None


def commit_pending_mutations(state: dict[str, Any], step_idx: int, workspace_root: str | None = None) -> None:
    """P0-01 & P0-02: Commit pending tool mutations to confirmed_mutation with baseline SHA-256."""
    pending = state.get("pending_mutations", [])
    if not pending:
        return
    for item in pending:
        p_path = item.get("path")
        if not p_path:
            continue
        norm_key = canonicalize_path(p_path, workspace_root=workspace_root)
        prev = state["mutated_files"].get(norm_key, {})
        prev_symbols: list[str] = prev.get("symbols", [])
        merged_symbols = list(prev_symbols)
        for s in item.get("symbols", []):
            if s not in merged_symbols:
                merged_symbols.append(s)

        probe = compute_file_probe(p_path, workspace_root=workspace_root)
        baseline = probe.get("sha256_full") if probe.get("exists") else None

        state["mutated_files"][norm_key] = {
            "path": p_path,
            "canonical_path": norm_key,
            "edit_count": int(prev.get("edit_count", 0)) + 1,
            "last_step": item.get("step_index", step_idx),
            "confirmed_step": step_idx,
            "last_tool": item.get("tool", "unknown"),
            "last_time": item.get("created_at", ""),
            "baseline_sha256": baseline,
            "size_bytes": probe.get("size_bytes"),
            "symbols": merged_symbols[:8],
        }

    # Evict oldest files beyond MAX_MUTATED_FILES
    if len(state["mutated_files"]) > MAX_MUTATED_FILES:
        sorted_keys = sorted(
            state["mutated_files"].keys(),
            key=lambda k: int(state["mutated_files"][k].get("last_step", 0)),
        )
        for k in sorted_keys[: len(state["mutated_files"]) - MAX_MUTATED_FILES]:
            del state["mutated_files"][k]

    state["pending_mutations"] = []


def process_step(
    state: dict[str, Any],
    obj: dict[str, Any],
    workspace_root: str | None = None,
) -> None:
    step_idx = obj.get("step_index")
    if not isinstance(step_idx, int):
        step_idx = state["total_steps_seen"]
    state["total_steps_seen"] = max(int(state["total_steps_seen"]), step_idx + 1)

    stype = str(obj.get("type") or "")
    created_at = str(obj.get("created_at") or "")
    content = str(obj.get("content") or "")
    status = str(obj.get("status") or "")

    # Tool output/result arrival: confirm or reject pending mutations based on execution result
    if stype in ("GENERIC", "TOOL_OUTPUT", "TOOL_RESULT", "USER_INPUT") and state.get("pending_mutations"):
        is_error = status == "ERROR" or "Encountered error in tool execution" in content
        if is_error:
            # Tool execution failed: discard pending mutations without registering as modified files
            state["pending_mutations"] = []
        else:
            commit_pending_mutations(state, step_idx, workspace_root=workspace_root)

    if stype == "CHECKPOINT":
        cps: list[int] = state["checkpoints_seen"]
        if step_idx not in cps:
            cps.append(step_idx)
            cps.sort()
            state["frozen_pre_checkpoint_actions"] = list(state["recent_actions"][-MAX_PRE_CP_ACTIONS:])
        state["pending_ask_questions"] = None
        if state.get("pending_mutations"):
            commit_pending_mutations(state, step_idx, workspace_root=workspace_root)
        return

    if stype == "USER_INPUT":
        state["pending_ask_questions"] = None
        req_text = extract_user_request(content)
        if req_text:
            entry = {
                "step_index": step_idx,
                "created_at": created_at,
                "text": truncate_text(req_text, 480),
            }
            if state["first_user_request"] is None:
                state["first_user_request"] = entry
            reqs: list[dict[str, Any]] = state["recent_user_requests"]
            if not reqs or reqs[-1].get("step_index") != step_idx:
                reqs.append(entry)
                if len(reqs) > MAX_RECENT_USER_REQUESTS:
                    del reqs[:-MAX_RECENT_USER_REQUESTS]
        return

    if stype == "GENERIC":
        pending = state.get("pending_ask_questions")
        if isinstance(pending, dict) and int(step_idx) > int(pending.get("step_index", -1)):
            lines = content.splitlines()
            current_tag = None
            current_body: list[str] = []
            parsed_answers: list[str] = []
            for line in lines:
                tag_match = re.match(r"^(A\d+)\s*:\s*(.*)$", line.strip())
                if tag_match:
                    if current_tag is not None:
                        parsed_answers.append(f"{current_tag}: " + " ".join(current_body).strip())
                    current_tag = tag_match.group(1)
                    current_body = [tag_match.group(2)]
                elif current_tag is not None and line.strip():
                    current_body.append(line.strip())
            if current_tag is not None:
                parsed_answers.append(f"{current_tag}: " + " ".join(current_body).strip())

            if not parsed_answers:
                parsed_answers = [m.group(1).strip() for m in ANSWER_LINE_RE.finditer(content)]

            if parsed_answers:
                q_list = pending.get("questions", [])
                pairs = []
                for idx, ans in enumerate(parsed_answers):
                    q_txt = q_list[idx] if idx < len(q_list) else f"Q{idx + 1}"
                    pairs.append({"q": truncate_text(str(q_txt), 200), "a": truncate_text(ans, 260)})
                qa_entry = {
                    "step_index": pending.get("step_index", step_idx),
                    "answer_step": step_idx,
                    "created_at": created_at,
                    "pairs": pairs,
                }
                qas: list[dict[str, Any]] = state["qa_decisions"]
                if not qas or qas[-1].get("answer_step") != step_idx:
                    qas.append(qa_entry)
                    if len(qas) > MAX_QA_DECISIONS:
                        del qas[:-MAX_QA_DECISIONS]
                state["pending_ask_questions"] = None
        return

    if stype == "PLANNER_RESPONSE":
        tool_calls = obj.get("tool_calls")
        has_tc = isinstance(tool_calls, list) and len(tool_calls) > 0
        state["last_planner_response"] = {
            "step_index": step_idx,
            "has_tool_calls": has_tc,
            "content": content[:4096] if content else "",
        }
        if not has_tc:
            return

        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tname, summary, mutated_path, syms = summarize_tool_call(tc)
            act_list: list[dict[str, Any]] = state["recent_actions"]
            act_list.append(
                {
                    "step_index": step_idx,
                    "tool": tname,
                    "summary": summary,
                }
            )
            if len(act_list) > MAX_RECENT_ACTIONS:
                del act_list[:-MAX_RECENT_ACTIONS]

            # Stage mutation as pending invocation (P0-01)
            if mutated_path:
                state.setdefault("pending_mutations", []).append(
                    {
                        "step_index": step_idx,
                        "tool": tname,
                        "path": mutated_path,
                        "symbols": syms,
                        "created_at": created_at,
                    }
                )

            if tc.get("name") == "ask_question":
                raw_args = unwrap_json_arg(tc.get("args") or {})
                qs_raw = unwrap_json_arg(raw_args.get("questions") if isinstance(raw_args, dict) else [])
                q_texts: list[str] = []
                if isinstance(qs_raw, list):
                    for q_item in qs_raw:
                        if isinstance(q_item, dict) and q_item.get("question"):
                            q_texts.append(str(q_item["question"]))
                state["pending_ask_questions"] = {
                    "step_index": step_idx,
                    "questions": q_texts,
                }


def sync_state_from_transcript(
    state: dict[str, Any],
    transcript_path_raw: str,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    primary, secondary = resolve_transcript_pair(transcript_path_raw)
    if primary is None:
        return state

    primary_str = str(primary.resolve())
    file_size = primary.stat().st_size
    state["runway_bytes"] = file_size
    if file_size >= RUNWAY_CRITICAL_BYTES:
        state["runway_status"] = "HIGH"
    elif file_size >= RUNWAY_WARNING_BYTES:
        state["runway_status"] = "WARNING"
    else:
        state["runway_status"] = "NORMAL"

    if state.get("source_transcript") != primary_str or int(state.get("byte_offset", 0)) > file_size:
        fresh = default_state()
        fresh["last_injected_checkpoint_step"] = state.get("last_injected_checkpoint_step", -1)
        fresh["stop_blocks_by_checkpoint"] = dict(state.get("stop_blocks_by_checkpoint", {}))
        fresh["source_transcript"] = primary_str
        state = fresh

    offset = int(state.get("byte_offset", 0))
    if file_size > offset:
        with primary.open("rb") as fh:
            fh.seek(offset)
            current_pos = offset
            for line_bytes in fh:
                if not line_bytes.endswith(b"\n"):
                    break
                current_pos += len(line_bytes)
                raw_line = line_bytes.decode("utf-8", errors="replace").strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                    if isinstance(obj, dict):
                        process_step(state, obj, workspace_root=workspace_root)
                except (ValueError, TypeError):
                    continue
            state["byte_offset"] = current_pos

    if state.get("pending_mutations"):
        commit_pending_mutations(state, state["total_steps_seen"], workspace_root=workspace_root)

    if secondary is not None and secondary.exists() and secondary != primary:
        try:
            sec_size = secondary.stat().st_size
            tail_read = min(sec_size, 32 * 1024)
            with secondary.open("rb") as sfh:
                sfh.seek(sec_size - tail_read)
                tail_bytes = sfh.read(tail_read)
            for line_raw in tail_bytes.splitlines():
                line_str = line_raw.decode("utf-8", errors="replace").strip()
                if '"CHECKPOINT"' in line_str:
                    try:
                        c_obj = json.loads(line_str)
                        if isinstance(c_obj, dict) and c_obj.get("type") == "CHECKPOINT":
                            c_idx = c_obj.get("step_index")
                            if isinstance(c_idx, int) and c_idx not in state["checkpoints_seen"]:
                                process_step(state, c_obj, workspace_root=workspace_root)
                    except (ValueError, TypeError):
                        pass
        except OSError:
            pass

    if len(state["checkpoints_seen"]) > 20:
        state["checkpoints_seen"] = state["checkpoints_seen"][-20:]
    if len(state["qa_decisions"]) > MAX_QA_DECISIONS:
        state["qa_decisions"] = state["qa_decisions"][-MAX_QA_DECISIONS:]
    if len(state["recent_user_requests"]) > MAX_RECENT_USER_REQUESTS:
        state["recent_user_requests"] = state["recent_user_requests"][-MAX_RECENT_USER_REQUESTS:]
    if len(state["recent_actions"]) > MAX_RECENT_ACTIONS:
        state["recent_actions"] = state["recent_actions"][-MAX_RECENT_ACTIONS:]
    if len(state.get("pending_mutations", [])) > 20:
        state["pending_mutations"] = state["pending_mutations"][-20:]
    if len(state["mutated_files"]) > MAX_MUTATED_FILES:
        sorted_files = sorted(
            state["mutated_files"].items(),
            key=lambda item: int(item[1].get("step", 0)),
            reverse=True,
        )
        state["mutated_files"] = dict(sorted_files[:MAX_MUTATED_FILES])

    return state
