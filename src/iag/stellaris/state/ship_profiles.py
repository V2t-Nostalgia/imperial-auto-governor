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
VERIFIED_CLONE_SHIP_SIZES = frozenset({"corvette"})
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
            _section_profile(section)
            for section in named_sections(block, "section")
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
        {
            str(stage["ship_size"])
            for stage in stages
            if stage.get("ship_size")
        }
    )
    clone_supported = (
        len(stages) == 1
        and stages[0].get("ship_size") in VERIFIED_CLONE_SHIP_SIZES
        and len(stages[0].get("sections", [])) == 1
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
            and component.get("size") == utility_size
        )
    if component.get("kind") != "weapon":
        return False
    tags = set(component.get("tags", []))
    if slot_template == "small_turret":
        return component.get("size") == "small" and "s_slot" in tags
    if slot_template == "point_defence_turret":
        return component.get("size") == "point_defence"
    return False


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
        choices.setdefault(key, {}).setdefault(component_id, set()).add(
            authority
        )

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
        component_rules = [
            component
            for component in ship_component_catalog(game_root)
            if not component.get("hidden", False)
            and component.get("source_family") == "standard"
            and bool(component.get("prerequisites"))
            and set(component["prerequisites"]).issubset(known)
            and component.get("potential_policy")
            in {"unrestricted", "regular_ship_or_arkship"}
        ]
        for design in designs:
            for stage in design["growth_stages"]:
                ship_size = str(stage.get("ship_size") or "")
                for section in stage["sections"]:
                    section_template = str(section.get("template") or "")
                    section_slot = str(section.get("slot") or "")
                    rule = ship_section_rule(game_root, section_template)
                    if rule.get("status") != "available":
                        continue
                    explicit_slots = rule.get("component_slots", {})
                    for installed in section["components"]:
                        component_slot = str(installed.get("slot") or "")
                        key = (
                            ship_size,
                            section_template,
                            section_slot,
                            component_slot,
                        )
                        slot_template = explicit_slots.get(component_slot)
                        utility_size = _utility_slot_size(component_slot, rule)
                        for component in component_rules:
                            if _component_matches_slot(
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
        if (
            referenced_starbase is not None
            and starbase_index != referenced_starbase
        ):
            continue
        starbase = starbases.get(starbase_index) or ""
        queued_handles = integer_values(queue, "items")
        modules = re.findall(r"\b\d+=([A-Za-z0-9_]+)", optional_section(starbase, "modules"))
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
                "shipyard_module_count": sum(module == "shipyard" for module in modules),
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
        "design_protocol_state": "fb2d_single_section_corvette_verified_experimental",
        "construction_protocol_state": "b43d_one_command_per_ship_verified_experimental",
    }


def clone_ship_design(
    profile: dict[str, Any],
    *,
    source_design_id: int,
    new_name: str,
    component_replacements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a conservative blueprint from one save-backed design."""
    if not DESIGN_NAME_RE.fullmatch(new_name):
        raise ValueError(
            "new_name must contain 1-48 ASCII letters, digits, spaces, '.', '_' or '-'."
        )
    designs = {
        int(design["design_id"]): design
        for design in profile.get("designs", [])
    }
    source = designs.get(source_design_id)
    if source is None:
        raise ValueError(f"Ship design {source_design_id} is not player-owned.")
    if not source.get("clone_protocol_supported", False):
        raise ValueError(
            "The verified fb2d clone path currently supports one-section corvettes only."
        )
    existing_names = {
        str(design.get("name_key") or "")
        for design in designs.values()
    }
    if new_name in existing_names:
        raise ValueError(f"A player ship design named {new_name!r} already exists.")

    stage = copy.deepcopy(source["growth_stages"][0])
    choices = {
        (
            str(item["ship_size"]),
            str(item["section_template"]),
            str(item["section_slot"]),
            str(item["component_slot"]),
        ): set(item["component_ids"])
        for item in profile.get("component_choice_index", [])
    }
    source_components: dict[tuple[str, str], dict[str, Any]] = {}
    section_templates: dict[str, str] = {}
    for section in stage["sections"]:
        section_slot = str(section.get("slot") or "")
        section_templates[section_slot] = str(section.get("template") or "")
        for component in section["components"]:
            source_components[(section_slot, str(component.get("slot") or ""))] = component

    changed: set[tuple[str, str]] = set()
    applied: list[dict[str, str]] = []
    for replacement in component_replacements:
        section_slot = str(replacement.get("section_slot") or "")
        component_slot = str(replacement.get("component_slot") or "")
        component_id = str(replacement.get("component_id") or "")
        slot_key = (section_slot, component_slot)
        if slot_key in changed:
            raise ValueError(f"Duplicate replacement for {section_slot}/{component_slot}.")
        component = source_components.get(slot_key)
        if component is None:
            raise ValueError(
                f"The source design has no component slot {section_slot}/{component_slot}."
            )
        choice_key = (
            str(stage.get("ship_size") or ""),
            section_templates[section_slot],
            section_slot,
            component_slot,
        )
        if component_id not in choices.get(choice_key, set()):
            raise ValueError(
                f"{component_id} is not legal for the same save-backed slot under "
                "the observed design and installed game rules."
            )
        old_component = str(component.get("component_id") or "")
        if component_id == old_component:
            raise ValueError(
                f"{section_slot}/{component_slot} already uses {component_id}."
            )
        component["component_id"] = component_id
        changed.add(slot_key)
        applied.append(
            {
                "section_slot": section_slot,
                "component_slot": component_slot,
                "from_component_id": old_component,
                "to_component_id": component_id,
            }
        )

    return {
        "source_design_id": source_design_id,
        "name": new_name,
        "entity": "screen",
        "graphical_culture": source.get("graphical_culture"),
        "growth_stages": [stage],
        "context_822c": int(profile["owner_country_id"]),
        "component_replacements": applied,
    }


def selected_ship_construction(
    profile: dict[str, Any],
    *,
    design_id: int,
    build_queue_id: int,
    quantity: int,
    maximum_quantity: int,
) -> dict[str, Any]:
    if not 1 <= quantity <= maximum_quantity:
        raise ValueError(
            f"quantity must be between 1 and {maximum_quantity}."
        )
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
