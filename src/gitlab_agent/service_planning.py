"""Static service evidence and pure plans. No lifecycle or credential operations.

Setup records are hints, never runtime ownership or acceptance. This reader is
local to service inspection; it does not change the persisted setup schema or
legacy setup loader.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
import platform
from typing import Any

import yaml

from .deployment_inventory import build_deployment_inventory
from .setup_state import SETUP_MODES, SetupState
from .static_service_files import read_bounded_regular

MAX_STATE_BYTES = 128 * 1024


@dataclass(frozen=True)
class Observation:
    status: str
    source: str
    scope: str
    value: Any = None
    observed_at: str | None = None


@dataclass(frozen=True)
class ServiceRole:
    role: str
    transport: Observation
    runtime_reference: str = "unknown"
    connection_reference: str = "unknown"


@dataclass(frozen=True)
class ServiceEvidence:
    system: str
    inventory: dict[str, object]
    setup: Observation
    mode: str | None
    roles: tuple[ServiceRole, ...]
    configuration: Observation
    credential_reference: Observation


def unchanged() -> dict[str, object]:
    return {"mutating": False, "can_execute": False,
            "changes": {"services": False, "configuration": False, "credentials": False},
            "ready": False, "safe_to_apply": False}


class _UniqueStateLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result or key == "<<":
                raise ValueError("ambiguous_state")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _controlled_state(raw: bytes) -> tuple[SetupState, bool]:
    text = raw.decode("utf-8", errors="strict")
    # Aliases/anchors and merge keys are not needed by the flat setup schema.
    if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
           for token in yaml.scan(text)):
        raise ValueError("ambiguous_state")
    data = yaml.load(text, Loader=_UniqueStateLoader)
    allowed = {field.name for field in fields(SetupState)}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("invalid_state")
    for key, value in data.items():
        if key == "version":
            if type(value) is not int:
                raise ValueError("invalid_state")
        elif key == "completed_phases":
            if not isinstance(value, list) or len(value) > 16:
                raise ValueError("invalid_state")
            if any(not isinstance(item, str) for item in value):
                raise ValueError("invalid_state")
        elif value is not None and not isinstance(value, str):
            raise ValueError("invalid_state")
        strings = value if isinstance(value, list) else [value]
        if any(isinstance(item, str) and (len(item) > 4096 or any(
                ord(char) < 32 or ord(char) == 127 for char in item)) for item in strings):
            raise ValueError("invalid_state")
    return SetupState.from_dict(data), data.get("mode") in SETUP_MODES


def inspect_setup_state(home: Path) -> tuple[SetupState | None, Observation, bool]:
    result = read_bounded_regular(home / ".config/reasonfirst/setup.yaml",
                                  max_bytes=MAX_STATE_BYTES, anchor=home)
    source = "known_setup_state_file"
    scope = "static_setup_metadata_not_live_acceptance"
    if result.status != "ok":
        return None, Observation(result.status, source, scope,
                                 {"reader_reason": result.reason}), False
    try:
        state, explicit_mode = _controlled_state(result.data)
    except Exception:
        # Parser/from_dict errors can include input-derived credential material.
        return None, Observation("invalid", source, scope), False
    # No arbitrary paths, runtime names, verification strings or identifiers are
    # reflected. Existing references remain in the original record, untouched.
    return state, Observation("recorded", source, scope, {
        "mode_explicit": explicit_mode,
        "last_verified_at": "setup_metadata_only" if state.last_verified_at else None,
        "read_guarantees": list(result.guarantees),
        "identity_values": "retained_in_original_record_not_reproduced",
    }), explicit_mode


def collect_service_evidence(*, home: Path | None = None,
                             system_name: str | None = None) -> ServiceEvidence:
    selected_home = home if home is not None else Path.home()
    system = system_name or platform.system()
    state, setup, explicit_mode = inspect_setup_state(selected_home)
    inventory = build_deployment_inventory(state=state, system_name=system, home=selected_home)
    roles: list[ServiceRole] = []
    if state is not None:
        for role, runtime, connection in (
            ("read", state.tunnel_runtime, state.tunnel_id),
            ("bridge", state.bridge_runtime, state.bridge_tunnel_id),
        ):
            if runtime or connection:
                roles.append(ServiceRole(role, Observation(
                    "unknown", "setup_state", "record_has_no_transport_field"),
                    "recorded_not_verified" if runtime else "unknown",
                    "recorded_not_verified" if connection else "unknown"))
    registration = inventory["registration"]
    if registration.get("transport") == "streamable-http":
        roles.append(ServiceRole("bridge", Observation(
            "recognized", "known_bridge_definition", "launcher_layout_not_live_probe",
            "streamable-http")))
    # Keep the known tunnel definition visible even if its argv cannot be safely
    # classified. Do not infer read/Bridge identity from its name or arbitrary argv.
    for definition in inventory.get("service_definitions", []):
        role = definition.get("role")
        if definition.get("status") in {"not_found", "not_inspected"}:
            continue
        if role == "tunnel" or (role == "bridge" and registration.get("transport") is None):
            roles.append(ServiceRole(role, Observation(
                "unknown", "known_" + role + "_definition", "unclassified_static_definition")))
    return ServiceEvidence(
        system, inventory, setup, state.mode if state is not None and explicit_mode else None,
        tuple(roles),
        Observation("recorded_not_verified" if state and state.config_file else "unknown",
                    "setup_state", "reference_presence_only_contents_not_read"),
        Observation("unknown", "no_reviewed_reference_source", "credential_contents_never_read"),
    )


def _target(system: str) -> dict[str, object]:
    manager = {"darwin": "launchd_user_launchagent", "macos": "launchd_user_launchagent",
               "linux": "systemd_user_service", "windows": "user_logon_scheduled_task"}.get(system.lower())
    return {"manager": manager, "scope": "intended_user_login", "implemented": False,
            "native_observation": "not_inspected" if manager else "unsupported"}


def service_status(evidence: ServiceEvidence) -> dict[str, object]:
    observations = {
        name: asdict(Observation("not_checked", "not_observed_in_slice1", scope))
        for name, scope in (
            ("native_registration", "native_service_manager"),
            ("login_autostart", "effective_login_configuration"),
            ("runtime_owner", "live_process_ownership"),
            ("process_identity", "running_executable_and_revision"),
            ("mcp_readiness", "local_mcp"), ("tunnel_readiness", "tunnel_connection"),
            ("end_to_end", "intended_reasoning_client_connection"),
        )
    }
    return {"ok": True, "command": "service-status", **unchanged(),
            "scope": "slice1_static_evidence_only", "target": _target(evidence.system),
            "installed_code": asdict(Observation("observed", "current_python_package",
                                                  "installed_cli_not_running_service",
                                                  evidence.inventory["installed_cli"])),
            "deployment": evidence.inventory, "setup_state": asdict(evidence.setup),
            "startup_runtime": asdict(Observation(
                "unverified", "current_package_location", "not_an_approved_startup_target",
                {"source_checkout": Path(__file__).parent.parent.name == "src",
                 "stable_runtime_binding": None})),
            "mode": evidence.mode, "mode_source": "explicit_setup_record" if evidence.mode else "unknown",
            "roles": [asdict(role) for role in evidence.roles],
            "role_entry_scope": "independent_evidence_not_runtime_instances",
            "identity_linkage_between_sources": "not_established",
            "configuration_provenance": asdict(evidence.configuration),
            "credential_reference": asdict(evidence.credential_reference),
            "observations": observations}


def build_service_plan(evidence: ServiceEvidence, *, autostart: str = "login") -> dict[str, object]:
    """Pure deterministic description; never an apply token or execution API."""
    if autostart != "login":
        raise ValueError("unsupported_autostart")
    blockers = [
        "slice1_cannot_execute_plans", "native_registration_and_login_not_inspected",
        "runtime_owner_and_identity_unobserved", "stable_runtime_path_and_version_unverified",
        "configuration_provenance_unverified", "credential_reference_and_login_availability_unverified",
        "explicit_handover_and_rollback_required",
    ]
    if evidence.setup.status != "recorded":
        blockers.append("setup_state_" + evidence.setup.status)
    if evidence.mode is None:
        blockers.append("deployment_mode_unknown")
    if not evidence.roles or any(role.transport.value not in {"stdio", "streamable-http"}
                                 for role in evidence.roles):
        blockers.append("deployment_transport_or_connection_unknown")
    if any(role.runtime_reference == "unknown" or role.connection_reference == "unknown"
           for role in evidence.roles):
        blockers.append("deployment_identity_incomplete")
    if evidence.mode in {"standard", "cli-only"} and any(
            role.role == "bridge" for role in evidence.roles):
        blockers.append("recorded_mode_conflicts_with_bridge_evidence")
    if _target(evidence.system)["manager"] is None:
        blockers.append("platform_unsupported")
    definitions = evidence.inventory.get("service_definitions", [evidence.inventory["registration"]])
    present = [item for item in definitions if item.get("status") not in {"not_found", "not_inspected"}]
    if evidence.inventory["registration"].get("status") == "unsupported_layout":
        blockers.append("bridge_command_layout_unclassified")
    if any(item.get("status") in {"invalid", "unreadable", "unsupported_layout", "unsupported"}
           for item in present):
        blockers.append("definition_requires_local_review")
    if present or len(evidence.roles) > 1 or evidence.inventory.get("bridge_config_marker") != "not_found":
        blockers.append("potential_owner_overlap_requires_handover_evidence")
    topology = []
    for role in evidence.roles:
        transport = role.transport.value
        topology.append({
            **asdict(role), "preserve_existing_references": True,
            "mcp_child_owner": "upstream_tunnel_runtime" if transport == "stdio" else "unknown",
            "supervision": "preserve_upstream_child_ownership_no_extra_mcp_supervisor" if transport == "stdio"
            else "keep_bridge_and_tunnel_owners_separate" if transport == "streamable-http"
            else "resolve_transport_before_proposing_supervision",
        })
    return {"ok": False, "command": "service-plan", **unchanged(),
            "plan_status": "blocked", "autostart": autostart,
            "proposed_os_owner": _target(evidence.system), "observed_runtime_owner": "unobserved",
            "mode": evidence.mode, "enable_bridge": False, "start_coding_worker": False,
            "preserved_topology": topology, "blockers": blockers,
            "preserved_topology_scope": "independent_evidence_not_runtime_instances",
            "prerequisites": [
                "Confirm intended user, device, connection, role and deployment mode explicitly.",
                "Bind stable source/package runtime, exact version and configuration provenance; do not use an unreviewed temporary checkout.",
                "Review explicit credential reference metadata and availability at login without reading secrets for planning.",
                "Preserve selected routing, proxy and loopback bypass; do not copy ambient environment.",
                "Observe native registration, live owner identity and readiness independently in a later authorized slice.",
            ],
            "handover_requirements": [
                "Static roles and recorded runtimes do not count running instances.",
                "Confirm each existing manual/legacy/managed owner and active work before an explicit handover.",
                "Revalidate evidence before any future mutation; never adopt or stop a process by label/PID alone.",
                "Inspect upstream foreground/daemon behavior before proposing a restart owner; never nest restart loops.",
            ],
            "rollback_requirements": [
                "Preserve reviewed previous definition, owner, runtime and configuration before a future switch.",
                "Specify restoration and deliberate stop/disable behavior; verify identity before rollback.",
                "Require separate login/reboot, recovery and end-to-end acceptance; setup timestamps are not acceptance.",
            ]}
