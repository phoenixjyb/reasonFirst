from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.static_service_files import read_bounded_regular


@unittest.skipUnless(os.name == "posix", "requires POSIX descriptor-relative no-follow reads")
class StaticServiceFileReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "definition.plist"
        self.path.write_bytes(b"safe")
        self.path.chmod(0o600)

    def read(self, **kwargs):
        return read_bounded_regular(self.path, max_bytes=32, **kwargs)

    def test_regular_file_returns_bounded_bytes_and_guarantees(self):
        result = self.read()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data, b"safe")
        self.assertIn("file_no_follow", result.guarantees)
        self.assertIn("parent_components_no_follow", result.guarantees)

    def test_selected_anchor_allows_system_alias_before_anchor_but_checks_children(self):
        selected_home = Path(self.temp.name)
        alias_path = selected_home / "definition.plist"
        alias_path.write_bytes(b"safe")
        alias_path.chmod(0o600)
        result = read_bounded_regular(alias_path, max_bytes=32, anchor=selected_home)
        self.assertEqual(result.status, "ok")
        self.assertIn("anchor_and_children_no_follow", result.guarantees)
        self.assertEqual(
            read_bounded_regular(self.root / "outside", max_bytes=32,
                                 anchor=self.root / "nested").status,
            "unsupported",
        )

    def test_missing_and_oversized_are_distinct(self):
        self.path.unlink()
        self.assertEqual(self.read().status, "missing")
        self.path.write_bytes(b"x" * 33)
        self.path.chmod(0o600)
        self.assertEqual(self.read().status, "invalid")

    def test_symlink_file_and_symlink_parent_are_rejected(self):
        target = self.root / "target"
        target.write_bytes(b"safe")
        target.chmod(0o600)
        self.path.unlink()
        self.path.symlink_to(target)
        self.assertEqual(self.read().status, "unsupported")
        self.path.unlink()
        self.path.symlink_to(self.root / "missing-target")
        self.assertEqual(self.read().status, "unsupported")

        nested = self.root / "nested"
        nested.mkdir()
        nested_path = nested / "definition"
        nested_path.write_bytes(b"safe")
        nested_path.chmod(0o600)
        link = self.root / "linked"
        link.symlink_to(nested, target_is_directory=True)
        self.assertEqual(read_bounded_regular(link / "definition", max_bytes=32).status,
                         "unsupported")

    def test_directory_and_fifo_are_rejected_without_blocking(self):
        self.path.unlink()
        self.path.mkdir()
        self.assertEqual(self.read().status, "unsupported")
        self.path.rmdir()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.path)
            self.assertEqual(self.read().status, "unsupported")

    def test_descriptor_and_path_owner_and_mode_are_both_checked(self):
        native_stat = os.stat
        native_fstat = os.fstat

        def altered(info):
            fields = list(info)
            fields[stat.ST_MODE] = stat.S_IFREG | 0o622
            return os.stat_result(fields)

        with patch("gitlab_agent.static_service_files.os.stat",
                   side_effect=lambda *a, **kw: altered(native_stat(*a, **kw))), \
             patch("gitlab_agent.static_service_files.os.fstat",
                   side_effect=lambda *a, **kw: altered(native_fstat(*a, **kw))):
            self.assertEqual(self.read().status, "unsupported")

        with patch("gitlab_agent.static_service_files.os.stat",
                   side_effect=lambda *a, **kw: altered(native_stat(*a, **kw))
                   if kw.get('dir_fd') is not None and str(a[0]) == self.path.name
                   else native_stat(*a, **kw)):
            self.assertEqual(self.read().status, "unsupported")

        with patch("gitlab_agent.static_service_files.os.stat", wraps=native_stat), \
             patch("gitlab_agent.static_service_files.os.fstat",
                   side_effect=lambda fd: os.stat_result(
                       [*list(native_fstat(fd))[:stat.ST_UID], 987654] +
                       list(native_fstat(fd))[stat.ST_UID + 1:])):
            self.assertEqual(self.read().status, "invalid")

    def test_replace_during_read_is_rejected_as_changed(self):
        native_read = os.read
        mutated = False

        def replace_after_read(fd, count):
            nonlocal mutated
            data = native_read(fd, count)
            if data and not mutated:
                mutated = True
                replacement = self.root / "replacement"
                replacement.write_bytes(b"other")
                replacement.chmod(0o600)
                replacement.replace(self.path)
            return data

        with patch("gitlab_agent.static_service_files.os.read", side_effect=replace_after_read):
            result = self.read()
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "file_changed")

    def test_leaf_replaced_with_symlink_before_open_is_never_read(self):
        target = self.root / "secret-target"
        target.write_bytes(b"do not read")
        target.chmod(0o600)
        native_open = os.open
        swapped = False

        def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if path == self.path.name and dir_fd is not None and not swapped:
                swapped = True
                self.path.unlink()
                self.path.symlink_to(target)
            if dir_fd is None:
                return native_open(path, flags, mode)
            return native_open(path, flags, mode, dir_fd=dir_fd)

        with patch("gitlab_agent.static_service_files.os.open", side_effect=replace_before_open), \
             patch("gitlab_agent.static_service_files.os.read",
                   side_effect=AssertionError("symlink target must not be read")):
            result = self.read()
        self.assertEqual(result.status, "unsupported")

    def test_leaf_replaced_with_different_regular_file_before_open_is_rejected(self):
        native_open = os.open
        swapped = False

        def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if path == self.path.name and dir_fd is not None and not swapped:
                swapped = True
                replacement = self.root / "replacement"
                replacement.write_bytes(b"other")
                replacement.chmod(0o600)
                replacement.replace(self.path)
            if dir_fd is None:
                return native_open(path, flags, mode)
            return native_open(path, flags, mode, dir_fd=dir_fd)

        with patch("gitlab_agent.static_service_files.os.open", side_effect=replace_before_open), \
             patch("gitlab_agent.static_service_files.os.read",
                   side_effect=AssertionError("changed descriptor must not be read")):
            result = self.read()
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "file_changed")

    def test_leaf_replaced_with_fifo_before_open_is_nonblocking_and_rejected(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo is unavailable")
        native_open = os.open
        swapped = False

        def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if path == self.path.name and dir_fd is not None and not swapped:
                swapped = True
                self.path.unlink()
                os.mkfifo(self.path)
            if dir_fd is None:
                return native_open(path, flags, mode)
            return native_open(path, flags, mode, dir_fd=dir_fd)

        with patch("gitlab_agent.static_service_files.os.open", side_effect=replace_before_open), \
             patch("gitlab_agent.static_service_files.os.read",
                   side_effect=AssertionError("FIFO must not be read")):
            result = self.read()
        self.assertEqual(result.status, "invalid")

    def test_read_error_is_redacted(self):
        with patch("gitlab_agent.static_service_files.os.read",
                   side_effect=PermissionError("credential-shaped private text")):
            result = self.read()
        self.assertEqual(result.status, "unreadable")
        self.assertNotIn("credential-shaped", repr(result))

    def test_missing_platform_guarantee_is_reported_as_unsupported(self):
        with patch("gitlab_agent.static_service_files.os.name", "nt"):
            result = self.read()
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason, "no_follow_open_unavailable")

    def test_zero_valued_nofollow_flag_is_not_treated_as_a_guarantee(self):
        with patch("gitlab_agent.static_service_files.os.O_NOFOLLOW", 0):
            result = self.read()
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason, "no_follow_open_unavailable")

    def test_observed_leaf_disappearance_is_changed_not_missing(self):
        for phase in ('open', 'read'):
            with self.subTest(phase=phase):
                self.path.write_bytes(b'safe')
                self.path.chmod(0o600)
                native_open, native_read = os.open, os.read
                changed = False

                def opening(path, flags, *args, **kwargs):
                    nonlocal changed
                    if phase == 'open' and path == self.path.name and not changed:
                        changed = True
                        self.path.unlink()
                    return native_open(path, flags, *args, **kwargs)

                def reading(fd, count):
                    nonlocal changed
                    data = native_read(fd, count)
                    if phase == 'read' and data and not changed:
                        changed = True
                        self.path.unlink()
                    return data

                with patch('os.open', side_effect=opening), patch('os.read', side_effect=reading):
                    result = self.read(anchor=self.root)
                self.assertTrue(changed)
                self.assertEqual((result.status, result.reason, result.data),
                                 ('invalid', 'file_changed', None))

    def test_directory_identity_changes_discard_bytes(self):
        # These are POSIX race simulations, not native Windows coverage.
        for selected in ('parent', 'anchor'):
            for phase in ('open', 'read'):
                with self.subTest(selected=selected, phase=phase), tempfile.TemporaryDirectory() as tmp:
                    outer = Path(tmp).resolve()
                    anchor = outer / 'home'
                    parent = anchor / 'definitions'
                    parent.mkdir(parents=True)
                    leaf = parent / 'file'
                    leaf.write_bytes(b'safe')
                    leaf.chmod(0o600)
                    directory = parent if selected == 'parent' else anchor
                    native_open, native_read = os.open, os.read
                    changed = False

                    def replace_directory():
                        nonlocal changed
                        changed = True
                        directory.rename(outer / 'detached')
                        leaf.parent.mkdir(parents=True)
                        leaf.write_bytes(b'other')
                        leaf.chmod(0o600)

                    def opening(path, flags, *args, **kwargs):
                        if phase == 'open' and not changed and (
                            path == anchor if selected == 'anchor' else path == 'definitions'
                        ):
                            replace_directory()
                        return native_open(path, flags, *args, **kwargs)

                    def reading(fd, count):
                        data = native_read(fd, count)
                        if phase == 'read' and data and not changed:
                            replace_directory()
                        return data

                    with patch('os.open', side_effect=opening), patch('os.read', side_effect=reading):
                        result = read_bounded_regular(leaf, max_bytes=32, anchor=anchor)
                    self.assertTrue(changed)
                    self.assertEqual(result.status, 'invalid')
                    self.assertIn(result.reason, ('anchor_changed', 'path_changed', 'file_changed'))
                    self.assertIsNone(result.data)

    def test_sibling_activity_does_not_change_directory_identity(self):
        for phase in ('open', 'read'):
            with self.subTest(phase=phase):
                native_open, native_read = os.open, os.read
                changed = False

                def sibling():
                    nonlocal changed
                    changed = True
                    (self.root / 'sibling').write_bytes(b'unrelated')
                    (self.root / 'sibling').unlink()

                def opening(path, flags, *args, **kwargs):
                    if phase == 'open' and path == self.root and not changed:
                        sibling()
                    return native_open(path, flags, *args, **kwargs)

                def reading(fd, count):
                    data = native_read(fd, count)
                    if phase == 'read' and data and not changed:
                        sibling()
                    return data

                with patch('os.open', side_effect=opening), patch('os.read', side_effect=reading):
                    result = self.read(anchor=self.root)
                self.assertTrue(changed)
                self.assertEqual((result.status, result.data), ('ok', b'safe'))

    def test_all_opened_descriptors_close_on_success_and_failure(self):
        parent = self.root / 'first' / 'second'
        parent.mkdir(parents=True)
        self.path = parent / 'definition.plist'
        self.path.write_bytes(b'safe')
        self.path.chmod(0o600)
        for failure in (False, True):
            with self.subTest(failure=failure):
                native_open, native_close, native_read = os.open, os.close, os.read
                opened, closed = [], []

                def opening(*args, **kwargs):
                    fd = native_open(*args, **kwargs)
                    opened.append(fd)
                    return fd

                def closing(fd):
                    closed.append(fd)
                    return native_close(fd)

                def reading(*args):
                    if failure:
                        raise PermissionError('synthetic read failure')
                    return native_read(*args)

                with patch('os.open', side_effect=opening), patch('os.close', side_effect=closing), \
                     patch('os.read', side_effect=reading):
                    result = self.read(anchor=self.root)
                self.assertEqual(result.status, 'unreadable' if failure else 'ok')
                self.assertEqual(len(opened), 4)  # anchor, two parents, leaf
                self.assertCountEqual(opened, closed)
                for fd in opened:
                    with self.assertRaises(OSError):
                        os.fstat(fd)


class UnsupportedReaderCapabilityTests(unittest.TestCase):
    """Portable capability simulations; no native Windows execution implied."""

    def test_missing_capabilities_fail_before_open(self):
        from gitlab_agent import static_service_files as reader
        for capability in ('_OPEN_SUPPORTS_DIR_FD', '_STAT_SUPPORTS_DIR_FD'):
            with self.subTest(capability=capability), patch.object(reader, capability, False), \
                 patch.object(os, 'open') as opening:
                result = reader.read_bounded_regular(Path('unused'), max_bytes=32)
            self.assertEqual(result.status, 'unsupported')
            self.assertIsNone(result.data)
            opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
