# Post-Compaction Continuity & Anti-Amnesia Directive

Whenever your conversation context contains `# Resuming from a compaction` (a `CHECKPOINT` step) or a `[COMPACTION GUARD RECOVERY CAPSULE]` system message, enforce these non-negotiable continuity laws:

1. **Ongoing Session — Never Re-Bootstrap:** A compaction event is a mid-session memory compression, **NOT** a new conversation. You are **STRICTLY PROHIBITED** from re-running new-session startup questions (such as asking what role you play on this session) or discarding active user work authorizations and role assignments already granted prior to compaction.
2. **Trust Verbatim Ground Truth Over Lossy Summary:** The `<summary>` inside a `CHECKPOINT` step is lossy prose. Always treat the verbatim `USER_INPUT` prompts, resolved `ask_question` Q&A pairs, and mutated-file ledger injected by Compaction Guard (or stored in `<appDataDir>/brain/<conversation-id>/.system_generated/compaction_guard_state.json` and `transcript_full.jsonl`) as authoritative ground truth.
3. **Mandatory Disk Re-Verification Before Resuming Edits:** Any file listed as modified before compaction has expired `VERIFIED` freshness. Before executing your next `edit_file`, `write_file`, or stateful command, call `read_file` on the active target lines to confirm exact current disk state and avoid duplicate or misaligned edits.
4. **Targeted Transcript Lookup When Exact Details Are Missing:** If a pre-compaction formula, stack trace, hash, or line number is absent from `<summary>`, query `<appDataDir>/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` (and read only the needed line in `transcript_full.jsonl`) or run:
   `python ~/.gemini/config/plugins/compaction-guard/scripts/compaction_guard.py inspect "<transcriptPath>"`
