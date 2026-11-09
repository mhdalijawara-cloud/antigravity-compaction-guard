"""Data models, constants, and schema validation for Compaction Guard."""

from __future__ import annotations

import re
from typing import Any, TypedDict

VERSION = "1.5.0"
STATE_SCHEMA_VERSION = 2

MAX_RECENT_USER_REQUESTS = 10
MAX_QA_DECISIONS = 15
MAX_RECENT_ACTIONS = 12
MAX_PRE_CP_ACTIONS = 10
MAX_MUTATED_FILES = 80
MAX_CAPSULE_CHARS = 5800

RUNWAY_WARNING_BYTES = 650 * 1024
RUNWAY_CRITICAL_BYTES = 720 * 1024

USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
ANSWER_LINE_RE = re.compile(r"^(A\d+\s*:.*)$", re.MULTILINE)

BOOTSTRAP_AMNESIA_RE = re.compile(
    r"("
    r"(?:konfirmasi|klarifikasi|arahan|kejelasan|menanyakan).*?peran\s*(?:apa|saya|yang)|"
    r"(?:sebagai\s+)?peran\s+apa\s+(?:yang\s+)?(?:harus\s+|akan\s+|perlu\s+)?(?:saya|anda\s+inginkan\s+untuk\s+saya)\s+(?:jalankan|emban|ambil|pegang|mainkan|kerjakan|bertugas)|"
    r"saya\s+(?:harus\s+|akan\s+|perlu\s+)?bertugas\s+sebagai\s+(?:peran\s+)?apa\b|"
    r"peran\s+apa\s+yang\s+(?:harus\s+|akan\s+|perlu\s+)?(?:saya|anda\s+inginkan\s+untuk\s+saya)\s+(?:emban|jalankan|ambil|kerjakan)|"
    r"(?:apa|mana)\s+(?:sebenarnya\s+)?peran\s+saya\s+(?:di|pada|dalam|untuk)\s+(?:sesi|proyek|project|percakapan)\s+ini|"
    r"bertugas\s+sebagai\s+peran\s+apa\s+(?:di|pada|dalam|untuk)\s+(?:sesi|proyek|project|percakapan)\s+ini|"
    r"sebagai\s+peran\s+apa\s+(?:saya\s+)?(?:bertugas|berjalan)\s+(?:di|pada|dalam|untuk)\s+(?:sesi|proyek|project|percakapan)\s+ini|"
    r"(?:mohon\s+)?(?:konfirmasi|arahan|kejelasan).*?(?:peran|tugas\s+peran)\s+(?:saya|apa|yang)|"
    r"(?:apakah\s+)?ada\s+yang\s+bisa\s+(?:saya|kami)\s+(?:bantu|membantu)\b|"
    r"(?:ada\s+yang\s+bisa\s+dibantu\b)|"
    r"(?:bagaimana|apa\s+yang)\s+(?:saya\s+)?(?:bisa|dapat)\s+(?:saya\s+)?(?:bantu|membantu|kerjakan)\b|"
    r"(?:halo|selamat\s+(?:pagi|siang|sore|malam)).*?(?:bisa\s+(?:saya|kami)\s+(?:bantu|membantu)|apa\s+yang\s+bisa\s+saya\s+(?:bantu|membantu))|"
    r"(?:apa\s+yang\s+ingin\s+anda\s+kerjakan|silakan\s+berikan\s+(?:instruksi|arahan|tugas\s+baru))|"
    r"(?:what|which)\s+(?:role|assignment)\s+(?:do\s+i|should\s+i|am\s+i|will\s+i|would\s+you\s+like\s+me\s+to|do\s+you\s+want\s+me\s+to|am\s+i\s+supposed\s+to)\s+(?:play|assume|take|have|serve|perform|assigned|given)|"
    r"(?:what|which)\s+is\s+my\s+(?:assigned\s+)?role\s+(?:for|in|on)\s+this\s+(?:session|project|conversation|workspace)|"
    r"(?:please\s+)?(?:specify|clarify|confirm)\s+(?:my\s+role|the\s+role\s+you\s+want\s+me\s+to\s+assume)|"
    r"(?:how|what)\s+(?:can|may|could)\s+i\s+(?:help|assist)\s+(?:you\s+)?(?:today|with|for\s+this\s+session)?|"
    r"what\s+can\s+i\s+(?:help|assist)\s+you\s+(?:with|today)|"
    r"i\s+am\s+ready\s+to\s+(?:help|assist|start|begin).*?what\s+(?:would|should|can)|"
    r"what\s+would\s+you\s+like\s+(?:me\s+to|us\s+to)\s+(?:work\s+on|build|do\s+today)|"
    r"please\s+provide\s+(?:the|your)\s+(?:task\s+)?(?:instructions|requirements|details|project)\s+(?:to\s+get\s+started|for\s+this\s+session)?"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Best-effort regex-based structural symbol hint generator (sampling first 64KB)
# Supports Python, TypeScript, JavaScript, Rust, Go, Java, and C#
SYMBOL_RE = re.compile(
    r"(?:"
    r"^\s*(?:(?:export|default|pub(?:\([^)]+\))?|public|private|protected|internal|abstract|final|sealed|static)\s+)*"
    r"(?:class|interface|type|struct|enum|trait|contract)\s+([A-Za-z0-9_]+)|"
    r"^\s*(?:(?:export|default|pub(?:\([^)]+\))?|public|private|protected|internal|abstract|final|sealed|static|async)\s+)*"
    r"(?:def|function\*?|fn|func)\s*(?:\([^)]+\)\s*)?([A-Za-z0-9_]+)|"
    r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|synchronized|native|async)\s+)+(?:void|[A-Za-z0-9_<>\[\]]+)\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*(?:throws\s+[A-Za-z0-9_,\s]+)?\s*\{|"
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_]+)(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_]+)\s*=>"
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

DISALLOWED_SYMBOLS = {
    "if", "else", "elif", "for", "while", "return", "switch", "case", "break",
    "continue", "default", "type", "func", "fn", "def", "class", "struct",
    "enum", "interface", "trait", "package", "import", "export", "var", "let",
    "const", "true", "false", "null", "nil", "None", "undefined", "async", "await",
    "try", "catch", "finally", "throws", "synchronized", "new", "this", "super",
}


def default_state() -> dict[str, Any]:
    """Return default empty state dictionary adhering to schema version 2."""
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "source_transcript": "",
        "byte_offset": 0,
        "total_steps_seen": 0,
        "first_user_request": None,
        "recent_user_requests": [],
        "pending_ask_questions": None,
        "pending_mutations": [],
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


def validate_state(data: Any) -> tuple[bool, str]:
    """Validate that state object adheres to formal structural rules."""
    if not isinstance(data, dict):
        return False, "State payload must be a JSON object"
    ver = data.get("schema_version")
    if ver not in (1, STATE_SCHEMA_VERSION):
        return False, f"Unsupported schema version: {ver}"
    if not isinstance(data.get("byte_offset", 0), int) or data["byte_offset"] < 0:
        return False, "Invalid or negative byte_offset"
    if not isinstance(data.get("checkpoints_seen", []), list):
        return False, "checkpoints_seen must be a list"
    if not isinstance(data.get("mutated_files", {}), dict):
        return False, "mutated_files must be a dictionary"
    return True, "Valid"
