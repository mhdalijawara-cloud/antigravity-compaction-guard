# Compaction Guard

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests: 16/16 Passed](https://img.shields.io/badge/tests-16%2F16%20passed-brightgreen.svg)](scripts/test_compaction_guard.py)
[![Antigravity: >=1.4.0](https://img.shields.io/badge/Antigravity-%3E%3D1.4.0-orange.svg)](plugin.json)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform: Linux | macOS | Windows](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)](hooks.json)

> **Deterministic lifecycle hook engine for Google Antigravity (`agy`).**  
> Prevents agent context amnesia, tracks modified code symbols, and verifies physical disk state across auto-compaction checkpoints with zero token overhead on normal turns.

---

## The Problem

When a Google Antigravity session approaches its hardcoded ~256K token threshold (`Store.ContextWindow()`), the engine performs a **checkpoint compaction**:

1. **Loss of Code Symbol Context (Upstream #1245):** Raw tool calls, syntax trees, and file diffs are collapsed into lossy prose summaries, erasing precise class, function, and schema signatures from active context.
2. **"File Still in Memory" Hallucination (Upstream #1294):** The checkpoint summary frequently claims previously modified files remain loaded in working memory. The agent assumes it knows the file contents, avoids re-reading from disk, and applies corrupted edits.
3. **Mid-Session Amnesia:** Agents frequently revert to new-session startup greetings (*"How can I help you today?"*) or re-query role assignments and user permissions mid-task.

Because native `PreCompact` and `PostCompact` hooks do not exist in Antigravity (upstream feature request `#1317`), compaction cannot be prevented from the outside. 

---

## How It Works

Compaction Guard implements an asynchronous, multi-hook lifecycle architecture to record state before compaction and enforce ground truth immediately after:

```
                  NORMAL AGENT LOOP (0 Token Tax)
┌─────────────────────────────────────────────────────────────────┐
│ [Tool Execution] ──► PostToolUse Hook (Incremental Recorder)    │
│                           │                                     │
│                           ▼                                     │
│               .system_generated/state.json                      │
│               • Verbatim USER_REQUEST prompts                   │
│               • Locked ask_question specifications              │
│               • Extracted AST code symbols (class, def, struct) │
│               • Physical disk SHA-256 & byte size               │
│               • Transcript Runway Meter (650KB threshold)       │
└─────────────────────────────────────────────────────────────────┘
                                │
               [ Engine Triggers CHECKPOINT (~256K) ]
                                │
                                ▼
                 POST-COMPACTION SURGICAL REHYDRATION
┌─────────────────────────────────────────────────────────────────┐
│ 1. PreInvocation Hook:                                          │
│    • Detects new CHECKPOINT step in transcript.                 │
│    • Injects single-shot Recovery Capsule via systemMessage.    │
│    • Probes physical disk: flags DISK_OK or MISSING_ON_DISK.    │
│                                                                 │
│ 2. rules/AGENTS.md Directive:                                   │
│    • Enforces non-negotiable amnesia rules across compaction.   │
│    • Mandates disk re-verification before further edits.        │
│                                                                 │
│ 3. Stop Hook (Circuit Breaker):                                 │
│    • Intercepts new-session startup prompts emitted mid-task.   │
│    • Emits continuation veto, forcing agent to resume task.     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Architecture Components

### 1. Continuous Flight Recorder (`PostToolUse` / `post-tool`)
- Incrementally trails `transcript_full.jsonl` using buffered byte-offset streaming (`< 20 ms`, O(1) memory).
- Handles in-flight trailing lines at EOF without offset corruption.
- Records:
  - Verbatim initial and recent `USER_REQUEST` prompts.
  - Locked `ask_question` user specifications.
  - **Structural Code Symbols:** Automatically extracts top-level classes, methods, functions, and interfaces modified across Python, TypeScript, JavaScript, Rust, and Go.
  - **Physical Disk Hashes:** Computes 12-character SHA-256 and byte sizes of modified files. Files larger than 64 MB are skipped safely to avoid I/O bottlenecks.
- Zero workspace pollution: writes state exclusively to `.system_generated/compaction_guard_state.json`.

### 2. Pre-Compaction Runway Meter (Early Warning Telemetry)
- Continuously monitors transcript byte growth.
- Flags compaction runway status:
  - `NORMAL`: `< 650 KB`
  - `WARNING`: `650 KB - 720 KB` (~220K tokens)
  - `CRITICAL`: `> 720 KB` (~245K tokens; auto-compaction imminent)

### 3. Zero-Tax Surgical Rehydration (`PreInvocation` / `pre-invocation`)
- Emits `{}` (`0` tokens) on normal turns.
- Injects a structured `systemMessage` Ground-Truth Recovery Capsule once per new `CHECKPOINT`.
- Capsule is strictly token-budgeted (~5.800 characters / ~1.200 tokens) and includes:
  - Active user authorizations.
  - Resolved `ask_question` decisions.
  - Mutated files ledger with live disk probe status (`DISK_OK` vs `MISSING_ON_DISK`) and modified code symbols.
  - Interrupted tool actions immediately prior to checkpointing.

### 4. Universal Amnesia Interceptor (`Stop` / `stop`)
- Single-shot circuit breaker per `CHECKPOINT`.
- Evaluates model output if it terminates without tool calls. If the agent emits a new-session onboarding greeting or asks to re-bootstrap roles mid-session, the hook emits `decision: "continue"`, blocking loop termination and reminding the agent of its active state.

---

## Installation

### User-Level Global Installation (Recommended)

Clone directly into your Antigravity global plugin directory:

```bash
git clone https://github.com/mhdalijawara-cloud/antigravity-compaction-guard.git ~/.gemini/config/plugins/compaction-guard
```

### Workspace-Level Installation

Clone into your project repository to share with your team:

```bash
git clone https://github.com/mhdalijawara-cloud/antigravity-compaction-guard.git .agents/plugins/compaction-guard
```

---

## Verification & Diagnostics

Compaction Guard includes a self-contained test suite and CLI inspector:

```bash
# Run the test suite (16 tests, mock JSONL transcripts)
python ~/.gemini/config/plugins/compaction-guard/scripts/test_compaction_guard.py -v

# Inspect session state, runway meter, and recovery capsule for any transcript
python ~/.gemini/config/plugins/compaction-guard/scripts/compaction_guard.py inspect "<path-to-transcript.jsonl>"

# Check version
python ~/.gemini/config/plugins/compaction-guard/scripts/compaction_guard.py --version
```

---

## Performance & Overhead

| Metric | Measured Value | Note |
| :--- | :--- | :--- |
| **Normal Turn Token Overhead** | `0 tokens` | Emits empty JSON `{}` |
| **Post-Checkpoint Capsule Size** | `< 1,200 tokens` | Strictly capped at 5.800 chars |
| **`PostToolUse` Hook Latency** | `< 20 ms` | Incremental byte-offset tailing |
| **Dependencies** | `Zero` | Python 3.10+ standard library only |
| **Disk Probe Footprint** | `< 5 ms` | SHA-256 on modified files (< 64 MB) |

---

## Author & License

- **Author:** Muhammad Ali Jawara Wiratama
- **License:** [MIT License](LICENSE)
