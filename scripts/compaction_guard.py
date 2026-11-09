#!/usr/bin/env python3
"""Deterministic Universal Anti-Compaction-Amnesia Lifecycle Hook Engine for Antigravity.

Subcommands:
  post-tool       PostToolUse hook: incrementally records verbatim user prompts,
                  resolved ask_question Q&A pairs, mutated files (with disk SHA-256
                  and extracted code symbols), and pre-checkpoint tool history into
                  .system_generated/.
  pre-invocation  PreInvocation hook: emits {} during normal turns (0 token overhead);
                  injects a structured systemMessage recovery capsule once per new
                  CHECKPOINT compaction event.
  stop            Stop hook: single-shot circuit breaker per CHECKPOINT blocking
                  premature new-session bootstrap resets mid-session.
  inspect <path>  CLI diagnostic: prints extracted session state, runway meter,
                  and recovery capsule for a given transcript.jsonl file.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

VERSION = "1.2.0"
STATE_SCHEMA_VERSION = 1
MAX_RECENT_USER_REQUESTS = 10
MAX_QA_DECISIONS = 15
MAX_RECENT_ACTIONS = 12
MAX_PRE_CP_ACTIONS = 10
MAX_MUTATED_FILES = 80
MAX_CAPSULE_CHARS = 5800

RUNWAY_WARNING_BYTES = 650 * 1024   # ~220K tokens
RUNWAY_CRITICAL_BYTES = 720 * 1024  # ~245K tokens (compaction imminent at ~256K)

USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
ANSWER_LINE_RE = re.compile(r"^(A\d+\s*:.*)$", re.MULTILINE)

# Universal & multi-lingual amnesia detector (Indonesian, English, and generic greetings)
BOOTSTRAP_AMNESIA_RE = re.compile(
    r"("
    # 1. Indonesian role & initialization questions
    r"sesi\s+terhubung\s+dengan\s+panduan.*?peran\s+apa|"
    r"(?:sebagai\s+)?peran\s+apa\s+(?:yang\s+)?(?:saya|harus\s+saya|akan\s+saya)\s+(?:jalankan|emban|ambil|pegang|mainkan|kerjakan|bertugas)|"
    r"saya\s+(?:harus\s+)?bertugas\s+sebagai\s+(?:peran\s+)?apa\b|"
    r"peran\s+apa\s+yang\s+(?:saya|harus\s+saya|akan\s+saya)\s+(?:emban|jalankan|ambil|kerjakan)|"
    r"(?:apa|mana)\s+peran\s+saya\s+(?:di|pada|dalam|untuk)\s+sesi\s+ini|"
    r"bertugas\s+sebagai\s+peran\s+apa\s+(?:di|pada|dalam|untuk)\s+sesi\s+ini|"
    r"sebagai\s+peran\s+apa\s+(?:saya\s+)?(?:bertugas|berjalan)\s+(?:di|pada|dalam|untuk)\s+sesi\s+ini|"
    r"(?:mohon\s+)?(?:konfirmasi|arahan|kejelasan).*?(?:peran|tugas\s+peran)\s+(?:saya|apa).*?sesi|"
    r"(?:halo|selamat\s+(?:pagi|siang|sore|malam)).*?ada\s+yang\s+bisa\s+(?:saya|kami)\s+bantu|"
    r"(?:halo|selamat\s+(?:pagi|siang|sore|malam)).*?apa\s+yang\s+bisa\s+saya\s+bantu|"
    r"(?:apa\s+yang\s+ingin\s+anda\s+kerjakan|silakan\s+berikan\s+(?:instruksi|arahan|tugas\s+baru))|"
    # 2. English role & assignment queries
    r"what\s+(?:role|assignment)\s+(?:do\s+i|should\s+i|am\s+i|will\s+i)\s+(?:play|assume|take|have|serve|perform|assigned|given)|"
    r"what\s+is\s+my\s+(?:assigned\s+)?role\s+(?:for|in|on)\s+this\s+session|"
    r"which\s+role\s+(?:should|am|do)\s+i\s+(?:assume|take|play|assigned)|"
    # 3. Universal startup greeting / amnesia prompts
    r"how\s+can\s+i\s+(?:help|assist)\s+you\s+(?:today|with\s+this\s+session)|"
    r"i\s+am\s+ready\s+to\s+(?:help|assist|start|begin).*?what\s+(?:would|should|can)|"
    r"what\s+would\s+you\s+like\s+(?:me\s+to|us\s+to)\s+(?:work\s+on|build|do\s+today)|"
    r"please\s+provide\s+(?:the|your)\s+(?:task|project|instructions|requirements)\s+(?:to\s+get\s+started|for\s+this\s+session)"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Structural code symbol extractor (Anti-Issue #1245)
SYMBOL_RE = re.compile(
    r"(?:"
    r"^\s*(?:export\s+)?(?:default\s+)?(?:class|interface|type|struct|enum|trait)\s+([A-Za-z0-9_]+)|"
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:def|function|fn|func)\s+([A-Za-z0-9_]+)|"
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\("
    r")",
    re.MULTILINE,
)

MUTATING_DIRECT_TOOLS = {
    "edit_file",
    "write_file",
    "mcp_filesystem_edit_file",
    "mcp_filesystem_write_file",
    "mcp_filesystem_append_file",
}
MUTATING_MCP_TOOLS = {"edit_file", "write_file", "append_file", "move_file"}


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "source_transcript": "",
        "byte_offset": 0,
        "total_steps_seen": 0,
        "first_user_request": None,
        "recent_user_requests": [],
        "pending_ask_questions": None,
        "qa_decisions": [],
        "mutated_files": {},
        "recent_actions": [],
        "frozen_pre_checkpoint_actions": [],
        "checkpoints_seen": [],
        "last_injected_checkpoint_step": -1,
        "stop_blocks_by_checkpoint": {},
        "last_planner_response": None,
        "runway_status": "NORMAL",
        "runway_bytes": 0,
        "updated_at": "",
    }


def _extract_code_symbols(text: str) -> list[str]:
    """Extract structural class, function, interface, or struct definitions from code edits."""
    if not text or not isinstance(text, str) or len(text) < 5:
        return []
    found: list[str] = []
    seen: set[str] = set()
    # Scan first 24KB of text to keep execution bounded < 2ms
    for m in SYMBOL_RE.finditer(text[:24576]):
        for g in m.groups():
            if g and g not in seen:
                seen.add(g)
                found.append(g)
                if len(found) >= 8:
                    return found
    return found


def _unwrap_json_arg(value: Any) -> Any:
    """Decode stringified JSON tool arguments used in Antigravity transcripts."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped[0] in ('"', "[", "{"):
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            return value
    return value


def _extract_user_request(raw_content: str) -> str:
    if not raw_content:
        return ""
    match = USER_REQ_RE.search(raw_content)
    if match:
        return match.group(1).strip()
    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw_content, flags=re.DOTALL)
    cleaned = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _truncate_text(text: str, max_len: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def _canonicalize_path(raw_path: str, workspace_root: str | None = None) -> str:
    """Return a lowercased canonical absolute path key on Windows to eliminate case duplicate entries."""
    try:
        p = Path(raw_path)
        if not p.is_absolute() and workspace_root:
            p = Path(workspace_root) / p
        resolved = p.resolve()
        return os.path.normcase(str(resolved))
    except Exception:
        return os.path.normcase(os.path.normpath(raw_path))


def resolve_workspace_root(payload: dict[str, Any]) -> str | None:
    """Extract primary workspace root from payload if available."""
    wpaths = payload.get("workspacePaths")
    if isinstance(wpaths, list) and len(wpaths) > 0 and isinstance(wpaths[0], str) and wpaths[0].strip():
        return wpaths[0].strip()
    return None


def _compute_file_probe(raw_path: str, workspace_root: str | None = None) -> dict[str, Any]:
    try:
        p = Path(raw_path)
        if not p.is_file():
            if not p.is_absolute() and workspace_root:
                candidate = Path(workspace_root) / p
                if candidate.is_file():
                    p = candidate
        if not p.exists() or not p.is_file():
            return {"exists": False, "size_bytes": None, "sha256_12": None, "resolved_path": str(p)}
        size = p.stat().st_size
        sha_hex: str | None = None
        if size <= 64 * 1024 * 1024:
            h = hashlib.sha256()
            with p.open("rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            sha_hex = h.hexdigest()[:12]
        return {"exists": True, "size_bytes": size, "sha256_12": sha_hex, "resolved_path": str(p)}
    except OSError:
        return {"exists": False, "size_bytes": None, "sha256_12": None, "resolved_path": raw_path}


def resolve_state_dir(payload: dict[str, Any]) -> Path | None:
    """Resolve hidden .system_generated directory so state never clutters user artifacts."""
    tpath_raw = payload.get("transcriptPath") or ""
    if tpath_raw:
        tp = Path(tpath_raw)
        if tp.parent.name == "logs" and tp.parent.parent.name == ".system_generated":
            return tp.parent.parent
        return tp.parent / ".system_generated"
    adir_raw = payload.get("artifactDirectoryPath") or ""
    if adir_raw:
        ap = Path(adir_raw)
        if ap.name == ".system_generated":
            return ap
        return ap / ".system_generated"
    return None


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


def load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return _default_state()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
            return _default_state()
        merged = _default_state()
        merged.update(data)
        return merged
    except (OSError, ValueError, TypeError):
        return _default_state()


def save_state_atomic(state_file: Path, state: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    tmp_file = state_file.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                os.replace(tmp_file, state_file)
                last_err = None
                break
            except (PermissionError, OSError) as e:
                last_err = e
                import time
                time.sleep(0.025 * (2**attempt))
        if last_err is not None:
            raise last_err
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass


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


def _summarize_tool_call(tc: dict[str, Any]) -> tuple[str, str, str | None, list[str]]:
    """Return (tool_name, concise_target_summary, mutated_file_path_or_none, extracted_symbols)."""
    name = str(tc.get("name") or "unknown")
    raw_args = tc.get("args")
    args = _unwrap_json_arg(raw_args) if isinstance(raw_args, (dict, str)) else {}
    if not isinstance(args, dict):
        args = {}

    mutated_path: str | None = None
    symbols: list[str] = []
    if name in MUTATING_DIRECT_TOOLS:
        p = _unwrap_json_arg(args.get("Path") or args.get("path") or "")
        if isinstance(p, str) and p.strip():
            mutated_path = p.strip()
        code_body = str(args.get("Replacement") or args.get("Content") or args.get("Target") or "")
        symbols = _extract_code_symbols(code_body)
        return name, f"path={mutated_path or '?'}", mutated_path, symbols

    if name == "call_mcp_tool":
        srv = _unwrap_json_arg(args.get("ServerName") or "")
        tname = _unwrap_json_arg(args.get("ToolName") or "")
        inner = _unwrap_json_arg(args.get("Arguments") or {})
        if not isinstance(inner, dict):
            inner = {}
        if srv == "filesystem" and tname in MUTATING_MCP_TOOLS:
            p = _unwrap_json_arg(inner.get("path") or inner.get("destination") or "")
            if isinstance(p, str) and p.strip():
                mutated_path = p.strip()
            code_body = str(inner.get("replacement") or inner.get("content") or inner.get("target") or "")
            symbols = _extract_code_symbols(code_body)
        return f"mcp:{srv}/{tname}", _truncate_text(json.dumps(inner, ensure_ascii=False), 90), mutated_path, symbols

    if name == "read_file":
        p = _unwrap_json_arg(args.get("Path") or "")
        s_line = _unwrap_json_arg(args.get("StartLine") or "")
        e_line = _unwrap_json_arg(args.get("EndLine") or "")
        rng = f":{s_line}-{e_line}" if (s_line or e_line) else ""
        return name, f"path={p}{rng}", None, []

    if name == "run_command":
        tsum = _unwrap_json_arg(args.get("toolSummary") or "")
        cmd = _unwrap_json_arg(args.get("CommandLine") or "")
        desc = f"{tsum} ({_truncate_text(str(cmd), 70)})" if tsum else _truncate_text(str(cmd), 90)
        return name, desc, None, []

    if name == "ask_question":
        qs = _unwrap_json_arg(args.get("questions") or [])
        q_count = len(qs) if isinstance(qs, list) else 1
        return name, f"{q_count} question(s)", None, []

    return name, _truncate_text(json.dumps(args, ensure_ascii=False), 80), None, []


def _process_step(state: dict[str, Any], obj: dict[str, Any], workspace_root: str | None = None) -> None:
    step_idx = obj.get("step_index")
    if not isinstance(step_idx, int):
        step_idx = state["total_steps_seen"]
    state["total_steps_seen"] = max(int(state["total_steps_seen"]), step_idx + 1)

    stype = str(obj.get("type") or "")
    created_at = str(obj.get("created_at") or "")
    content = str(obj.get("content") or "")

    if stype == "CHECKPOINT":
        cps: list[int] = state["checkpoints_seen"]
        if step_idx not in cps:
            cps.append(step_idx)
            cps.sort()
            state["frozen_pre_checkpoint_actions"] = list(state["recent_actions"][-MAX_PRE_CP_ACTIONS:])
        state["pending_ask_questions"] = None
        return

    if stype == "USER_INPUT":
        state["pending_ask_questions"] = None
        req_text = _extract_user_request(content)
        if req_text:
            entry = {
                "step_index": step_idx,
                "created_at": created_at,
                "text": _truncate_text(req_text, 480),
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
            answers = [m.group(1).strip() for m in ANSWER_LINE_RE.finditer(content)]
            if answers:
                q_list = pending.get("questions", [])
                pairs = []
                for idx, ans in enumerate(answers):
                    q_txt = q_list[idx] if idx < len(q_list) else f"Q{idx + 1}"
                    pairs.append({"q": _truncate_text(str(q_txt), 200), "a": _truncate_text(ans, 260)})
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
            "content": content[:800] if content else "",
        }
        if not has_tc:
            return

        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tname, summary, mutated_path, syms = _summarize_tool_call(tc)
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

            if mutated_path:
                norm_key = _canonicalize_path(mutated_path, workspace_root=workspace_root)
                prev = state["mutated_files"].get(norm_key, {})
                prev_symbols: list[str] = prev.get("symbols", [])
                merged_symbols = list(prev_symbols)
                for s in syms:
                    if s not in merged_symbols:
                        merged_symbols.append(s)
                state["mutated_files"][norm_key] = {
                    "path": mutated_path,
                    "canonical_path": norm_key,
                    "edit_count": int(prev.get("edit_count", 0)) + 1,
                    "last_step": step_idx,
                    "last_tool": tname,
                    "last_time": created_at,
                    "symbols": merged_symbols[:8],
                }
                if len(state["mutated_files"]) > MAX_MUTATED_FILES:
                    sorted_keys = sorted(
                        state["mutated_files"].keys(),
                        key=lambda k: int(state["mutated_files"][k].get("last_step", 0)),
                    )
                    for k in sorted_keys[: len(state["mutated_files"]) - MAX_MUTATED_FILES]:
                        del state["mutated_files"][k]

            if tc.get("name") == "ask_question":
                raw_args = _unwrap_json_arg(tc.get("args") or {})
                qs_raw = _unwrap_json_arg(raw_args.get("questions") if isinstance(raw_args, dict) else [])
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
        state["runway_status"] = "CRITICAL"
    elif file_size >= RUNWAY_WARNING_BYTES:
        state["runway_status"] = "WARNING"
    else:
        state["runway_status"] = "NORMAL"

    if state.get("source_transcript") != primary_str or int(state.get("byte_offset", 0)) > file_size:
        fresh = _default_state()
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
                    # Partial line write in flight at EOF; stop here and resume on next sync
                    break
                current_pos += len(line_bytes)
                raw_line = line_bytes.decode("utf-8", errors="replace").strip()
                if not raw_line:
                    continue
                obj: dict[str, Any] | None = None
                try:
                    parsed_line = json.loads(raw_line)
                    if isinstance(parsed_line, dict):
                        obj = parsed_line
                except ValueError as parse_err:
                    sys.stderr.write(f"[CompactionGuard] Malformed JSONL line skipped: {parse_err}\n")
                if obj is not None:
                    _process_step(state, obj, workspace_root=workspace_root)
            state["byte_offset"] = current_pos

    # Secondary tail check in case compact transcript flushed CHECKPOINT slightly ahead
    if secondary is not None and secondary.exists():
        try:
            sec_size = secondary.stat().st_size
            tail_read = min(sec_size, 65536)
            with secondary.open("rb") as sfh:
                sfh.seek(sec_size - tail_read)
                tail_text = sfh.read().decode("utf-8", errors="replace")
            lines = tail_text.splitlines()
            if sec_size > tail_read and lines:
                lines = lines[1:]  # discard first potentially truncated line
            for line in lines:
                line = line.strip()
                if not line or "CHECKPOINT" not in line:
                    continue
                sec_obj: dict[str, Any] | None = None
                try:
                    parsed_sec = json.loads(line)
                    if isinstance(parsed_sec, dict):
                        sec_obj = parsed_sec
                except ValueError as sec_err:
                    sys.stderr.write(f"[CompactionGuard] Malformed tail JSONL skipped: {sec_err}\n")
                if sec_obj is not None and sec_obj.get("type") == "CHECKPOINT":
                    _process_step(state, sec_obj, workspace_root=workspace_root)
        except OSError as tail_io_err:
            sys.stderr.write(f"[CompactionGuard] Secondary transcript tail check error: {tail_io_err}\n")

    return state


def _cap_section(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    marker = "\n  ...[older items omitted; see transcript]..."
    return text[: max(0, budget - len(marker))] + marker


def build_recovery_capsule(
    state: dict[str, Any],
    checkpoint_step: int,
    transcript_path_raw: str,
    workspace_root: str | None = None,
) -> str:
    sections: list[str] = []
    sections.append(
        "[COMPACTION GUARD RECOVERY CAPSULE — AUTHORITATIVE GROUND TRUTH]\n"
        f"Auto-compaction (`CHECKPOINT`) occurred at step {checkpoint_step}. "
        "This is an ACTIVE ONGOING SESSION, NOT a brand-new session.\n"
        "MANDATORY CONTINUITY RULES:\n"
        "1. DO NOT execute new-session role bootstrap questions or startup greetings (e.g., NEVER output 'What can I help you with today?', ask for role re-confirmation, or repeat new-session initialization prompts).\n"
        "2. DO NOT invalidate or re-ask for role assignments or work authorizations already granted below.\n"
        "3. Before resuming any code/file edit, call `read_file` on modified target files below to re-verify exact current lines on disk (`Freshness`).\n"
        f"4. Full untruncated log remains on disk if exact line details are needed: `{transcript_path_raw}`."
    )

    # User Requests (budget: 1300 chars)
    req_lines: list[str] = []
    first_req = state.get("first_user_request")
    recent_reqs: list[dict[str, Any]] = state.get("recent_user_requests", [])
    if isinstance(first_req, dict):
        req_lines.append(f"- [Initial step {first_req['step_index']}] {first_req['text']}")
    for r in recent_reqs[-5:]:
        if isinstance(first_req, dict) and r.get("step_index") == first_req.get("step_index"):
            continue
        req_lines.append(f"- [step {r['step_index']}] {r['text']}")
    if req_lines:
        sections.append(
            _cap_section("## Verbatim User Requests & Active Authorizations\n" + "\n".join(req_lines), 1300)
        )

    # Locked Q&A Decisions — keep latest decisions intact (budget: 1900 chars)
    qas: list[dict[str, Any]] = state.get("qa_decisions", [])
    if qas:
        selected_blocks: list[str] = []
        used = 0
        for qa in reversed(qas[-8:]):
            b_lines = [f"- [Resolved at step {qa.get('answer_step')}]:"]
            for pair in qa.get("pairs", []):
                b_lines.append(f"  - Q: {_truncate_text(str(pair.get('q', '')), 150)}")
                b_lines.append(f"    A: {_truncate_text(str(pair.get('a', '')), 210)}")
            block_str = "\n".join(b_lines)
            if used + len(block_str) > 1800 and selected_blocks:
                break
            selected_blocks.append(block_str)
            used += len(block_str) + 1
        selected_blocks.reverse()
        sections.append("## Locked Specifications (`ask_question` Decisions)\n" + "\n".join(selected_blocks))

    # Mutated Files (with fresh disk SHA-256 & byte size, budget: 1000 chars)
    mutated: dict[str, dict[str, Any]] = state.get("mutated_files", {})
    if mutated:
        f_lines: list[str] = []
        sorted_files = sorted(mutated.values(), key=lambda x: int(x.get("last_step", 0)), reverse=True)
        for item in sorted_files[:10]:
            raw_p = str(item.get("path") or "")
            probe = _compute_file_probe(raw_p, workspace_root=workspace_root)
            status = (
                f"DISK_OK size={probe['size_bytes']}B sha256={probe['sha256_12']}"
                if probe["exists"]
                else "MISSING_ON_DISK"
            )
            sym_list = item.get("symbols", [])
            sym_desc = f", symbols: {', '.join(sym_list[:4])}" if sym_list else ""
            f_lines.append(
                f"- `{raw_p}` (edits={item.get('edit_count')}, last_step={item.get('last_step')}, {status}{sym_desc})"
            )
        sections.append(
            _cap_section("## Files Modified in This Session (Re-Read Before Further Edits)\n" + "\n".join(f_lines), 1000)
        )

    # Interrupted Pre-Compaction Actions — preserve newest steps right before compaction (budget: 900 chars)
    pre_acts: list[dict[str, Any]] = (
        state.get("frozen_pre_checkpoint_actions") or state.get("recent_actions") or []
    )
    if pre_acts:
        kept_acts: list[str] = []
        used_acts = 0
        for a in reversed(pre_acts[-MAX_PRE_CP_ACTIONS:]):
            line_a = f"- [step {a.get('step_index')}] `{a.get('tool')}` -> {a.get('summary')}"
            if used_acts + len(line_a) > 820 and kept_acts:
                break
            kept_acts.append(line_a)
            used_acts += len(line_a) + 1
        kept_acts.reverse()
        sections.append("## Last Tool Actions Immediately Before Compaction\n" + "\n".join(kept_acts))

    capsule = "\n\n".join(sections)
    if len(capsule) > MAX_CAPSULE_CHARS:
        suffix = "\n...[Capsule truncated; inspect transcript]..."
        cut_at = MAX_CAPSULE_CHARS - len(suffix)
        last_nl = capsule.rfind("\n", 0, cut_at)
        if last_nl != -1 and last_nl > cut_at - 200:
            cut_at = last_nl
        capsule = capsule[:cut_at] + suffix
    return capsule


def handle_post_tool(payload: dict[str, Any]) -> dict[str, Any]:
    state_dir = resolve_state_dir(payload)
    tpath = str(payload.get("transcriptPath") or "")
    w_root = resolve_workspace_root(payload)
    if state_dir is not None and tpath:
        state_file = state_dir / "compaction_guard_state.json"
        state = load_state(state_file)
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
    state = load_state(state_file)
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
                            "Konteks otorisasi sesi, keputusan `ask_question`, dan jejak modifikasi berkas dipulihkan otomatis."
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
    state = load_state(state_file)
    state = sync_state_from_transcript(state, tpath, workspace_root=w_root)

    checkpoints: list[int] = state.get("checkpoints_seen", [])
    total_steps = int(state.get("total_steps_seen", 0))
    last_resp = state.get("last_planner_response")

    if not checkpoints or total_steps <= 5 or not isinstance(last_resp, dict):
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
        and resp_step > latest_cp
        and BOOTSTRAP_AMNESIA_RE.search(resp_content)
    ):
        blocks_map[cp_key] = already_blocked + 1
        save_state_atomic(state_file, state)
        return {
            "decision": "continue",
            "reason": (
                "[COMPACTION GUARD AMNESIA BLOCK / ANTI-AMNESIA INTERCEPTOR] Anda baru saja melewati context compaction "
                "di tengah sesi yang SUDAH berjalan dan keliru mengulang salam / pertanyaan bootstrap sesi baru. "
                "/ Context compaction occurred mid-session; you have mistakenly emitted a new-session startup query. "
                "This is an ACTIVE ONGOING SESSION. Periksa kembali kapsul pemulihan Compaction Guard di atas, "
                "verifikasi berkas di disk via read_file, dan lanjutkan tugas aktif tanpa mereset otorisasi."
            ),
        }

    save_state_atomic(state_file, state)
    return {"decision": "allow"}


def handle_inspect(transcript_path_raw: str) -> int:
    state = _default_state()
    state = sync_state_from_transcript(state, transcript_path_raw)
    latest_cp = max(state["checkpoints_seen"]) if state["checkpoints_seen"] else -1
    file_size = 0
    p = Path(transcript_path_raw)
    if p.exists():
        file_size = p.stat().st_size
    runway_pct = min(100.0, (file_size / (750 * 1024)) * 100)
    summary_obj = {
        "version": VERSION,
        "source_transcript": state["source_transcript"],
        "total_steps_seen": state["total_steps_seen"],
        "checkpoints_seen": state["checkpoints_seen"],
        "user_requests_recorded": len(state["recent_user_requests"]),
        "qa_decisions_recorded": len(state["qa_decisions"]),
        "mutated_files_recorded": len(state["mutated_files"]),
        "runway_meter": {
            "transcript_bytes": file_size,
            "compaction_threshold_pct": round(runway_pct, 1),
            "status": state.get("runway_status", "NORMAL"),
        },
    }
    print(json.dumps(summary_obj, indent=2, ensure_ascii=False))
    print("\n" + "=" * 72 + "\n")
    print(build_recovery_capsule(state, latest_cp, transcript_path_raw))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = argv if argv is not None else sys.argv[1:]
    mode = args[0] if args else "pre-invocation"

    if mode in ("--help", "-h", "help"):
        sys.stdout.write(
            f"Compaction Guard v{VERSION} — Universal Anti-Amnesia Lifecycle Shield\n"
            "Usage: compaction_guard.py [post-tool | pre-invocation | stop | inspect <path> | version]\n"
        )
        return 0

    if mode in ("--version", "-v", "version"):
        sys.stdout.write(f"compaction-guard v{VERSION}\n")
        return 0

    if mode == "inspect":
        if len(args) < 2:
            sys.stderr.write("Usage: compaction_guard.py inspect <transcript.jsonl>\n")
            return 2
        return handle_inspect(args[1])

    payload: dict[str, Any] = {}
    state_dir: Path | None = None
    try:
        raw_stdin = sys.stdin.read().strip()
        if raw_stdin:
            parsed = json.loads(raw_stdin)
            if isinstance(parsed, dict):
                payload = parsed
        state_dir = resolve_state_dir(payload)

        if mode == "post-tool":
            out = handle_post_tool(payload)
        elif mode == "pre-invocation":
            out = handle_pre_invocation(payload)
        elif mode == "stop":
            out = handle_stop(payload)
        else:
            raise ValueError(f"Unsupported hook subcommand: {mode!r}")

        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0
    except Exception as exc:
        err_msg = log_hook_error(state_dir, mode, exc)
        if mode == "stop":
            fallback: dict[str, Any] = {"decision": "allow"}
        elif mode == "pre-invocation":
            fallback = {
                "injectSteps": [
                    {
                        "ephemeralMessage": (
                            f"[Compaction Guard Diagnostic Warning] Hook error logged: {err_msg}"
                        )
                    }
                ]
            }
        else:
            fallback = {}
        sys.stdout.write(json.dumps(fallback, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
