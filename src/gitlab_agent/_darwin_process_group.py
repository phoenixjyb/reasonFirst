"""Read-only, bounded observation of one owned Darwin process group.

Used only to disambiguate killpg(EPERM), not as process-tree containment.
No process-name search, argv/environment inspection, or per-PID signalling.
The caller must retain its unreaped Popen leader throughout this observation.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time


# Public Darwin libproc ABI: proc_info.h / libproc.h in Apple's XNU sources.
# PROC_PIDT_SHORTBSDINFO with arg=1 includes zombie processes. The short
# structure has a fixed 64-byte layout on supported 64-bit Darwin platforms.
_PROC_PGRP_ONLY = 2
_PROC_PIDT_SHORTBSDINFO = 13
_SZOMB = 5
_MAX_GROUP_MEMBERS = 256
_OBSERVE_SECONDS = 0.2


class _ShortBSDInfo(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint32), ("ppid", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32), ("status", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        *[(name, ctypes.c_uint32) for name in
          ("flags", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "rfu")],
    ]


class _GroupInfo:
    def __init__(self) -> None:
        if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(_ShortBSDInfo) != 64:
            raise RuntimeError("command_group_observation_unavailable")
        self.lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_void_p, ctypes.c_int]
        self.lib.proc_listpids.restype = ctypes.c_int
        self.lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int,
                                         ctypes.c_uint64, ctypes.c_void_p,
                                         ctypes.c_int]
        self.lib.proc_pidinfo.restype = ctypes.c_int

    def members(self, pgid: int) -> frozenset[int]:
        # One extra slot makes a full/truncated result fail closed. Never
        # resize or request a host-wide PID list, even for an oversized group.
        values = (ctypes.c_int * (_MAX_GROUP_MEMBERS + 1))()
        size = ctypes.sizeof(values)
        ctypes.set_errno(0)
        used = self.lib.proc_listpids(_PROC_PGRP_ONLY, pgid, values, size)
        if (ctypes.get_errno() != 0 or used <= 0 or used >= size
                or used % ctypes.sizeof(ctypes.c_int)):
            raise RuntimeError("command_group_observation_unavailable")
        pids = list(values[:used // ctypes.sizeof(ctypes.c_int)])
        if any(pid <= 1 for pid in pids) or len(set(pids)) != len(pids):
            raise RuntimeError("command_group_observation_unavailable")
        return frozenset(pids)

    def status(self, pid: int) -> tuple[int, int, int, int]:
        info = _ShortBSDInfo()
        ctypes.set_errno(0)
        used = self.lib.proc_pidinfo(pid, _PROC_PIDT_SHORTBSDINFO, 1,
                                    ctypes.byref(info), ctypes.sizeof(info))
        if ctypes.get_errno() != 0 or used != ctypes.sizeof(info):
            raise RuntimeError("command_group_observation_unavailable")
        # Names, uid/gid fields, and flags are neither returned nor logged.
        return info.pid, info.ppid, info.pgid, info.status


def owned_group_is_zombie_only(proc) -> bool:
    """True only for a complete, stable, zombie-only owned-group observation.

    Any live member, missing leader, changed membership, API error, incomplete
    record, unsupported platform, or expired observation budget returns False.
    This function never polls/reaps the child. A False result must preserve the
    original signalling error, not authorize a weaker cleanup strategy.
    """
    if (sys.platform != "darwin" or type(proc.pid) is not int or proc.pid <= 1
            or proc.returncode is not None):
        return False
    deadline = time.monotonic() + _OBSERVE_SECONDS
    try:
        observer = _GroupInfo()
        if time.monotonic() >= deadline:
            return False
        members = observer.members(proc.pid)
        if proc.pid not in members:
            return False
        for pid in sorted(members):
            if time.monotonic() >= deadline:
                return False
            actual_pid, parent, group, status = observer.status(pid)
            if actual_pid != pid or group != proc.pid or status != _SZOMB:
                return False
            if pid == proc.pid and parent != os.getpid():
                return False
        if time.monotonic() >= deadline:
            return False
        # Zombies cannot fork. Recheck the group to reject observed additions,
        # departures, or reaping during the per-member observations.
        return (observer.members(proc.pid) == members
                and proc.returncode is None and time.monotonic() < deadline)
    except BaseException:
        # Observation never makes failed cleanup successful without evidence;
        # even an interrupted observation leaves the original EPERM as failure.
        return False
