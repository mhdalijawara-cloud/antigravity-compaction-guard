"""Physical file integrity auditing, baseline hashing, and workspace boundary containment."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys
from typing import Any


def is_path_within_workspace(raw_path: str, workspace_root: str | None = None) -> bool:
    """Validate that path resides strictly inside workspace_root (anti-traversal / P1-02)."""
    if not workspace_root or not raw_path:
        return True
    try:
        w_res = Path(workspace_root).resolve()
        p = Path(raw_path)
        if not p.is_absolute():
            p_res = (w_res / p).resolve()
        else:
            p_res = p.resolve()
        try:
            p_res.relative_to(w_res)
            return True
        except ValueError:
            return False
    except (OSError, ValueError):
        return False


def canonicalize_path(raw_path: str, workspace_root: str | None = None) -> str:
    """Return a lowercased canonical absolute path key to eliminate case duplicate entries cross-platform."""
    cleaned = raw_path
    if re.match(r"^[a-zA-Z]:[/\\]", cleaned):
        drive = cleaned[0].lower()
        rest = cleaned[2:].replace("\\", "/")
        cleaned = f"{drive}:{rest}"
        if sys.platform != "win32":
            return cleaned
    try:
        p = Path(cleaned)
        if not p.is_absolute() and workspace_root:
            p = Path(workspace_root) / p
        resolved = p.resolve()
        return os.path.normcase(str(resolved))
    except Exception:
        return os.path.normcase(os.path.normpath(raw_path))


def compute_file_probe(raw_path: str, workspace_root: str | None = None) -> dict[str, Any]:
    """Compute physical file existence, exact byte size, full 64-char SHA-256, and boundary check."""
    try:
        p = Path(raw_path)
        if not p.is_absolute() and workspace_root:
            candidate = Path(workspace_root) / p
            if candidate.is_file() or candidate.exists():
                p = candidate

        # P1-02: Workspace containment boundary verification
        if workspace_root and not is_path_within_workspace(str(p), workspace_root):
            return {
                "exists": p.is_file(),
                "size_bytes": p.stat().st_size if p.is_file() else None,
                "sha256_full": None,
                "sha256_12": None,
                "status": "OUTSIDE_WORKSPACE_BOUNDARY",
                "resolved_path": p.name,
                "workspace_contained": False,
            }

        if not p.exists() or not p.is_file():
            return {
                "exists": False,
                "size_bytes": None,
                "sha256_full": None,
                "sha256_12": None,
                "status": "MISSING_ON_DISK",
                "resolved_path": str(p),
                "workspace_contained": True,
            }
        size = p.stat().st_size
        if size > 64 * 1024 * 1024:
            return {
                "exists": True,
                "size_bytes": size,
                "sha256_full": None,
                "sha256_12": None,
                "status": "HASH_SKIPPED_SIZE_LIMIT",
                "resolved_path": str(p),
                "workspace_contained": True,
            }
        h = hashlib.sha256()
        with p.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        full_hex = h.hexdigest()
        return {
            "exists": True,
            "size_bytes": size,
            "sha256_full": full_hex,
            "sha256_12": full_hex[:12],
            "status": "OK",
            "resolved_path": str(p),
            "workspace_contained": True,
        }
    except OSError:
        return {
            "exists": False,
            "size_bytes": None,
            "sha256_full": None,
            "sha256_12": None,
            "status": "UNREADABLE",
            "resolved_path": raw_path,
            "workspace_contained": True,
        }


def resolve_workspace_root(payload: dict[str, Any]) -> str | None:
    """Extract primary workspace root from payload if available."""
    wpaths = payload.get("workspacePaths")
    if isinstance(wpaths, list) and len(wpaths) > 0 and isinstance(wpaths[0], str) and wpaths[0].strip():
        return wpaths[0].strip()
    for k in ("workspacePath", "cwd", "workingDirectory"):
        val = payload.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


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
