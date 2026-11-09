# Antigravity Compaction Guard

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Antigravity: 2.0](https://img.shields.io/badge/Antigravity-2.0%2B%20(v2.29.0)-orange.svg)](plugin.json)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

Deterministic lifecycle hook engine for Google Antigravity (`agy`). Prevents agent context amnesia, tracks modified code symbols, and verifies physical disk state across auto-compaction checkpoints with zero token overhead during normal turns.

---

## Background

When an Antigravity conversation trajectory approaches its context boundary (~256K tokens, defined in `Store.ContextWindow()`), the platform triggers a **checkpoint compaction**:

1. **Loss of Code Symbol Context:** Raw tool outputs and syntax structures are collapsed into lossy prose summaries, removing exact function signatures, class interfaces, and schema definitions from active memory.
2. **"File Still in Memory" Hallucination:** The checkpoint summary frequently claims previously modified files remain available in working memory. The agent assumes it retains exact file contents, avoids reading from disk, and applies misaligned edits.
3. **Mid-Session Re-Bootstrapping:** Agents often lose session continuity post-compaction, emitting generic onboarding greetings (*"How can I help you today?"*) or asking to re-confirm roles and project scopes mid-task.

Because native `PreCompact` and `PostCompact` hooks are not yet exposed by the platform (upstream feature request `#1317`), compaction cannot be intercepted beforehand. Compaction Guard provides a deterministic continuous recorder and post-checkpoint ground truth enforcement layer to resolve these limitations.

---

## Architecture

Compaction Guard coordinates three lifecycle hooks (`PostToolUse`, `PreInvocation`, `Stop`) with an in-session flight recorder:

```
                  NORMAL EXECUTION LOOP (0 Token Overhead)
┌────────────────────────────────────────────────────────────────────────┐
│ [Tool Execution] ──► PostToolUse Hook (Incremental Flight Recorder)    │
│                            │                                           │
│                            ▼                                           │
│                .system_generated/compaction_guard_state.json           │
│                • Verbatim user requests & authorizations               │
│                • Resolved ask_question specifications                  │
│                • Extracted code symbols (class, def, interface, fn)    │
│                • Physical disk SHA-256 and byte sizes                  │
│                • Transcript runway telemetry                           │
└────────────────────────────────────────────────────────────────────────┘
                                 │
              [ Engine Triggers CHECKPOINT Compaction ]
                                 │
                                 ▼
                    POST-COMPACTION CONTINUITY
┌────────────────────────────────────────────────────────────────────────┐
│ 1. PreInvocation Hook:                                                 │
│    • Detects unhandled CHECKPOINT step in transcript.                  │
│    • Injects single-shot Recovery Capsule via systemMessage.           │
│    • Probes physical disk: reports DISK_OK or MISSING_ON_DISK.         │
│                                                                        │
│ 2. rules/AGENTS.md Directive:                                          │
│    • Enforces post-compaction continuity laws.                         │
│    • Mandates disk re-verification before resuming code edits.         │
│                                                                        │
│ 3. Stop Hook (Circuit Breaker):                                        │
│    • Intercepts new-session startup greetings emitted mid-task.        │
│    • Emits single-shot continuation veto, resuming active workflow.    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Core Capabilities

### 1. Continuous Flight Recorder (`PostToolUse`)
- Incrementally tails `transcript_full.jsonl` using buffered byte-offset streaming (`< 20 ms`, O(1) memory).
- Tolerates partial line writes at EOF during concurrent logging without offset desynchronization.
- Extracts and records:
  - Initial and recent `USER_REQUEST` text.
  - User-resolved `ask_question` decision pairs.
  - **Code Symbols:** Extracts modified top-level and member definitions (`class`, `def`, `interface`, `type`, `struct`, `fn`) across Python, TypeScript, JavaScript, Rust, and Go.
  - **Physical Disk Hashes:** Computes 12-character SHA-256 and byte sizes of modified files (skips binary files > 64 MB).
- State is isolated to `.system_generated/compaction_guard_state.json` inside the session artifact directory.

### 2. Zero-Overhead Ground-Truth Rehydration (`PreInvocation`)
- Emits `{}` (`0` tokens) on normal turns.
- When a new `CHECKPOINT` is detected, injects an authoritative `systemMessage` Ground-Truth Recovery Capsule.
- Strictly bounded within a token budget (~5,800 characters) to prevent context bloat:
  - Verbatim user requests and permissions.
  - Locked `ask_question` requirements.
  - Mutated files ledger with live disk verification status (`DISK_OK` vs `MISSING_ON_DISK`) and modified code symbols.
  - Tool actions executed immediately prior to compaction.

### 3. Mid-Session Amnesia Circuit Breaker (`Stop`)
- Single-shot circuit breaker per `CHECKPOINT`.
- If the model finishes a turn without tool calls and outputs a new-session onboarding greeting or asks to re-bootstrap roles, the hook emits `decision: "continue"` with an orientation reminder, preventing accidental task termination.

### 4. Transcript Runway Telemetry
- Evaluates transcript file size and logs runway alerts:
  - `NORMAL`: `< 650 KB`
  - `WARNING`: `650 KB - 720 KB` (~220K tokens)
  - `CRITICAL`: `> 720 KB` (~245K tokens; auto-compaction imminent)

---

## Installation

### Global Installation (User Level)

Clone directly into the Antigravity global plugins directory:

```bash
git clone https://github.com/mhdalijawara-cloud/antigravity-compaction-guard.git ~/.gemini/config/plugins/compaction-guard
```

### Workspace Installation (Project Level)

Clone into your workspace configuration directory:

```bash
git clone https://github.com/mhdalijawara-cloud/antigravity-compaction-guard.git .agents/plugins/compaction-guard
```

---

## Diagnostics and Testing

```bash
# Run the test suite (16 tests, mock JSONL transcripts)
python ./scripts/test_compaction_guard.py -v

# Inspect session state, runway meter, and recovery capsule for any transcript
python ./scripts/compaction_guard.py inspect "<path-to-transcript.jsonl>"

# Print version
python ./scripts/compaction_guard.py --version
```

---

## Specifications

| Property | Value |
| :--- | :--- |
| **Normal Turn Overhead** | `0 tokens` (emits `{}`) |
| **Recovery Capsule Budget** | `< 1,200 tokens` (~5,800 characters) |
| **Execution Latency** | `< 20 ms` per hook call |
| **Runtime Dependencies** | None (Python 3.10+ standard library only) |
| **Platform Support** | Linux, macOS, Windows |
| **Tested Environment** | Antigravity 2.0 (v2.29.0) |

---

## License

[MIT License](LICENSE) — Copyright (c) 2026 Muhammad Ali Jawara Wiratama
