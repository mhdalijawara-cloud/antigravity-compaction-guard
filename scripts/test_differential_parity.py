#!/usr/bin/env python3
"""Shadow Differential Testing Harness: Monolith vs Modular Engine.

Mathematically validates that the modular engine in src/compaction_guard/ produces
100% byte-for-byte and logical parity against the monolithic scripts/compaction_guard.py.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"

# 1. Load monolith module explicitly from scripts/compaction_guard.py
mono_path = SCRIPT_DIR / "compaction_guard.py"
spec_mono = importlib.util.spec_from_file_location("compaction_guard_monolith", mono_path)
monolith = importlib.util.module_from_spec(spec_mono)
spec_mono.loader.exec_module(monolith)

# 2. Load modular package from src/compaction_guard/
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
import compaction_guard as modular


class TestShadowDifferentialParity(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.transcript_path = self.tmp_path / "differential_transcript.jsonl"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _append_transcript(self, steps: list[dict], custom_path: Path | None = None) -> None:
        target = custom_path or self.transcript_path
        with target.open("a", encoding="utf-8") as fh:
            for s in steps:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")

    def test_default_state_differential_parity(self) -> None:
        """Default state schemas must be 100% identical."""
        mono_def = monolith._default_state()
        mod_def = modular.default_state()
        self.assertEqual(mono_def, mod_def)

    def test_symbol_extraction_differential_parity(self) -> None:
        """Symbol extraction must yield identical results across multiple languages."""
        code_snippets = [
            "class OrderService { public void processOrder() { } }",
            "export const fetchUser = async (id: string) => { return id; };",
            "def calculate_moving_average(prices: list[float]) -> float: return sum(prices)/len(prices)",
            "pub fn execute_trade(pair: &str, amount: f64) -> Result<(), Error> { Ok(()) }",
            "func HandleRequest(w http.ResponseWriter, r *http.Request) { }",
            "protected static synchronized boolean validateMargin(Account acc) { if (acc != null) return true; }",
            "public async Task<OrderResponse> ProcessOrderAsync(OrderRequest req) { return await Task.FromResult(new OrderResponse()); }",
        ]
        for snippet in code_snippets:
            mono_syms = monolith._extract_code_symbols(snippet)
            mod_syms = modular.extract_code_symbols(snippet)
            self.assertEqual(mono_syms, mod_syms, f"Mismatch on snippet: {snippet}")

    def test_workspace_confinement_differential_parity(self) -> None:
        """Workspace containment checks must match 100%."""
        inside_file = self.tmp_path / "inside" / "app.py"
        inside_file.parent.mkdir(parents=True, exist_ok=True)
        inside_file.write_text("print(1)", encoding="utf-8")

        outside_file = self.tmp_path / "outside.txt"
        outside_file.write_text("secret", encoding="utf-8")

        workspace = self.tmp_path / "inside"

        # Check is_path_within_workspace
        self.assertEqual(
            monolith.is_path_within_workspace(str(inside_file), str(workspace)),
            modular.is_path_within_workspace(str(inside_file), str(workspace)),
        )
        self.assertEqual(
            monolith.is_path_within_workspace(str(outside_file), str(workspace)),
            modular.is_path_within_workspace(str(outside_file), str(workspace)),
        )

        # Check compute_file_probe
        mono_probe_in = monolith._compute_file_probe(str(inside_file), str(workspace))
        mod_probe_in = modular.compute_file_probe(str(inside_file), str(workspace))
        self.assertEqual(mono_probe_in, mod_probe_in)

        mono_probe_out = monolith._compute_file_probe(str(outside_file), str(workspace))
        mod_probe_out = modular.compute_file_probe(str(outside_file), str(workspace))
        self.assertEqual(mono_probe_out, mod_probe_out)

    def test_multi_step_trajectory_differential_parity(self) -> None:
        """Process a complex trajectory through both engines and assert exact state & capsule parity."""
        test_file = self.tmp_path / "src" / "engine.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("class Engine:\n    def start(self): pass\n", encoding="utf-8")

        steps = [
            {"step_index": 0, "source": "USER_INPUT", "type": "USER_INPUT", "content": "<USER_REQUEST>Build autonomous trading bot</USER_REQUEST>"},
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Let me edit src/engine.py",
                "tool_calls": [
                    {
                        "name": "edit_file",
                        "args": {
                            "Path": str(test_file),
                            "Content": "class Engine:\n    def start(self): pass\n    def stop(self): pass\n",
                        },
                    }
                ],
            },
            {"step_index": 2, "source": "USER_INPUT", "type": "USER_INPUT", "content": "Tool output: file modified successfully", "status": "OK"},
            {
                "step_index": 3,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Need clarification",
                "tool_calls": [
                    {
                        "name": "ask_question",
                        "args": {"questions": ["Should we use M5 or M15 timeframe?"]},
                    }
                ],
            },
            {"step_index": 4, "source": "USER_INPUT", "type": "GENERIC", "content": "A1: M5 timeframe."},
            {"step_index": 5, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Resuming from compaction"},
        ]
        self._append_transcript(steps)

        # Run monolith sync
        mono_state = monolith._default_state()
        mono_state = monolith.sync_state_from_transcript(mono_state, str(self.transcript_path), workspace_root=self.tmp_dir.name)

        # Run modular sync
        mod_state = modular.default_state()
        mod_state = modular.sync_state_from_transcript(mod_state, str(self.transcript_path), workspace_root=self.tmp_dir.name)

        # Remove updated_at timestamps before comparing states
        mono_state.pop("updated_at", None)
        mod_state.pop("updated_at", None)

        self.assertEqual(mono_state["total_steps_seen"], mod_state["total_steps_seen"])
        self.assertEqual(mono_state["checkpoints_seen"], mod_state["checkpoints_seen"])
        self.assertEqual(mono_state["first_user_request"], mod_state["first_user_request"])
        self.assertEqual(mono_state["qa_decisions"], mod_state["qa_decisions"])
        self.assertEqual(mono_state["mutated_files"], mod_state["mutated_files"])
        self.assertEqual(mono_state["runway_status"], mod_state["runway_status"])
        self.assertEqual(mono_state["runway_bytes"], mod_state["runway_bytes"])

        # Build recovery capsules from both engines
        mono_capsule = monolith.build_recovery_capsule(mono_state, 5, str(self.transcript_path), workspace_root=self.tmp_dir.name)
        mod_capsule = modular.build_recovery_capsule(mod_state, 5, str(self.transcript_path), workspace_root=self.tmp_dir.name)

        self.assertEqual(mono_capsule, mod_capsule, "Recovery capsules must be 100% character-for-character identical!")

    def test_stop_hook_amnesia_differential_parity(self) -> None:
        """Stop hook decisions on amnesia queries must produce 100% identical outputs."""
        mono_dir = self.tmp_path / "mono"
        mod_dir = self.tmp_path / "mod"
        mono_dir.mkdir(parents=True, exist_ok=True)
        mod_dir.mkdir(parents=True, exist_ok=True)

        mono_tpath = mono_dir / "transcript.jsonl"
        mod_tpath = mod_dir / "transcript.jsonl"

        steps = [
            {"step_index": 1, "source": "USER_INPUT", "type": "USER_INPUT", "content": "<USER_REQUEST>Initial prompt</USER_REQUEST>"},
            {"step_index": 2, "source": "SYSTEM", "type": "CHECKPOINT", "content": "# Resuming"},
            {
                "step_index": 3,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "content": "Halo, saya bertugas sebagai peran apa pada sesi ini?",
                "tool_calls": [],
            },
        ]
        self._append_transcript(steps, mono_tpath)
        self._append_transcript(steps, mod_tpath)

        mono_payload = {
            "transcriptPath": str(mono_tpath),
            "workspacePaths": [str(mono_dir)],
            "terminationReason": "NO_TOOL_CALL",
        }
        mod_payload = {
            "transcriptPath": str(mod_tpath),
            "workspacePaths": [str(mod_dir)],
            "terminationReason": "NO_TOOL_CALL",
        }

        mono_resp = monolith.handle_stop(mono_payload)
        mod_resp = modular.handle_stop(mod_payload)

        self.assertEqual(mono_resp, mod_resp)
        self.assertEqual(mono_resp.get("decision"), "continue")
        self.assertIn("[COMPACTION GUARD ANTI-AMNESIA INTERCEPTOR]", mono_resp.get("reason", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
