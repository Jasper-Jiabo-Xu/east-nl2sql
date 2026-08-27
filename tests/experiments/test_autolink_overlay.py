from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from east_v5.experiments.autolink_overlay import (
    AUTOLINK_FILES,
    AUTOLINK_STAGES,
    HarnessError,
    apply_hashed_provider_overlay,
    child_provider_environment,
    validate_captured_request_bodies,
    validate_model_routes,
)


def routes() -> list[dict[str, object]]:
    return [
        {"stage": "complete_schema", "model": "deepseek-v4-flash", "thinking": False, "reasoning_effort": None, "temperature": 0, "max_tokens": 256, "timeout_seconds": 10, "retry_count": 3, "call_budget": 1, "token_budget": 256},
        *[{"stage": stage, "model": "deepseek-v4-flash", "thinking": True, "reasoning_effort": "high", "temperature": None, "max_tokens": 512, "timeout_seconds": 20, "retry_count": 3, "call_budget": 1, "token_budget": 512} for stage in AUTOLINK_STAGES[1:]],
    ]


class AutoLinkOverlayTest(unittest.TestCase):
    def _source_and_overlay(self, root: Path) -> tuple[Path, Path]:
        source = root / "source"
        source.mkdir()
        items = []
        for stage in AUTOLINK_STAGES:
            path = source / AUTOLINK_FILES[stage]
            path.parent.mkdir(parents=True, exist_ok=True)
            model = "deepseek-chat" if stage == "complete_schema" else "deepseek-reasoner"
            path.write_text(f'client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=os.environ.get("OPENAI_BASE_URL"))\nresponse = client.chat.completions.create(\n                    model="{model}",\n                    messages=messages,\n                )\n', encoding="utf-8")
            items.append({"stage": stage, "path": AUTOLINK_FILES[stage], "original_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source_model": model, "route": "non_thinking" if stage == "complete_schema" else "thinking"})
        overlay = root / "overlay.json"
        overlay.write_text(json.dumps({"overlay_version": "eas125-autolink-provider-v4-v1", "upstream_commit": "0" * 40, "routes": items}), encoding="utf-8")
        return source, overlay

    def test_overlay_is_copy_only_and_captures_all_four_v4_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, overlay = self._source_and_overlay(root)
            result = apply_hashed_provider_overlay(source, root / "copy", overlay, routes())
            self.assertEqual(set(result), {"overlay_sha256", "patched_tree_sha256"})
            for stage in AUTOLINK_STAGES:
                original = (source / AUTOLINK_FILES[stage]).read_text()
                patched = (root / "copy" / AUTOLINK_FILES[stage]).read_text()
                self.assertIn("deepseek-reasoner" if stage != "complete_schema" else "deepseek-chat", original)
                self.assertIn('model="deepseek-v4-flash"', patched)
                self.assertIn("max_tokens=", patched)
                self.assertIn('"thinking": {"type": "enabled"}', patched) if stage != "complete_schema" else self.assertIn("temperature=0", patched)

    def test_drift_and_unlisted_routes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, overlay = self._source_and_overlay(root)
            (source / AUTOLINK_FILES["sql_revise"]).write_text("drift", encoding="utf-8")
            with self.assertRaises(HarnessError) as raised:
                apply_hashed_provider_overlay(source, root / "copy", overlay, routes())
            self.assertEqual(raised.exception.code, "AUTOLINK_OVERLAY_SOURCE_DRIFT")
        bad = routes(); bad.pop()
        with self.assertRaises(HarnessError) as raised:
            validate_model_routes(bad)
        self.assertEqual(raised.exception.code, "AUTOLINK_MODEL_ROUTE_DRIFT")

    def test_child_environment_maps_only_runtime_secret(self) -> None:
        env = child_provider_environment({"DEEPSEEK_API_KEY": "not-written", "KEEP": "1"})
        self.assertEqual((env["OPENAI_API_KEY"], env["OPENAI_BASE_URL"], env["KEEP"]), ("not-written", "https://api.deepseek.com", "1"))
        with self.assertRaises(HarnessError) as raised:
            child_provider_environment({})
        self.assertEqual(raised.exception.code, "DEEPSEEK_AUTH_MISSING")

    def test_fake_endpoint_capture_rejects_missing_or_drifting_stage_body(self) -> None:
        captured = [
            {"stage": route["stage"], "model": route["model"], "max_tokens": route["max_tokens"], "timeout": route["timeout_seconds"], "thinking": route["thinking"], "reasoning_effort": route["reasoning_effort"], "temperature": route["temperature"]}
            for route in routes()
        ]
        validate_captured_request_bodies(captured, routes())
        captured[-1]["model"] = "drift"
        with self.assertRaises(HarnessError) as raised:
            validate_captured_request_bodies(captured, routes())
        self.assertEqual(raised.exception.code, "AUTOLINK_REQUEST_CAPTURE_DRIFT")
