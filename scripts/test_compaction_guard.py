#!/usr/bin/env python3
"""Non-tautological test suite for Compaction Guard lifecycle hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import compaction_guard as cg  # noqa: E402


class TestCompactionGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.brain_dir = Path(self.tmp_dir.name) / "brain" / "test-conv"
        self.logs_dir = self.brain_dir / ".system_generated" / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = self.logs_dir / "transcript.jsonl"
        self.transcript_full_path = self.logs_dir / "transcript_full.jsonl"
        self.target_src = Path(self.tmp_dir.name) / "rl_agent.py"
        self.target_src.write_text("OBSERVATION_DIM = 29\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _append_steps(self, steps: list[dict]) -> None:
        with self.transcript_full_path.open("a", encoding="utf-8") as fh_full, self.transcript_path.open(
            "a", encoding="utf-8"
        ) as fh_comp:
            for s in steps:
                line = json.dumps(s, ensure_ascii=False) + "\n"
                fh_full.write(line)
                fh_comp.write(line)

    def _base_payload(self, extra: dict | None = None) -> dict:
        p = {
            "conversationId": "test-conv",
            "workspacePaths": [self.tmp_dir.name],
            "transcriptPath": str(self.transcript_path),
            "artifactDirectoryPath": str(self.brain_dir),
            "modelName": "auto",
            "lastUserInput": "lanjutkan",
        }
        if extra:
            p.update(extra)
        return p

    def test_zero_overhead_when_no_checkpoint(self) -> None:
        self._append_steps(
            [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>\nKamu Role 1, izin diberikan penuh.\n</USER_REQUEST>",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [
                        {
                            "name": "edit_file",
                            "args": {"Path": json.dumps(str(self.target_src))},
                        }
                    ],
                },
            ]
        )
        post_out = cg.handle_post_tool(self._base_payload({"stepIdx": 1}))
        self.assertEqual(post_out, {})

        pre_out = cg.handle_pre_invocation(self._base_payload({"invocationNum": 2, "initialNumSteps": 2}))
        self.assertEqual(pre_out, {}, "Normal turn without CHECKPOINT must emit 0-overhead empty JSON")

    def test_post_compaction_capsule_injection_and_idempotency(self) -> None:
        q_json = json.dumps([{"question": "Pilih dimensi state?", "options": ["29", "27"]}])
        self._append_steps(
            [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>\nBertugas sebagai Role 1. Izin kerja otonom diberikan.\n</USER_REQUEST>\n<ADDITIONAL_METADATA>time</ADDITIONAL_METADATA>",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [{"name": "ask_question", "args": {"questions": q_json}}],
                },
                {
                    "step_index": 2,
                    "source": "MODEL",
                    "type": "GENERIC",
                    "content": "Created At: 2026-11-08\nCompleted At: 2026-11-08\nA1: 29 -> Ekspansi MAE & MFE",
                },
                {
                    "step_index": 3,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [
                        {"name": "edit_file", "args": {"Path": json.dumps(str(self.target_src))}}
                    ],
                },
                {
                    "step_index": 4,
                    "source": "SYSTEM",
                    "type": "CHECKPOINT",
                    "content": "# Resuming from a compaction\n<summary>lossy</summary>",
                },
            ]
        )

        # First PreInvocation after CHECKPOINT must inject structured systemMessage capsule
        out_1 = cg.handle_pre_invocation(self._base_payload({"invocationNum": 3, "initialNumSteps": 5}))
        self.assertIn("injectSteps", out_1)
        sys_msg = out_1["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertIn("Role 1. Izin kerja otonom diberikan.", sys_msg)
        self.assertIn("Pilih dimensi state?", sys_msg)
        self.assertIn("A1: 29 -> Ekspansi MAE & MFE", sys_msg)
        self.assertIn("rl_agent.py", sys_msg)
        self.assertIn("DISK_OK size=", sys_msg)
        self.assertIn("DO NOT execute new-session role bootstrap", sys_msg)

        # Second PreInvocation without a new CHECKPOINT must emit {} (no duplicate spam)
        out_2 = cg.handle_pre_invocation(self._base_payload({"invocationNum": 4, "initialNumSteps": 7}))
        self.assertEqual(out_2, {})

        # A second compaction later (step 10) must re-fire once for the new checkpoint
        self._append_steps(
            [
                {
                    "step_index": 10,
                    "source": "SYSTEM",
                    "type": "CHECKPOINT",
                    "content": "# Resuming from a compaction\n",
                }
            ]
        )
        out_3 = cg.handle_pre_invocation(self._base_payload({"invocationNum": 1, "initialNumSteps": 11}))
        self.assertIn("injectSteps", out_3)
        self.assertIn("step 10", out_3["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_stop_hook_blocks_amnesia_bootstrap_once_only(self) -> None:
        # Initial session step 1 (no CHECKPOINT): must ALLOW normal bootstrap question!
        self._append_steps(
            [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "<USER_REQUEST>halo</USER_REQUEST>"},
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "content": "Sesi terhubung dengan panduan GEMINI.md. Saya bertugas sebagai peran apa pada sesi ini?",
                },
            ]
        )
        initial_stop = cg.handle_stop(self._base_payload({"terminationReason": "NO_TOOL_CALL"}))
        self.assertEqual(initial_stop["decision"], "allow")

        # Mid-session step 8 after CHECKPOINT at step 6: must BLOCK amnesia reset once!
        self._append_steps(
            [
                {"step_index": 2, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "<USER_REQUEST>Role 1 lanjutkan</USER_REQUEST>"},
                {"step_index": 3, "source": "MODEL", "type": "GENERIC", "content": "ok"},
                {"step_index": 4, "source": "MODEL", "type": "GENERIC", "content": "ok"},
                {"step_index": 5, "source": "MODEL", "type": "GENERIC", "content": "ok"},
                {"step_index": 6, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Resuming from a compaction"},
                {
                    "step_index": 7,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "content": "Sesi terhubung dengan panduan [GEMINI.md](file:///path). Saya bertugas sebagai peran apa pada sesi ini?",
                },
            ]
        )
        amnesia_stop_1 = cg.handle_stop(self._base_payload({"terminationReason": "NO_TOOL_CALL"}))
        self.assertEqual(amnesia_stop_1["decision"], "continue")
        self.assertIn("COMPACTION GUARD AMNESIA BLOCK", amnesia_stop_1["reason"])

        # Circuit breaker check: calling Stop again on the same checkpoint MUST allow (zero infinite loop)
        amnesia_stop_2 = cg.handle_stop(self._base_payload({"terminationReason": "NO_TOOL_CALL"}))
        self.assertEqual(amnesia_stop_2["decision"], "allow")

    def test_windows_cmd_subprocess_unicode_safety(self) -> None:
        self._append_steps(
            [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>Uji karakter Unicode: ≤ ≥ → λ_loss = 2.0</USER_REQUEST>",
                },
                {"step_index": 1, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Resuming"},
            ]
        )
        hooks_cfg = json.loads((SCRIPT_DIR.parent / "hooks.json").read_text(encoding="utf-8"))
        cmd = hooks_cfg["compaction-guard"]["PreInvocation"][0]["command"]
        payload_str = json.dumps(self._base_payload(), ensure_ascii=False)
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(SCRIPT_DIR.parent),
            input=payload_str.encode("utf-8"),
            capture_output=True,
            timeout=10,
            check=True,
        )
        parsed = json.loads(proc.stdout.decode("utf-8"))
        self.assertIn("injectSteps", parsed)
        self.assertIn("λ_loss = 2.0", parsed["injectSteps"][0]["systemMessage"]["systemMessage"])

        # Verify zero clutter in top-level artifactDirectoryPath (only hidden .system_generated exists)
        top_entries = [p.name for p in self.brain_dir.iterdir()]
        self.assertEqual(top_entries, [".system_generated"])

    def test_real_compacted_transcript_replay_if_available(self) -> None:
        real_tp = Path(
            r"C:\Users\Pongo\.gemini\antigravity\brain\2dc6f470-b6f5-40bb-9e41-6fb1d785995b\.system_generated\logs\transcript.jsonl"
        )
        if not real_tp.exists():
            self.skipTest("Historical transcript 2dc6f470 not present on this machine")
        state = cg._default_state()
        state = cg.sync_state_from_transcript(state, str(real_tp))
        self.assertEqual(state["checkpoints_seen"], [322, 748, 1211, 1521])
        self.assertGreater(len(state["qa_decisions"]), 0)
        self.assertGreater(len(state["mutated_files"]), 0)
        capsule = cg.build_recovery_capsule(state, 1521, str(real_tp))
        self.assertLessEqual(len(capsule), cg.MAX_CAPSULE_CHARS)
        self.assertIn("## Verbatim User Requests & Active Authorizations", capsule)
        self.assertIn("## Locked Specifications (`ask_question` Decisions)", capsule)
        self.assertIn("## Files Modified in This Session", capsule)
        self.assertIn("## Last Tool Actions Immediately Before Compaction", capsule)
        self.assertIn("rl_agent.py", capsule)

    def test_amnesia_evasion_variants_and_negative_controls(self) -> None:
        """Adversarial check: paraphrased role questions must trigger Stop hook, but benign controls must pass."""
        variants = [
            "Sesi terhubung dengan panduan GEMINI.md. Saya bertugas sebagai peran apa pada sesi ini?",
            "Sebagai peran apa saya bertugas pada sesi ini?",
            "Peran apa yang saya emban pada sesi ini?",
            "Peran apa yang harus saya jalankan?",
            "Apa peran saya di sesi ini?",
            "Saya harus bertugas sebagai peran apa pada sesi ini?",
            "Mohon konfirmasi arahan peran saya untuk sesi ini?",
            "What role do I play on this session?",
            "What role am I assigned on this session?",
            "What is my assigned role for this session?",
            "Which role should I assume?",
        ]
        negatives = [
            "Peran fitur ini dalam pipeline adalah untuk mengukur volatilitas pasar.",
            "Kita akan melanjutkan tugas sebagai Role 1 untuk melatih model.",
            "Role 3 telah memverifikasi arsitektur tanpa veto.",
            "Fungsi ini bertugas sebagai transformer fitur.",
            "The role of the discriminator is to evaluate policy quality.",
        ]

        # Verify all variants match regex
        for v in variants:
            self.assertTrue(
                bool(cg.BOOTSTRAP_AMNESIA_RE.search(v)),
                f"Variant failed to be caught by BOOTSTRAP_AMNESIA_RE: {v!r}",
            )
        # Verify negative controls do not match regex
        for n in negatives:
            self.assertFalse(
                bool(cg.BOOTSTRAP_AMNESIA_RE.search(n)),
                f"False positive on benign control: {n!r}",
            )

    def test_windows_path_case_deduplication(self) -> None:
        """Adversarial check: Windows paths differing only in drive letter case must merge into 1 key."""
        file_lower = f"c:/{self.target_src.name}"
        file_upper = f"C:/{self.target_src.name}"
        self._append_steps(
            [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "test"},
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [{"name": "edit_file", "args": {"Path": file_lower}}],
                },
                {
                    "step_index": 2,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [{"name": "edit_file", "args": {"Path": file_upper}}],
                },
            ]
        )
        state = cg._default_state()
        state = cg.sync_state_from_transcript(state, str(self.transcript_path), workspace_root=self.tmp_dir.name)
        # There must be strictly 1 mutated file entry, not 2!
        self.assertEqual(len(state["mutated_files"]), 1)
        single_entry = list(state["mutated_files"].values())[0]
        self.assertEqual(single_entry["edit_count"], 2)

    def test_relative_path_workspace_probing(self) -> None:
        """Adversarial check: relative path tool calls must be probed against workspacePaths correctly."""
        rel_sub = Path(self.tmp_dir.name) / "sub_dir" / "engine.py"
        rel_sub.parent.mkdir(parents=True, exist_ok=True)
        rel_sub.write_text("class Engine: pass\n", encoding="utf-8")

        self._append_steps(
            [
                {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "create engine"},
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [{"name": "write_file", "args": {"Path": "sub_dir/engine.py"}}],
                },
                {"step_index": 2, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Resuming"},
            ]
        )
        state = cg._default_state()
        state = cg.sync_state_from_transcript(state, str(self.transcript_path), workspace_root=self.tmp_dir.name)
        capsule = cg.build_recovery_capsule(state, 2, str(self.transcript_path), workspace_root=self.tmp_dir.name)
        self.assertIn("sub_dir/engine.py", capsule)
        self.assertIn("DISK_OK", capsule)
        self.assertNotIn("MISSING_ON_DISK", capsule)

    def test_transcript_streaming_and_incomplete_eof(self) -> None:
        """Adversarial check: incomplete trailing line at EOF must not corrupt byte offset or state."""
        # Append complete line
        with self.transcript_full_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "req1"}) + "\n")
            # Incomplete line write without \n
            fh.write('{"step_index": 1, "type": "USER_INPUT", "content": "partial in-flight')

        state = cg._default_state()
        state = cg.sync_state_from_transcript(state, str(self.transcript_path))
        self.assertEqual(state["total_steps_seen"], 1)
        self.assertEqual(len(state["recent_user_requests"]), 1)
        self.assertEqual(state["recent_user_requests"][0]["text"], "req1")

        # Now complete the line
        with self.transcript_full_path.open("a", encoding="utf-8") as fh:
            fh.write(' req2"}\n')

        state = cg.sync_state_from_transcript(state, str(self.transcript_path))
        self.assertEqual(state["total_steps_seen"], 2)
        self.assertEqual(len(state["recent_user_requests"]), 2)

    def test_secondary_tail_deep_checkpoint_detection(self) -> None:
        """Adversarial check: secondary transcript checkpoint occurring > 6 lines before EOF must be caught."""
        # Setup secondary compact transcript with CHECKPOINT at line -10
        lines = [json.dumps({"step_index": 50, "type": "CHECKPOINT", "content": "# Resuming"})]
        for i in range(1, 10):
            lines.append(json.dumps({"step_index": 50 + i, "type": "GENERIC", "content": f"msg {i}"}))

        with self.transcript_path.open("w", encoding="utf-8") as fh:
            for l in lines:
                fh.write(l + "\n")

        # Point primary to a dummy empty full transcript so secondary tail is read
        dummy_full = self.logs_dir / "transcript_full.jsonl"
        dummy_full.write_text("", encoding="utf-8")

        state = cg._default_state()
        state = cg.sync_state_from_transcript(state, str(self.transcript_path))
        self.assertIn(50, state["checkpoints_seen"])

    def test_mutated_files_capped_at_max(self) -> None:
        """Adversarial check: state['mutated_files'] must be capped at MAX_MUTATED_FILES."""
        steps = [{"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "mass edits"}]
        for i in range(cg.MAX_MUTATED_FILES + 15):
            steps.append(
                {
                    "step_index": i + 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [{"name": "write_file", "args": {"Path": f"file_{i:03d}.py"}}],
                }
            )
        self._append_steps(steps)
        state = cg._default_state()
        state = cg.sync_state_from_transcript(state, str(self.transcript_path), workspace_root=self.tmp_dir.name)
        self.assertLessEqual(len(state["mutated_files"]), cg.MAX_MUTATED_FILES)
        self.assertEqual(len(state["mutated_files"]), cg.MAX_MUTATED_FILES)

    def test_atomic_save_retry_and_temp_cleanup(self) -> None:
        """Adversarial check: atomic save must clean up all temporary files."""
        state_file = self.brain_dir / ".system_generated" / "compaction_guard_state.json"
        state = cg._default_state()
        state["total_steps_seen"] = 42
        cg.save_state_atomic(state_file, state)
        self.assertTrue(state_file.exists())
        # Ensure zero orphan .tmp files
        tmp_files = list(state_file.parent.glob("*.tmp*"))
        self.assertEqual(tmp_files, [])

    def test_code_symbol_extraction_python_and_ts(self) -> None:
        """Feature 1: Code symbol extraction for Python and TypeScript/JavaScript."""
        py_code = (
            "class EngineManager:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def start_pipeline(self):\n"
            "        pass\n"
        )
        symbols = cg._extract_code_symbols(py_code)
        self.assertIn("EngineManager", symbols)
        self.assertIn("__init__", symbols)
        self.assertIn("start_pipeline", symbols)

        ts_code = (
            "export interface UserProfile {\n"
            "    id: string;\n"
            "}\n"
            "export function fetchProfile() {\n"
            "    return null;\n"
            "}\n"
        )
        ts_symbols = cg._extract_code_symbols(ts_code)
        self.assertIn("UserProfile", ts_symbols)
        self.assertIn("fetchProfile", ts_symbols)

    def test_runway_meter_thresholds(self) -> None:
        """Feature 2: Pre-compaction runway telemetry based on transcript file size."""
        # Write normal sized transcript
        self.transcript_full_path.write_text(
            json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "hi"}) + "\n",
            encoding="utf-8",
        )
        state = cg._default_state()
        state = cg.sync_state_from_transcript(state, str(self.transcript_path))
        self.assertEqual(state.get("runway_status"), "NORMAL")

        # Simulate large transcript > RUNWAY_WARNING_BYTES
        padding = "X" * (cg.RUNWAY_WARNING_BYTES + 1024)
        self.transcript_full_path.write_text(
            json.dumps({"step_index": 1, "type": "USER_INPUT", "content": padding}) + "\n",
            encoding="utf-8",
        )
        state = cg.sync_state_from_transcript(state, str(self.transcript_path))
        self.assertIn(state.get("runway_status"), ("WARNING", "CRITICAL"))

    def test_universal_startup_amnesia_patterns(self) -> None:
        """Feature 3: Universal multi-lingual startup greetings and amnesia patterns."""
        amnesia_phrases = [
            "How can I help you today?",
            "What would you like me to build today?",
            "I am ready to help, what would you like us to work on?",
            "Halo, apa yang bisa saya bantu?",
            "Selamat pagi, ada yang bisa saya bantu untuk sesi ini?",
            "Silakan berikan instruksi baru.",
            "What is my assigned role for this session?",
        ]
        for phrase in amnesia_phrases:
            self.assertTrue(
                bool(cg.BOOTSTRAP_AMNESIA_RE.search(phrase)),
                f"Universal phrase must trigger amnesia detector: {phrase!r}",
            )

    def test_cli_flags_non_blocking(self) -> None:
        """CLI flags like --help and --version must return immediately with 0 without blocking."""
        self.assertEqual(cg.main(["--help"]), 0)
        self.assertEqual(cg.main(["--version"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
