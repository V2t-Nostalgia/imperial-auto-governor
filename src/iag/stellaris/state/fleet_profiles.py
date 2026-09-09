#!/usr/bin/env python3
"""Extract conservative fleet-movement targets from Stellaris 4.x saves.

The d32c multiplayer move command does not address a destination by its
``galactic_object`` ID.  Captures from Stellaris 4.4.6 show two target forms:

* ``0c3a`` references an entry in ``starbase_mgr.starbases`` when the system
  has a starbase.
* ``132a`` references the system's primary stellar planet when no starbase is
  present.

This module only derives those verified identifiers.  It deliberately does
not infer diplomatic access, path safety, or whether issuing a move is wise.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from statistics import fmean, median
from typing import Any

from iag.stellaris.state.extract_game_state import construction_queues
from iag.stellaris.state.planet_profiles import (
    find_braced_section,
    load_gamestate,
    parse_numeric_map,
)

INVALID_OBJECT_ID = 0xFFFFFFFF
STARBASE_DESTINATION_TAG_HEX = "0c3a01001400"
STELLAR_DESTINATION_TAG_HEX = "132a01001400"
COORDINATE_SCALE = 100_000
MILITARY_SHIP_CLASS = "shipclass_military"
SCIENCE_SHIP_CLASS = "shipclass_science_ship"
CONSTRUCTION_SHIP_CLASS = "shipclass_constructor"
COLONY_SHIP_CLASS = "shipclass_colonizer"

SCIENCE_AUTOMATION_OPTIONS = frozenset(
    {
        "AUTOMATION_COMPLETE_SPECIAL_PROJECTS",
        "AUTOMATION_EXPLORE",
        "AUTOMATION_SURVEY",
        "AUTOMATION_ANOMALIES",
        "AUTOMATION_ASTRAL_RIFTS",
        "AUTOMATION_DIGSITES",
        "AUTOMATION_SEND_GRAVITY_SNARES",
    }
)
CONSTRUCTION_AUTOMATION_OPTIONS = frozenset(
    {
        "AUTOMATION_COMPLETE_SPECIAL_PROJECTS",
        "AUTOMATION_MINING_STATIONS",
        "AUTOMATION_RESEARCH_STATIONS",
        "AUTOMATION_OBSERVATION_POSTS",
    }
)


def optional_section(text: str, name: str) -> str:
    try:
        return find_braced_section(text, name, allow_indent=True)
    except ValueError:
        match = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*\{{", text)
        if not match:
            return ""
        open_brace = text.find("{", match.start())
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
                    return text[open_brace + 1 : index]
        return ""


def integer_scalar(block: str, key: str) -> int | None:
    match = re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(key)}=(-?\d+)(?=\s|\}}|$)",
        block,
    )
    return int(match.group(1)) if match else None


def float_scalar(block: str, key: str) -> float | None:
    """Read a Clausewitz integer or decimal scalar without guessing units."""
    match = re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(key)}=(-?\d+(?:\.\d+)?)(?=\s|\}}|$)",
        block,
    )
    return float(match.group(1)) if match else None


def quoted_value(block: str, key: str) -> str | None:
    match = re.search(
        rf'(?<![A-Za-z0-9_]){re.escape(key)}="([^"]*)"',
        block,
    )
    return match.group(1) if match else None


def integer_values(block: str, key: str) -> list[int]:
    section = optional_section(block, key)
    return [int(value) for value in re.findall(r"\b\d+\b", section)]


def bare_scalar(block: str, key: str) -> str | None:
    match = re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(key)}=([A-Za-z0-9_.:-]+)(?=\s|\}}|$)",
        block,
    )
    return match.group(1) if match else None


def repeated_integer(block: str, key: str) -> list[int]:
    return [
        int(value)
        for value in re.findall(
            rf"(?m)^\s*{re.escape(key)}=(\d+)\s*$",
            block,
        )
    ]


def name_key(block: str) -> str | None:
    return quoted_value(optional_section(block, "name"), "key")


def name_hint(block: str) -> str | None:
    """Return a readable save-backed hint without requiring localization files."""
    name = optional_section(block, "name")
    key = quoted_value(name, "key")
    if not key:
        return None

    def variable(variable_key: str) -> str | None:
        match = re.search(
            rf'key="{re.escape(variable_key)}"\s+value\s*=\s*\{{\s*key="([^"]+)"',
            name,
        )
        return match.group(1) if match else None

    if key == "%SEQ%":
        sequence_format = variable("fmt")
        sequence_number = variable("num")
        if sequence_format and sequence_number:
            return f"{sequence_format} {sequence_number}"
    if key == "PREFIX_NAME_FORMAT":
        nested_name = variable("NAME")
        if nested_name:
            return nested_name
    return key


def coordinate_origin(block: str, section_name: str) -> int | None:
    section = optional_section(block, section_name)
    value = integer_scalar(section, "origin") if section else None
    return None if value in (None, INVALID_OBJECT_ID) else value


def coordinate_profile(block: str, section_name: str) -> dict[str, Any] | None:
    """Read one local-system coordinate, preserving its galactic origin."""
    section = optional_section(block, section_name)
    if not section:
        return None
    origin = integer_scalar(section, "origin")
    x = float_scalar(section, "x")
    y = float_scalar(section, "y")
    if origin in (None, INVALID_OBJECT_ID) or x is None or y is None:
        return None
    return {"x": x, "y": y, "origin": origin}


def coordinate_to_fixed(value: float | str, field: str) -> int:
    """Convert a displayed save coordinate to the protocol's signed i64 unit."""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a finite decimal coordinate.") from error
    if not decimal_value.is_finite():
        raise ValueError(f"{field} must be a finite decimal coordinate.")
    quantized = decimal_value.quantize(
        Decimal("0.00001"),
        rounding=ROUND_HALF_UP,
    )
    fixed = int(quantized * COORDINATE_SCALE)
    if not -(1 << 63) <= fixed < (1 << 63):
        raise ValueError(f"{field} exceeds the signed 64-bit protocol range.")
    return fixed


def player_countries(text: str) -> list[int]:
    players = optional_section(text, "player")
    return sorted({int(value) for value in re.findall(r"\bcountry=(\d+)", players)})


def planet_map(text: str) -> dict[int, str | None]:
    collection = find_braced_section(text, "planets")
    planets = parse_numeric_map(collection.strip())
    if planets:
        return planets
    return parse_numeric_map(
        find_braced_section(collection, "planet", allow_indent=True).strip()
    )


def starbase_map(text: str) -> dict[int, str | None]:
    manager = find_braced_section(text, "starbase_mgr")
    collection = find_braced_section(manager, "starbases", allow_indent=True)
    return parse_numeric_map(collection.strip())


def owned_fleet_ids(country_block: str) -> list[int]:
    manager = optional_section(country_block, "fleets_manager")
    owned = optional_section(manager, "owned_fleets")
    return [int(value) for value in re.findall(r"\bfleet=(\d+)", owned)]


def owned_fleet_template_ids(country_block: str) -> list[int]:
    """Return the save-backed fleet templates owned by one country."""
    manager = optional_section(country_block, "fleet_template_manager")
    return integer_values(manager, "fleet_template")


def council_leader_ids(text: str, owner: int) -> set[int]:
    """Return leaders occupying the selected country's council positions."""
    manager = optional_section(text, "council_positions")
    positions = optional_section(manager, "council_positions") or manager
    if not positions:
        return set()
    result: set[int] = set()
    for block in parse_numeric_map(positions.strip()).values():
        if not block or integer_scalar(block, "country") != owner:
            continue
        leader_id = integer_scalar(block, "leader")
        if leader_id not in (None, INVALID_OBJECT_ID):
            result.add(int(leader_id))
    return result


def anonymous_sections(text: str) -> list[str]:
    """Return anonymous direct-child objects from a Clausewitz list."""
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

        depth = 0
        child_start = index + 1
        child_quoted = False
        child_escaped = False
        while index < len(text):
            nested = text[index]
            if child_quoted:
                if child_escaped:
                    child_escaped = False
                elif nested == "\\":
                    child_escaped = True
                elif nested == '"':
                    child_quoted = False
            elif nested == '"':
                child_quoted = True
            elif nested == "{":
                depth += 1
            elif nested == "}":
                depth -= 1
                if depth == 0:
                    output.append(text[child_start:index])
                    index += 1
                    break
            index += 1
        else:
            raise ValueError("Unclosed anonymous Clausewitz object.")
    return output


def named_sections(text: str, key: str) -> list[str]:
    """Return repeated braced values while skipping each matched subtree."""
    pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*=\s*\n?\s*\{{")
    output: list[str] = []
    cursor = 0
    while match := pattern.search(text, cursor):
        open_brace = text.find("{", match.start())
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
                    output.append(text[open_brace + 1 : index])
                    cursor = index + 1
                    break
        else:
            raise ValueError(f"Unclosed Clausewitz object: {key}")
    return output


def bitset_contains(mask: int | None, object_id: int) -> bool:
    """Return whether a Clausewitz integer bitset contains one object ID."""
    return mask is not None and object_id >= 0 and bool(mask & (1 << object_id))


def hyperlane_neighbors(system_block: str) -> list[int]:
    """Return the explicit neighboring system IDs from save hyperlane edges."""
    neighbors = {
        destination
        for edge in anonymous_sections(optional_section(system_block, "hyperlane"))
        if (destination := integer_scalar(edge, "to")) is not None
        and destination != INVALID_OBJECT_ID
    }
    return sorted(neighbors)


def fleet_template_profile(template_id: int, block: str) -> dict[str, Any]:
    """Parse desired fleet composition without inferring missing counts."""
    designs: list[dict[str, Any]] = []
    design_list = optional_section(block, "fleet_template_design")
    for entry in anonymous_sections(design_list):
        implementation = optional_section(entry, "ship_design_implementation")
        design_id = integer_scalar(implementation, "design")
        if design_id is None:
            continue
        serialized_count = integer_scalar(entry, "count")
        designs.append(
            {
                "design_id": design_id,
                "upgrade_id": integer_scalar(implementation, "upgrade"),
                "growth_stage": integer_scalar(implementation, "growth_stage"),
                # Clausewitz omits the default integer value of one.
                "target_count": (
                    serialized_count if serialized_count is not None else 1
                ),
                "target_count_was_omitted": serialized_count is None,
            }
        )

    home_base = optional_section(block, "home_base")
    orbitable = optional_section(home_base, "orbitable")
    return {
        "fleet_template_id": template_id,
        "fleet_id": integer_scalar(block, "fleet"),
        "home_starbase_index": integer_scalar(orbitable, "starbase"),
        "design_targets": designs,
        "queued_item_handles": integer_values(block, "all_queued"),
        "fleet_size": integer_scalar(block, "fleet_size"),
        "is_edited_by_human": bare_scalar(block, "is_edited_by_human") == "yes",
    }


def actual_design_counts(
    ship_ids: list[int],
    ships: dict[int, str | None],
) -> dict[int, int]:
    """Count currently existing ships by their save-backed design ID."""
    counts: Counter[int] = Counter()
    for ship_id in ship_ids:
        ship = ships.get(ship_id) or ""
        implementation = optional_section(ship, "ship_design_implementation")
        design_id = integer_scalar(implementation, "design")
        if design_id is not None:
            counts[design_id] += 1
    return dict(sorted(counts.items()))


def ship_durability_summary(
    ship_ids: list[int],
    ships: dict[int, str | None],
) -> dict[str, Any]:
    """Aggregate remaining durability without exposing per-ship records.

    Stellaris serializes current and maximum values on each ship. A missing
    current scalar with a positive maximum represents the default numeric
    value zero, while a missing or non-positive maximum means that the ship
    does not have that durability layer and is excluded from that layer's
    distribution.
    """

    field_pairs = {
        "hull": ("hitpoints", "max_hitpoints"),
        "armor": ("armor_hitpoints", "max_armor_hitpoints"),
        "shield": ("shield_hitpoints", "max_shield_hitpoints"),
    }
    values: dict[str, list[float]] = {key: [] for key in field_pairs}
    for ship_id in ship_ids:
        block = ships.get(ship_id)
        if not block:
            continue
        for layer, (current_field, maximum_field) in field_pairs.items():
            maximum = float_scalar(block, maximum_field)
            if maximum is None or maximum <= 0:
                continue
            current = float_scalar(block, current_field)
            remaining = 0.0 if current is None else current
            percent = max(0.0, min(100.0, remaining / maximum * 100.0))
            values[layer].append(percent)

    def aggregate(samples: list[float]) -> dict[str, int | float | None]:
        if not samples:
            return {
                "mean_percent": None,
                "median_percent": None,
                "sample_count": 0,
            }
        return {
            "mean_percent": round(fmean(samples), 2),
            "median_percent": round(float(median(samples)), 2),
            "sample_count": len(samples),
        }

    return {
        "basis": "per_ship_current_divided_by_max_percent",
        **{layer: aggregate(samples) for layer, samples in values.items()},
    }


def fleet_leader_ids(
    ship_ids: list[int],
    ships: dict[int, str | None],
) -> list[int]:
    """Resolve assigned leaders from the fleet's ship entities."""
    return sorted(
        {
            int(leader_id)
            for ship_id in ship_ids
            if (block := ships.get(ship_id))
            and (leader_id := integer_scalar(block, "leader"))
            not in (None, INVALID_OBJECT_ID)
        }
    )


def upgradeable_ship_count(
    ship_ids: list[int],
    ships: dict[int, str | None],
) -> int:
    """Count ships whose save-backed implementation names an upgrade design."""
    count = 0
    for ship_id in ship_ids:
        implementation = optional_section(
            ships.get(ship_id) or "",
            "ship_design_implementation",
        )
        upgrade_id = integer_scalar(implementation, "upgrade")
        if upgrade_id not in (None, INVALID_OBJECT_ID):
            count += 1
    return count


def durability_requires_repair(summary: dict[str, Any]) -> bool:
    """Return whether any observed durability layer is below full strength."""
    return any(
        layer.get("sample_count", 0) > 0
        and layer.get("mean_percent") is not None
        and float(layer["mean_percent"]) < 100.0
        for layer in (summary.get(name, {}) for name in ("hull", "armor", "shield"))
    )


def movement_profile(fleet_block: str) -> dict[str, Any]:
    manager = optional_section(fleet_block, "movement_manager")
    coordinate = optional_section(manager, "coordinate")
    target = optional_section(manager, "target")
    current_coordinate = coordinate_profile(manager, "coordinate")
    target_coordinate = coordinate_profile(manager, "target_coordinate")
    if target_coordinate is None:
        target_coordinate = coordinate_profile(target, "coordinate")
    orbit = optional_section(manager, "orbit")
    orbitable = optional_section(orbit, "orbitable")
    current_system = integer_scalar(coordinate, "origin")
    target_system = (
        int(target_coordinate["origin"])
        if target_coordinate is not None
        else None
    )
    return {
        "state": bare_scalar(manager, "state"),
        "current_coordinate": current_coordinate,
        "target_coordinate": target_coordinate,
        "current_system_id": (
            None if current_system in (None, INVALID_OBJECT_ID) else current_system
        ),
        "target_system_id": (
            None if target_system in (None, INVALID_OBJECT_ID) else target_system
        ),
        "orbit_starbase_index": integer_scalar(orbitable, "starbase"),
    }


def fleet_availability(
    *,
    ship_class: str | None,
    ship_ids: list[int],
    mobile: bool,
    valid_for_combat: bool,
    movement: dict[str, Any],
    mia_origin: int | None,
    combat_fleet_ids: list[int],
) -> tuple[str, list[str]]:
    """Return a conservative callable state and machine-readable reasons."""
    reasons: list[str] = []
    if not ship_ids:
        return "UNAVAILABLE", ["fleet_has_no_ships"]
    if mia_origin is not None:
        return "MIA", ["mia_from_is_valid"]
    if combat_fleet_ids:
        return "UNCERTAIN", ["fleet_is_in_combat"]
    if not mobile:
        return "UNAVAILABLE", ["fleet_is_not_mobile"]
    if not valid_for_combat:
        return "UNAVAILABLE", ["valid_for_combat_is_false"]
    if ship_class != MILITARY_SHIP_CLASS:
        return "UNAVAILABLE", ["not_a_verified_military_fleet"]

    movement_state = str(movement.get("state") or "")
    if movement_state and movement_state not in {
        "move_idle",
        "move_orbit",
        "move_formation",
    }:
        reasons.append(f"movement_state:{movement_state}")
        return "BUSY", reasons
    return "AVAILABLE", reasons


def civilian_fleet_availability(
    *,
    ship_class: str | None,
    ship_ids: list[int],
    mobile: bool,
    movement: dict[str, Any],
    mia_origin: int | None,
    combat_fleet_ids: list[int],
    has_current_order: bool,
) -> tuple[str, list[str]]:
    """Return a conservative callable state for verified civilian ships."""
    verified = {
        SCIENCE_SHIP_CLASS,
        CONSTRUCTION_SHIP_CLASS,
        COLONY_SHIP_CLASS,
    }
    if ship_class not in verified:
        return "UNAVAILABLE", ["not_a_verified_civilian_ship"]
    if not ship_ids:
        return "UNAVAILABLE", ["fleet_has_no_ships"]
    if mia_origin is not None:
        return "MIA", ["mia_from_is_valid"]
    if combat_fleet_ids:
        return "UNCERTAIN", ["fleet_is_in_combat"]
    if not mobile:
        return "UNAVAILABLE", ["fleet_is_not_mobile"]
    if has_current_order:
        return "BUSY", ["fleet_has_current_order"]
    movement_state = str(movement.get("state") or "")
    if movement_state and movement_state not in {
        "move_idle",
        "move_orbit",
        "move_formation",
    }:
        return "BUSY", [f"movement_state:{movement_state}"]
    return "AVAILABLE", []


def _fleet_coordinate(block: str) -> dict[str, Any] | None:
    movement = movement_profile(block).get("current_coordinate")
    if movement is not None:
        return movement
    return coordinate_profile(optional_section(block, "combat"), "coordinate")


def _coordinates_match(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> bool:
    if left is None or right is None:
        return False
    return (
        int(left["origin"]) == int(right["origin"])
        and math.isclose(float(left["x"]), float(right["x"]), abs_tol=0.00002)
        and math.isclose(float(left["y"]), float(right["y"]), abs_tol=0.00002)
    )


def hostile_fleet_targets(
    *,
    owner: int,
    country_block: str,
    countries: dict[int, str | None],
    fleets: dict[int, str | None],
) -> list[dict[str, Any]]:
    """Resolve current hostile intel to unique global fleet object IDs.

    A host save contains fleets that the player has not discovered.  Exposing
    that global collection would leak hidden information, so targets enter the
    profile only when the selected country's ``intel.hostile`` observation
    matches exactly one live fleet by owner, current coordinate and power.
    """
    fleet_owners: dict[int, int] = {}
    ambiguous_owner_ids: set[int] = set()
    for country_id, block in countries.items():
        if not block:
            continue
        for fleet_id in owned_fleet_ids(block):
            previous = fleet_owners.get(fleet_id)
            if previous is not None and previous != country_id:
                ambiguous_owner_ids.add(fleet_id)
            else:
                fleet_owners[fleet_id] = country_id
    for fleet_id in ambiguous_owner_ids:
        fleet_owners.pop(fleet_id, None)

    candidates: list[dict[str, Any]] = []
    for fleet_id, block in fleets.items():
        if not block:
            continue
        fleet_owner = fleet_owners.get(fleet_id)
        if fleet_owner is None or fleet_owner == owner:
            continue
        settings = optional_section(block, "settings")
        ship_ids = integer_values(block, "ships")
        coordinate = _fleet_coordinate(block)
        if (
            not ship_ids
            or coordinate is None
            or bare_scalar(settings, "valid_for_combat") != "yes"
        ):
            continue
        candidates.append(
            {
                "fleet_id": fleet_id,
                "owner_country_id": fleet_owner,
                "name_key": name_key(block),
                "display_name_hint": name_hint(block),
                "ship_class": bare_scalar(block, "ship_class"),
                "ship_count": len(ship_ids),
                "military_power": float_scalar(block, "military_power"),
                "coordinate": coordinate,
            }
        )

    resolved: dict[int, dict[str, Any]] = {}
    intel = optional_section(country_block, "intel")
    for system_observation in anonymous_sections(intel):
        observed_system = integer_scalar(system_observation, "object")
        hostile = optional_section(system_observation, "hostile")
        for observation in anonymous_sections(hostile):
            observed_owner = integer_scalar(observation, "owner")
            observed_coordinate = coordinate_profile(observation, "coordinate")
            observed_power = float_scalar(observation, "military_power")
            if (
                observed_owner is None
                or observed_owner == owner
                or observed_coordinate is None
                or observed_power is None
                or observed_power <= 0
            ):
                continue
            matches = [
                candidate
                for candidate in candidates
                if candidate["owner_country_id"] == observed_owner
                and _coordinates_match(
                    candidate.get("coordinate"),
                    observed_coordinate,
                )
            ]
            if len(matches) > 1:
                matches = [
                    candidate
                    for candidate in matches
                    if candidate.get("military_power") is not None
                    and math.isclose(
                        float(candidate["military_power"]),
                        observed_power,
                        abs_tol=0.02,
                    )
                ]
            if len(matches) != 1:
                continue
            target = matches[0]
            fleet_id = int(target["fleet_id"])
            resolved[fleet_id] = {
                **target,
                "system_id": observed_system,
                "intel_name_key": name_key(observation),
                "intel_display_name_hint": name_hint(observation),
                "intel_military_power": observed_power,
                "target_authority": "player_hostile_intel_exact_live_match",
            }
    return [resolved[fleet_id] for fleet_id in sorted(resolved)]


def system_destination(
    system_block: str,
    planets: dict[int, str | None],
    starbases: dict[int, str | None],
) -> dict[str, Any] | None:
    system_planets = repeated_integer(system_block, "planet")
    starbase_indices = [
        value
        for value in integer_values(system_block, "starbases")
        if value != INVALID_OBJECT_ID
    ]
    primary_star_id = next(
        (
            planet_id
            for planet_id in system_planets
            if quoted_value(planets.get(planet_id) or "", "planet_class")
            and quoted_value(planets.get(planet_id) or "", "planet_class").endswith(
                "_star"
            )
        ),
        None,
    )

    if len(starbase_indices) == 1:
        starbase_index = starbase_indices[0]
        starbase = starbases.get(starbase_index)
        if starbase is None:
            return None
        return {
            "destination_tag_hex": STARBASE_DESTINATION_TAG_HEX,
            "destination_object": starbase_index,
            "destination_kind": "starbase_manager_entry",
            "starbase_level": quoted_value(starbase, "level"),
            "station_object": integer_scalar(starbase, "station"),
        }
    if len(starbase_indices) > 1 or primary_star_id is None:
        return None
    return {
        "destination_tag_hex": STELLAR_DESTINATION_TAG_HEX,
        "destination_object": primary_star_id,
        "destination_kind": "primary_stellar_planet",
        "planet_class": quoted_value(planets[primary_star_id] or "", "planet_class"),
    }


def extract_fleet_profiles(text: str, owner: int | None = None) -> dict[str, Any]:
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

    fleets = parse_numeric_map(find_braced_section(text, "fleet").strip())
    ships = parse_numeric_map(find_braced_section(text, "ships").strip())
    template_blocks = parse_numeric_map(
        find_braced_section(text, "fleet_template").strip()
    )
    templates = [
        fleet_template_profile(template_id, block)
        for template_id in owned_fleet_template_ids(country)
        if (block := template_blocks.get(template_id)) is not None
    ]
    templates_by_id = {
        int(template["fleet_template_id"]): template for template in templates
    }
    systems = parse_numeric_map(find_braced_section(text, "galactic_object").strip())
    planets = planet_map(text)
    starbases = starbase_map(text)
    queues = construction_queues(text)
    council_leaders = council_leader_ids(text, owner)

    owned_fleet_id_values = owned_fleet_ids(country)
    owned_fleet_id_set = set(owned_fleet_id_values)
    owned_planet_ids = set(integer_values(country, "owned_planets"))
    def queue_belongs_to_owner(starbase_block: str, key: str) -> bool:
        queue_id = integer_scalar(starbase_block, key)
        if queue_id in (None, INVALID_OBJECT_ID):
            return False
        queue_block = queues.get(int(queue_id))
        return bool(queue_block) and integer_scalar(queue_block, "owner") == owner

    owned_starbase_indices = {
        starbase_index
        for starbase_index, starbase_block in starbases.items()
        if starbase_block
        and (
            integer_scalar(starbase_block, "station") in owned_fleet_id_set
            or queue_belongs_to_owner(starbase_block, "build_queue")
            or queue_belongs_to_owner(
                starbase_block,
                "shipyard_build_queue",
            )
        )
    }
    starbase_to_system = {
        starbase_index: system_id
        for system_id, system_block in systems.items()
        if system_block
        for starbase_index in integer_values(system_block, "starbases")
        if starbase_index != INVALID_OBJECT_ID
    }
    shipyard_output: list[dict[str, Any]] = []
    for starbase_index in sorted(owned_starbase_indices):
        starbase_block = starbases.get(starbase_index) or ""
        module_block = optional_section(starbase_block, "modules")
        shipyard_count = len(
            re.findall(
                r"(?<![A-Za-z0-9_])\d+=shipyard(?=\s|$)",
                module_block,
            )
        )
        queue_id = integer_scalar(starbase_block, "shipyard_build_queue")
        queue_block = queues.get(int(queue_id)) if queue_id is not None else None
        if (
            shipyard_count < 1
            or queue_id in (None, INVALID_OBJECT_ID)
            or not queue_block
            or integer_scalar(queue_block, "owner") != owner
        ):
            continue
        system_id = starbase_to_system.get(starbase_index)
        system_block = systems.get(system_id) or ""
        queue_items = integer_values(queue_block, "items")
        shipyard_output.append(
            {
                "starbase_index": starbase_index,
                "station_object": integer_scalar(starbase_block, "station"),
                "system_id": system_id,
                "system_name_key": name_key(system_block),
                "system_display_name_hint": name_hint(system_block),
                "starbase_level": quoted_value(starbase_block, "level"),
                "shipyard_count": shipyard_count,
                "shipyard_build_queue_id": int(queue_id),
                "queue_item_handles": queue_items,
                "queue_length": len(queue_items),
            }
        )

    fleet_output: list[dict[str, Any]] = []
    for fleet_id in owned_fleet_id_values:
        block = fleets.get(fleet_id)
        if block is None:
            continue
        settings = optional_section(block, "settings")
        ship_class = bare_scalar(block, "ship_class")
        mobile = bare_scalar(settings, "mobile") == "yes"
        valid_for_combat = bare_scalar(settings, "valid_for_combat") == "yes"
        stationary_installation = bare_scalar(settings, "station") == "yes"
        ship_ids = integer_values(block, "ships")
        fleet_template_id = integer_scalar(block, "fleet_template")
        template = (
            templates_by_id.get(fleet_template_id)
            if fleet_template_id is not None
            else None
        )
        existing_counts = actual_design_counts(ship_ids, ships)
        composition: list[dict[str, Any]] = []
        if template is not None:
            for target in template["design_targets"]:
                design_id = int(target["design_id"])
                target_count = target["target_count"]
                current_count = existing_counts.get(design_id, 0)
                composition.append(
                    {
                        **target,
                        "current_count": current_count,
                        "missing_count": (
                            max(0, int(target_count) - current_count)
                            if target_count is not None
                            else None
                        ),
                    }
                )
        targeted_design_ids = {
            int(item["design_id"]) for item in composition
        }
        composition.extend(
            {
                "design_id": design_id,
                "upgrade_id": None,
                "growth_stage": None,
                "target_count": None,
                "target_count_was_omitted": False,
                "current_count": current_count,
                "missing_count": None,
            }
            for design_id, current_count in existing_counts.items()
            if design_id not in targeted_design_ids
        )
        movement = movement_profile(block)
        mia_origin = coordinate_origin(block, "mia_from")
        combat_fleet_ids = integer_values(block, "in_combat_with")
        has_current_order = bool(optional_section(block, "current_order"))
        durability_summary = ship_durability_summary(ship_ids, ships)
        leader_ids = fleet_leader_ids(ship_ids, ships)
        has_council_leader = any(
            leader_id in council_leaders for leader_id in leader_ids
        )
        upgradeable_count = upgradeable_ship_count(ship_ids, ships)
        availability, availability_reasons = fleet_availability(
            ship_class=ship_class,
            ship_ids=ship_ids,
            mobile=mobile,
            valid_for_combat=valid_for_combat,
            movement=movement,
            mia_origin=mia_origin,
            combat_fleet_ids=combat_fleet_ids,
        )
        civilian_availability, civilian_availability_reasons = (
            civilian_fleet_availability(
                ship_class=ship_class,
                ship_ids=ship_ids,
                mobile=mobile,
                movement=movement,
                mia_origin=mia_origin,
                combat_fleet_ids=combat_fleet_ids,
                has_current_order=has_current_order,
            )
        )
        civilian_role = {
            SCIENCE_SHIP_CLASS: "science",
            CONSTRUCTION_SHIP_CLASS: "construction",
            COLONY_SHIP_CLASS: "colony",
        }.get(ship_class)
        verified_automation_options = (
            set(SCIENCE_AUTOMATION_OPTIONS)
            if ship_class == SCIENCE_SHIP_CLASS
            else (
                set(CONSTRUCTION_AUTOMATION_OPTIONS)
                if ship_class == CONSTRUCTION_SHIP_CLASS
                else set()
            )
        )
        if has_council_leader:
            verified_automation_options.discard("AUTOMATION_ASTRAL_RIFTS")
        fleet_output.append(
            {
                "fleet_id": fleet_id,
                "name_key": name_key(block),
                "display_name_hint": name_hint(block),
                "fleet_template_id": fleet_template_id,
                "fleet_composition": composition,
                "reinforcement_queue_item_handles": (
                    list(template["queued_item_handles"])
                    if template is not None
                    else []
                ),
                "ship_class": ship_class,
                "ship_ids": ship_ids,
                "ship_count": len(ship_ids),
                "durability_summary": durability_summary,
                "needs_repair": durability_requires_repair(durability_summary),
                "upgradeable_ship_count": upgradeable_count,
                "leader_ids": leader_ids,
                "has_council_leader": has_council_leader,
                "can_automate_astral_rifts": (
                    ship_class == SCIENCE_SHIP_CLASS
                    and not has_council_leader
                ),
                "verified_automation_options": sorted(
                    verified_automation_options
                ),
                "military_power": float_scalar(block, "military_power"),
                "mobile": mobile,
                "valid_for_combat": valid_for_combat,
                "stationary_installation": stationary_installation,
                "player_controllable": not stationary_installation,
                "d32c_move_verified_family": (
                    mobile and ship_class == MILITARY_SHIP_CLASS
                ),
                "coordinate_move_verified_family": (
                    mobile and ship_class == MILITARY_SHIP_CLASS
                ),
                "attack_verified_family": (
                    mobile and ship_class == MILITARY_SHIP_CLASS
                ),
                "repair_verified_family": (
                    mobile and ship_class == MILITARY_SHIP_CLASS
                ),
                "upgrade_verified_family": (
                    mobile and ship_class == MILITARY_SHIP_CLASS
                ),
                "civilian_role": civilian_role,
                "automation_verified_family": ship_class
                in {SCIENCE_SHIP_CLASS, CONSTRUCTION_SHIP_CLASS},
                "build_starbase_verified_family": (
                    ship_class == CONSTRUCTION_SHIP_CLASS
                ),
                "existing_colonization_verified_family": (
                    ship_class == COLONY_SHIP_CLASS
                ),
                "movement": movement,
                "mia_from_system_id": mia_origin,
                "in_combat_with": combat_fleet_ids,
                "has_current_order": has_current_order,
                "availability": availability,
                "availability_reasons": availability_reasons,
                "ai_callable_now": availability == "AVAILABLE",
                "maintenance_callable_now": (
                    availability == "AVAILABLE" and not has_current_order
                ),
                "civilian_availability": civilian_availability,
                "civilian_availability_reasons": (
                    civilian_availability_reasons
                ),
                "civilian_callable_now": (
                    civilian_availability == "AVAILABLE"
                ),
            }
        )

    # 4f2c cannot target a different system. Retaining coordinates for every
    # object in the galaxy would only enlarge the model context and expose
    # destinations that the validator must reject, so keep targets for systems
    # currently occupied by an owned fleet.
    coordinate_system_ids = {
        int(system_id)
        for fleet in fleet_output
        if fleet.get("coordinate_move_verified_family", False)
        and (system_id := fleet["movement"].get("current_system_id")) is not None
    }
    system_output: list[dict[str, Any]] = []
    for system_id, block in sorted(systems.items()):
        if block is None:
            continue
        planet_ids = repeated_integer(block, "planet")
        discovery = integer_values(block, "discovery")
        starbase_indices = [
            value
            for value in integer_values(block, "starbases")
            if value != INVALID_OBJECT_ID
        ]
        owned_colonies = sorted(owned_planet_ids.intersection(planet_ids))
        owned_starbases = sorted(
            owned_starbase_indices.intersection(starbase_indices)
        )
        primary_stellar_planet_id = next(
            (
                planet_id
                for planet_id in planet_ids
                if quoted_value(planets.get(planet_id) or "", "planet_class")
                and quoted_value(
                    planets.get(planet_id) or "",
                    "planet_class",
                ).endswith("_star")
            ),
            None,
        )
        surveyed_planet_ids = [
            planet_id
            for planet_id in planet_ids
            if bitset_contains(
                integer_scalar(planets.get(planet_id) or "", "surveyed_by"),
                owner,
            )
        ]
        ownership_evidence = []
        if owned_colonies:
            ownership_evidence.append("owned_colony")
        if owned_starbases:
            ownership_evidence.append("owned_starbase")
        system_output.append(
            {
                "system_id": system_id,
                "name_key": name_key(block),
                "display_name_hint": name_hint(block),
                "star_class": quoted_value(block, "star_class"),
                "planet_ids": planet_ids,
                "primary_stellar_planet_id": primary_stellar_planet_id,
                "surveyed_planet_ids": surveyed_planet_ids,
                "fully_surveyed_by_owner": bool(planet_ids)
                and len(surveyed_planet_ids) == len(planet_ids),
                "starbase_indices": starbase_indices,
                "discovered_by_owner": owner in discovery,
                "hyperlane_neighbors": hyperlane_neighbors(block),
                "owned_by_owner": bool(ownership_evidence),
                "owned_colony_ids": owned_colonies,
                "owned_starbase_indices": owned_starbases,
                "ownership_evidence": ownership_evidence,
                "move_destination": system_destination(block, planets, starbases),
                "construction_target": (
                    {
                        "target_system_object": system_id,
                        "target_kind": "galactic_object",
                        "target_authority": (
                            "paired_e02c_target_system_mapping"
                        ),
                    }
                    if owner in discovery
                    and not starbase_indices
                    and not ownership_evidence
                    and bool(planet_ids)
                    and len(surveyed_planet_ids) == len(planet_ids)
                    else None
                ),
                "coordinate_targets": [
                    {
                        "kind": "planet",
                        "object_id": planet_id,
                        "name_key": name_key(planets.get(planet_id) or ""),
                        "planet_class": quoted_value(
                            planets.get(planet_id) or "",
                            "planet_class",
                        ),
                        "coordinate": coordinate_profile(
                            planets.get(planet_id) or "",
                            "coordinate",
                        ),
                    }
                    for planet_id in planet_ids
                    if system_id in coordinate_system_ids
                    and coordinate_profile(
                        planets.get(planet_id) or "",
                        "coordinate",
                    )
                    is not None
                ],
            }
        )

    hostile_targets = hostile_fleet_targets(
        owner=owner,
        country_block=country,
        countries=countries,
        fleets=fleets,
    )
    return {
        "schema": "iag.stellaris_fleet_state.v1",
        "schema_version": 1,
        "game_date": quoted_value(text, "date"),
        "owner_country_id": owner,
        "player_country_ids": players,
        "player_ship_design_ids": integer_values(
            optional_section(country, "ship_design_collection"),
            "ship_design",
        ),
        "fleets": fleet_output,
        "hostile_targets": hostile_targets,
        "fleet_templates": templates,
        "shipyards": shipyard_output,
        "systems": system_output,
    }


def selected_fleet_reinforcement(
    profile: dict[str, Any],
    *,
    fleet_id: int,
    design_id: int,
    target_count: int,
    maximum_target_increase: int,
) -> dict[str, Any]:
    """Validate one exact Fleet Manager target and reinforcement operation."""
    fleet = next(
        (item for item in profile["fleets"] if item["fleet_id"] == fleet_id),
        None,
    )
    if fleet is None:
        raise ValueError(f"Fleet {fleet_id} is not owned by the selected country.")
    if fleet.get("ship_class") != "shipclass_military" or not fleet.get(
        "player_controllable", False
    ):
        raise ValueError(f"Fleet {fleet_id} is not a controllable military fleet.")
    fleet_template_id = fleet.get("fleet_template_id")
    if fleet_template_id is None:
        raise ValueError(f"Fleet {fleet_id} has no save-backed fleet template.")
    if design_id not in profile.get("player_ship_design_ids", []):
        raise ValueError(f"Ship design {design_id} is not player-owned.")
    if not 1 <= target_count <= 999:
        raise ValueError("target_count must be between 1 and 999.")
    if not 1 <= maximum_target_increase <= 999:
        raise ValueError("maximum_target_increase must be between 1 and 999.")
    if fleet.get("reinforcement_queue_item_handles"):
        raise ValueError(
            f"Fleet {fleet_id} already has save-backed reinforcement items queued."
        )

    row = next(
        (
            item
            for item in fleet.get("fleet_composition", [])
            if int(item["design_id"]) == design_id
        ),
        None,
    )
    previous_target = int(row["target_count"]) if row is not None else 0
    current_count = int(row["current_count"]) if row is not None else 0
    if target_count < previous_target:
        raise ValueError(
            "The first reinforcement release only permits increasing a template target."
        )
    increase = target_count - previous_target
    if increase > maximum_target_increase:
        raise ValueError(
            "The requested target increase exceeds the player-configured limit."
        )
    if increase == 0 and current_count >= target_count:
        raise ValueError("The fleet already meets the requested target count.")

    target = {
        "context_822c": int(profile["owner_country_id"]),
        "fleet_template_id": int(fleet_template_id),
        "design_id": design_id,
        "upgrade_id": (
            int(row["upgrade_id"])
            if row is not None and row.get("upgrade_id") is not None
            else 0xFFFFFFFF
        ),
        "growth_stage": (
            int(row["growth_stage"])
            if row is not None and row.get("growth_stage") is not None
            else 0
        ),
    }
    return {
        "action": "reinforce_fleet_to_target",
        "fleet": {
            "fleet_id": fleet_id,
            "name_key": fleet.get("name_key"),
            "fleet_template_id": int(fleet_template_id),
        },
        "design_id": design_id,
        "previous_target_count": previous_target,
        "requested_target_count": target_count,
        "current_count": current_count,
        "target_increase": increase,
        "expected_ship_shortfall": max(0, target_count - current_count),
        "template_edit_target": target,
        "reinforcement_target": {
            "context_822c": target["context_822c"],
            "fleet_template_id": target["fleet_template_id"],
        },
        "protocol_sequence": [
            *(["add_fleet_template_ship"] * increase),
            *(["reinforce_selected_fleet"] if current_count < target_count else []),
        ],
    }


def resolve_created_fleet_template(
    profile: dict[str, Any],
    *,
    baseline_template_ids: list[int],
    expected_template_id: int | None = None,
) -> dict[str, Any]:
    """Resolve one newly created owned template without predicting its ID."""
    baseline = {int(value) for value in baseline_template_ids}
    templates = {
        int(item["fleet_template_id"]): item
        for item in profile.get("fleet_templates", [])
    }
    if expected_template_id is not None:
        template_id = int(expected_template_id)
        if template_id in baseline:
            raise ValueError("The resolved fleet template existed before creation.")
        template = templates.get(template_id)
        if template is None:
            raise ValueError(
                f"Fleet template {template_id} is absent from the fresh save."
            )
        return template

    created = [
        template
        for template_id, template in sorted(templates.items())
        if template_id not in baseline
    ]
    if len(created) != 1:
        raise ValueError(
            "A fresh save must contain exactly one newly owned fleet template; "
            f"found {len(created)}."
        )
    return created[0]


def selected_new_fleet_reinforcement(
    profile: dict[str, Any],
    *,
    fleet_template_id: int,
    design_id: int,
    target_count: int,
    maximum_target_increase: int,
) -> dict[str, Any]:
    """Validate composition and reinforcement for a newly created template."""
    template = next(
        (
            item
            for item in profile.get("fleet_templates", [])
            if int(item["fleet_template_id"]) == fleet_template_id
        ),
        None,
    )
    if template is None:
        raise ValueError(
            f"Fleet template {fleet_template_id} is not owned by the selected country."
        )
    if design_id not in profile.get("player_ship_design_ids", []):
        raise ValueError(f"Ship design {design_id} is not player-owned.")
    if not 1 <= target_count <= 999:
        raise ValueError("target_count must be between 1 and 999.")
    if not 1 <= maximum_target_increase <= 999:
        raise ValueError("maximum_target_increase must be between 1 and 999.")
    if template.get("queued_item_handles"):
        raise ValueError(
            f"Fleet template {fleet_template_id} already has reinforcement items queued."
        )

    design_targets = list(template.get("design_targets", []))
    foreign_targets = [
        item
        for item in design_targets
        if int(item["design_id"]) != design_id
    ]
    if foreign_targets:
        raise ValueError(
            "The newly created fleet template contains an unexpected ship design."
        )
    matching_targets = [
        item for item in design_targets if int(item["design_id"]) == design_id
    ]
    if len(matching_targets) > 1:
        raise ValueError("The new template contains duplicate design targets.")
    row = matching_targets[0] if matching_targets else None
    previous_target = int(row["target_count"]) if row is not None else 0
    if target_count < previous_target:
        raise ValueError(
            "The requested initial target is below the save-backed template target."
        )
    increase = target_count - previous_target
    if increase > maximum_target_increase:
        raise ValueError(
            "The requested initial fleet size exceeds the player-configured limit."
        )

    fleet = next(
        (
            item
            for item in profile.get("fleets", [])
            if item.get("fleet_template_id") == fleet_template_id
        ),
        None,
    )
    composition = list(fleet.get("fleet_composition", [])) if fleet else []
    current_row = next(
        (
            item
            for item in composition
            if int(item["design_id"]) == design_id
        ),
        None,
    )
    current_count = int(current_row["current_count"]) if current_row else 0
    if increase == 0 and current_count >= target_count:
        raise ValueError("The new fleet already meets the requested target count.")

    target = {
        "context_822c": int(profile["owner_country_id"]),
        "fleet_template_id": int(fleet_template_id),
        "design_id": int(design_id),
        "upgrade_id": (
            int(row["upgrade_id"])
            if row is not None and row.get("upgrade_id") is not None
            else 0xFFFFFFFF
        ),
        "growth_stage": (
            int(row["growth_stage"])
            if row is not None and row.get("growth_stage") is not None
            else 0
        ),
    }
    return {
        "action": "configure_new_fleet",
        "fleet_template_id": int(fleet_template_id),
        "fleet_id": int(fleet["fleet_id"]) if fleet is not None else None,
        "design_id": int(design_id),
        "previous_target_count": previous_target,
        "requested_target_count": target_count,
        "current_count": current_count,
        "target_increase": increase,
        "expected_ship_shortfall": max(0, target_count - current_count),
        "template_edit_target": target,
        "reinforcement_target": {
            "context_822c": target["context_822c"],
            "fleet_template_id": target["fleet_template_id"],
        },
        "protocol_sequence": [
            *(["add_fleet_template_ship"] * increase),
            *(["reinforce_selected_fleet"] if current_count < target_count else []),
        ],
    }


def selected_move(
    profile: dict[str, Any],
    source_fleet: int,
    destination_system: int,
) -> dict[str, Any]:
    fleet = next(
        (item for item in profile["fleets"] if item["fleet_id"] == source_fleet),
        None,
    )
    if fleet is None:
        raise ValueError(f"Fleet {source_fleet} is not owned by the selected country.")
    if not fleet["d32c_move_verified_family"]:
        raise ValueError(f"Fleet {source_fleet} is not in the verified d32c family.")
    if not fleet["ai_callable_now"]:
        raise ValueError(
            f"Fleet {source_fleet} is not callable: {fleet['availability']}."
        )
    system = next(
        (
            item
            for item in profile["systems"]
            if item["system_id"] == destination_system
        ),
        None,
    )
    if system is None:
        raise ValueError(f"System {destination_system} does not exist in the save.")
    destination = system["move_destination"]
    if destination is None:
        raise ValueError(
            f"System {destination_system} has no unambiguous verified move target."
        )
    current_system_id = fleet["movement"]["current_system_id"]
    current_system = next(
        (
            item
            for item in profile["systems"]
            if item["system_id"] == current_system_id
        ),
        None,
    )
    return {
        "action": "move_fleet",
        "source_fleet": {
            "fleet_id": source_fleet,
            "name_key": fleet["name_key"],
            "display_name_hint": fleet.get("display_name_hint"),
            "current_system_id": current_system_id,
        },
        "destination_system": {
            "system_id": destination_system,
            "name_key": system["name_key"],
            "display_name_hint": system.get("display_name_hint"),
            "discovered_by_owner": system.get("discovered_by_owner", False),
            "owned_by_owner": system.get("owned_by_owner", False),
            "adjacent_to_source": bool(
                current_system
                and destination_system
                in current_system.get("hyperlane_neighbors", [])
            ),
        },
        "target": {
            "source_fleet_object": source_fleet,
            "destination_tag_hex": destination["destination_tag_hex"],
            "destination_object": destination["destination_object"],
        },
    }


def selected_coordinate_move(
    profile: dict[str, Any],
    source_fleet: int,
    x: float | str,
    y: float | str,
    *,
    maximum_abs_coordinate: float = 1000.0,
) -> dict[str, Any]:
    """Validate one 4f2c move within the fleet's current star system."""
    if not math.isfinite(maximum_abs_coordinate) or maximum_abs_coordinate <= 0:
        raise ValueError("maximum_abs_coordinate must be a positive finite value.")
    fleet = next(
        (item for item in profile["fleets"] if item["fleet_id"] == source_fleet),
        None,
    )
    if fleet is None:
        raise ValueError(f"Fleet {source_fleet} is not owned by the selected country.")
    if not fleet.get("coordinate_move_verified_family", False):
        raise ValueError(f"Fleet {source_fleet} is not in the verified 4f2c family.")
    if not fleet["ai_callable_now"]:
        raise ValueError(
            f"Fleet {source_fleet} is not callable: {fleet['availability']}."
        )
    system_origin = fleet["movement"].get("current_system_id")
    if system_origin is None:
        raise ValueError(f"Fleet {source_fleet} has no valid current system origin.")

    x_fixed = coordinate_to_fixed(x, "x")
    y_fixed = coordinate_to_fixed(y, "y")
    maximum_fixed = coordinate_to_fixed(maximum_abs_coordinate, "maximum_abs_coordinate")
    if abs(x_fixed) > maximum_fixed or abs(y_fixed) > maximum_fixed:
        raise ValueError(
            "The requested coordinate exceeds the player-configured in-system bound."
        )
    return {
        "action": "move_fleet_to_coordinate",
        "source_fleet": {
            "fleet_id": source_fleet,
            "name_key": fleet["name_key"],
            "display_name_hint": fleet.get("display_name_hint"),
            "current_system_id": system_origin,
            "current_coordinate": fleet["movement"].get("current_coordinate"),
        },
        "destination_coordinate": {
            "x": x_fixed / COORDINATE_SCALE,
            "y": y_fixed / COORDINATE_SCALE,
            "system_origin": system_origin,
        },
        "target": {
            "source_fleet_object": source_fleet,
            "x_fixed": x_fixed,
            "y_fixed": y_fixed,
            "system_origin": system_origin,
        },
    }


def selected_attack(
    profile: dict[str, Any],
    source_fleet: int,
    target_fleet: int,
) -> dict[str, Any]:
    """Validate one attack against a target visible in player hostile intel."""
    source = next(
        (item for item in profile["fleets"] if item["fleet_id"] == source_fleet),
        None,
    )
    if source is None:
        raise ValueError(f"Fleet {source_fleet} is not owned by the selected country.")
    if not source.get("attack_verified_family", False):
        raise ValueError(f"Fleet {source_fleet} is not in the verified 6b33 family.")
    if not source.get("ai_callable_now", False):
        raise ValueError(
            f"Fleet {source_fleet} is not callable: {source['availability']}."
        )
    target = next(
        (
            item
            for item in profile.get("hostile_targets", [])
            if int(item["fleet_id"]) == target_fleet
        ),
        None,
    )
    if target is None:
        raise ValueError(
            f"Fleet {target_fleet} is not a uniquely resolved current hostile target."
        )
    return {
        "action": "attack_fleet",
        "source_fleet": {
            "fleet_id": source_fleet,
            "name_key": source.get("name_key"),
            "display_name_hint": source.get("display_name_hint"),
            "military_power": source.get("military_power"),
            "current_system_id": source.get("movement", {}).get(
                "current_system_id"
            ),
        },
        "hostile_target": dict(target),
        "target": {
            "source_fleet_object": source_fleet,
            "target_fleet_object": target_fleet,
        },
    }


def _maintenance_fleet(
    profile: dict[str, Any],
    fleet_id: int,
    *,
    capability: str,
) -> dict[str, Any]:
    fleet = next(
        (item for item in profile["fleets"] if item["fleet_id"] == fleet_id),
        None,
    )
    if fleet is None:
        raise ValueError(f"Fleet {fleet_id} is not owned by the selected country.")
    if not fleet.get(capability, False):
        raise ValueError(f"Fleet {fleet_id} is not in the verified maintenance family.")
    if not fleet.get("maintenance_callable_now", False):
        raise ValueError(
            f"Fleet {fleet_id} is not maintenance-callable: "
            f"{fleet.get('availability')}."
        )
    return fleet


def selected_fleet_repair(
    profile: dict[str, Any],
    fleet_id: int,
) -> dict[str, Any]:
    """Validate one save-backed return-to-base repair order."""
    fleet = _maintenance_fleet(
        profile,
        fleet_id,
        capability="repair_verified_family",
    )
    if not fleet.get("needs_repair", False):
        raise ValueError(f"Fleet {fleet_id} has no observed durability damage.")
    return {
        "action": "repair_fleet",
        "source_fleet": {
            "fleet_id": fleet_id,
            "name_key": fleet.get("name_key"),
            "display_name_hint": fleet.get("display_name_hint"),
            "current_system_id": fleet.get("movement", {}).get(
                "current_system_id"
            ),
            "durability_summary": fleet.get("durability_summary"),
        },
        "target": {
            "context_822c": int(profile["owner_country_id"]),
            "source_fleet_object": fleet_id,
        },
    }


def selected_fleet_upgrade(
    profile: dict[str, Any],
    fleet_id: int,
    shipyard_build_queue_id: int,
) -> dict[str, Any]:
    """Validate one fleet upgrade against an owned save-backed shipyard queue."""
    fleet = _maintenance_fleet(
        profile,
        fleet_id,
        capability="upgrade_verified_family",
    )
    if int(fleet.get("upgradeable_ship_count", 0)) < 1:
        raise ValueError(f"Fleet {fleet_id} has no save-backed ship upgrade target.")
    shipyard = next(
        (
            item
            for item in profile.get("shipyards", [])
            if int(item["shipyard_build_queue_id"])
            == int(shipyard_build_queue_id)
        ),
        None,
    )
    if shipyard is None:
        raise ValueError(
            f"Shipyard queue {shipyard_build_queue_id} is not an owned verified queue."
        )
    return {
        "action": "upgrade_fleet",
        "source_fleet": {
            "fleet_id": fleet_id,
            "name_key": fleet.get("name_key"),
            "display_name_hint": fleet.get("display_name_hint"),
            "current_system_id": fleet.get("movement", {}).get(
                "current_system_id"
            ),
            "upgradeable_ship_count": int(fleet["upgradeable_ship_count"]),
        },
        "destination_shipyard": dict(shipyard),
        "target": {
            "context_822c": int(profile["owner_country_id"]),
            "source_fleet_object": fleet_id,
            "shipyard_build_queue_id": int(shipyard_build_queue_id),
        },
    }


def selected_ship_automation(
    profile: dict[str, Any],
    source_fleet: int,
    options: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Validate one science- or construction-ship automation request."""
    fleet = next(
        (item for item in profile["fleets"] if item["fleet_id"] == source_fleet),
        None,
    )
    if fleet is None:
        raise ValueError(f"Fleet {source_fleet} is not owned by the selected country.")
    if not fleet.get("automation_verified_family", False):
        raise ValueError(f"Fleet {source_fleet} does not support verified automation.")
    if not fleet.get("civilian_callable_now", False):
        raise ValueError(
            f"Fleet {source_fleet} is not callable: "
            f"{fleet.get('civilian_availability')}."
        )
    normalized = tuple(str(option) for option in options)
    if not normalized:
        raise ValueError("At least one automation option is required.")
    if len(set(normalized)) != len(normalized):
        raise ValueError("Automation options must not contain duplicates.")
    allowed = frozenset(fleet.get("verified_automation_options", []))
    unsupported = [option for option in normalized if option not in allowed]
    if unsupported:
        if (
            unsupported[0] == "AUTOMATION_ASTRAL_RIFTS"
            and fleet.get("has_council_leader")
        ):
            raise ValueError(
                "A science ship led by a council member cannot automate Astral Rifts."
            )
        raise ValueError(
            f"Automation option {unsupported[0]} is not valid for this ship class."
        )
    return {
        "action": "configure_ship_automation",
        "source_fleet": {
            "fleet_id": source_fleet,
            "name_key": fleet.get("name_key"),
            "display_name_hint": fleet.get("display_name_hint"),
            "ship_class": fleet.get("ship_class"),
            "civilian_role": fleet.get("civilian_role"),
        },
        "options": list(normalized),
        "target": {
            "context_822c": int(profile["owner_country_id"]),
            "source_fleet_object": source_fleet,
            "options": list(normalized),
        },
    }


def selected_construction_ship_starbase(
    profile: dict[str, Any],
    source_fleet: int,
    destination_system: int,
) -> dict[str, Any]:
    """Validate one adjacent surveyed-system outpost order."""
    fleet = next(
        (item for item in profile["fleets"] if item["fleet_id"] == source_fleet),
        None,
    )
    if fleet is None:
        raise ValueError(f"Fleet {source_fleet} is not owned by the selected country.")
    if not fleet.get("build_starbase_verified_family", False):
        raise ValueError(f"Fleet {source_fleet} is not a construction ship.")
    if not fleet.get("civilian_callable_now", False):
        raise ValueError(
            f"Fleet {source_fleet} is not callable: "
            f"{fleet.get('civilian_availability')}."
        )
    system = next(
        (
            item
            for item in profile["systems"]
            if item["system_id"] == destination_system
        ),
        None,
    )
    if system is None:
        raise ValueError(f"System {destination_system} does not exist in the save.")
    target = system.get("construction_target")
    if target is None:
        raise ValueError(
            f"System {destination_system} is not an unowned, surveyed outpost target."
        )
    current_system_id = fleet.get("movement", {}).get("current_system_id")
    current_system = next(
        (
            item
            for item in profile["systems"]
            if item["system_id"] == current_system_id
        ),
        None,
    )
    if (
        current_system is None
        or destination_system not in current_system.get("hyperlane_neighbors", [])
    ):
        raise ValueError(
            "The first autonomous outpost release only permits an adjacent system."
        )
    if any(
        int(item.get("system_id", -1)) == destination_system
        for item in profile.get("hostile_targets", [])
    ):
        raise ValueError("The target system currently contains visible hostile fleets.")
    return {
        "action": "build_starbase",
        "source_fleet": {
            "fleet_id": source_fleet,
            "name_key": fleet.get("name_key"),
            "display_name_hint": fleet.get("display_name_hint"),
            "current_system_id": current_system_id,
        },
        "destination_system": {
            "system_id": destination_system,
            "name_key": system.get("name_key"),
            "display_name_hint": system.get("display_name_hint"),
            "fully_surveyed_by_owner": system.get("fully_surveyed_by_owner"),
        },
        "target": {
            "source_fleet_object": source_fleet,
            "target_system_object": int(target["target_system_object"]),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("save", type=Path)
    parser.add_argument("--owner", type=int)
    parser.add_argument("--source-fleet", type=int)
    parser.add_argument("--destination-system", type=int)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (args.source_fleet is None) != (args.destination_system is None):
        raise ValueError(
            "--source-fleet and --destination-system must be supplied together."
        )
    profile = extract_fleet_profiles(load_gamestate(args.save), owner=args.owner)
    result: dict[str, Any] = profile
    if args.source_fleet is not None:
        result = {
            "schema_version": profile["schema_version"],
            "game_date": profile["game_date"],
            "owner_country_id": profile["owner_country_id"],
            "selected_move": selected_move(
                profile,
                args.source_fleet,
                args.destination_system,
            ),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
