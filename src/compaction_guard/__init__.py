"""Antigravity Compaction Guard: Deterministic Session Evidence & Recovery Engine.

Modular domain package for Antigravity lifecycle hooks.
"""

from __future__ import annotations

from .cli import (
    handle_doctor,
    handle_inspect,
    handle_purge,
    handle_status,
    main,
)
from .hooks import (
    handle_post_tool,
    handle_pre_invocation,
    handle_stop,
)
from .integrity import (
    canonicalize_path,
    compute_file_probe,
    is_path_within_workspace,
    resolve_state_dir,
    resolve_workspace_root,
)
from .locking import file_lock
from .models import (
    BOOTSTRAP_AMNESIA_RE,
    DISALLOWED_SYMBOLS,
    MUTATING_DIRECT_TOOLS,
    MUTATING_MCP_TOOLS,
    STATE_SCHEMA_VERSION,
    SYMBOL_RE,
    USER_REQ_RE,
    VERSION,
    default_state,
    validate_state,
)
from .recovery import (
    build_recovery_capsule,
    cap_section,
)
from .state import (
    load_state,
    log_hook_error,
    save_state_atomic,
)
from .transcript import (
    commit_pending_mutations,
    extract_code_symbols,
    extract_user_request,
    process_step,
    resolve_transcript_pair,
    summarize_tool_call,
    sync_state_from_transcript,
    truncate_text,
    unwrap_json_arg,
)

__version__ = VERSION
__all__ = [
    "VERSION",
    "STATE_SCHEMA_VERSION",
    "default_state",
    "validate_state",
    "file_lock",
    "is_path_within_workspace",
    "canonicalize_path",
    "compute_file_probe",
    "resolve_workspace_root",
    "resolve_state_dir",
    "extract_user_request",
    "truncate_text",
    "extract_code_symbols",
    "unwrap_json_arg",
    "summarize_tool_call",
    "resolve_transcript_pair",
    "commit_pending_mutations",
    "process_step",
    "sync_state_from_transcript",
    "load_state",
    "save_state_atomic",
    "log_hook_error",
    "cap_section",
    "build_recovery_capsule",
    "handle_post_tool",
    "handle_pre_invocation",
    "handle_stop",
    "handle_inspect",
    "handle_purge",
    "handle_doctor",
    "handle_status",
    "main",
    "BOOTSTRAP_AMNESIA_RE",
    "SYMBOL_RE",
    "USER_REQ_RE",
    "DISALLOWED_SYMBOLS",
    "MUTATING_DIRECT_TOOLS",
    "MUTATING_MCP_TOOLS",
]
