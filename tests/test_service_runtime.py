from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import importlib.machinery
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import service_runtime as r


class ServiceRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-runtime-synthetic-secret-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.prefix, self.base_prefix = self.root / "venv", self.root / "base"
        self.invoked = self.prefix / "bin" / "python"
        self.base_invoked = self.base_prefix / "bin" / "python"
        self.config = self.prefix / "pyvenv.cfg"
        for executable in (self.invoked, self.base_invoked):
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"synthetic-python-binary")
        self.config.write_bytes(b"synthetic-venv-config")
        self.names = ("gitlab_agent", "gitlab_agent.upgrade.service_runtime", "uvicorn.server")
        paths = (self.root / "source" / "gitlab_agent" / "__init__.py",
                 self.root / "source" / "gitlab_agent" / "upgrade" / "service_runtime.py",
                 self.prefix / "site-packages" / "uvicorn" / "server.py")
        self.modules = {}
        for name, path in zip(self.names, paths):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# synthetic module\n")
            module = ModuleType(name)
            module.__file__ = str(path)
            module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, origin=str(path))
            self.modules[name] = module

    @contextmanager
    def selected(self):
        # All observed files are synthetic and owned by this test. Concurrent
        # edits to the checkout never enter a runtime observation in this suite.
        with patch.multiple(sys, executable=str(self.invoked), _base_executable=str(self.base_invoked),
                            prefix=str(self.prefix), base_prefix=str(self.base_prefix)), \
                patch.object(r, "_MODULE_NAMES", self.names), \
                patch.dict(sys.modules, self.modules), \
                patch.object(r, "_sdk_versions", return_value=r._SDK_VERSIONS):
            yield

    def assert_code(self, code, action):
        with self.assertRaises(r.ServiceRuntimeError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn("synthetic-secret", str(raised.exception))
        self.assertNotIn(str(self.root), repr(raised.exception))
        return raised.exception

    def test_capture_and_revalidate_selected_source_and_installed_modules(self):
        with self.selected():
            observed = r.capture_service_runtime()
            self.assertIsNone(observed.revalidate())
            self.assertEqual(observed.digest, r.capture_service_runtime().digest)
            self.assertEqual(observed._interpreter.invoked, self.invoked)
            self.assertEqual(observed._interpreter.base_invoked, self.base_invoked)
            self.assertFalse(observed._modules[0].file.path.is_relative_to(self.prefix))
            self.assertTrue(observed._modules[-1].file.path.is_relative_to(self.prefix))

    def test_packaged_reasonfirst_root_under_current_prefix_is_supported(self):
        for name in self.names[:2]:
            module = self.modules[name]
            previous = Path(module.__file__)
            selected = self.prefix / "site-packages" / previous.relative_to(self.root / "source")
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_bytes(previous.read_bytes())
            module.__file__ = module.__spec__.origin = str(selected)
        with self.selected():
            observed = r.capture_service_runtime()
            self.assertIsNone(observed.revalidate())
            self.assertTrue(all(item.file.path.is_relative_to(self.prefix) for item in observed._modules))

    def test_reasonfirst_modules_cannot_mix_package_roots(self):
        module = self.modules[self.names[1]]
        external = self.root / "other-package" / "service_runtime.py"
        external.parent.mkdir()
        external.write_bytes(Path(module.__file__).read_bytes())
        module.__file__ = module.__spec__.origin = str(external)
        with self.selected():
            self.assert_code("runtime_module_changed", r.capture_service_runtime)

    def test_sdk_origins_outside_current_prefix_are_rejected(self):
        module = self.modules[self.names[-1]]
        external = self.root / "system-site-packages" / "server.py"
        external.parent.mkdir()
        external.write_bytes(Path(module.__file__).read_bytes())
        module.__file__ = module.__spec__.origin = str(external)
        with self.selected():
            self.assert_code("runtime_module_changed", r.capture_service_runtime)

    def test_observation_is_frozen_and_public_evidence_is_detached_and_private(self):
        with self.selected():
            observed = r.capture_service_runtime()
            with self.assertRaises(FrozenInstanceError):
                observed._digest = "changed"
            public = observed.summary()
            self.assertEqual(public["scope"], r.SCOPE)
            self.assertEqual(public["selected_module_count"], 3)
            self.assertRegex(public["digest"], r"\A[0-9a-f]{64}\Z")
            for key in ("runtime_identity_verified", "running_code_verified", "activation_authorized"):
                self.assertIs(public[key], False)
            for key in ("interpreter_files_observed", "selected_module_origins_observed",
                        "selected_module_files_observed", "current_process_only"):
                self.assertIs(public[key], True)
            public["digest"] = "changed"
            public["runtime_identity_verified"] = True
            self.assertNotEqual(observed.summary()["digest"], "changed")
            self.assertIs(observed.summary()["runtime_identity_verified"], False)
            rendered = repr(observed) + json.dumps(observed.summary())
            self.assertNotIn(str(self.root), rendered)
            self.assertNotIn("synthetic-secret", rendered)
            self.assertNotIn("invoked", rendered)

    def test_summary_is_observed_evidence_without_an_implicit_filesystem_probe(self):
        with self.selected():
            observed = r.capture_service_runtime()
            with patch.object(r, "_observe_interpreter", side_effect=AssertionError), \
                    patch.object(r, "_observe_file", side_effect=AssertionError):
                self.assertEqual(observed.digest, observed.summary()["digest"])

    def test_wrong_pid_precedes_interpreter_modules_or_filesystem_access(self):
        with self.selected():
            observed = r.capture_service_runtime()
            with patch.object(r.os, "getpid", return_value=observed._pid + 1), \
                    patch.object(r, "_observe_interpreter", side_effect=AssertionError), \
                    patch.object(r, "_module_origin", side_effect=AssertionError), \
                    patch.object(r, "_sdk_versions", side_effect=AssertionError):
                for action in (observed.revalidate, observed.summary, lambda: observed.digest):
                    self.assert_code("runtime_wrong_process", action)

    def test_other_venv_invocation_is_distinct_even_when_all_file_fingerprints_match(self):
        other = self.root / "other-venv" / "bin" / "python"
        other.parent.mkdir(parents=True)
        with self.selected():
            shared_file = r._observe_file(self.base_invoked, r.MAX_EXECUTABLE_BYTES)
            with patch.object(r, "_observe_file", return_value=shared_file), \
                    patch.object(r, "_observe_modules", return_value=()):
                observed = r.capture_service_runtime()
                with patch.object(sys, "executable", str(other)):
                    after = r._observe_interpreter()
                    self.assertEqual(after.files, observed._interpreter.files)
                    self.assertNotEqual(after.invoked, observed._interpreter.invoked)
                    self.assert_code("runtime_identity_changed", observed.revalidate)

    def test_executable_invocation_leaf_is_preserved(self):
        with self.selected():
            observed = r.capture_service_runtime()
            original = r._observe_file
            alternate = self.invoked.with_name("python3")
            with patch.object(sys, "executable", str(alternate)), \
                    patch.object(r, "_observe_file", side_effect=lambda path, limit:
                                 original(self.invoked if path == alternate else path, limit)):
                self.assert_code("runtime_identity_changed", observed.revalidate)

    def test_python_version_and_prefix_changes_are_not_recaptured(self):
        with self.selected():
            observed = r.capture_service_runtime()
            with patch.object(sys, "version_info", (9, 8, 7)):
                self.assert_code("runtime_identity_changed", observed.revalidate)
            alternate = self.root / "different-base"
            alternate.mkdir()
            with patch.object(sys, "base_prefix", str(alternate)):
                self.assert_code("runtime_identity_changed", observed.revalidate)
            self.assertIsNone(observed.revalidate())

    def test_interpreter_and_venv_configuration_drift_are_rejected(self):
        for selected in (self.invoked, self.base_invoked, self.config):
            with self.subTest(file=selected.name), self.selected():
                observed = r.capture_service_runtime()
                selected.write_bytes(selected.read_bytes() + b"changed")
                self.assert_code("runtime_identity_changed", observed.revalidate)

    def test_missing_venv_configuration_does_not_fall_back_to_base_python(self):
        with self.selected():
            self.config.unlink()
            self.assert_code("runtime_file_unavailable", r.capture_service_runtime)

    def test_module_object_replacement_at_the_same_path_is_rejected(self):
        with self.selected():
            observed = r.capture_service_runtime()
            name = self.names[1]
            replacement = ModuleType(name)
            replacement.__file__ = self.modules[name].__file__
            replacement.__spec__ = self.modules[name].__spec__
            with patch.dict(sys.modules, {name: replacement}):
                self.assert_code("runtime_module_changed", observed.revalidate)

    def test_module_origin_replacement_with_equal_bytes_is_rejected(self):
        with self.selected():
            observed = r.capture_service_runtime()
            module = self.modules[self.names[1]]
            changed = Path(module.__file__).with_name("other.py")
            changed.write_bytes(Path(module.__file__).read_bytes())
            module.__file__ = str(changed)
            module.__spec__.origin = str(changed)
            self.assert_code("runtime_module_changed", observed.revalidate)

    def test_disagreeing_module_and_spec_origins_are_rejected(self):
        with self.selected():
            observed = r.capture_service_runtime()
            self.modules[self.names[1]].__spec__.origin = self.modules[self.names[0]].__file__
            self.assert_code("runtime_module_changed", observed.revalidate)

    def test_module_file_content_drift_is_rejected(self):
        with self.selected():
            observed = r.capture_service_runtime()
            path = Path(self.modules[self.names[1]].__file__)
            path.write_bytes(b"# changed module\n")
            self.assert_code("runtime_file_changed", observed.revalidate)

    def test_missing_module_file_fails_without_a_fallback_or_path_disclosure(self):
        with self.selected():
            path = Path(self.modules[self.names[1]].__file__)
            path.unlink()
            self.assert_code("runtime_capture_failed", r.capture_service_runtime)

    def test_non_regular_and_oversized_files_are_rejected_before_read(self):
        with patch.object(r.os, "read", side_effect=AssertionError("must not read")):
            self.assert_code("runtime_file_limit", lambda: r._observe_file(self.config, 1))
            self.assert_code("runtime_file_unavailable", lambda: r._observe_file(self.prefix, 100))

    def test_growth_cannot_exceed_the_read_bound_and_descriptor_is_closed(self):
        path = self.root / "growing.py"
        path.write_bytes(b"1234")
        original_read, original_close = os.read, os.close
        grew, sizes = False, []

        def reading(fd, count):
            nonlocal grew
            chunk = original_read(fd, count)
            sizes.append(len(chunk))
            if not grew:
                grew = True
                with path.open("ab") as stream:
                    stream.write(b"5678901234567890")
            return chunk

        with patch.object(r.os, "read", side_effect=reading), \
                patch.object(r.os, "close", wraps=original_close) as close:
            self.assert_code("runtime_file_limit", lambda: r._observe_file(path, 8))
        self.assertEqual(sum(sizes), 9)
        self.assertEqual(close.call_count, 1)

    def test_change_between_descriptor_reads_is_rejected(self):
        with self.config.open("rb") as stream:
            original = os.fstat(stream.fileno())
        changed = SimpleNamespace(**{key: getattr(original, key) for key in (
            "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")})
        changed.st_mtime_ns += 1
        with patch.object(r.os, "fstat", side_effect=(original, changed)):
            self.assert_code("runtime_file_changed", lambda: r._observe_file(self.config, 100))

    def test_path_replacement_after_read_is_rejected(self):
        replacement = self.root / "replacement.py"
        replacement.write_bytes(self.config.read_bytes())
        original_open, original_close = os.open, os.close
        opened = []

        def named_open(path, flags):
            fd = original_open(replacement if opened else path, flags)
            opened.append(fd)
            return fd

        with patch.object(r.os, "open", side_effect=named_open), \
                patch.object(r.os, "close", wraps=original_close) as close:
            self.assert_code("runtime_file_changed", lambda: r._observe_file(self.config, 100))
        self.assertEqual(len(opened), 2)
        self.assertCountEqual([call.args[0] for call in close.call_args_list], opened)

    def test_named_stat_mode_and_ctime_differences_do_not_cause_false_drift(self):
        original_stat = os.stat
        named = original_stat(self.config)
        changed = SimpleNamespace(**{key: getattr(named, key) for key in (
            "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")})
        changed.st_mode |= 0o111
        changed.st_ctime_ns += 1

        def named_stat(path, *args, **kwargs):
            if Path(path) == self.config and kwargs.get("follow_symlinks") is False:
                return changed
            return original_stat(path, *args, **kwargs)

        with patch.object(r.os, "stat", side_effect=named_stat):
            observed = r._observe_file(self.config, 100)
        self.assertNotEqual(observed.identity, r._identity(changed))
        self.assertEqual(observed, r._observe_file(self.config, 100))

    def test_second_open_failure_closes_the_original_descriptor(self):
        original_open, original_close = os.open, os.close
        opened = []

        def named_open(path, flags):
            if opened:
                raise OSError("synthetic-secret")
            fd = original_open(path, flags)
            opened.append(fd)
            return fd

        with patch.object(r.os, "open", side_effect=named_open), \
                patch.object(r.os, "close", wraps=original_close) as close:
            self.assert_code("runtime_file_unavailable", lambda: r._observe_file(self.config, 100))
        close.assert_called_once_with(opened[0])

    def test_sdk_versions_must_match_the_reviewed_listener_versions(self):
        with patch.object(r.importlib.metadata, "version", side_effect=("2.3.0", "0.54.0")):
            self.assertEqual(r._sdk_versions(), r._SDK_VERSIONS)
        with patch.object(r.importlib.metadata, "version", return_value="synthetic-secret"):
            self.assert_code("runtime_identity_changed", r._sdk_versions)
        with self.selected():
            observed = r.capture_service_runtime()
            with patch.object(r, "_sdk_versions", return_value=(("mcp", "different"),)):
                self.assert_code("runtime_identity_changed", observed.revalidate)

    def test_unexpected_failures_and_unrecognized_codes_are_non_reflective(self):
        with patch.object(r, "_sdk_versions", side_effect=RuntimeError("synthetic-secret")):
            error = self.assert_code("runtime_capture_failed", r.capture_service_runtime)
            self.assertIsNone(error.__cause__)
            self.assertTrue(error.__suppress_context__)
        for code in ("synthetic-secret", None, {"path": str(self.root)}):
            self.assertEqual(str(r.ServiceRuntimeError(code)), "runtime_capture_failed")


if __name__ == "__main__":
    unittest.main()
