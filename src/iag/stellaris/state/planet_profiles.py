#!/usr/bin/env python3
"""Extract Stellaris 4.x planet/build-queue/district/zone profiles from a save."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iag.stellaris.state.state_index import WorldStateIndex


ENTRY_RE = re.compile(r"^\s*(\d+)=\s*$")
INLINE_ENTRY_RE = re.compile(r"^\s*(\d+)=\s*\{(.*)\}\s*$")
STRUCTURAL_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')


def find_braced_section(
    text: str,
    name: str,
    allow_indent: bool = False,
) -> str:
    needle = f"{name}="
    search_from = 0
    open_brace = -1
    while True:
        match_start = text.find(needle, search_from)
        if match_start < 0:
            break
        line_start = text.rfind("\n", 0, match_start) + 1
        prefix = text[line_start:match_start]
        line_aligned = not prefix or (allow_indent and prefix.isspace())
        if line_aligned:
            cursor = match_start + len(needle)
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor < len(text) and text[cursor] == "{":
                open_brace = cursor
                break
        search_from = match_start + len(needle)
    if open_brace < 0:
        raise ValueError(f"Section not found: {name}")
    depth = 0
    # Let the regex engine skip complete quoted strings in C instead of
    # walking every character of a multi-megabyte save in Python.
    for token in STRUCTURAL_TOKEN_RE.finditer(text, open_brace):
        value = token.group(0)
        if value == "{":
            depth += 1
        elif value == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : token.start()]
    raise ValueError(f"Unclosed section: {name}")


def parse_numeric_map(section: str) -> dict[int, str | None]:
    lines = section.splitlines()
    result: dict[int, str | None] = {}
    index = 0
    depth = 0
    while index < len(lines):
        line = lines[index]
        inline_entry = INLINE_ENTRY_RE.match(line) if depth == 0 else None
        if inline_entry:
            result[int(inline_entry.group(1))] = inline_entry.group(2).strip()
            index += 1
            continue
        entry = ENTRY_RE.match(line) if depth == 0 else None
        if entry and index + 1 < len(lines):
            object_id = int(entry.group(1))
            next_line = lines[index + 1].strip()
            if next_line == "none":
                result[object_id] = None
                index += 2
                continue
            if next_line == "{":
                start = index + 2
                local_depth = 1
                cursor = start
                while cursor < len(lines):
                    local_depth += lines[cursor].count("{")
                    local_depth -= lines[cursor].count("}")
                    if local_depth == 0:
                        result[object_id] = "\n".join(lines[start:cursor])
                        index = cursor + 1
                        break
                    cursor += 1
                else:
                    raise ValueError(f"Unclosed numeric entry: {object_id}")
                continue
        depth += line.count("{")
        depth -= line.count("}")
        index += 1
    return result


def scalar(block: str, key: str) -> int | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}=(-?\d+)\s*$", block)
    return int(match.group(1)) if match else None


def quoted_scalar(block: str, key: str) -> str | None:
    match = re.search(rf'(?m)^\s*{re.escape(key)}="([^"]*)"\s*$', block)
    return match.group(1) if match else None


def integer_list(block: str, key: str) -> list[int]:
    match = re.search(
        rf"(?ms)^\s*{re.escape(key)}=\s*\n\s*\{{(.*?)^\s*\}}",
        block,
    )
    if not match:
        return []
    return [int(value) for value in re.findall(r"\d+", match.group(1))]


def object_reference_list(block: str, key: str) -> list[int | None]:
    """Preserve ordered object slots, including empty Clausewitz references."""
    match = re.search(
        rf"(?ms)^\s*{re.escape(key)}=\s*\n\s*\{{(.*?)^\s*\}}",
        block,
    )
    if not match:
        return []

    references: list[int | None] = []
    for token in re.findall(r"\binvalid\b|\bnone\b|\d+", match.group(1)):
        if token in {"invalid", "none"}:
            references.append(None)
            continue
        object_id = int(token)
        references.append(None if object_id == 0xFFFFFFFF else object_id)
    return references


def optional_numeric_map(text: str, section_name: str) -> dict[int, str | None]:
    try:
        return parse_numeric_map(find_braced_section(text, section_name).strip())
    except ValueError:
        return {}


def planet_name(block: str) -> str:
    try:
        name_block = find_braced_section(block, "name", allow_indent=True)
    except ValueError:
        return "<unnamed>"
    key = quoted_scalar(name_block, "key")
    return key or "<unnamed>"


def planet_name_variables(block: str) -> dict[str, str]:
    """Preserve nested localisation variables used by generated planet names."""
    try:
        name_block = find_braced_section(block, "name", allow_indent=True)
    except ValueError:
        return {}
    return {
        variable: value
        for variable, value in re.findall(
            r'(?ms)\{\s*key="([^"]+)"\s*value=\s*\{\s*key="([^"]+)"\s*\}\s*\}',
            name_block,
        )
    }


def planet_active_modifiers(block: str) -> list[str]:
    """Read permanent and timed modifier IDs applied to a planet.

    Permanent generated planet modifiers are stored in the same
    ``timed_modifier.items`` collection with ``days=-1``.  The neighbouring
    ``planet_modifier=\"pm_*\"`` values are presentation/localisation IDs, not
    the static-modifier rule IDs used by ``common/static_modifiers``.
    """
    try:
        modifier_block = find_braced_section(
            block,
            "timed_modifier",
            allow_indent=True,
        )
    except ValueError:
        return []
    return re.findall(r'\bmodifier\s*=\s*"([^"]+)"', modifier_block)


def load_gamestate(save_path: Path) -> str:
    with zipfile.ZipFile(save_path) as archive:
        return archive.read("gamestate").decode("utf-8-sig", errors="replace")


def extract_profiles(
    text: str,
    owner: int | None,
    *,
    state_index: WorldStateIndex | None = None,
) -> dict[str, object]:
    if state_index is None:
        planets = parse_numeric_map(find_braced_section(text, "planets").strip())
        # The planet collection wraps its numeric entries in planet={...}.
        if not planets:
            planet_collection = find_braced_section(
                find_braced_section(text, "planets"),
                "planet",
                allow_indent=True,
            )
            planets = parse_numeric_map(planet_collection.strip())
        colonies = optional_numeric_map(text, "colony")
        districts = parse_numeric_map(find_braced_section(text, "districts").strip())
        zones = parse_numeric_map(find_braced_section(text, "zones").strip())
        buildings = parse_numeric_map(find_braced_section(text, "buildings").strip())
        deposits = optional_numeric_map(text, "deposit")
    else:
        planets = state_index.planets()
        colonies = state_index.optional_numeric_map("colony")
        districts = state_index.numeric_map("districts")
        zones = state_index.numeric_map("zones")
        buildings = state_index.numeric_map("buildings")
        deposits = state_index.optional_numeric_map("deposit")

    output: list[dict[str, object]] = []
    for planet_id, block in planets.items():
        if block is None:
            continue
        planet_owner = scalar(block, "owner")
        if owner is not None and planet_owner != owner:
            continue
        build_queue = scalar(block, "build_queue")
        if build_queue is None:
            continue

        colony_id = scalar(block, "colony")
        colony_block = colonies.get(colony_id) if colony_id is not None else None
        # Stellaris 4.4 stores active colony district ownership in the top-level
        # colony object. Older 4.x saves may still expose districts on the planet
        # block, so keep that as a fallback.
        district_ids = integer_list(colony_block or "", "districts")
        if not district_ids:
            district_ids = integer_list(block, "districts")

        district_profiles: list[dict[str, object]] = []
        for district_id in district_ids:
            district_block = districts.get(district_id)
            if not district_block:
                continue
            zone_slot_references = object_reference_list(
                district_block,
                "zones",
            )
            zone_profiles: list[dict[str, object]] = []
            for slot_selector, zone_id in enumerate(zone_slot_references):
                if zone_id is None:
                    continue
                zone_block = zones.get(zone_id)
                if not zone_block:
                    continue
                building_profiles: list[dict[str, object]] = []
                for building_id in integer_list(zone_block, "buildings"):
                    building_block = buildings.get(building_id)
                    building_profiles.append(
                        {
                            "object_id": building_id,
                            "type": (
                                quoted_scalar(building_block, "type")
                                if building_block
                                else None
                            ),
                            "position": (
                                scalar(building_block, "position")
                                if building_block
                                else None
                            ),
                        }
                    )
                zone_profiles.append(
                    {
                        "zone_id": zone_id,
                        "slot_selector": slot_selector,
                        "type": quoted_scalar(zone_block, "type"),
                        "buildings": building_profiles,
                    }
                )
            district_profiles.append(
                {
                    "district_id": district_id,
                    "district_id_hex_le": district_id.to_bytes(
                        4,
                        "little",
                        signed=False,
                    ).hex(),
                    "type": quoted_scalar(district_block, "type"),
                    "level": scalar(district_block, "level"),
                    "zone_slot_references": zone_slot_references,
                    "zones": zone_profiles,
                }
            )

        deposit_ids = integer_list(block, "deposits")
        output.append(
            {
                "name": planet_name(block),
                "name_variables": planet_name_variables(block),
                "planet_id": planet_id,
                "owner": planet_owner,
                "colony_id": colony_id,
                "planet_class": quoted_scalar(block, "planet_class"),
                "planet_size": scalar(block, "planet_size"),
                "active_modifiers": planet_active_modifiers(block),
                "build_queue_id": build_queue,
                "build_queue_id_hex_le": build_queue.to_bytes(
                    4,
                    "little",
                    signed=False,
                ).hex(),
                "army_build_queue_id": scalar(block, "army_build_queue"),
                "deposit_types": [
                    deposit_type
                    for deposit_id in deposit_ids
                    if (
                        deposit_type := quoted_scalar(
                            deposits.get(deposit_id) or "",
                            "type",
                        )
                    )
                ],
                "districts": district_profiles,
            }
        )

    return {
        "owner_filter": owner,
        "planet_count": len(output),
        "planets": sorted(output, key=lambda item: int(item["planet_id"])),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("save", type=Path)
    parser.add_argument("--owner", type=int, default=0)
    parser.add_argument("--all-owners", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    owner = None if args.all_owners else args.owner
    result = extract_profiles(load_gamestate(args.save), owner)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
