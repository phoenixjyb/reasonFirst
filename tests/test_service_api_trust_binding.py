"""Selected API trust across managed contexts, controllers and owner lifecycle."""
from __future__ import annotations

from contextlib import chdir
from dataclasses import replace
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.bridge_preview import controller as c
from gitlab_agent.bridge_preview.admission import ControllerAdmission
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_child, service_children, service_managed as service
from gitlab_agent.upgrade import service_trust as trust_module
from gitlab_agent.upgrade.service_configuration import capture_service_configuration
from gitlab_agent.upgrade.startup_state import create_disposable_configuration

import test_service_binding as prior_binding
from tls_fixtures import LocalAuthority


PRIVATE_VALUE = "synthetic-selected-trust-private-marker"
WORKSPACE_ID = "b123456789cd"
TRUST_SUMMARY = {
    "schema_version": 1,
    "scope": "selected-gitlab-api-trust-v1",
    "material_retained": True,
    "selected_bundle_count": 2,
    "current_process_only": True,
    "tls_peer_verified": False,
    "provider_configuration_verified": False,
    "native_git_trust_verified": False,
    "native_ssh_trust_verified": False,
    "activation_authorized": False,
}


def completed_child(result):
    return subprocess.CompletedProcess([], 0, json.dumps({
        "protocol": service_child.RESPONSE_PROTOCOL,
        "exit_code": 0, "result": result, "error": None,
    }).encode("utf-8"), b"")


class TrustContextFixture:
    """All selected trust files and configuration belong to this fixture."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-api-trust-binding-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "trust 空间's private-marker"
        self.root.mkdir()
        self.authority = LocalAuthority(self.root)
        self.ca_bytes = self.authority.ca_path.read_bytes()
        self.public_ca = self.root / "selected-public.pem"
        self.public_ca.write_bytes(self.ca_bytes)
        patcher = patch.object(trust_module.certifi, "where", return_value=str(self.public_ca))
        self.public_selection = patcher.start()
        self.addCleanup(patcher.stop)
        self.fixture = create_disposable_configuration(self.root / "selected")
        self.configuration = self.configuration_for()
        self.cwd = self.root / "selected-cwd"
        self.cwd.mkdir()
        self.context = self.context_for(self.configuration)

    def configuration_for(self, *, api_token=PRIVATE_VALUE, auth_mode="api", ca_path=None):
        snapshot = self.fixture.snapshot
        return capture_service_configuration(
            replace(snapshot.settings, api_token=api_token, git_token=PRIVATE_VALUE,
                    api_ca_bundle=ca_path or self.authority.ca_path),
            snapshot.bridge_configuration(),
            bridge_config_path=snapshot.bridge_config_path,
            state_dir=snapshot.state_dir,
            gitlab_auth_mode=auth_mode,
        )

    def context_for(self, configuration):
        env = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC"}
        }
        env.update(self.fixture.environment)
        with patch.dict(os.environ, env, clear=True), chdir(self.cwd):
            return service_children.capture_service_children(configuration)

    def controller_for(self, *, configuration=None, context=None):
        selected = configuration or self.configuration
        context = context or (self.context if selected is self.configuration else self.context_for(selected))
        controller = c.BridgeController(
            admission=ControllerAdmission(), service_configuration=selected,
            child_context=context,
        )
        self.addCleanup(controller.close)
        root = selected.settings.workspace_root
        worktree = root / "worktrees" / WORKSPACE_ID
        worktree.mkdir(parents=True, exist_ok=True)
        (worktree / "selected.txt").write_text("local selected content\n", encoding="utf-8")
        (root / "state").mkdir(parents=True, exist_ok=True)
        state = {
            "workspace_id": WORKSPACE_ID, "project": "synthetic/project",
            "repo_path": str(root / "repos" / "synthetic.git"),
            "worktree_path": str(worktree), "base_ref": "main", "base_sha": "a" * 40,
            "branch": "chatgpt/selected", "created_at": "2026-01-01T00:00:00Z",
        }
        (root / "state" / (WORKSPACE_ID + ".json")).write_text(json.dumps(state), encoding="utf-8")
        controller._state["workspaces"][WORKSPACE_ID] = {
            **state, "kind": "local", "target": selected.configured_target().to_dict(),
        }
        return controller

    def assert_private_error(self, error):
        for private in (PRIVATE_VALUE, str(self.root), str(self.authority.ca_path), "BEGIN CERTIFICATE"):
            self.assertNotIn(private, str(error))
            self.assertNotIn(private, repr(error))

    def assert_child_error(self, code, operation):
        with self.assertRaises(service_children.ServiceChildBindingError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assert_private_error(raised.exception)
        return raised.exception


class ServiceAPITrustContextTests(TrustContextFixture, unittest.TestCase):
    def test_parent_capture_and_cached_views_do_not_select_api_trust(self):
        self.authority.ca_path.unlink()
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("eager trust capture")):
            context = self.context_for(self.configuration)
            self.assertIsNone(context.api_trust)
            self.assertIsNone(context.revalidate())
            self.assertIs(context.summary()["environment_retained"], True)
            context.environment_copy()
        self.public_selection.assert_not_called()

    def test_parent_selects_once_and_retains_exact_settings_and_object(self):
        selected = self.context.select_api_trust(self.configuration.settings)
        self.assertIs(selected, self.context.api_trust)
        self.assertIs(selected.settings, self.configuration.settings)
        self.assertEqual(selected.summary(), TRUST_SUMMARY)
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("trust recapture")):
            self.assertIs(self.context.select_api_trust(self.configuration.settings), selected)
            options = self.context.api_client_options(self.configuration.settings)
        self.assertIsInstance(options["verify"], ssl.SSLContext)
        self.assertEqual(options["verify"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(options["verify"].check_hostname)

    def test_actual_parent_api_use_can_select_trust_lazily(self):
        self.assertIsNone(self.context.api_trust)
        options = self.context.api_client_options(self.configuration.settings, asynchronous=True)
        self.assertIs(self.context.api_trust.settings, self.configuration.settings)
        self.assertIsInstance(options["verify"], ssl.SSLContext)
        self.assertFalse(options["follow_redirects"])

    def test_equal_settings_cannot_authorize_another_selection(self):
        copied_settings = replace(self.configuration.settings)
        self.assertEqual(copied_settings, self.configuration.settings)
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("foreign settings captured")):
            with self.assertRaises(service_children.ServiceChildBindingError) as raised:
                self.context.select_api_trust(copied_settings)
        self.assert_private_error(raised.exception)

    def test_selected_client_options_reject_equal_foreign_settings_before_fallback(self):
        self.context.select_api_trust(self.configuration.settings)
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("ordinary fallback")):
            with self.assertRaises(service_children.ServiceChildBindingError) as raised:
                self.context.api_client_options(replace(self.configuration.settings))
        self.assert_private_error(raised.exception)

    def test_first_capture_failure_keeps_local_inputs_usable_and_allows_later_selection(self):
        self.authority.ca_path.unlink()
        with self.assertRaises((trust_module.ServiceAPITrustError, service_children.ServiceChildBindingError)) as raised:
            self.context.select_api_trust(self.configuration.settings)
        self.assert_private_error(raised.exception)
        self.assertIsNone(self.context.api_trust)
        self.assertIsNone(self.context.revalidate())
        self.assertTrue(self.context.summary()["environment_retained"])
        self.authority.ca_path.write_bytes(self.ca_bytes)
        selected = self.context.select_api_trust(self.configuration.settings)
        self.assertIs(selected, self.context.api_trust)

    def test_selected_bundle_drift_is_sticky_after_file_restoration(self):
        self.context.select_api_trust(self.configuration.settings)
        self.authority.ca_path.write_bytes(b"changed synthetic trust material")
        self.assert_child_error("child_trust_failed", self.context.revalidate)
        self.authority.ca_path.write_bytes(self.ca_bytes)
        for operation in (
            self.context.revalidate, self.context.summary,
            lambda: self.context.select_api_trust(self.configuration.settings),
            lambda: self.context.api_client_options(self.configuration.settings),
        ):
            self.assert_child_error("child_trust_failed", operation)

    def test_public_root_drift_invalidates_the_same_owned_context(self):
        self.context.select_api_trust(self.configuration.settings)
        self.public_ca.write_bytes(b"changed public trust material")
        self.assert_child_error("child_trust_failed", self.context.revalidate)

    def test_equal_summary_foreign_trust_object_cannot_replace_the_selected_object(self):
        selected = self.context.select_api_trust(self.configuration.settings)
        foreign = trust_module.capture_api_trust(self.configuration.settings)
        self.assertIsNot(foreign, selected)
        self.assertEqual(foreign.summary(), selected.summary())
        object.__setattr__(self.context, "_api_trust", foreign)
        self.assert_child_error("child_trust_failed", self.context.revalidate)
        object.__setattr__(self.context, "_api_trust", selected)
        self.assert_child_error("child_trust_failed", self.context.revalidate)

    def test_absent_helper_material_never_recaptures_a_valid_local_ca_path(self):
        helper = service_children.capture_helper_children(self.configuration.settings, api_trust=None)
        self.assertIsNone(helper.api_trust)
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("helper recaptured trust")):
            with self.assertRaises(service_children.ServiceChildBindingError) as raised:
                helper.api_client_options(self.configuration.settings)
        self.assert_private_error(raised.exception)

    def test_helper_reuses_transferred_material_bound_to_exact_decoded_settings(self):
        selected = self.context.select_api_trust(self.configuration.settings)
        helper_settings = replace(self.configuration.settings)
        imported = trust_module.decode_api_trust(helper_settings, trust_module.encode_api_trust(selected))
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("helper recaptured trust")):
            helper = service_children.capture_helper_children(helper_settings, api_trust=imported)
            self.assertIs(helper.api_trust, imported)
            self.assertIs(helper.api_trust.settings, helper_settings)
            self.assertIsInstance(helper.api_client_options(helper_settings)["verify"], ssl.SSLContext)
        self.assertIsNot(helper.api_trust, selected)

    def test_helper_rejects_trust_bound_to_equal_but_different_settings(self):
        selected = self.context.select_api_trust(self.configuration.settings)
        with self.assertRaises(service_children.ServiceChildBindingError) as raised:
            service_children.capture_helper_children(replace(self.configuration.settings), api_trust=selected)
        self.assert_private_error(raised.exception)

    def test_wrong_process_is_rejected_before_inherited_context_lock(self):
        self.context.select_api_trust(self.configuration.settings)

        class ForbiddenLock:
            def __enter__(self):
                raise AssertionError("wrong process entered inherited lock")

            def __exit__(self, *args):
                return False

        original_lock = self.context._lock
        object.__setattr__(self.context, "_lock", ForbiddenLock())
        try:
            with patch.object(service_children.os, "getpid", return_value=self.context._pid + 1):
                for operation in (
                    lambda: self.context.api_trust,
                    lambda: self.context.select_api_trust(self.configuration.settings),
                    lambda: self.context.api_client_options(self.configuration.settings),
                ):
                    self.assert_child_error("child_wrong_process", operation)
        finally:
            object.__setattr__(self.context, "_lock", original_lock)
        self.assert_child_error("child_wrong_process", self.context.summary)

    def test_cached_trust_summary_is_detached_private_and_does_not_reread_files(self):
        selected = self.context.select_api_trust(self.configuration.settings)
        with patch.object(selected.__class__, "revalidate", side_effect=AssertionError("summary revalidated")), \
                patch.object(Path, "open", side_effect=AssertionError("summary opened a path")), \
                patch.object(os, "open", side_effect=AssertionError("summary opened a descriptor")):
            summary = self.context.api_trust.summary()
            summary["material_retained"] = False
            self.assertEqual(self.context.api_trust.summary(), TRUST_SUMMARY)
        rendered = repr(selected) + json.dumps(selected.summary())
        for private in (PRIVATE_VALUE, str(self.root), "BEGIN CERTIFICATE", "ephemeral test CA"):
            self.assertNotIn(private, rendered)


class ControllerAPITrustBindingTests(TrustContextFixture, unittest.TestCase):
    def test_local_helper_does_not_require_or_select_api_trust(self):
        controller = self.controller_for()
        self.authority.ca_path.unlink()
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("local work selected trust")), \
                patch.object(c.subprocess, "run", return_value=completed_child({"content": "local content"})):
            self.assertEqual(controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")["content"],
                             "1: local content")
        self.assertIsNone(controller.child_context.api_trust)
        self.assertTrue(controller._admission.snapshot()["admission_open"])

    def test_local_evidence_resume_finish_and_offline_routes_leave_trust_unselected(self):
        controller = self.controller_for()
        self.authority.ca_path.unlink()
        arguments = (
            ["evidence", WORKSPACE_ID],
            ["resume", WORKSPACE_ID],
            ["finish", WORKSPACE_ID, "--dry-run"],
            ["doctor", "--offl"],
            ["doctor", "--git-only"],
            ["start", "synthetic/project", "--goal", "synthetic goal", "--no-launch", "--offline-doctor"],
            ["start", "synthetic/project", "--goal", "synthetic goal", "--no-launch", "--git-only"],
        )
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("local route selected trust")), \
                patch.object(c.subprocess, "run", return_value=completed_child({"ok": True})) as runner:
            for argv in arguments:
                with self.subTest(argv=argv):
                    controller._run_json(controller._module_command("gitlab_agent.actual_coder_cli", *argv))
                    self.assertIsNone(controller.child_context.api_trust)
        self.assertEqual(runner.call_count, len(arguments))

    def test_api_helper_receives_parent_selection_before_process_launch(self):
        controller = self.controller_for()
        selected_objects = []

        def run(argv, **kwargs):
            selected = controller.child_context.api_trust
            self.assertIsNotNone(selected)
            self.assertIs(selected.settings, controller.service_configuration.settings)
            selected_objects.append(selected)
            self.assertIsInstance(kwargs["input"], bytes)
            self.assertNotIn(PRIVATE_VALUE, repr(argv))
            self.assertNotIn("BEGIN CERTIFICATE", repr(argv))
            self.assertNotIn("BEGIN CERTIFICATE", repr(kwargs["env"]))
            return completed_child({"ok": True})

        with patch.object(c.subprocess, "run", side_effect=run):
            controller.project_preflight("synthetic/project", ref="main")
            controller.project_preflight("synthetic/project", ref="main")
        self.assertEqual(len(selected_objects), 2)
        self.assertIs(selected_objects[0], selected_objects[1])

    def test_first_api_capture_failure_blocks_no_local_work_and_can_be_retried(self):
        controller = self.controller_for()
        self.authority.ca_path.unlink()
        with patch.object(c.subprocess, "run", side_effect=AssertionError("invalid trust launched a helper")) as runner:
            with self.assertRaises(c.BridgeError) as raised:
                controller.project_preflight("synthetic/project", ref="main")
        self.assert_private_error(raised.exception)
        runner.assert_not_called()
        self.assertIsNone(controller.child_context.api_trust)
        self.assertTrue(controller._admission.snapshot()["admission_open"])
        with patch.object(c.subprocess, "run", return_value=completed_child({"content": "still available"})):
            self.assertEqual(controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")["content"],
                             "1: still available")
        self.authority.ca_path.write_bytes(self.ca_bytes)
        with patch.object(c.subprocess, "run", return_value=completed_child({"ok": True})):
            controller.project_preflight("synthetic/project", ref="main")
        self.assertIsNotNone(controller.child_context.api_trust)

    def test_api_work_without_a_token_does_not_select_optional_trust(self):
        configuration = self.configuration_for(api_token="", auth_mode="api")
        controller = self.controller_for(configuration=configuration)
        self.authority.ca_path.unlink()
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("missing token selected trust")), \
                patch.object(c.subprocess, "run", return_value=completed_child({"ok": False})):
            with self.assertRaises(c.BridgeError):
                controller.project_preflight("synthetic/project", ref="main")
        self.assertIsNone(controller.child_context.api_trust)
        self.assertTrue(controller._admission.snapshot()["admission_open"])

    def test_selected_trust_drift_blocks_local_helper_before_launch_and_stays_closed(self):
        controller = self.controller_for()
        controller.child_context.select_api_trust(self.configuration.settings)
        self.authority.ca_path.write_bytes(b"changed selected API trust")
        with patch.object(c.subprocess, "run", side_effect=AssertionError("helper launched after drift")) as runner:
            with self.assertRaisesRegex(c.BridgeError, "^child_binding_failed$"):
                controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
            self.authority.ca_path.write_bytes(self.ca_bytes)
            with self.assertRaises(c.BridgeError):
                controller.read(workspace_id=WORKSPACE_ID, path="selected.txt")
        runner.assert_not_called()
        self.assertFalse(controller._admission.snapshot()["admission_open"])


class ManagedAPITrustBindingTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real owner/controller with only external listener fakes."""

    patch = prior_binding.ManagedServiceBindingTests.patch
    patch_dict = prior_binding.ManagedServiceBindingTests.patch_dict
    cleanup_owners = prior_binding.ManagedServiceBindingTests.cleanup_owners
    assert_code = prior_binding.ManagedServiceBindingTests.assert_code
    assert_async_code = prior_binding.ManagedServiceBindingTests.assert_async_code
    assert_unverified = prior_binding.ManagedServiceBindingTests.assert_unverified

    async def asyncSetUp(self):
        await prior_binding.ManagedServiceBindingTests.asyncSetUp(self)
        self.trust_root = self.configuration.state_dir.parent / "trust inputs 空间"
        self.trust_root.mkdir()
        self.authority = LocalAuthority(self.trust_root)
        self.ca_bytes = self.authority.ca_path.read_bytes()
        self.public_ca = self.trust_root / "public.pem"
        self.public_ca.write_bytes(self.ca_bytes)
        self.patch(trust_module.certifi, "where", return_value=str(self.public_ca))
        self.configuration = capture_service_configuration(
            replace(self.configuration.settings, api_ca_bundle=self.authority.ca_path),
            self.configuration.bridge_configuration(),
            bridge_config_path=self.configuration.bridge_config_path,
            state_dir=self.configuration.state_dir,
        )
        self.owner = service.ManagedBridgeService(self.launch, configuration=self.configuration)
        self.owners = [self.owner]

    async def selected_owner(self):
        await self.owner.start()
        selected = self.owner._child_context.select_api_trust(self.configuration.settings)
        snapshot = self.owner.maintenance_snapshot()
        self.assertTrue(snapshot["api_trust_bound"])
        self.assertEqual(snapshot["api_trust_observation"], TRUST_SUMMARY)
        return selected

    def foreign_context(self, *, select_trust):
        configuration = capture_service_configuration(
            replace(self.configuration.settings, api_ca_bundle=None),
            self.configuration.bridge_configuration(),
            bridge_config_path=self.configuration.bridge_config_path,
            state_dir=self.configuration.state_dir,
        )
        context = service_children.capture_service_children(configuration)
        if select_trust:
            context.select_api_trust(configuration.settings)
            self.assertEqual(context.api_trust.summary()["selected_bundle_count"], 1)
        return context

    def assert_replacement_keeps_original_history(self, foreign):
        original = self.owner._child_context
        self.owner._child_context = foreign
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["api_trust_bound"])
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertEqual(snapshot["api_trust_observation"], TRUST_SUMMARY)
        self.assertEqual(snapshot["error_code"], "child_binding_failed")
        self.owner._child_context = original
        restored = self.owner.maintenance_snapshot()
        self.assertFalse(restored["api_trust_bound"])
        self.assertEqual(restored["api_trust_observation"], TRUST_SUMMARY)

    async def test_startup_and_maintenance_keep_trust_unselected_until_api_use(self):
        self.authority.ca_path.unlink()
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("eager owner trust selection")):
            await self.owner.start()
            lease = self.owner.try_enter_maintenance()
            self.owner.leave_maintenance(lease)
            snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["api_trust_bound"])
        self.assertIsNone(snapshot["api_trust_observation"])
        self.assertTrue(snapshot["child_inputs_bound"])
        self.assert_unverified(snapshot)

    async def test_selected_owner_retains_exact_context_settings_and_private_summary(self):
        selected = await self.selected_owner()
        context = self.owner._child_context
        self.assertIs(context.configuration, self.configuration)
        self.assertIs(self.owner._controller.child_context, context)
        self.assertIs(self.owner._core._reasonfirst_child_context, context)
        self.assertIs(selected.settings, self.configuration.settings)
        snapshot = self.owner.maintenance_snapshot()
        self.assert_unverified(snapshot)
        rendered = json.dumps(snapshot)
        for private in (str(self.trust_root), str(self.authority.ca_path), prior_binding.PRIVATE_SENTINEL,
                        "BEGIN CERTIFICATE", "ephemeral test CA"):
            self.assertNotIn(private, rendered)
        self.assertFalse(snapshot["child_observation"]["provider_configuration_verified"])

    async def test_snapshot_is_detached_and_performs_no_trust_or_runtime_rereads(self):
        selected = await self.selected_owner()
        with patch.object(service_children.ServiceChildContext, "revalidate", side_effect=AssertionError("child reread")), \
                patch.object(type(selected), "revalidate", side_effect=AssertionError("trust reread")), \
                patch.object(os, "open", side_effect=AssertionError("snapshot opened file")), \
                patch.object(Path, "open", side_effect=AssertionError("snapshot opened path")):
            snapshot = self.owner.maintenance_snapshot()
            snapshot["api_trust_observation"]["material_retained"] = False
            self.assertEqual(self.owner.maintenance_snapshot()["api_trust_observation"], TRUST_SUMMARY)

    async def test_initial_api_capture_failure_does_not_disable_owner_maintenance(self):
        await self.owner.start()
        self.authority.ca_path.unlink()
        with self.assertRaises((trust_module.ServiceAPITrustError, service_children.ServiceChildBindingError)):
            self.owner._child_context.select_api_trust(self.configuration.settings)
        lease = self.owner.try_enter_maintenance()
        self.owner.leave_maintenance(lease)
        snapshot = self.owner.maintenance_snapshot()
        self.assertEqual(snapshot["lifecycle"], "running")
        self.assertTrue(snapshot["admission"]["admission_open"])
        self.assertFalse(snapshot["api_trust_bound"])
        self.assertIsNone(snapshot["api_trust_observation"])

    async def test_trust_drift_blocks_maintenance_and_retains_historical_evidence(self):
        await self.selected_owner()
        self.authority.ca_path.write_bytes(b"changed selected trust")
        self.assert_code("child_binding_failed", self.owner.try_enter_maintenance)
        self.authority.ca_path.write_bytes(self.ca_bytes)
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["api_trust_bound"])
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertEqual(snapshot["api_trust_observation"], TRUST_SUMMARY)
        self.assert_unverified(snapshot)

    async def test_trust_drift_cannot_release_an_existing_maintenance_lease(self):
        await self.selected_owner()
        lease = self.owner.try_enter_maintenance()
        self.public_ca.write_bytes(b"changed selected public roots")
        self.assert_code("child_binding_failed", self.owner.leave_maintenance, lease)
        self.public_ca.write_bytes(self.ca_bytes)
        with self.assertRaises(service.ServiceError):
            self.owner.leave_maintenance(lease)
        self.assertFalse(self.owner.maintenance_snapshot()["api_trust_bound"])

    async def test_foreign_trust_replacement_does_not_authorize_the_same_owner(self):
        selected = await self.selected_owner()
        foreign = trust_module.capture_api_trust(self.configuration.settings)
        object.__setattr__(self.owner._child_context, "_api_trust", foreign)
        self.assert_code("child_binding_failed", self.owner.try_enter_maintenance)
        object.__setattr__(self.owner._child_context, "_api_trust", selected)
        with self.assertRaises(service.ServiceError):
            self.owner.try_enter_maintenance()
        self.assertFalse(self.owner.maintenance_snapshot()["api_trust_bound"])

    async def test_unselected_foreign_context_cannot_erase_owner_trust_history(self):
        await self.selected_owner()
        self.assert_replacement_keeps_original_history(self.foreign_context(select_trust=False))

    async def test_selected_foreign_context_cannot_replace_owner_trust_history(self):
        await self.selected_owner()
        self.assert_replacement_keeps_original_history(self.foreign_context(select_trust=True))

    async def test_context_replacement_before_first_selected_snapshot_keeps_original_history(self):
        await self.owner.start()
        self.owner._child_context.select_api_trust(self.configuration.settings)
        self.assert_replacement_keeps_original_history(self.foreign_context(select_trust=True))

    async def test_closing_owner_clears_live_trust_flag_and_preserves_cached_summary(self):
        selected = await self.selected_owner()
        await self.owner.aclose()
        with patch.object(type(selected), "revalidate", side_effect=AssertionError("closed owner reread")):
            snapshot = self.owner.maintenance_snapshot()
        for name in ("resolved_policy_bound", "current_process_bound", "child_inputs_bound", "api_trust_bound"):
            self.assertFalse(snapshot[name])
        self.assertEqual(snapshot["api_trust_observation"], TRUST_SUMMARY)
        self.assert_unverified(snapshot)

    async def test_close_before_first_selected_snapshot_keeps_historical_trust(self):
        await self.owner.start()
        self.owner._child_context.select_api_trust(self.configuration.settings)
        await self.owner.aclose()
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["api_trust_bound"])
        self.assertEqual(snapshot["api_trust_observation"], TRUST_SUMMARY)

    async def test_failure_before_first_selected_snapshot_keeps_historical_trust(self):
        await self.owner.start()
        self.owner._child_context.select_api_trust(self.configuration.settings)
        self.authority.ca_path.write_bytes(b"drift before first selected snapshot")
        with self.assertRaises(service_children.ServiceChildBindingError):
            self.owner._child_context.revalidate()
        snapshot = self.owner.maintenance_snapshot()
        self.assertFalse(snapshot["api_trust_bound"])
        self.assertFalse(snapshot["admission"]["admission_open"])
        self.assertEqual(snapshot["api_trust_observation"], TRUST_SUMMARY)

    async def test_ordinary_owner_does_not_adopt_trust_material(self):
        with patch.object(AgentSettings, "load", return_value=self.configuration.settings), \
                patch.object(c, "load_bridge_config", return_value=self.configuration.bridge_configuration()), \
                patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("ordinary owner captured trust")):
            owner = service.ManagedBridgeService(self.launch)
            self.owners.append(owner)
            await owner.start()
        snapshot = owner.maintenance_snapshot()
        self.assertFalse(snapshot["api_trust_bound"])
        self.assertIsNone(snapshot["api_trust_observation"])


if __name__ == "__main__":
    unittest.main()
