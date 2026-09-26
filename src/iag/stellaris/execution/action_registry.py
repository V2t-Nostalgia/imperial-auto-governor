"""Public semantic action contracts for Stellaris side effects.

Applications depend on these models, never on packet fields or runtime hook
addresses.  A backend is responsible for resolving a validated semantic target
to its private transport representation.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from iag.core.contracts import FrozenContract

ACTION_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
TECHNOLOGY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class ActionRisk(StrEnum):
    """Stable risk classes used by permission and audit policy."""

    STATE_CHANGE = "state_change"
    DESTRUCTIVE_STATE_CHANGE = "destructive_state_change"
    STATE_CHANGE_REQUIRES_FRESH_SAVE = "state_change_requires_fresh_save"


class VerificationState(StrEnum):
    """Evidence level currently attached to an action implementation."""

    LIVE_VERIFIED = "live_verified"
    PAIRED_CAPTURE = "paired_capture"


class BackendId(StrEnum):
    """Internal backend identities; Applications select capabilities instead."""

    SESSION_PROXY = "session_proxy"
    CARRIER_CLICK = "carrier_click"
    NATIVE_RUNTIME = "native_runtime"


class SemanticTarget(FrozenContract):
    """Base class for public, transport-independent action targets."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BuildBuildingTarget(SemanticTarget):
    colony_id: int = Field(ge=0)
    zone_id: int = Field(ge=0)
    building_id: str = Field(min_length=1, max_length=160)


class UpgradeBuildingTarget(SemanticTarget):
    colony_id: int = Field(ge=0)
    building_object_id: int = Field(ge=0)
    target_building_id: str = Field(min_length=1, max_length=160)


class ReplaceBuildingTarget(UpgradeBuildingTarget):
    pass


class BuildDistrictTarget(SemanticTarget):
    colony_id: int = Field(ge=0)
    district_type: str = Field(min_length=1, max_length=160)


class BuildZoneTarget(SemanticTarget):
    colony_id: int = Field(ge=0)
    district_id: int = Field(ge=0)
    slot_index: int = Field(ge=0)
    zone_type: str = Field(min_length=1, max_length=160)


class MoveFleetTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)
    destination_system_id: int = Field(ge=0)


class MoveFleetToCoordinateTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)
    system_id: int = Field(ge=0)
    x: int
    y: int


class AttackFleetTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)
    target_fleet_id: int = Field(ge=0)


class BombardmentStanceTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)
    stance: Literal["raiding", "selective", "indiscriminate"]


class LandArmiesTarget(SemanticTarget):
    army_fleet_id: int = Field(ge=0)
    target_colony_id: int = Field(ge=0)


class RecruitArmyTarget(SemanticTarget):
    recruitment_starbase_id: int = Field(ge=0)
    source_colony_id: int = Field(ge=0)
    army_type: str = Field(min_length=1, max_length=160)
    species_id: int = Field(ge=0)
    quantity: int = Field(default=1, ge=1, le=100)


class RepairFleetTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)


class UpgradeFleetTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)
    shipyard_starbase_id: int = Field(ge=0)


class ConfigureShipAutomationTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)
    options: tuple[str, ...] = Field(min_length=1, max_length=16)


class BuildStarbaseTarget(SemanticTarget):
    construction_fleet_id: int = Field(ge=0)
    target_system_id: int = Field(ge=0)


class OrderColonyShipTarget(SemanticTarget):
    target_planet_id: int = Field(ge=0)
    species_id: int = Field(ge=0)
    colony_designation: str = Field(min_length=1, max_length=160)
    ship_design_id: int = Field(ge=0)
    source_shipyard_starbase_id: int = Field(ge=0)


class ColonizeWithExistingShipTarget(SemanticTarget):
    colony_ship_fleet_id: int = Field(ge=0)
    target_planet_id: int = Field(ge=0)


class UpgradeStarbaseTarget(SemanticTarget):
    starbase_id: int = Field(ge=0)
    target_level: str = Field(min_length=1, max_length=160)


class SetStarbaseComponentTarget(SemanticTarget):
    starbase_id: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=160)
    slot_index: int = Field(ge=0)


class ResearchTarget(SemanticTarget):
    """A candidate-backed research choice; country/actor data is authority state."""

    area: Literal["physics", "society", "engineering"]
    technology_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=TECHNOLOGY_ID_RE.pattern,
    )
    expected_current_technology_id: str | None = Field(
        default=None,
        max_length=160,
        pattern=TECHNOLOGY_ID_RE.pattern,
    )


class BuildShipTarget(SemanticTarget):
    shipyard_starbase_id: int = Field(ge=0)
    design_id: int = Field(ge=0)
    quantity: int = Field(default=1, ge=1, le=1000)


class ShipDesignComponent(SemanticTarget):
    slot: str = Field(min_length=1, max_length=160)
    component_id: str = Field(min_length=1, max_length=160)


class ShipDesignSection(SemanticTarget):
    template: str = Field(min_length=1, max_length=160)
    slot: str = Field(min_length=1, max_length=80)
    components: tuple[ShipDesignComponent, ...] = ()


class ShipDesignGrowthStage(SemanticTarget):
    ship_size: str = Field(min_length=1, max_length=160)
    sections: tuple[ShipDesignSection, ...] = Field(min_length=1)
    required_components: tuple[ShipDesignComponent, ...] = ()


class CreateShipDesignTarget(SemanticTarget):
    name: str = Field(min_length=1, max_length=160)
    entity: str = Field(min_length=1, max_length=160)
    graphical_culture: str = Field(min_length=1, max_length=160)
    upgrade_components_automatically: bool = False
    growth_stages: tuple[ShipDesignGrowthStage, ...] = Field(min_length=1)


class CreateFleetTemplateTarget(SemanticTarget):
    pass


class FleetTemplateShipTarget(SemanticTarget):
    fleet_template_id: int = Field(ge=0)
    design_id: int = Field(ge=0)
    quantity: int = Field(default=1, ge=1, le=1000)


class ReinforceFleetTarget(SemanticTarget):
    fleet_template_id: int = Field(ge=0)


class CreateNewFleetTarget(SemanticTarget):
    design_id: int = Field(ge=0)
    target_count: int = Field(ge=1, le=1000)


class ReinforceFleetToTargetTarget(SemanticTarget):
    fleet_id: int = Field(ge=0)
    design_id: int = Field(ge=0)
    target_count: int = Field(ge=1, le=1000)


class BackendVerification(FrozenContract):
    """Evidence level for one action/backend implementation pair."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    backend_id: BackendId
    verification_state: VerificationState


class ActionSpec(FrozenContract):
    """One versioned semantic action exposed by the platform."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        arbitrary_types_allowed=True,
    )

    action_type: str = Field(pattern=ACTION_TYPE_RE.pattern)
    action_version: int = Field(default=1, ge=1)
    target_model: type[SemanticTarget] = Field(exclude=True)
    risk_class: ActionRisk
    semantic_capability: str
    supported_backends: tuple[BackendId, ...]
    broker_enabled_backends: tuple[BackendId, ...] = ()
    verification_state: VerificationState
    backend_verification: tuple[BackendVerification, ...] = Field(min_length=1)
    migration_note: str | None = None

    @model_validator(mode="after")
    def validate_capability_and_backends(self) -> ActionSpec:
        expected = f"stellaris.action.{self.action_type}.v{self.action_version}"
        if self.semantic_capability != expected:
            raise ValueError(f"Semantic capability must be {expected!r}.")
        unsupported = set(self.broker_enabled_backends) - set(self.supported_backends)
        if unsupported:
            raise ValueError(
                "Broker-enabled backends must be declared as supported: "
                f"{sorted(unsupported)}"
            )
        verification_backends = tuple(
            item.backend_id for item in self.backend_verification
        )
        if len(set(verification_backends)) != len(verification_backends):
            raise ValueError("Backend verification entries must be unique.")
        if set(verification_backends) != set(self.supported_backends):
            raise ValueError(
                "Backend verification must cover every supported backend exactly."
            )
        aggregate = (
            VerificationState.LIVE_VERIFIED
            if any(
                item.verification_state == VerificationState.LIVE_VERIFIED
                for item in self.backend_verification
            )
            else VerificationState.PAIRED_CAPTURE
        )
        if self.verification_state != aggregate:
            raise ValueError(
                "Action verification must equal the strongest backend evidence."
            )
        return self

    def verification_for(self, backend_id: BackendId) -> VerificationState:
        """Return evidence for one backend without conflating transports."""

        for item in self.backend_verification:
            if item.backend_id == backend_id:
                return item.verification_state
        raise KeyError(
            f"Action {self.action_type!r} does not support backend {backend_id!r}."
        )

    def validate_target(self, value: object) -> SemanticTarget:
        """Validate a target against this action's strict semantic model."""

        if isinstance(value, self.target_model):
            return value
        return self.target_model.model_validate(value)


class ActionRegistry:
    """Immutable-by-convention registry for reviewed semantic actions."""

    def __init__(self, specs: tuple[ActionSpec, ...]) -> None:
        self._specs: dict[tuple[str, int], ActionSpec] = {}
        for spec in specs:
            key = (spec.action_type, spec.action_version)
            if key in self._specs:
                raise ValueError(f"Semantic action is already registered: {key!r}")
            self._specs[key] = spec

    def get(self, action_type: str, action_version: int = 1) -> ActionSpec:
        try:
            return self._specs[(action_type, action_version)]
        except KeyError as error:
            raise KeyError(
                f"Unknown semantic action: {action_type}.v{action_version}"
            ) from error

    def all(self) -> tuple[ActionSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def capabilities(self) -> tuple[str, ...]:
        return tuple(spec.semantic_capability for spec in self.all())


_ALL_PROXY_BACKENDS = (BackendId.SESSION_PROXY,)
_ECONOMY_BACKENDS = (BackendId.SESSION_PROXY, BackendId.CARRIER_CLICK)
_MIGRATION_NOTE = (
    "Semantic contract is registered, but the existing Application still uses "
    "its verified legacy execution path until a save-backed resolver is migrated."
)


def _spec(
    action_type: str,
    target_model: type[SemanticTarget],
    risk_class: ActionRisk,
    verification_state: VerificationState,
    *,
    supported_backends: tuple[BackendId, ...] = _ALL_PROXY_BACKENDS,
    broker_enabled_backends: tuple[BackendId, ...] = (),
    backend_verification: tuple[tuple[BackendId, VerificationState], ...] | None = None,
    migration_note: str | None = _MIGRATION_NOTE,
) -> ActionSpec:
    verification = backend_verification or tuple(
        (backend_id, verification_state) for backend_id in supported_backends
    )
    return ActionSpec(
        action_type=action_type,
        target_model=target_model,
        risk_class=risk_class,
        semantic_capability=f"stellaris.action.{action_type}.v1",
        supported_backends=supported_backends,
        broker_enabled_backends=broker_enabled_backends,
        verification_state=verification_state,
        backend_verification=tuple(
            BackendVerification(
                backend_id=backend_id,
                verification_state=backend_state,
            )
            for backend_id, backend_state in verification
        ),
        migration_note=migration_note,
    )


BUILTIN_ACTION_SPECS: tuple[ActionSpec, ...] = (
    _spec(
        "build_building",
        BuildBuildingTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=_ECONOMY_BACKENDS,
    ),
    _spec(
        "upgrade_building",
        UpgradeBuildingTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=_ECONOMY_BACKENDS,
    ),
    _spec(
        "replace_building",
        ReplaceBuildingTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=_ECONOMY_BACKENDS,
    ),
    _spec(
        "build_district",
        BuildDistrictTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=_ECONOMY_BACKENDS,
    ),
    _spec(
        "build_zone",
        BuildZoneTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=_ECONOMY_BACKENDS,
    ),
    _spec(
        "move_fleet",
        MoveFleetTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=(BackendId.SESSION_PROXY, BackendId.NATIVE_RUNTIME),
        broker_enabled_backends=(
            BackendId.SESSION_PROXY,
            BackendId.NATIVE_RUNTIME,
        ),
        backend_verification=(
            (BackendId.SESSION_PROXY, VerificationState.LIVE_VERIFIED),
            (BackendId.NATIVE_RUNTIME, VerificationState.LIVE_VERIFIED),
        ),
        migration_note=None,
    ),
    _spec(
        "move_fleet_to_coordinate",
        MoveFleetToCoordinateTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "attack_fleet",
        AttackFleetTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=(BackendId.SESSION_PROXY, BackendId.NATIVE_RUNTIME),
        broker_enabled_backends=(BackendId.NATIVE_RUNTIME,),
        backend_verification=(
            (BackendId.SESSION_PROXY, VerificationState.PAIRED_CAPTURE),
            (BackendId.NATIVE_RUNTIME, VerificationState.LIVE_VERIFIED),
        ),
        migration_note=(
            "The native runtime path is live verified. The fleet Application "
            "still uses its existing path until its candidate resolver is "
            "migrated to the Broker."
        ),
    ),
    _spec(
        "set_orbital_bombardment_stance",
        BombardmentStanceTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
    ),
    _spec(
        "land_armies",
        LandArmiesTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "recruit_army",
        RecruitArmyTarget,
        ActionRisk.STATE_CHANGE_REQUIRES_FRESH_SAVE,
        VerificationState.LIVE_VERIFIED,
    ),
    _spec(
        "repair_fleet",
        RepairFleetTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "upgrade_fleet",
        UpgradeFleetTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "configure_ship_automation",
        ConfigureShipAutomationTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "build_starbase",
        BuildStarbaseTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "order_colony_ship_and_colonize",
        OrderColonyShipTarget,
        ActionRisk.STATE_CHANGE_REQUIRES_FRESH_SAVE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "colonize_with_existing_ship",
        ColonizeWithExistingShipTarget,
        ActionRisk.STATE_CHANGE_REQUIRES_FRESH_SAVE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "upgrade_starbase",
        UpgradeStarbaseTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "set_starbase_module",
        SetStarbaseComponentTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "set_starbase_building",
        SetStarbaseComponentTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "start_research",
        ResearchTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
        broker_enabled_backends=(BackendId.SESSION_PROXY,),
        migration_note=None,
    ),
    _spec(
        "stop_research",
        ResearchTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.LIVE_VERIFIED,
        supported_backends=(BackendId.SESSION_PROXY, BackendId.NATIVE_RUNTIME),
        broker_enabled_backends=(
            BackendId.SESSION_PROXY,
            BackendId.NATIVE_RUNTIME,
        ),
        backend_verification=(
            (BackendId.SESSION_PROXY, VerificationState.PAIRED_CAPTURE),
            (BackendId.NATIVE_RUNTIME, VerificationState.LIVE_VERIFIED),
        ),
        migration_note=None,
    ),
    _spec(
        "build_ship",
        BuildShipTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "create_ship_design",
        CreateShipDesignTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "create_fleet_template",
        CreateFleetTemplateTarget,
        ActionRisk.STATE_CHANGE_REQUIRES_FRESH_SAVE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "add_fleet_template_ship",
        FleetTemplateShipTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "remove_fleet_template_ship",
        FleetTemplateShipTarget,
        ActionRisk.DESTRUCTIVE_STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "reinforce_selected_fleet",
        ReinforceFleetTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "create_new_fleet",
        CreateNewFleetTarget,
        ActionRisk.STATE_CHANGE_REQUIRES_FRESH_SAVE,
        VerificationState.PAIRED_CAPTURE,
    ),
    _spec(
        "reinforce_fleet_to_target",
        ReinforceFleetToTargetTarget,
        ActionRisk.STATE_CHANGE,
        VerificationState.PAIRED_CAPTURE,
    ),
)


_BUILTIN_ACTION_REGISTRY = ActionRegistry(BUILTIN_ACTION_SPECS)


def builtin_action_registry() -> ActionRegistry:
    """Return the reviewed built-in semantic action registry."""

    return _BUILTIN_ACTION_REGISTRY
