#!/usr/bin/env python3
"""Extract player ship designs and direct shipyard queues from Stellaris saves.

The parser keeps save object handles intact.  In particular, construction item
handles are not treated as simple queue indices because Stellaris reuses an
index with a new generation after cancellation.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from iag.stellaris.game_knowledge import (
    ship_component_catalog,
    ship_section_rule,
    ship_section_rules,
)
from iag.stellaris.state.extract_game_state import (
    construction_items,
    construction_queues,
    resource_values,
)
from iag.stellaris.state.fleet_profiles import (
    bare_scalar,
    integer_scalar,
    integer_values,
    name_hint,
    name_key,
    optional_section,
    player_countries,
    quoted_value,
    starbase_map,
)
from iag.stellaris.state.planet_profiles import (
    find_braced_section,
    load_gamestate,
    parse_numeric_map,
)
from iag.stellaris.state.research_profiles import extract_research_profile

INVALID_OBJECT_ID = 0xFFFFFFFF
SHIPYARD_DESTINATION_TAG_HEX = "0c3a01001400"
DIRECT_MILITARY_SHIP_SIZES = frozenset(
    {
        "corvette",
        "frigate",
        "destroyer",
        "cruiser",
        "battleship",
        "titan",
        "juggernaut",
    }
)
VERIFIED_DESIGN_SHIP_SIZES = DIRECT_MILITARY_SHIP_SIZES
REQUIRED_COMPONENT_SETS = frozenset(
    {
        "power_core",
        "ftl_components",
        "thruster_components",
        "sensor_components",
        "combat_computers",
        "ship_aura_components",
    }
)
DESIGN_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,48}$")
GAME_DATE_RE = re.compile(r'(?m)^date="([^"]+)"\s*$')


def _matching_brace(text: str, open_brace: int) -> int:
    depth = 0
    quoted = False
    escaped = False
    for index in range(open_brace, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("Unclosed Clausewitz object.")


def named_sections(text: str, key: str) -> list[str]:
    """Return repeated braced values while skipping each matched subtree."""
    pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*=\s*\n?\s*\{{")
    output: list[str] = []
    cursor = 0
    while match := pattern.search(text, cursor):
        open_brace = text.find("{", match.start())
        close_brace = _matching_brace(text, open_brace)
        output.append(text[open_brace + 1 : close_brace])
        cursor = close_brace + 1
    return output


def anonymous_sections(text: str) -> list[str]:
    """Return anonymous direct child objects from a list-like section."""
    output: list[str] = []
    index = 0
    quoted = False
    escaped = False
    while index < len(text):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
            index += 1
            continue
        if char != "{":
            index += 1
            continue
        close_brace = _matching_brace(text, index)
        output.append(text[index + 1 : close_brace])
        index = close_brace + 1
    return output


def repeated_quoted(block: str, key: str) -> list[str]:
    return re.findall(
        rf'(?m)^\s*{re.escape(key)}="([^"]*)"\s*$',
        block,
    )


def _component_profile(block: str) -> dict[str, str | None]:
    return {
        "slot": quoted_value(block, "slot"),
        "component_id": quoted_value(block, "template"),
    }


def _section_profile(block: str) -> dict[str, Any]:
    return {
        "template": quoted_value(block, "template"),
        "slot": quoted_value(block, "slot"),
        "components": [
            _component_profile(component)
            for component in named_sections(block, "component")
        ],
    }


def _growth_stage_profile(block: str) -> dict[str, Any]:
    return {
        "ship_size": quoted_value(block, "ship_size"),
        "parent": integer_scalar(block, "parent"),
        "sections": [
            _section_profile(section) for section in named_sections(block, "section")
        ],
        "required_components": repeated_quoted(block, "required_component"),
    }


def _design_profile(design_id: int, block: str) -> dict[str, Any]:
    name = optional_section(block, "name")
    stages = [
        _growth_stage_profile(stage)
        for stage in anonymous_sections(optional_section(block, "growth_stages"))
    ]
    ship_sizes = sorted(
        {str(stage["ship_size"]) for stage in stages if stage.get("ship_size")}
    )
    clone_supported = (
        len(stages) == 1
        and stages[0].get("ship_size") in VERIFIED_DESIGN_SHIP_SIZES
        and bool(stages[0].get("sections"))
    )
    return {
        "design_id": design_id,
        "name_key": quoted_value(name, "key"),
        "display_name_hint": name_hint(block),
        "name_is_literal": bare_scalar(name, "literal") == "yes",
        "auto_generated": bare_scalar(block, "auto_gen_design") == "yes",
        "upgrade_components_automatically": (
            bare_scalar(block, "upgrade_ship_components") == "yes"
        ),
        "obsolete": bare_scalar(block, "obsolete") == "yes",
        "entity": quoted_value(block, "entity"),
        "graphical_culture": quoted_value(block, "graphical_culture"),
        "growth_stages": stages,
        "ship_sizes": ship_sizes,
        "direct_military_construction_supported": bool(ship_sizes)
        and all(size in DIRECT_MILITARY_SHIP_SIZES for size in ship_sizes),
        "clone_protocol_supported": clone_supported,
    }


def _utility_slot_size(
    component_slot: str,
    section_rule: dict[str, Any],
) -> str | None:
    fields = {
        "SMALL_UTILITY_": ("small", "small_utility_slots"),
        "MEDIUM_UTILITY_": ("medium", "medium_utility_slots"),
        "LARGE_UTILITY_": ("large", "large_utility_slots"),
        "AUX_UTILITY_": ("aux", "aux_utility_slots"),
    }
    for prefix, (size, count_field) in fields.items():
        if not component_slot.startswith(prefix):
            continue
        try:
            position = int(component_slot.removeprefix(prefix))
        except ValueError:
            return None
        if 1 <= position <= int(section_rule.get(count_field) or 0):
            return size
    return None


def _component_matches_slot(
    component: dict[str, Any],
    *,
    slot_template: str | None,
    utility_size: str | None,
) -> bool:
    if utility_size is not None:
        return (
            component.get("kind") == "utility"
            and str(component.get("size") or "").lower() == utility_size
            and str(component.get("component_set") or "").lower()
            not in REQUIRED_COMPONENT_SETS
        )
    size = str(component.get("size") or "").lower()
    tags = {str(tag).lower() for tag in component.get("tags", [])}
    weapon_slots = {
        "small_turret": ("small", "s_slot"),
        "medium_turret": ("medium", "m_slot"),
        "large_turret": ("large", "l_slot"),
        "extra_large_turret": ("extra_large", "x_slot"),
        "invisible_extra_large_fixed": ("extra_large", "x_slot"),
        "medium_missile_turret": ("torpedo", "g_slot"),
        "invisible_titanic_fixed": ("titanic", "t_slot"),
    }
    if slot_template in weapon_slots:
        expected_size, expected_tag = weapon_slots[slot_template]
        return (
            component.get("kind") == "weapon"
            and size == expected_size
            and expected_tag in tags
        )
    if slot_template == "point_defence_turret":
        return component.get("kind") == "weapon" and size == "point_defence"
    if slot_template == "large_strike_craft":
        return component.get("kind") == "strike_craft" and size == "large"
    return False


def _technology_options_met(
    rule: dict[str, Any],
    known_technologies: set[str],
    *,
    allow_empty: bool,
) -> bool:
    options = rule.get("prerequisite_options")
    if not isinstance(options, list):
        required = set(rule.get("prerequisites", []))
        return (not required and allow_empty) or required.issubset(known_technologies)
    for option in options:
        required = (
            {str(value) for value in option} if isinstance(option, list) else set()
        )
        if required and required.issubset(known_technologies):
            return True
        if not required and allow_empty:
            return True
    return False


def _declared_component_slots(section_rule: dict[str, Any]) -> dict[str, str | None]:
    slots: dict[str, str | None] = dict(section_rule.get("component_slots", {}))
    utility_fields = (
        ("SMALL_UTILITY_", "small_utility_slots"),
        ("MEDIUM_UTILITY_", "medium_utility_slots"),
        ("LARGE_UTILITY_", "large_utility_slots"),
        ("AUX_UTILITY_", "aux_utility_slots"),
    )
    for prefix, field in utility_fields:
        for index in range(1, int(section_rule.get(field) or 0) + 1):
            slots[f"{prefix}{index}"] = None
    return slots


def _section_roles(
    sections: list[dict[str, Any]],
    game_root: Path,
) -> set[str]:
    templates: set[str] = set()
    for section in sections:
        rule = ship_section_rule(game_root, str(section.get("template") or ""))
        templates.update(
            str(value) for value in rule.get("component_slots", {}).values()
        )
    roles: set[str] = set()
    if "small_turret" in templates:
        roles.add("swarm")
    if "point_defence_turret" in templates:
        roles.add("picket")
    if templates.intersection({"medium_turret", "large_turret"}):
        roles.add("line")
    if templates.intersection(
        {
            "large_turret",
            "extra_large_turret",
            "invisible_extra_large_fixed",
            "invisible_titanic_fixed",
        }
    ):
        roles.add("artillery")
    if "medium_missile_turret" in templates:
        roles.add("torpedo")
    if "large_strike_craft" in templates:
        roles.add("carrier")
    return roles


def _condition_predicate(
    field: str,
    value: str,
    *,
    ship_size: str,
    roles: set[str],
) -> bool | None:
    field = field.lower()
    value = value.lower()
    if field == "country_uses_bio_ships":
        return value == "no"
    if field in {"is_arkship_ship", "is_eager_explorer_ship", "is_waystation_ship"}:
        return value == "no"
    if field == "is_ship_class":
        return value == "shipclass_military"
    if field == "is_ship_size":
        return value == ship_size
    if field in {"ship_uses_mauler_components", "ship_uses_harbinger_components"}:
        return value == "no"
    if field.startswith("ship_uses_space_fauna_"):
        return value == "no"
    if field in {"ship_uses_jump_drives", "ship_uses_military_cloaks"}:
        return value == "yes"
    if field.startswith("ship_uses_"):
        subject = field.removeprefix("ship_uses_")
        for suffix in ("_components", "_reactors", "_thrusters", "_cloaks"):
            if subject.endswith(suffix):
                return (subject.removesuffix(suffix) == ship_size) == (value == "yes")
        if subject.endswith("_role"):
            return (subject.removesuffix("_role") in roles) == (value == "yes")
    if field == "is_galvanic_empire":
        return value == "no"
    return None


def _condition_block_result(
    entries: list[tuple[str, Any]],
    *,
    ship_size: str,
    roles: set[str],
    mode: str = "and",
) -> bool | None:
    results: list[bool | None] = []
    for field, value in entries:
        if field == "__value__":
            results.append(None)
        elif isinstance(value, list):
            upper = field.upper()
            if upper == "OR":
                results.append(
                    _condition_block_result(
                        value,
                        ship_size=ship_size,
                        roles=roles,
                        mode="or",
                    )
                )
            elif upper == "AND":
                results.append(
                    _condition_block_result(
                        value,
                        ship_size=ship_size,
                        roles=roles,
                    )
                )
            elif upper == "NOT":
                nested = _condition_block_result(
                    value,
                    ship_size=ship_size,
                    roles=roles,
                )
                results.append(None if nested is None else not nested)
            elif field.lower() in {"from", "root", "owner"}:
                results.append(
                    _condition_block_result(
                        value,
                        ship_size=ship_size,
                        roles=roles,
                    )
                )
            else:
                results.append(None)
        else:
            results.append(
                _condition_predicate(
                    field,
                    str(value),
                    ship_size=ship_size,
                    roles=roles,
                )
            )
    if not results:
        return True
    if mode == "or":
        if any(result is True for result in results):
            return True
        return None if any(result is None for result in results) else False
    if any(result is False for result in results):
        return False
    return None if any(result is None for result in results) else True


def _component_available_for_ship(
    component: dict[str, Any],
    *,
    known_technologies: set[str],
    ship_size: str,
    roles: set[str],
) -> bool:
    if component.get("hidden", False) or component.get("source_family") != "standard":
        return False
    if not _technology_options_met(
        component,
        known_technologies,
        allow_empty=True,
    ):
        return False
    potentials = component.get("potential_blocks", [])
    if not isinstance(potentials, list):
        return False
    return all(
        _condition_block_result(
            block,
            ship_size=ship_size,
            roles=roles,
        )
        is True
        for block in potentials
        if isinstance(block, list)
    )


def _component_choice_index(
    designs: list[dict[str, Any]],
    *,
    game_root: Path | None = None,
    known_technologies: set[str] | None = None,
) -> list[dict[str, Any]]:
    choices: dict[
        tuple[str, str, str, str],
        dict[str, set[str]],
    ] = {}

    def add_choice(
        key: tuple[str, str, str, str],
        component_id: str,
        authority: str,
    ) -> None:
        choices.setdefault(key, {}).setdefault(component_id, set()).add(authority)

    for design in designs:
        for stage in design["growth_stages"]:
            ship_size = str(stage.get("ship_size") or "")
            for section in stage["sections"]:
                section_template = str(section.get("template") or "")
                section_slot = str(section.get("slot") or "")
                for component in section["components"]:
                    component_slot = str(component.get("slot") or "")
                    component_id = str(component.get("component_id") or "")
                    if not all(
                        (
                            ship_size,
                            section_template,
                            section_slot,
                            component_slot,
                            component_id,
                        )
                    ):
                        continue
                    key = (
                        ship_size,
                        section_template,
                        section_slot,
                        component_slot,
                    )
                    add_choice(key, component_id, "save_observed")

    if game_root is not None:
        known = known_technologies or set()
        component_rules = ship_component_catalog(game_root)
        for design in designs:
            for stage in design["growth_stages"]:
                ship_size = str(stage.get("ship_size") or "")
                roles = _section_roles(stage["sections"], game_root)
                for section in stage["sections"]:
                    section_template = str(section.get("template") or "")
                    section_slot = str(section.get("slot") or "")
                    rule = ship_section_rule(game_root, section_template)
                    if (
                        rule.get("status") != "available"
                        or rule.get("ship_size") != ship_size
                        or rule.get("fits_on_slot") != section_slot
                    ):
                        continue
                    for component_slot, slot_template in _declared_component_slots(
                        rule
                    ).items():
                        key = (
                            ship_size,
                            section_template,
                            section_slot,
                            component_slot,
                        )
                        utility_size = _utility_slot_size(component_slot, rule)
                        for component in component_rules:
                            if _component_available_for_ship(
                                component,
                                known_technologies=known,
                                ship_size=ship_size,
                                roles=roles,
                            ) and _component_matches_slot(
                                component,
                                slot_template=slot_template,
                                utility_size=utility_size,
                            ):
                                add_choice(
                                    key,
                                    str(component["component_id"]),
                                    "installed_rule_and_owned_technology",
                                )
    return [
        {
            "ship_size": key[0],
            "section_template": key[1],
            "section_slot": key[2],
            "component_slot": key[3],
            "component_ids": sorted(component_ids),
            "component_evidence": [
                {
                    "component_id": component_id,
                    "authorities": sorted(authorities),
                }
                for component_id, authorities in sorted(component_ids.items())
            ],
        }
        for key, component_ids in sorted(choices.items())
    ]


def _queue_item_profile(
    item_handle: int,
    block: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "item_handle": item_handle,
        "kind": "unknown",
    }
    if not block:
        return result
    buildable = optional_section(block, "buildable_ship")
    if not buildable:
        return result
    implementation = optional_section(buildable, "ship_design_implementation")
    orbitable = optional_section(buildable, "orbitable")
    result.update(
        {
            "kind": "ship",
            "queue_id": integer_scalar(block, "queue"),
            "paying_country": integer_scalar(block, "paying_country"),
            "progress": _number_scalar(block, "progress"),
            "progress_needed": _number_scalar(block, "progress_needed"),
            "resources": resource_values(optional_section(block, "resources")),
            "design_id": integer_scalar(implementation, "design"),
            "upgrade_id": integer_scalar(implementation, "upgrade"),
            "growth_stage": integer_scalar(implementation, "growth_stage"),
            "starbase_index": integer_scalar(orbitable, "starbase"),
        }
    )
    return result


def _number_scalar(block: str, key: str) -> float | None:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}=(-?\d+(?:\.\d+)?)\s*$",
        block,
    )
    return float(match.group(1)) if match else None


def _system_index(text: str) -> dict[int, dict[str, Any]]:
    systems = parse_numeric_map(find_braced_section(text, "galactic_object").strip())
    output: dict[int, dict[str, Any]] = {}
    for system_id, block in systems.items():
        if not block:
            continue
        for starbase_index in integer_values(block, "starbases"):
            output[starbase_index] = {
                "system_id": system_id,
                "system_name_key": name_key(block),
            }
    return output


def extract_ship_profiles(
    text: str,
    owner: int | None = None,
    *,
    game_root: Path | None = None,
) -> dict[str, Any]:
    """Return player-owned active designs and direct shipyard queues."""
    players = player_countries(text)
    if owner is None:
        if len(players) != 1:
            raise ValueError(
                "Could not infer one player country; pass --owner explicitly."
            )
        owner = players[0]

    countries = parse_numeric_map(find_braced_section(text, "country").strip())
    country = countries.get(owner)
    if country is None:
        raise ValueError(f"Country {owner} does not exist in this save.")

    design_collection = optional_section(country, "ship_design_collection")
    design_ids = integer_values(design_collection, "ship_design")
    design_blocks = parse_numeric_map(find_braced_section(text, "ship_design").strip())
    designs = [
        _design_profile(design_id, block)
        for design_id in design_ids
        if (block := design_blocks.get(design_id)) is not None
    ]
    try:
        known_technologies = set(
            extract_research_profile(text, owner=owner)["known_technologies"]
        )
    except ValueError:
        known_technologies = set()

    queues = construction_queues(text)
    items = construction_items(text)
    starbases = starbase_map(text)
    systems = _system_index(text)
    queue_to_starbase: dict[int, int] = {}
    for starbase_index, starbase_block in starbases.items():
        if not starbase_block:
            continue
        queue_id = integer_scalar(starbase_block, "shipyard_build_queue")
        if queue_id not in (None, INVALID_OBJECT_ID):
            queue_to_starbase[int(queue_id)] = starbase_index

    shipyards: list[dict[str, Any]] = []
    for queue_id, queue in sorted(queues.items()):
        if not queue or integer_scalar(queue, "owner") != owner:
            continue
        referenced_starbase = queue_to_starbase.get(queue_id)
        legacy_ship_queue = bare_scalar(queue, "type") == "ships"
        if referenced_starbase is None and not legacy_ship_queue:
            continue
        location = optional_section(queue, "location")
        if integer_scalar(location, "type") != 0:
            continue
        starbase_index = integer_scalar(location, "id")
        if starbase_index is None:
            continue
        if referenced_starbase is not None and starbase_index != referenced_starbase:
            continue
        starbase = starbases.get(starbase_index) or ""
        queued_handles = integer_values(queue, "items")
        modules = re.findall(
            r"\b\d+=([A-Za-z0-9_]+)", optional_section(starbase, "modules")
        )
        shipyards.append(
            {
                "build_queue_id": queue_id,
                "queue_type": bare_scalar(queue, "type"),
                "queue_link_authority": (
                    "starbase_shipyard_build_queue"
                    if referenced_starbase is not None
                    else "legacy_symbolic_queue_type"
                ),
                "owner_country_id": owner,
                "starbase_index": starbase_index,
                "station_object": integer_scalar(starbase, "station"),
                "starbase_level": quoted_value(starbase, "level"),
                "starbase_type": quoted_value(starbase, "type"),
                "construction_type": bare_scalar(starbase, "construction_type"),
                "modules": modules,
                "shipyard_module_count": sum(
                    module == "shipyard" for module in modules
                ),
                "queue_disabled_flag": bare_scalar(queue, "disabled") == "yes",
                "simultaneous": integer_scalar(queue, "simultaneous"),
                "queue_length": len(queued_handles),
                "queued_items": [
                    _queue_item_profile(handle, items.get(handle))
                    for handle in queued_handles
                ],
                **systems.get(starbase_index, {}),
            }
        )

    date_match = GAME_DATE_RE.search(text)
    return {
        "schema": "iag.ship_state.v1",
        "game_date": date_match.group(1) if date_match else None,
        "owner_country_id": owner,
        "designs": designs,
        "component_choice_index": _component_choice_index(
            designs,
            game_root=game_root,
            known_technologies=known_technologies,
        ),
        "component_choice_authority": (
            "save_observed_plus_installed_rules_and_owned_technology"
            if game_root is not None
            else "save_observed_only"
        ),
        "known_technologies": sorted(known_technologies),
        "shipyards": shipyards,
        "design_protocol_state": "fb2d_multisection_full_blueprint_verified_experimental",
        "construction_protocol_state": "b43d_one_command_per_ship_verified_experimental",
    }


def _owned_design(profile: dict[str, Any], design_id: int) -> dict[str, Any]:
    source = next(
        (
            design
            for design in profile.get("designs", [])
            if int(design["design_id"]) == design_id
        ),
        None,
    )
    if source is None:
        raise ValueError(f"Ship design {design_id} is not player-owned.")
    if not source.get("clone_protocol_supported", False):
        raise ValueError(
            "The verified fb2d path requires one standard military growth stage "
            "with at least one section."
        )
    return source


def _observed_component_choices(
    profile: dict[str, Any],
) -> dict[tuple[str, str, str, str], set[str]]:
    return {
        (
            str(item["ship_size"]),
            str(item["section_template"]),
            str(item["section_slot"]),
            str(item["component_slot"]),
        ): {str(component_id) for component_id in item["component_ids"]}
        for item in profile.get("component_choice_index", [])
    }


def _section_rule_available(
    rule: dict[str, Any],
    *,
    ship_size: str,
    section_slot: str,
    known_technologies: set[str],
) -> bool:
    return (
        rule.get("status") == "available"
        and rule.get("ship_size") == ship_size
        and rule.get("fits_on_slot") == section_slot
        and _technology_options_met(rule, known_technologies, allow_empty=True)
    )


def _component_summary(component: dict[str, Any]) -> dict[str, Any]:
    return {
        "component_id": component["component_id"],
        "kind": component.get("kind"),
        "size": component.get("size"),
        "component_set": component.get("component_set"),
        "ship_behavior": component.get("ship_behavior"),
        "power": component.get("power"),
        "tags": component.get("tags", []),
    }


def ship_design_options(
    profile: dict[str, Any],
    *,
    source_design_id: int,
    game_root: Path,
    section_template: str | None = None,
) -> dict[str, Any]:
    """Return bounded, source-backed options for one player design."""
    source = _owned_design(profile, source_design_id)
    stage = source["growth_stages"][0]
    ship_size = str(stage.get("ship_size") or "")
    known = {str(value) for value in profile.get("known_technologies", [])}
    source_slots = {str(section.get("slot") or "") for section in stage["sections"]}
    rules = [
        rule
        for rule in ship_section_rules(game_root).values()
        if str(rule.get("fits_on_slot") or "") in source_slots
        and _section_rule_available(
            rule,
            ship_size=ship_size,
            section_slot=str(rule.get("fits_on_slot") or ""),
            known_technologies=known,
        )
    ]
    section_options = [
        {
            "section_template": rule["section_template"],
            "section_slot": rule["fits_on_slot"],
            "component_slots": list(_declared_component_slots(rule)),
            "prerequisite_options": rule.get("prerequisite_options", [[]]),
        }
        for rule in sorted(
            rules,
            key=lambda item: (
                str(item.get("fits_on_slot") or ""),
                str(item.get("section_template") or ""),
            ),
        )
    ]

    catalog = ship_component_catalog(game_root)
    catalog_by_id = {str(item["component_id"]): item for item in catalog}
    observed = _observed_component_choices(profile)
    selected: dict[str, Any] | None = None
    required_roles = _section_roles(stage["sections"], game_root)
    if section_template is not None:
        rule = next(
            (
                item
                for item in rules
                if item.get("section_template") == section_template
            ),
            None,
        )
        if rule is None:
            raise ValueError(
                f"Section {section_template!r} is not legal for design {source_design_id}."
            )
        section_slot = str(rule["fits_on_slot"])
        hypothetical_sections = [
            (
                {"template": section_template, "slot": section_slot, "components": []}
                if str(section.get("slot") or "") == section_slot
                else section
            )
            for section in stage["sections"]
        ]
        roles = _section_roles(hypothetical_sections, game_root)
        required_roles = roles
        slots: list[dict[str, Any]] = []
        for component_slot, slot_template in _declared_component_slots(rule).items():
            key = (ship_size, section_template, section_slot, component_slot)
            component_ids = set(observed.get(key, set()))
            utility_size = _utility_slot_size(component_slot, rule)
            component_ids.update(
                str(component["component_id"])
                for component in catalog
                if _component_available_for_ship(
                    component,
                    known_technologies=known,
                    ship_size=ship_size,
                    roles=roles,
                )
                and _component_matches_slot(
                    component,
                    slot_template=slot_template,
                    utility_size=utility_size,
                )
            )
            slots.append(
                {
                    "component_slot": component_slot,
                    "slot_template": slot_template,
                    "may_be_empty": True,
                    "components": [
                        _component_summary(catalog_by_id[component_id])
                        if component_id in catalog_by_id
                        else {
                            "component_id": component_id,
                            "authority": "save_observed",
                        }
                        for component_id in sorted(component_ids)
                    ],
                }
            )
        selected = {
            "section_template": section_template,
            "section_slot": section_slot,
            "component_slots": slots,
        }

    required_options: list[dict[str, Any]] = []
    seen_sets: set[str] = set()
    for component_id in stage.get("required_components", []):
        source_component = catalog_by_id.get(str(component_id))
        component_set = str(
            source_component.get("component_set") if source_component else ""
        )
        if not component_set or component_set in seen_sets:
            continue
        seen_sets.add(component_set)
        candidates = [
            component
            for component in catalog
            if str(component.get("component_set") or "") == component_set
            and _component_available_for_ship(
                component,
                known_technologies=known,
                ship_size=ship_size,
                roles=required_roles,
            )
        ]
        if source_component is not None and source_component not in candidates:
            candidates.append(source_component)
        required_options.append(
            {
                "component_set": component_set,
                "current_component_id": component_id,
                "components": [
                    _component_summary(component)
                    for component in sorted(
                        candidates,
                        key=lambda item: str(item["component_id"]),
                    )
                ],
            }
        )

    return {
        "schema": "iag.ship_design_options.v1",
        "source_design_id": source_design_id,
        "source_name": source.get("name_key"),
        "ship_size": ship_size,
        "current_sections": copy.deepcopy(stage["sections"]),
        "current_required_components": list(stage.get("required_components", [])),
        "upgrade_components_automatically": bool(
            source.get("upgrade_components_automatically", False)
        ),
        "section_options": section_options,
        "selected_section": selected,
        "required_component_options": required_options,
        "authority": "player_save_plus_installed_rules_plus_owned_technology",
    }


def customize_ship_design(
    profile: dict[str, Any],
    *,
    source_design_id: int,
    new_name: str,
    component_replacements: list[dict[str, Any]] | None = None,
    section_replacements: list[dict[str, Any]] | None = None,
    required_component_replacements: list[dict[str, Any]] | None = None,
    upgrade_components_automatically: bool | None = None,
    game_root: Path | None = None,
) -> dict[str, Any]:
    """Create a fully validated blueprint anchored to a player-owned design."""
    if not DESIGN_NAME_RE.fullmatch(new_name):
        raise ValueError(
            "new_name must contain 1-48 ASCII letters, digits, spaces, '.', '_' or '-'."
        )
    designs = {
        int(design["design_id"]): design for design in profile.get("designs", [])
    }
    source = _owned_design(profile, source_design_id)
    existing_names = {str(design.get("name_key") or "") for design in designs.values()}
    if new_name in existing_names:
        raise ValueError(f"A player ship design named {new_name!r} already exists.")

    component_replacements = component_replacements or []
    section_replacements = section_replacements or []
    required_component_replacements = required_component_replacements or []
    if (section_replacements or required_component_replacements) and game_root is None:
        raise ValueError(
            "Section and required-component changes need an installed game root."
        )

    stage = copy.deepcopy(source["growth_stages"][0])
    ship_size = str(stage.get("ship_size") or "")
    known = {str(value) for value in profile.get("known_technologies", [])}
    choices = _observed_component_choices(profile)
    catalog = ship_component_catalog(game_root) if game_root is not None else ()
    catalog_by_id = {str(item["component_id"]): item for item in catalog}
    section_by_slot = {
        str(section.get("slot") or ""): section for section in stage["sections"]
    }
    if len(section_by_slot) != len(stage["sections"]):
        raise ValueError("The source design contains duplicate or empty section slots.")

    changed_sections: set[str] = set()
    applied_sections: list[dict[str, Any]] = []
    for replacement in section_replacements:
        if not isinstance(replacement, dict):
            raise TypeError("Each section replacement must be an object.")
        section_slot = str(replacement.get("section_slot") or "")
        section_template = str(replacement.get("section_template") or "")
        if section_slot in changed_sections:
            raise ValueError(f"Duplicate section replacement for {section_slot}.")
        current = section_by_slot.get(section_slot)
        if current is None:
            raise ValueError(f"The source design has no section slot {section_slot!r}.")
        assert game_root is not None
        rule = ship_section_rule(game_root, section_template)
        if not _section_rule_available(
            rule,
            ship_size=ship_size,
            section_slot=section_slot,
            known_technologies=known,
        ):
            raise ValueError(
                f"Section {section_template!r} is not unlocked and legal for "
                f"{ship_size}/{section_slot}."
            )
        components = replacement.get("components")
        if not isinstance(components, list):
            raise TypeError(
                "A section replacement requires a complete components array."
            )
        normalized_components: list[dict[str, str]] = []
        seen_slots: set[str] = set()
        for component in components:
            if not isinstance(component, dict):
                raise TypeError("Each section component must be an object.")
            component_slot = str(component.get("component_slot") or "")
            component_id = str(component.get("component_id") or "")
            if not component_slot or not component_id:
                raise ValueError(
                    "Section components require component_slot and component_id."
                )
            if component_slot in seen_slots:
                raise ValueError(
                    f"Duplicate component slot in {section_slot}: {component_slot}."
                )
            seen_slots.add(component_slot)
            normalized_components.append(
                {"slot": component_slot, "component_id": component_id}
            )
        replacement_section = {
            "template": section_template,
            "slot": section_slot,
            "components": normalized_components,
        }
        stage["sections"][stage["sections"].index(current)] = replacement_section
        section_by_slot[section_slot] = replacement_section
        changed_sections.add(section_slot)
        applied_sections.append(
            {
                "section_slot": section_slot,
                "from_section_template": current.get("template"),
                "to_section_template": section_template,
                "component_count": len(normalized_components),
            }
        )

    roles = _section_roles(stage["sections"], game_root) if game_root else set()

    def validate_component(
        section: dict[str, Any],
        component_slot: str,
        component_id: str,
    ) -> None:
        section_slot = str(section.get("slot") or "")
        section_template = str(section.get("template") or "")
        key = (ship_size, section_template, section_slot, component_slot)
        if component_id in choices.get(key, set()):
            return
        if game_root is None:
            raise ValueError(
                f"{component_id} is not legal for the save-backed slot "
                f"{section_slot}/{component_slot}."
            )
        rule = ship_section_rule(game_root, section_template)
        slots = _declared_component_slots(rule)
        if component_slot not in slots:
            raise ValueError(
                f"Section {section_template} has no component slot {component_slot}."
            )
        candidate = catalog_by_id.get(component_id)
        if candidate is None or not (
            _component_available_for_ship(
                candidate,
                known_technologies=known,
                ship_size=ship_size,
                roles=roles,
            )
            and _component_matches_slot(
                candidate,
                slot_template=slots[component_slot],
                utility_size=_utility_slot_size(component_slot, rule),
            )
        ):
            raise ValueError(
                f"{component_id} is not unlocked and legal for "
                f"{section_slot}/{component_slot}."
            )

    for section_slot in changed_sections:
        section = section_by_slot[section_slot]
        rule = ship_section_rule(game_root, str(section["template"]))  # type: ignore[arg-type]
        declared = _declared_component_slots(rule)
        for component in section["components"]:
            component_slot = str(component["slot"])
            if component_slot not in declared:
                raise ValueError(
                    f"Section {section['template']} has no component slot {component_slot}."
                )
            validate_component(section, component_slot, str(component["component_id"]))

    changed_components: set[tuple[str, str]] = set()
    applied_components: list[dict[str, Any]] = []
    for replacement in component_replacements:
        if not isinstance(replacement, dict):
            raise TypeError("Each component replacement must be an object.")
        section_slot = str(replacement.get("section_slot") or "")
        component_slot = str(replacement.get("component_slot") or "")
        component_id = str(replacement.get("component_id") or "")
        slot_key = (section_slot, component_slot)
        if slot_key in changed_components:
            raise ValueError(
                f"Duplicate replacement for {section_slot}/{component_slot}."
            )
        section = section_by_slot.get(section_slot)
        if section is None:
            raise ValueError(f"The source design has no section slot {section_slot!r}.")
        validate_component(section, component_slot, component_id)
        component = next(
            (
                item
                for item in section["components"]
                if str(item.get("slot") or "") == component_slot
            ),
            None,
        )
        old_component = str(component.get("component_id") or "") if component else None
        if component_id == old_component:
            raise ValueError(
                f"{section_slot}/{component_slot} already uses {component_id}."
            )
        if component is None:
            section["components"].append(
                {"slot": component_slot, "component_id": component_id}
            )
        else:
            component["component_id"] = component_id
        changed_components.add(slot_key)
        applied_components.append(
            {
                "section_slot": section_slot,
                "component_slot": component_slot,
                "from_component_id": old_component,
                "to_component_id": component_id,
            }
        )

    required = list(stage.get("required_components", []))
    required_by_set: dict[str, tuple[int, str]] = {}
    for index, component_id in enumerate(required):
        component = catalog_by_id.get(str(component_id))
        component_set = str(component.get("component_set") if component else "")
        if component_set:
            required_by_set[component_set] = (index, str(component_id))
    changed_required: set[str] = set()
    applied_required: list[dict[str, str]] = []
    for replacement in required_component_replacements:
        if not isinstance(replacement, dict):
            raise TypeError("Each required-component replacement must be an object.")
        component_set = str(replacement.get("component_set") or "")
        component_id = str(replacement.get("component_id") or "")
        if component_set in changed_required:
            raise ValueError(f"Duplicate required-component set: {component_set}.")
        current = required_by_set.get(component_set)
        if current is None:
            raise ValueError(
                f"The source design has no required component set {component_set!r}."
            )
        candidate = catalog_by_id.get(component_id)
        if (
            candidate is None
            or str(candidate.get("component_set") or "") != component_set
            or not _component_available_for_ship(
                candidate,
                known_technologies=known,
                ship_size=ship_size,
                roles=roles,
            )
        ):
            raise ValueError(
                f"{component_id} is not unlocked and legal for required set "
                f"{component_set}."
            )
        index, old_component = current
        if component_id == old_component:
            raise ValueError(f"{component_set} already uses {component_id}.")
        required[index] = component_id
        changed_required.add(component_set)
        applied_required.append(
            {
                "component_set": component_set,
                "from_component_id": old_component,
                "to_component_id": component_id,
            }
        )
    stage["required_components"] = required

    if game_root is not None:
        for component_id in required:
            component = catalog_by_id.get(str(component_id))
            if component is None or component.get("source_family") != "standard":
                continue
            if not _component_available_for_ship(
                component,
                known_technologies=known,
                ship_size=ship_size,
                roles=roles,
            ):
                raise ValueError(
                    f"Required component {component_id} is incompatible with the "
                    "resulting sections or current technology."
                )

    automatic_upgrade = (
        bool(source.get("upgrade_components_automatically", False))
        if upgrade_components_automatically is None
        else upgrade_components_automatically
    )
    if not isinstance(automatic_upgrade, bool):
        raise TypeError("upgrade_components_automatically must be a boolean.")

    all_component_ids = [
        str(component["component_id"])
        for section in stage["sections"]
        for component in section["components"]
    ] + [str(component_id) for component_id in required]
    unknown_power = [
        component_id
        for component_id in all_component_ids
        if component_id not in catalog_by_id
        or not isinstance(catalog_by_id[component_id].get("power"), (int, float))
    ]
    power_balance: dict[str, Any]
    if unknown_power:
        power_balance = {
            "status": "not_fully_resolved",
            "unknown_component_ids": sorted(set(unknown_power)),
        }
    else:
        total_power = sum(
            float(catalog_by_id[component_id]["power"])
            for component_id in all_component_ids
        )
        if total_power < 0:
            raise ValueError(
                f"The resulting design has insufficient reactor power ({total_power:g})."
            )
        power_balance = {"status": "validated", "remaining_power": total_power}

    blueprint = {
        "source_design_id": source_design_id,
        "name": new_name,
        "graphical_culture": source.get("graphical_culture"),
        "upgrade_components_automatically": automatic_upgrade,
        "growth_stages": [stage],
        "context_822c": int(profile["owner_country_id"]),
        "section_replacements": applied_sections,
        "component_replacements": applied_components,
        "required_component_replacements": applied_required,
        "power_balance": power_balance,
    }
    if source.get("entity"):
        blueprint["entity"] = source["entity"]
    return blueprint


def clone_ship_design(
    profile: dict[str, Any],
    *,
    source_design_id: int,
    new_name: str,
    component_replacements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Backward-compatible wrapper for component-only design cloning."""
    return customize_ship_design(
        profile,
        source_design_id=source_design_id,
        new_name=new_name,
        component_replacements=component_replacements,
    )


def selected_ship_construction(
    profile: dict[str, Any],
    *,
    design_id: int,
    build_queue_id: int,
    quantity: int,
    maximum_quantity: int,
) -> dict[str, Any]:
    if not 1 <= quantity <= maximum_quantity:
        raise ValueError(f"quantity must be between 1 and {maximum_quantity}.")
    design = next(
        (
            item
            for item in profile.get("designs", [])
            if int(item["design_id"]) == design_id
        ),
        None,
    )
    if design is None:
        raise ValueError(f"Ship design {design_id} is not player-owned.")
    if design.get("obsolete"):
        raise ValueError(f"Ship design {design_id} is obsolete.")
    if not design.get("direct_military_construction_supported", False):
        raise ValueError(
            f"Ship design {design_id} is not a supported direct military design."
        )
    shipyard = next(
        (
            item
            for item in profile.get("shipyards", [])
            if int(item["build_queue_id"]) == build_queue_id
        ),
        None,
    )
    if shipyard is None:
        raise ValueError(f"Shipyard queue {build_queue_id} is not player-owned.")
    return {
        "design": design,
        "shipyard": shipyard,
        "quantity": quantity,
        "target": {
            "context_822c": int(profile["owner_country_id"]),
            "build_queue_id": build_queue_id,
            "design_id": design_id,
            "upgrade_id": INVALID_OBJECT_ID,
            "growth_stage": 0,
            "destination_tag_hex": SHIPYARD_DESTINATION_TAG_HEX,
            "destination_object": int(shipyard["starbase_index"]),
        },
    }


def extract_ship_profiles_from_save(
    save_path: Path,
    owner: int | None = None,
    *,
    game_root: Path | None = None,
) -> dict[str, Any]:
    return extract_ship_profiles(
        load_gamestate(save_path),
        owner=owner,
        game_root=game_root,
    )
