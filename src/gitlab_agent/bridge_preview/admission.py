"""Atomic admission and idle leases for one explicitly enrolled controller.

This ledger is process-local. It neither proves that other processes are idle
nor authorizes activation. Unknown outcomes are sticky maintenance blockers;
they do not disable normal work, approval resolution, or cancellation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import threading


MAX_OPERATIONS = 256
MAX_TURNS = 128
MAX_COMPLETED_TURNS = 256
MAX_IDENTITY_CHARS = 4096

_ERROR_CODES = frozenset({
    "admission_failed", "admission_wrong_process", "admission_unclaimed",
    "admission_already_claimed", "admission_closed", "maintenance_active",
    "maintenance_busy", "invalid_maintenance_lease", "invalid_reservation",
    "invalid_activity_kind", "invalid_activity_identity", "invalid_outcome",
    "admission_capacity", "turn_already_active", "workspace_already_active",
    "invalid_turn_transition", "turn_identity_mismatch",
})
UNKNOWN_REASONS = frozenset({
    "unknown_activity", "recovered_state_unverified", "transport_lost",
    "unexpected_event", "untracked_request", "operation_outcome_unknown",
    "app_creation_failed", "callback_response_unsent", "unknown_backend",
    "start_outcome_unknown", "turn_identity_mismatch", "completed_history_full",
})


class AdmissionError(RuntimeError):
    """Only fixed codes cross the admission boundary."""

    def __init__(self, code: str):
        self.code = code if type(code) is str and code in _ERROR_CODES else "admission_failed"
        super().__init__(self.code)


@dataclass(frozen=True, eq=False, slots=True, repr=False)
class OperationReservation:
    _instance: object


@dataclass(frozen=True, eq=False, slots=True, repr=False)
class TurnReservation:
    _instance: object


@dataclass(frozen=True, eq=False, slots=True, repr=False)
class MaintenanceLease:
    _instance: object
    _generation: int
    _pid: int


@dataclass
class _Turn:
    app_key: str
    thread_id: str
    workspace_key: str
    state: str = "starting"
    turn_id: str | None = None
    early_id: str | None = None
    early_completed: bool = False
    stop_requested: bool = False
    started_seen: bool = False


class ControllerAdmission:
    """One-use controller enrollment with bounded, exact-identity work tracking."""

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._instance = object()
        self._lock = threading.RLock()
        self._claimed = False
        self._closed = False
        self._generation = 0
        self._lease: MaintenanceLease | None = None
        self._operations: dict[OperationReservation, str] = {}
        self._turns: dict[TurnReservation, _Turn] = {}
        self._by_thread: dict[tuple[str, str], TurnReservation] = {}
        self._completed: set[tuple[str, str, str]] = set()
        self._unknown: set[str] = set()

    def _check_pid(self) -> None:
        # Must precede every mutex acquisition: a fork can inherit a mutex held
        # by a thread that does not exist in the child.
        if os.getpid() != self._pid:
            raise AdmissionError("admission_wrong_process")

    def _require_claimed(self) -> None:
        if not self._claimed:
            raise AdmissionError("admission_unclaimed")

    def _require_open(self) -> None:
        self._require_claimed()
        if self._closed:
            raise AdmissionError("admission_closed")
        if self._lease is not None:
            raise AdmissionError("maintenance_active")

    @staticmethod
    def _identity(value: str, *, empty: bool = False) -> str:
        if type(value) is not str or len(value) > MAX_IDENTITY_CHARS or (not value and not empty):
            raise AdmissionError("invalid_activity_identity")
        return value

    def _mark_unknown(self, reason: str) -> None:
        self._unknown.add(reason if type(reason) is str and reason in UNKNOWN_REASONS else "unknown_activity")

    def claim_controller(self) -> None:
        self._check_pid()
        with self._lock:
            if self._claimed:
                raise AdmissionError("admission_already_claimed")
            if self._closed:
                raise AdmissionError("admission_closed")
            self._claimed = True

    def reserve_operation(self, *, kind: str = "operation") -> OperationReservation:
        self._check_pid()
        with self._lock:
            self._require_open()
            if type(kind) is not str or kind not in {"operation", "approval", "callback"}:
                raise AdmissionError("invalid_activity_kind")
            if len(self._operations) >= MAX_OPERATIONS:
                raise AdmissionError("admission_capacity")
            reservation = OperationReservation(self._instance)
            self._operations[reservation] = kind
            return reservation

    def finish_operation(self, reservation: OperationReservation, *, uncertain: bool = False) -> None:
        self._check_pid()
        with self._lock:
            self._require_claimed()
            if type(reservation) is not OperationReservation or reservation not in self._operations:
                raise AdmissionError("invalid_reservation")
            if type(uncertain) is not bool:
                raise AdmissionError("invalid_outcome")
            if uncertain:
                self._mark_unknown("operation_outcome_unknown")
            del self._operations[reservation]

    def reserve_turn(self, *, app_key: str, thread_id: str, workspace_key: str = "") -> TurnReservation:
        """Reserve immediately before turn/start; an outer operation covers preparation."""
        self._check_pid()
        with self._lock:
            self._require_open()
            app_key = self._identity(app_key)
            thread_id = self._identity(thread_id)
            workspace_key = self._identity(workspace_key, empty=True)
            if (app_key, thread_id) in self._by_thread:
                raise AdmissionError("turn_already_active")
            if workspace_key and any(t.workspace_key == workspace_key for t in self._turns.values()):
                raise AdmissionError("workspace_already_active")
            if (len(self._turns) >= MAX_TURNS
                    or len(self._completed) + len(self._turns) >= MAX_COMPLETED_TURNS):
                # Reserve terminal-history space before the external start.
                # A normal completion must not make a clean instance unknown.
                raise AdmissionError("admission_capacity")
            reservation = TurnReservation(self._instance)
            self._turns[reservation] = _Turn(app_key, thread_id, workspace_key)
            self._by_thread[app_key, thread_id] = reservation
            return reservation

    def _turn(self, reservation: TurnReservation) -> _Turn:
        if type(reservation) is not TurnReservation or reservation not in self._turns:
            raise AdmissionError("invalid_reservation")
        return self._turns[reservation]

    def _retire_turn(self, reservation: TurnReservation, turn: _Turn) -> None:
        assert turn.turn_id is not None
        identity = (turn.app_key, turn.thread_id, turn.turn_id)
        if identity not in self._completed:
            if len(self._completed) >= MAX_COMPLETED_TURNS:
                # Do not silently evict identities and later mistake duplicate
                # events for a new turn. Overflow remains an explicit blocker.
                self._mark_unknown("completed_history_full")
            else:
                self._completed.add(identity)
        del self._turns[reservation]
        del self._by_thread[turn.app_key, turn.thread_id]

    def bind_turn(self, reservation: TurnReservation, *, turn_id: str) -> bool:
        """Bind the reply; return whether it may still be displayed as active."""
        self._check_pid()
        with self._lock:
            self._require_claimed()
            turn = self._turn(reservation)
            turn_id = self._identity(turn_id)
            if turn.state != "starting" or turn.turn_id is not None:
                raise AdmissionError("invalid_turn_transition")
            if ((turn.app_key, turn.thread_id, turn_id) in self._completed
                    or (turn.early_id is not None and turn.early_id != turn_id)):
                self._mark_unknown("turn_identity_mismatch")
                raise AdmissionError("turn_identity_mismatch")
            turn.turn_id = turn_id
            if self._closed:
                self._mark_unknown("operation_outcome_unknown")
            if turn.early_completed:
                self._retire_turn(reservation, turn)
                return False
            elif self._closed:
                turn.state = "unknown"
                return False
            else:
                turn.state = "stopping" if turn.stop_requested else "running"
                return True

    def fail_turn(self, reservation: TurnReservation) -> None:
        """An uncertain start cannot release its thread/workspace reservation."""
        self._check_pid()
        with self._lock:
            self._require_claimed()
            turn = self._turn(reservation)
            turn.state = "unknown"
            self._mark_unknown("start_outcome_unknown")

    def _turn_event(self, kind: str, *, app_key: str, thread_id: str, turn_id: str) -> bool:
        self._check_pid()
        with self._lock:
            self._require_claimed()
            try:
                identity = (self._identity(app_key), self._identity(thread_id), self._identity(turn_id))
            except AdmissionError:
                self._mark_unknown("unexpected_event")
                raise
            if identity in self._completed:
                return False
            reservation = self._by_thread.get((app_key, thread_id))
            if reservation is None:
                self._mark_unknown("unexpected_event")
                return False
            turn = self._turns[reservation]
            if turn.state == "starting":
                if turn.early_id is not None and turn.early_id != turn_id:
                    self._mark_unknown("unexpected_event")
                    return False
                turn.early_id = turn_id
                if kind == "completed":
                    if turn.early_completed:
                        return False
                    turn.early_completed = True
                elif kind == "stopping":
                    if turn.stop_requested or turn.early_completed:
                        return False
                    turn.stop_requested = True
                elif turn.started_seen or turn.early_completed or turn.stop_requested:
                    return False
                else:
                    turn.started_seen = True
                return True
            if turn.turn_id != turn_id:
                self._mark_unknown("unexpected_event")
                return False
            if kind == "completed":
                self._retire_turn(reservation, turn)
                return True
            elif kind == "stopping" and turn.state == "running":
                turn.state = "stopping"
                return True
            elif kind == "started" and turn.state == "running" and not turn.started_seen:
                turn.started_seen = True
                return True
            # Duplicate started events never undo stopping/unknown state.
            return False

    def turn_started(self, *, app_key: str, thread_id: str, turn_id: str) -> bool:
        return self._turn_event("started", app_key=app_key, thread_id=thread_id, turn_id=turn_id)

    def turn_completed(self, *, app_key: str, thread_id: str, turn_id: str) -> bool:
        return self._turn_event("completed", app_key=app_key, thread_id=thread_id, turn_id=turn_id)

    def turn_stopping(self, *, app_key: str, thread_id: str, turn_id: str) -> bool:
        return self._turn_event("stopping", app_key=app_key, thread_id=thread_id, turn_id=turn_id)

    def mark_unknown(self, reason: str) -> None:
        self._check_pid()
        with self._lock:
            self._mark_unknown(reason)

    def snapshot(self) -> dict[str, object]:
        self._check_pid()
        with self._lock:
            counts = {
                "operations": sum(k == "operation" for k in self._operations.values()),
                "callbacks": sum(k == "callback" for k in self._operations.values()),
                "approvals": sum(k == "approval" for k in self._operations.values()),
                **{name + "_turns": sum(t.state == name for t in self._turns.values())
                   for name in ("starting", "running", "stopping", "unknown")},
            }
            state = ("closed" if self._closed else "unclaimed" if not self._claimed
                     else "unknown" if self._unknown else "maintenance" if self._lease is not None else "open")
            return {
                "schema_version": 1, "scope": "controller-instance", "state": state,
                "admission_open": self._claimed and not self._closed and self._lease is None,
                "counts": counts, "unknown_reasons": sorted(self._unknown),
                "idle_observed": self._claimed and not self._closed and not self._unknown
                                 and not self._operations and not self._turns,
                "maintenance_held": self._lease is not None,
                "activation_authorized": False, "global_idle_verified": False,
            }

    def try_enter_maintenance(self) -> MaintenanceLease:
        """Atomically reserve an already-idle controller; never drain its work."""
        self._check_pid()
        with self._lock:
            self._require_claimed()
            if self._closed:
                raise AdmissionError("admission_closed")
            if self._lease is not None or self._operations or self._turns or self._unknown:
                raise AdmissionError("maintenance_busy")
            self._generation += 1
            self._lease = MaintenanceLease(self._instance, self._generation, self._pid)
            return self._lease

    def leave_maintenance(self, lease: MaintenanceLease) -> None:
        self._check_pid()
        with self._lock:
            self._require_claimed()
            if (type(lease) is not MaintenanceLease or lease is not self._lease
                    or lease._instance is not self._instance or lease._generation != self._generation
                    or lease._pid != self._pid):
                raise AdmissionError("invalid_maintenance_lease")
            self._lease = None

    def close(self) -> None:
        """Permanently deny admission, without erasing outstanding work evidence."""
        self._check_pid()
        with self._lock:
            if not self._closed:
                self._closed = True
                self._generation += 1
                self._lease = None
