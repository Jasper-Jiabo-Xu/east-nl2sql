"""Production agent entrypoints must traverse the repo-side Foundation gate."""
from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


generator = importlib.import_module("east_v5.agents.241.generator")
validator = importlib.import_module("east_v5.agents.242.validator")
regression = importlib.import_module("east_v5.agents.260.regression")


class FoundationAgentEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bootstrap = Mock()
        self.bootstrap.foundation_repo_launcher.return_value.launch.return_value = {"schema_version": "foundation-repo-launch-receipt/v1"}
        self.bootstrap.foundation_242_launcher.return_value.verify_downstream.return_value = {"status": "accepted", "task_identity": {"agent_uuid": "242"}}
        self.bootstrap.foundation_260_launcher.return_value.verify_downstream.return_value = {"status": "accepted", "task_identity": {"agent_uuid": "260"}}
        self.assembly = Mock()
        self.assembly.generator.return_value.build_bound_data.return_value = {"bound": "ok"}
        self.assembly.validator.return_value.freeze_bound_data.return_value = {"verified": "ok"}

    def test_241_launches_before_real_generator_call(self) -> None:
        output, launch = generator.build_foundation_bound_data_from_runtime(self.bootstrap, self.assembly, {"closure": "sealed"})
        self.assertEqual((output, launch["schema_version"]), ({"bound": "ok"}, "foundation-repo-launch-receipt/v1"))
        self.bootstrap.foundation_repo_launcher.return_value.launch.assert_called_once_with()
        self.assembly.generator.return_value.build_bound_data.assert_called_once()

    def test_242_requires_241_receipt_before_validator_call(self) -> None:
        frozen, accepted = validator.freeze_foundation_bound_data_from_runtime(self.bootstrap, self.assembly, {"launch": "241"}, {"bound": "data"}, {"closure": "sealed"}, Mock())
        self.assertEqual((frozen, accepted["status"]), ({"verified": "ok"}, "accepted"))
        self.bootstrap.foundation_242_launcher.return_value.verify_downstream.assert_called_once_with({"launch": "241"})
        self.assembly.validator.return_value.freeze_bound_data.assert_called_once()

    def test_260_requires_242_acceptance_before_copy_gate(self) -> None:
        with patch.object(regression, "run_foundation_regression", return_value={"report": "ok"}) as run:
            report, accepted = regression.run_foundation_regression_from_runtime(self.bootstrap, {"accepted": "242"}, Path("repo"), {}, {}, {}, {}, Mock(), Mock(), set())
        self.assertEqual((report, accepted["status"]), ({"report": "ok"}, "accepted"))
        self.bootstrap.foundation_260_launcher.return_value.verify_downstream.assert_called_once_with({"accepted": "242"})
        run.assert_called_once()

    def test_no_envelope_241_entrypoint_derives_bootstrap_then_issues_its_own_identity(self) -> None:
        with patch("east_v5.runtime.bootstrap.RuntimeBootstrap.from_current_foundation_task", return_value=self.bootstrap) as factory:
            output, launch = generator.build_foundation_bound_data_from_current_task(self.assembly, {"closure": "sealed"})
        self.assertEqual((output, launch["schema_version"]), ({"bound": "ok"}, "foundation-repo-launch-receipt/v1"))
        factory.assert_called_once_with()
        self.bootstrap.foundation_task_identity_issuer.return_value.issue.assert_called_once_with()

    def test_no_envelope_242_entrypoint_derives_bootstrap_then_issues_its_own_identity(self) -> None:
        with patch("east_v5.runtime.bootstrap.RuntimeBootstrap.from_current_foundation_task", return_value=self.bootstrap) as factory:
            frozen, accepted = validator.freeze_foundation_bound_data_from_current_task(self.assembly, {"launch": "241"}, {"bound": "data"}, {"closure": "sealed"}, Mock())
        self.assertEqual((frozen, accepted["status"]), ({"verified": "ok"}, "accepted"))
        factory.assert_called_once_with()
        self.bootstrap.foundation_task_identity_issuer.return_value.issue.assert_called_once_with()

    def test_no_envelope_260_entrypoint_derives_bootstrap_then_issues_its_own_identity(self) -> None:
        with patch("east_v5.runtime.bootstrap.RuntimeBootstrap.from_current_foundation_task", return_value=self.bootstrap) as factory, patch.object(regression, "run_foundation_regression", return_value={"report": "ok"}):
            report, accepted = regression.run_foundation_regression_from_current_task({"accepted": "242"}, {}, {}, {}, {}, Mock(), Mock(), set())
        self.assertEqual((report, accepted["status"]), ({"report": "ok"}, "accepted"))
        factory.assert_called_once_with()
        self.bootstrap.foundation_task_identity_issuer.return_value.issue.assert_called_once_with()
        with self.assertRaises(TypeError):
            regression.run_foundation_regression_from_current_task({"accepted": "242"}, {}, {}, {}, {}, Mock(), Mock(), set(), repo_root=Path("injected-repo"))  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
