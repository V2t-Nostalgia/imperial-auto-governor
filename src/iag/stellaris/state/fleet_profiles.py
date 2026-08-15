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
import re
from pathlib import Path
from typing import Any

from iag.stellaris.state.planet_profiles import (
    find_braced_section,
    load_gamestate,
    parse_numeric_map,
)


INVALID_OBJECT_ID = 0xFFFFFFFF
STARBASE_DESTINATION_TAG_HEX = "0c3a01001400"
STELLAR_DESTINATION_TAG_HEX = "132a01001400"


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


def movement_profile(fleet_block: str) -> dict[str, Any]:
    manager = optional_section(fleet_block, "movement_manager")
    coordinate = optional_section(manager, "coordinate")
    target = optional_section(manager, "target")
    target_coordinate = optional_section(target, "coordinate")
    orbit = optional_section(manager, "orbit")
    orbitable = optional_section(orbit, "orbitable")
    current_system = integer_scalar(coordinate, "origin")
    target_system = integer_scalar(target_coordinate, "origin")
    return {
        "state": bare_scalar(manager, "state"),
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
    if ship_class != "shipclass_military":
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


def system_destination(
    system_block: str,
    planets: dict[int, str | None],
    starbases: dict[int, str | None],
) -> dict[str, Any] | None:
    system_planets = repeated_integer(system_block, "planet")
    starbase_indices = integer_values(system_block, "starbases")
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
    systems = parse_numeric_map(find_braced_section(text, "galactic_object").strip())
    planets = planet_map(text)
    starbases = starbase_map(text)

    fleet_output: list[dict[str, Any]] = []
    for fleet_id in owned_fleet_ids(country):
        block = fleets.get(fleet_id)
        if block is None:
            continue
        settings = optional_section(block, "settings")
        ship_class = bare_scalar(block, "ship_class")
        mobile = bare_scalar(settings, "mobile") == "yes"
        valid_for_combat = bare_scalar(settings, "valid_for_combat") == "yes"
        stationary_installation = bare_scalar(settings, "station") == "yes"
        ship_ids = integer_values(block, "ships")
        movement = movement_profile(block)
        mia_origin = coordinate_origin(block, "mia_from")
        combat_fleet_ids = integer_values(block, "in_combat_with")
        availability, availability_reasons = fleet_availability(
            ship_class=ship_class,
            ship_ids=ship_ids,
            mobile=mobile,
            valid_for_combat=valid_for_combat,
            movement=movement,
            mia_origin=mia_origin,
            combat_fleet_ids=combat_fleet_ids,
        )
        fleet_output.append(
            {
                "fleet_id": fleet_id,
                "name_key": name_key(block),
                "display_name_hint": name_hint(block),
                "fleet_template_id": integer_scalar(block, "fleet_template"),
                "ship_class": ship_class,
                "ship_ids": ship_ids,
                "ship_count": len(ship_ids),
                "military_power": float_scalar(block, "military_power"),
                "mobile": mobile,
                "valid_for_combat": valid_for_combat,
                "stationary_installation": stationary_installation,
                "player_controllable": not stationary_installation,
                "d32c_move_verified_family": (
                    mobile and ship_class == "shipclass_military"
                ),
                "movement": movement,
                "mia_from_system_id": mia_origin,
                "in_combat_with": combat_fleet_ids,
                "availability": availability,
                "availability_reasons": availability_reasons,
                "ai_callable_now": availability == "AVAILABLE",
            }
        )

    system_output: list[dict[str, Any]] = []
    for system_id, block in sorted(systems.items()):
        if block is None:
            continue
        planet_ids = repeated_integer(block, "planet")
        discovery = integer_values(block, "discovery")
        system_output.append(
            {
                "system_id": system_id,
                "name_key": name_key(block),
                "star_class": quoted_value(block, "star_class"),
                "planet_ids": planet_ids,
                "starbase_indices": integer_values(block, "starbases"),
                "discovered_by_owner": owner in discovery,
                "move_destination": system_destination(block, planets, starbases),
            }
        )

    return {
        "schema": "iag.stellaris_fleet_state.v1",
        "schema_version": 1,
        "game_date": quoted_value(text, "date"),
        "owner_country_id": owner,
        "player_country_ids": players,
        "fleets": fleet_output,
        "systems": system_output,
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
    return {
        "action": "move_fleet",
        "source_fleet": {
            "fleet_id": source_fleet,
            "name_key": fleet["name_key"],
            "current_system_id": fleet["movement"]["current_system_id"],
        },
        "destination_system": {
            "system_id": destination_system,
            "name_key": system["name_key"],
        },
        "target": {
            "source_fleet_object": source_fleet,
            "destination_tag_hex": destination["destination_tag_hex"],
            "destination_object": destination["destination_object"],
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
