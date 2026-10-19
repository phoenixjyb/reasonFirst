"""Bounded, non-following reads for static service evidence.

This is a consistency and metadata check, not a same-user hostile-code sandbox.
Platforms without the primitives needed to reject symlinks during open return
``unsupported`` instead of treating a best-effort read as verified.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import errno
from typing import Literal


ReadStatus = Literal["ok", "missing", "invalid", "unreadable", "unsupported"]
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd


@dataclass(frozen=True)
class BoundedRead:
    status: ReadStatus
    data: bytes | None = None
    reason: str | None = None
    guarantees: tuple[str, ...] = ()


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    # Sibling activity changes directory size/timestamps without changing which
    # directory the selected path names. File contents use the stricter signature.
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _metadata_ok(
    info: os.stat_result, *, owner_uid: int, reject_writable_bits: int
) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == owner_uid
        and not info.st_mode & reject_writable_bits
    )


def _missing(error: OSError) -> bool:
    return isinstance(error, FileNotFoundError) or error.errno == errno.ENOENT


def read_bounded_regular(
    path: Path,
    *,
    max_bytes: int,
    anchor: Path | None = None,
    owner_uid: int | None = None,
    reject_writable_bits: int = 0o022,
) -> BoundedRead:
    """Read one owned regular file after matching path and descriptor metadata.

    Directory descriptors prevent parent-component symlink traversal; the file
    open uses ``O_NOFOLLOW`` and ``O_NONBLOCK`` so a racing FIFO cannot hang the
    reader. Path and descriptor ownership/mode are checked before and after the
    read. Retained parent descriptors are revalidated against their selected
    paths before returning bytes. These checks are not an atomic snapshot.
    Unsupported platforms make no positive ownership/no-follow claim.
    """
    guarantees = (("anchor_and_children_no_follow", "file_no_follow",
                   "descriptor_path_metadata_match", "bounded_nonblocking_read")
                  if anchor is not None else
                  ("parent_components_no_follow", "file_no_follow",
                   "descriptor_path_metadata_match", "bounded_nonblocking_read"))
    if max_bytes < 0:
        return BoundedRead("invalid", reason="invalid_limit")
    if (os.name != "posix" or not getattr(os, "O_NOFOLLOW", 0)
            or not getattr(os, "O_DIRECTORY", 0)):
        return BoundedRead("unsupported", reason="no_follow_open_unavailable")
    if (not hasattr(os, "getuid") or not hasattr(os, "O_NONBLOCK")
            or not _OPEN_SUPPORTS_DIR_FD or not _STAT_SUPPORTS_DIR_FD):
        return BoundedRead("unsupported", reason="descriptor_metadata_unavailable")
    if owner_uid is None:
        owner_uid = os.getuid()

    target = Path(os.path.abspath(path))
    if not target.is_absolute():
        return BoundedRead("unsupported", reason="path_layout_unsupported")

    if anchor is not None:
        selected_anchor = Path(os.path.abspath(anchor))
        try:
            relative = target.relative_to(selected_anchor)
        except ValueError:
            return BoundedRead("unsupported", reason="outside_selected_anchor")
        relative_parts = relative.parts
        if not relative_parts or any(part in {".", ".."} for part in relative_parts):
            return BoundedRead("unsupported", reason="path_layout_unsupported")
        components = relative_parts
    else:
        components = target.parts
        if len(components) < 2:
            return BoundedRead("unsupported", reason="path_layout_unsupported")

    # Keep the entire chain: closing ancestors early loses the ability to detect
    # a renamed/replaced directory while the leaf still exists in a detached tree.
    directories: list[tuple[Path | str, int | None, int, tuple[int, int, int]]] = []
    file_fd: int | None = None
    observed_before_open = False
    leaf_observed = False

    def directories_match() -> bool:
        try:
            return all(
                _directory_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity
                and _directory_identity(os.fstat(fd)) == identity
                for name, parent, fd, identity in directories
            )
        except OSError:
            return False

    try:
        anchor_name = selected_anchor if anchor is not None else components[0]
        anchor_path_info = os.stat(anchor_name, follow_symlinks=False)
        if not stat.S_ISDIR(anchor_path_info.st_mode):
            return BoundedRead("unsupported", reason="unsafe_anchor", guarantees=guarantees)
        observed_before_open = True
        directory_fd = os.open(
            anchor_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        identity = _directory_identity(anchor_path_info)
        directories.append((anchor_name, None, directory_fd, identity))
        if _directory_identity(os.fstat(directory_fd)) != identity or not directories_match():
            return BoundedRead("invalid", reason="anchor_changed", guarantees=guarantees)
        observed_before_open = False
        directory_components = components[:-1] if anchor is not None else components[1:-1]
        for component in directory_components:
            directory_info = os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISDIR(directory_info.st_mode):
                return BoundedRead("unsupported", reason="unsafe_path", guarantees=guarantees)
            observed_before_open = True
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            identity = _directory_identity(directory_info)
            directories.append((component, directory_fd, next_fd, identity))
            if _directory_identity(os.fstat(next_fd)) != identity or not directories_match():
                return BoundedRead("invalid", reason="path_changed", guarantees=guarantees)
            directory_fd = next_fd
            observed_before_open = False

        leaf = components[-1]
        path_before = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
        leaf_observed = True
        if not _metadata_ok(path_before, owner_uid=owner_uid,
                            reject_writable_bits=reject_writable_bits):
            return BoundedRead("unsupported", reason="unsafe_file_metadata", guarantees=guarantees)
        if path_before.st_size > max_bytes:
            return BoundedRead("invalid", reason="file_too_large", guarantees=guarantees)

        file_fd = os.open(
            leaf,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        descriptor_before = os.fstat(file_fd)
        path_opened = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
        if (not _metadata_ok(descriptor_before, owner_uid=owner_uid,
                             reject_writable_bits=reject_writable_bits)
                or _signature(path_before) != _signature(descriptor_before)
                or _signature(path_opened) != _signature(descriptor_before)):
            return BoundedRead("invalid", reason="file_changed", guarantees=guarantees)

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            block = os.read(file_fd, min(64 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        raw = b"".join(chunks)
        descriptor_after = os.fstat(file_fd)
        path_after = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
        if (_signature(descriptor_before) != _signature(descriptor_after)
                or _signature(path_after) != _signature(descriptor_after)):
            return BoundedRead("invalid", reason="file_changed", guarantees=guarantees)
        if len(raw) > max_bytes:
            return BoundedRead("invalid", reason="file_too_large", guarantees=guarantees)
        if not directories_match():
            return BoundedRead("invalid", reason="path_changed", guarantees=guarantees)
        return BoundedRead("ok", data=raw, guarantees=guarantees)
    except OSError as error:
        if _missing(error):
            if observed_before_open or leaf_observed or not directories_match():
                return BoundedRead("invalid", reason="file_changed", guarantees=guarantees)
            return BoundedRead("missing", reason="path_missing", guarantees=guarantees)
        if isinstance(error, (PermissionError, BlockingIOError)):
            return BoundedRead("unreadable", reason="read_failed", guarantees=guarantees)
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            return BoundedRead("unsupported", reason="unsafe_path", guarantees=guarantees)
        return BoundedRead("unreadable", reason="read_failed", guarantees=guarantees)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for _, _, fd, _ in reversed(directories):
            os.close(fd)
