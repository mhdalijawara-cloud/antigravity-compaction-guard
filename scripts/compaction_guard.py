#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Muhammad Ali Jawara Wiratama
"""Thin CLI and Antigravity Hook entrypoint facade delegating to src/."""

from __future__ import annotations

from pathlib import Path
import sys

# Ensure repository root is on sys.path for importing src
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.cli import (
    handle_doctor,
    handle_inspect,
    handle_purge,
    handle_status,
    main,
)
from src.hooks import (
    handle_post_tool,
    handle_pre_invocation,
    handle_stop,
)
from src.integrity import (
    canonicalize_path,
    canonicalize_path as _canonicalize_path,
    compute_file_probe,
    compute_file_probe as _compute_file_probe,
    is_path_within_workspace,
    resolve_state_dir,
    resolve_workspace_root,
)
from src.locking import file_lock
from src.models import (
    BOOTSTRAP_AMNESIA_RE,
    DISALLOWED_SYMBOLS,
    MAX_CAPSULE_CHARS,
    MAX_MUTATED_FILES,
    MAX_PRE_CP_ACTIONS,
    MAX_QA_DECISIONS,
    MAX_RECENT_ACTIONS,
    MAX_RECENT_USER_REQUESTS,
    MUTATING_DIRECT_TOOLS,
    MUTATING_MCP_TOOLS,
    RUNWAY_CRITICAL_BYTES,
    RUNWAY_WARNING_BYTES,
    STATE_SCHEMA_VERSION,
    SYMBOL_RE,
    USER_REQ_RE,
    VERSION,
    default_state,
    default_state as _default_state,
    validate_state,
)
from src.recovery import (
    build_recovery_capsule,
    cap_section,
)
from src.state import (
    load_state,
    log_hook_error,
    save_state_atomic,
)
from src.transcript import (
    commit_pending_mutations,
    extract_code_symbols,
    extract_code_symbols as _extract_code_symbols,
    extract_user_request,
    process_step,
    resolve_transcript_pair,
    summarize_tool_call,
    sync_state_from_transcript,
    truncate_text,
    unwrap_json_arg,
)

__version__ = VERSION

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
