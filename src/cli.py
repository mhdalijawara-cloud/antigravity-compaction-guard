"""CLI commands suite: inspect, purge, doctor, status, and hook dispatching."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from .hooks import handle_post_tool, handle_pre_invocation, handle_stop
from .integrity import resolve_state_dir
from .locking import file_lock
from .models import VERSION, default_state
from .recovery import build_recovery_capsule
from .state import log_hook_error
from .transcript import sync_state_from_transcript


def handle_inspect(transcript_path_raw: str) -> int:
    state = default_state()
    state = sync_state_from_transcript(state, transcript_path_raw)
    latest_cp = max(state["checkpoints_seen"]) if state["checkpoints_seen"] else -1
    file_size = 0
    p = Path(transcript_path_raw)
    if p.exists():
        file_size = p.stat().st_size
    runway_pct = min(100.0, (file_size / (750 * 1024)) * 100)
    summary_obj = {
        "version": VERSION,
        "schema_version": state["schema_version"],
        "source_transcript": state["source_transcript"],
        "total_steps_seen": state["total_steps_seen"],
        "checkpoints_seen": state["checkpoints_seen"],
        "user_requests_recorded": len(state["recent_user_requests"]),
        "qa_decisions_recorded": len(state["qa_decisions"]),
        "mutated_files_recorded": len(state["mutated_files"]),
        "pending_mutations_count": len(state.get("pending_mutations", [])),
        "runway_meter": {
            "transcript_bytes": file_size,
            "heuristic_note": "Raw JSONL byte size telemetry (heuristic indicator, not direct active context tokens)",
            "compaction_risk_level": state.get("runway_status", "NORMAL"),
            "threshold_pct": round(runway_pct, 1),
            "status": state.get("runway_status", "NORMAL"),
        },
    }
    print(json.dumps(summary_obj, indent=2, ensure_ascii=False))
    print("\n" + "=" * 72 + "\n")
    print(build_recovery_capsule(state, latest_cp, transcript_path_raw))
    return 0


def handle_purge(
    target_dir_raw: str | None = None,
    older_than_days: int = 7,
    dry_run: bool = False,
) -> int:
    """Purge stale state, quarantined files, and temporary swap artifacts (P1-08)."""
    now = time.time()
    cutoff = now - (older_than_days * 86400)
    dirs_to_check: list[Path] = []
    if target_dir_raw:
        dirs_to_check.append(Path(target_dir_raw))
    else:
        dirs_to_check.append(Path.cwd() / ".agents" / "plugins" / "compaction-guard")
        dirs_to_check.append(Path.home() / ".gemini" / "config" / "plugins" / "compaction-guard")
        brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
        if brain_dir.exists():
            for p in brain_dir.glob("*/.system_generated"):
                if p.is_dir():
                    dirs_to_check.append(p)
        cwd_sys = Path.cwd() / ".system_generated"
        if cwd_sys.exists():
            dirs_to_check.append(cwd_sys)

    purged_count = 0
    total_bytes = 0

    print(f"[*] Compaction Guard Purge Engine v{VERSION}")
    print(f"[*] Retention cutoff: files older than {older_than_days} days (dry_run={dry_run})\n")

    seen_files: set[Path] = set()
    for d in dirs_to_check:
        if not d.exists() or not d.is_dir():
            continue
        try:
            for item in d.iterdir():
                if not item.is_file():
                    continue
                name = item.name
                should_purge = False
                reason = ""
                mtime = item.stat().st_mtime

                if ".tmp." in name:
                    if now - mtime > 3600:
                        should_purge = True
                        reason = "stale temp swap file"
                elif ".corrupt." in name:
                    if mtime < cutoff:
                        should_purge = True
                        reason = "expired quarantined corrupt state"
                elif name.endswith(".lock"):
                    if now - mtime > 3600:
                        should_purge = True
                        reason = "stale lock file"
                elif name.endswith(".bak"):
                    if mtime < cutoff:
                        should_purge = True
                        reason = "expired state backup"
                elif name == "compaction_guard_error.log":
                    if mtime < cutoff:
                        should_purge = True
                        reason = "expired error log"

                if should_purge and item not in seen_files:
                    seen_files.add(item)
                    size = item.stat().st_size
                    purged_count += 1
                    total_bytes += size
                    if dry_run:
                        print(f"  [DRY RUN] Would remove: {item} ({size} B, {reason})")
                    else:
                        try:
                            item.unlink()
                            print(f"  [PURGED] Removed: {item} ({size} B, {reason})")
                        except OSError as e:
                            print(f"  [!] Failed to remove {item}: {e}")
        except OSError:
            pass

    print(f"\n[OK] Purge complete: {purged_count} file(s) processed ({total_bytes} bytes).")
    return 0


def handle_doctor() -> int:
    """P2: Diagnostic health check verifying environment, interpreter, locking, and files."""
    print("============================================================")
    print(f"  Compaction Guard Doctor v{VERSION}")
    print("============================================================\n")
    all_ok = True

    # 1. Python version check
    py_ver = sys.version_info
    py_str = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    if py_ver >= (3, 10):
        print(f"[OK] Python Interpreter: {py_str} (>= 3.10 required) -> PASS")
    else:
        print(f"[FAIL] Python Interpreter: {py_str} (< 3.10) -> FAIL")
        all_ok = False

    # 2. File lock check
    test_lock = Path.cwd() / ".doctor_test.lock"
    try:
        with file_lock(test_lock, timeout_seconds=1.0) as acquired:
            if acquired:
                print(f"[OK] Advisory OS File Lock ({'msvcrt' if sys.platform == 'win32' else 'fcntl'}) -> PASS")
            else:
                print("[FAIL] Advisory OS File Lock: acquisition timed out -> FAIL")
                all_ok = False
    except Exception as e:
        print(f"[FAIL] Advisory OS File Lock error: {e} -> FAIL")
        all_ok = False
    finally:
        if test_lock.exists():
            try:
                test_lock.unlink()
            except OSError:
                pass

    # 3. Installation presence check
    global_plugin = Path.home() / ".gemini" / "config" / "plugins" / "compaction-guard"
    workspace_plugin = Path.cwd() / ".agents" / "plugins" / "compaction-guard"
    installed_any = False
    for label, p in [("Global", global_plugin), ("Workspace", workspace_plugin)]:
        manifest = p / "plugin.json"
        hooks = p / "hooks.json"
        if manifest.exists() and hooks.exists():
            print(f"[OK] Installation ({label}): present at {p}")
            installed_any = True
        else:
            print(f"[-] Installation ({label}): not installed at {p}")

    print("\n" + ("=" * 60))
    if all_ok:
        print("[OK] Compaction Guard Doctor: System is healthy and operational.\n")
        return 0
    else:
        print("[!] Compaction Guard Doctor: One or more health checks failed.\n")
        return 1


def handle_status() -> int:
    """P2: Print quick runtime status of active plugin and installations."""
    print(f"Compaction Guard v{VERSION} -- Modular Engine")
    global_plugin = Path.home() / ".gemini" / "config" / "plugins" / "compaction-guard"
    workspace_plugin = Path.cwd() / ".agents" / "plugins" / "compaction-guard"
    print(f"Global plugin:    {'ACTIVE' if (global_plugin / 'plugin.json').exists() else 'NOT INSTALLED'} ({global_plugin})")
    print(f"Workspace plugin: {'ACTIVE' if (workspace_plugin / 'plugin.json').exists() else 'NOT INSTALLED'} ({workspace_plugin})")
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
            f"Compaction Guard v{VERSION} -- Deterministic Session Evidence & Recovery Engine\n"
            "Usage: compaction_guard.py [post-tool | pre-invocation | stop | inspect <path> | purge | doctor | status | version]\n"
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

    if mode == "purge":
        days = 7
        target_dir = None
        dry = False
        idx = 1
        while idx < len(args):
            if args[idx] in ("--older-than-days", "-d") and idx + 1 < len(args):
                days = int(args[idx + 1])
                idx += 2
            elif args[idx] in ("--dir", "--workspace", "-w") and idx + 1 < len(args):
                target_dir = args[idx + 1]
                idx += 2
            elif args[idx] == "--dry-run":
                dry = True
                idx += 1
            else:
                idx += 1
        return handle_purge(target_dir, older_than_days=days, dry_run=dry)

    if mode == "doctor":
        return handle_doctor()

    if mode == "status":
        return handle_status()

    payload: dict[str, Any] = {}
    state_dir: Path | None = None
    try:
        raw_stdin = ""
        if not sys.stdin.isatty():
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
