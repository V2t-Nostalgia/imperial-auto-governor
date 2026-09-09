#!/usr/bin/env python3
"""Extract version-locked Stellaris rule evidence from the local installation."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
SCRIPTED_VARIABLE_RE = re.compile(
    r"(?m)^\s*@([A-Za-z0-9_.-]+)\s*=\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:#.*)?$"
)
TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|[{}=]|[^\s{}=]+')
VERSION_RE = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", re.IGNORECASE)

DEFINITION_DIRS = {
    "building": "common/buildings",
    "zone": "common/zones",
    "district": "common/districts",
    "zone_slot": "common/zone_slots",
    "deposit": "common/deposits",
    "static_modifier": "common/static_modifiers",
    "technology": "common/technology",
    "trait": "common/traits",
    "planet_class": "common/planet_classes",
    "starbase_level": "common/starbase_levels",
    "starbase_module": "common/starbase_modules",
    "starbase_building": "common/starbase_buildings",
}

DISTRICT_CAPACITY_FIELDS = {
    "planet_max_districts_add",
    "planet_max_districts_mult",
    "district_city_max_add",
    "district_city_max_mult",
    "district_generator_max_add",
    "district_generator_max_mult",
    "district_mining_max_add",
    "district_mining_max_mult",
    "district_farming_max_add",
    "district_farming_max_mult",
}

RESOURCE_ZONE_SLOT_TECHNOLOGIES = {
    "slot_energy": "tech_power_hub_1",
    "slot_minerals": "tech_mineral_purification_1",
    "slot_food": "tech_food_processing_1",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_version(value: object) -> str | None:
    match = VERSION_RE.search(str(value or ""))
    return match.group(1) if match else None


def _steam_library_roots() -> list[Path]:
    homes = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
    ]
    if os.name == "nt":
        for environment_name in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(environment_name)
            if base:
                homes.append(Path(base) / "Steam")

    libraries: list[Path] = []
    for steam_root in homes:
        if steam_root not in libraries:
            libraries.append(steam_root)
        vdf_path = steam_root / "steamapps/libraryfolders.vdf"
        try:
            vdf = vdf_path.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, PermissionError):
            continue
        for raw_path in re.findall(r'"path"\s+"([^"]+)"', vdf):
            decoded = raw_path.replace("\\\\", "\\")
            candidate = Path(decoded)
            if candidate not in libraries:
                libraries.append(candidate)
    return libraries


def detect_game_root(config: dict[str, Any]) -> Path | None:
    configured = str(config.get("game_root", "")).strip()
    if configured:
        candidate = Path(configured).expanduser()
        if (candidate / "launcher-settings.json").is_file():
            return candidate.resolve()

    for library in _steam_library_roots():
        candidate = library / "steamapps/common/Stellaris"
        if (candidate / "launcher-settings.json").is_file():
            return candidate.resolve()
    return None


def install_identity(game_root: Path) -> dict[str, Any]:
    settings_path = game_root / "launcher-settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    return {
        "game_root": str(game_root),
        "version": settings.get("version"),
        "raw_version": settings.get("rawVersion"),
        "normalized_version": normalized_version(
            settings.get("rawVersion") or settings.get("version")
        ),
        "mod_compatibility_version": settings.get("modsCompatibilityVersion"),
        "distribution": settings.get("distPlatform"),
        "launcher_settings_path": str(settings_path),
        "launcher_settings_sha256": file_sha256(settings_path),
    }


@lru_cache(maxsize=8)
def scripted_variables(game_root: Path) -> tuple[dict[str, float], dict[str, str]]:
    values: dict[str, float] = {}
    sources: dict[str, str] = {}
    variable_root = game_root / "common/scripted_variables"
    if not variable_root.is_dir():
        return values, sources

    for path in sorted(variable_root.rglob("*.txt")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for match in SCRIPTED_VARIABLE_RE.finditer(text):
            name = match.group(1)
            value = float(match.group(2))
            values[name] = int(value) if value.is_integer() else value
            sources[name] = str(path)
    return values, sources


@lru_cache(maxsize=128)
def variables_for_source(game_root: Path, source_path: Path) -> dict[str, float]:
    """Merge global scripted variables with variables local to a definition file."""
    global_values, _ = scripted_variables(game_root)
    values = dict(global_values)
    text = source_path.read_text(encoding="utf-8-sig", errors="replace")
    for match in SCRIPTED_VARIABLE_RE.finditer(text):
        value = float(match.group(2))
        values[match.group(1)] = int(value) if value.is_integer() else value
    return values


def strip_clausewitz_comments(text: str) -> str:
    output: list[str] = []
    in_quote = False
    escaped = False
    in_comment = False
    for character in text:
        if in_comment:
            if character == "\n":
                in_comment = False
                output.append(character)
            continue
        if escaped:
            output.append(character)
            escaped = False
            continue
        if in_quote and character == "\\":
            output.append(character)
            escaped = True
            continue
        if character == '"':
            in_quote = not in_quote
            output.append(character)
            continue
        if character == "#" and not in_quote:
            in_comment = True
            continue
        output.append(character)
    return "".join(output)


def extract_named_block(text: str, object_id: str) -> tuple[str, int] | None:
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(object_id)}[ \t]*=[ \t]*\{{")
    match = pattern.search(text)
    if not match:
        return None

    open_brace = text.find("{", match.start(), match.end())
    depth = 0
    in_quote = False
    escaped = False
    in_comment = False
    for index in range(open_brace, len(text)):
        character = text[index]
        if in_comment:
            if character == "\n":
                in_comment = False
            continue
        if escaped:
            escaped = False
            continue
        if in_quote and character == "\\":
            escaped = True
            continue
        if character == '"':
            in_quote = not in_quote
            continue
        if character == "#" and not in_quote:
            in_comment = True
            continue
        if in_quote:
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                line = text.count("\n", 0, match.start()) + 1
                return text[match.start() : index + 1], line
    raise ValueError(f"Unclosed definition for {object_id}.")


@lru_cache(maxsize=512)
def find_definition_source(
    game_root: Path,
    kind: str,
    object_id: str,
) -> tuple[Path, str, int] | None:
    definition_root = game_root / DEFINITION_DIRS[kind]
    for path in sorted(definition_root.rglob("*.txt")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        extracted = extract_named_block(text, object_id)
        if extracted:
            block, line = extracted
            return path, block, line
    return None


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _parse_scope(
    tokens: list[str],
    index: int = 0,
    *,
    stop_on_brace: bool = False,
) -> tuple[list[tuple[str, Any]], int]:
    entries: list[tuple[str, Any]] = []
    while index < len(tokens):
        token = tokens[index]
        if token == "}":
            if stop_on_brace:
                return entries, index + 1
            index += 1
            continue
        if token == "{":
            _, index = _parse_scope(tokens, index + 1, stop_on_brace=True)
            continue

        key = _unquote(token)
        index += 1
        if index >= len(tokens) or tokens[index] != "=":
            entries.append(("__value__", key))
            continue
        index += 1
        if index >= len(tokens):
            entries.append((key, None))
            break
        if tokens[index] == "{":
            child, index = _parse_scope(tokens, index + 1, stop_on_brace=True)
            entries.append((key, child))
        else:
            entries.append((key, _unquote(tokens[index])))
            index += 1
    return entries, index


def parse_clausewitz(text: str) -> list[tuple[str, Any]]:
    tokens = TOKEN_RE.findall(strip_clausewitz_comments(text))
    return _parse_scope(tokens)[0]


def _resolved_number(value: object, variables: dict[str, float]) -> float | int | None:
    raw = str(value)
    if raw.startswith("@"):
        return variables.get(raw[1:])
    if not NUMERIC_RE.fullmatch(raw):
        return None
    number = float(raw)
    return int(number) if number.is_integer() else number


def numeric_assignments(
    entries: Iterable[tuple[str, Any]],
    variables: dict[str, float],
    *,
    parent: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key, value in entries:
        if key == "__value__":
            continue
        path = parent + (key,)
        if isinstance(value, list):
            output.extend(numeric_assignments(value, variables, parent=path))
            continue
        resolved = _resolved_number(value, variables)
        if resolved is None:
            continue
        output.append(
            {
                "path": ".".join(path),
                "raw": value,
                "resolved": resolved,
                "source_kind": (
                    "scripted_variable" if str(value).startswith("@") else "literal"
                ),
            }
        )
    return output


@lru_cache(maxsize=512)
def technology_rule(
    game_root: Path,
    technology_id: str,
) -> dict[str, Any]:
    """Return version-locked, source-backed metadata for one technology."""
    source = find_definition_source(game_root, "technology", technology_id)
    if source is None:
        return {"status": "missing", "technology_id": technology_id}

    path, block, line = source
    entries = _object_entries(block, technology_id)
    variables = variables_for_source(game_root, path)
    raw_cost = _scalar_value(entries, "cost")
    raw_tier = _scalar_value(entries, "tier")
    modifier_entries = _child_blocks(entries, "modifier")
    modifiers: list[dict[str, Any]] = []
    for child in modifier_entries:
        modifiers.extend(numeric_assignments(child, variables))
    return {
        "status": "ok",
        "technology_id": technology_id,
        "area": _scalar_value(entries, "area"),
        "tier": _resolved_number(raw_tier, variables),
        "tier_raw": raw_tier,
        "cost": _resolved_number(raw_cost, variables),
        "cost_raw": raw_cost,
        "category": _list_values(entries, "category"),
        "prerequisites": _list_values(entries, "prerequisites"),
        "feature_flags": _list_values(entries, "feature_flags"),
        "gateway": _scalar_value(entries, "gateway"),
        "is_rare": _scalar_value(entries, "is_rare") == "yes",
        "is_dangerous": _scalar_value(entries, "is_dangerous") == "yes",
        "is_start_technology": _scalar_value(entries, "start_tech") == "yes",
        "modifiers": modifiers,
        "source": {
            "path": str(path),
            "line": line,
            "sha256": file_sha256(path),
        },
    }


def _object_entries(block: str, object_id: str) -> list[tuple[str, Any]]:
    for key, value in parse_clausewitz(block):
        if key == object_id and isinstance(value, list):
            return value
    return []


def _list_values(
    entries: list[tuple[str, Any]],
    key: str,
) -> list[str]:
    for entry_key, value in entries:
        if entry_key != key or not isinstance(value, list):
            continue
        return [
            str(item_value) for item_key, item_value in value if item_key == "__value__"
        ]
    return []


def _scalar_value(
    entries: list[tuple[str, Any]],
    key: str,
) -> str | None:
    for entry_key, value in entries:
        if entry_key == key and not isinstance(value, list):
            return str(value)
    return None


def _child_blocks(
    entries: list[tuple[str, Any]],
    key: str,
) -> list[list[tuple[str, Any]]]:
    return [
        value
        for entry_key, value in entries
        if entry_key == key and isinstance(value, list)
    ]


@lru_cache(maxsize=256)
def planet_class_rule(game_root: Path, planet_class: str) -> dict[str, Any]:
    """Return the installed rule evidence needed for colonization filtering."""
    source = find_definition_source(game_root, "planet_class", planet_class)
    if source is None:
        return {"status": "missing", "planet_class": planet_class}
    path, block, line = source
    entries = _object_entries(block, planet_class)
    return {
        "status": "available",
        "planet_class": planet_class,
        "colonizable": _scalar_value(entries, "colonizable") == "yes",
        "climate": _scalar_value(entries, "climate"),
        "source_path": str(path),
        "source_line": line,
    }


def species_habitability_rule(
    game_root: Path,
    trait_ids: tuple[str, ...],
    planet_class: str,
) -> dict[str, Any]:
    """Estimate habitability from the species' installed trait definitions.

    This reproduces the base preference plus unconditional species tolerance
    used by ordinary organic species.  Country, planet and event modifiers are
    intentionally not guessed, so callers must label the result as an estimate.
    """
    target_field = f"{planet_class}_habitability"
    base = 0.0
    tolerance = 0.0
    floor = 0.0
    found_base = False
    evidence: list[dict[str, Any]] = []
    missing: list[str] = []
    for trait_id in trait_ids:
        source = find_definition_source(game_root, "trait", trait_id)
        if source is None:
            missing.append(trait_id)
            continue
        path, block, line = source
        variables = variables_for_source(game_root, path)
        entries = _object_entries(block, trait_id)
        values: list[dict[str, Any]] = []
        for modifier in _child_blocks(entries, "modifier"):
            values.extend(numeric_assignments(modifier, variables))
        contributions: dict[str, float] = {}
        for assignment in values:
            field = str(assignment["path"]).split(".")[-1]
            raw = assignment["resolved"]
            if not isinstance(raw, (int, float)):
                continue
            value = float(raw)
            if field == target_field:
                base += value
                found_base = True
                contributions[target_field] = (
                    contributions.get(target_field, 0.0) + value
                )
            elif field == "pop_environment_tolerance":
                tolerance += value
                contributions[field] = contributions.get(field, 0.0) + value
            elif field == "habitability_floor_add":
                floor += value
                contributions[field] = contributions.get(field, 0.0) + value
        if contributions:
            evidence.append(
                {
                    "trait_id": trait_id,
                    "contributions": contributions,
                    "source_path": str(path),
                    "source_line": line,
                }
            )
    if not found_base:
        return {
            "status": "unresolved",
            "planet_class": planet_class,
            "trait_ids": list(trait_ids),
            "missing_trait_definitions": missing,
            "evidence": evidence,
        }
    value = max(floor, min(1.0, base + tolerance))
    return {
        "status": "estimated",
        "planet_class": planet_class,
        "habitability": value,
        "base": base,
        "species_tolerance": tolerance,
        "habitability_floor": floor,
        "trait_ids": list(trait_ids),
        "missing_trait_definitions": missing,
        "authority": "installed_species_traits_without_dynamic_modifiers",
        "evidence": evidence,
    }


@lru_cache(maxsize=64)
def starbase_level_rule(game_root: Path, level_id: str) -> dict[str, Any]:
    """Return the installed normal starbase level and its slot declarations."""
    source = find_definition_source(game_root, "starbase_level", level_id)
    if source is None:
        return {"status": "missing", "level_id": level_id}
    path, block, line = source
    entries = _object_entries(block, level_id)
    module_slots = _list_values(entries, "module_slots")
    building_slots = _list_values(entries, "building_slots")
    return {
        "status": "available",
        "level_id": level_id,
        "next_level": _scalar_value(entries, "next_level"),
        "module_slot_count": len(module_slots),
        "building_slot_count": len(building_slots),
        "source_path": str(path),
        "source_line": line,
    }


@lru_cache(maxsize=128)
def starbase_component_rule(
    game_root: Path,
    kind: str,
    component_id: str,
) -> dict[str, Any]:
    """Return source and simple technology gates for one starbase component."""
    if kind not in {"module", "building"}:
        raise ValueError("Starbase component kind must be module or building.")
    definition_kind = f"starbase_{kind}"
    source = find_definition_source(game_root, definition_kind, component_id)
    if source is None:
        return {
            "status": "missing",
            "kind": kind,
            "component_id": component_id,
        }
    path, block, line = source
    entries = _object_entries(block, component_id)
    required_technologies = sorted(
        set(re.findall(r"\bhas_technology\s*=\s*\"?([A-Za-z0-9_.-]+)", block))
    )
    shown_technology = _scalar_value(entries, "show_in_tech")
    if shown_technology:
        required_technologies.append(shown_technology)
    return {
        "status": "available",
        "kind": kind,
        "component_id": component_id,
        "initial": _scalar_value(entries, "initial") == "yes",
        "required_technologies": sorted(set(required_technologies)),
        "source_path": str(path),
        "source_line": line,
    }


def _wrapped_definitions(
    definition_root: Path,
    wrappers: set[str],
) -> list[tuple[str, str, list[tuple[str, Any]], Path, int]]:
    output: list[tuple[str, str, list[tuple[str, Any]], Path, int]] = []
    if not definition_root.is_dir():
        return output
    for path in sorted(definition_root.rglob("*.txt")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for wrapper, entries in parse_clausewitz(text):
            if wrapper not in wrappers or not isinstance(entries, list):
                continue
            object_id = _scalar_value(entries, "key")
            if not object_id:
                continue
            key_match = re.search(
                rf'(?m)^\s*key\s*=\s*"{re.escape(object_id)}"',
                text,
            )
            line = text.count("\n", 0, key_match.start()) + 1 if key_match else 1
            output.append((wrapper, object_id, entries, path, line))
    return output


def _condition_leaves(
    entries: list[tuple[str, Any]],
) -> list[tuple[str, str]]:
    leaves: list[tuple[str, str]] = []
    for key, value in entries:
        if isinstance(value, list):
            leaves.extend(_condition_leaves(value))
        elif key != "__value__":
            leaves.append((str(key), str(value).lower()))
    return leaves


def _ship_component_potential_policy(
    entries: list[tuple[str, Any]],
) -> str:
    potentials = _child_blocks(entries, "potential")
    if not potentials:
        return "unrestricted"
    if len(potentials) != 1:
        return "conditional_unresolved"
    leaves = set(_condition_leaves(potentials[0]))
    ordinary_conditions = {
        ("country_uses_bio_ships", "no"),
        ("is_arkship_ship", "yes"),
    }
    if ("country_uses_bio_ships", "no") in leaves and leaves.issubset(
        ordinary_conditions
    ):
        return "regular_ship_or_arkship"
    return "conditional_unresolved"


def _technology_prerequisite_options(
    entries: list[tuple[str, Any]],
) -> list[list[str]]:
    """Convert the small technology-only prerequisite grammar to DNF."""

    def entry_options(key: str, value: Any) -> list[set[str]]:
        if key == "__value__":
            return [{str(value)}]
        if not isinstance(value, list):
            return []
        if key.upper() == "OR":
            alternatives: list[set[str]] = []
            for child_key, child_value in value:
                alternatives.extend(entry_options(child_key, child_value))
            return alternatives
        return block_options(value)

    def block_options(block: list[tuple[str, Any]]) -> list[set[str]]:
        options: list[set[str]] = [set()]
        for key, value in block:
            choices = entry_options(key, value)
            if not choices:
                continue
            combined = [left | right for left in options for right in choices]
            options = combined[:256]
        return options

    options = block_options(entries)
    return [sorted(option) for option in options] or [[]]


@lru_cache(maxsize=8)
def _weapon_power_table(game_root: Path) -> dict[str, float]:
    """Read 4.4's generated weapon power table with a real CSV parser."""
    path = game_root / "common/component_templates/weapon_components.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = (line for line in handle if not line.lstrip().startswith("#"))
        reader = csv.DictReader(rows, delimiter=";")
        output: dict[str, float] = {}
        for row in reader:
            component_id = str(row.get("key") or "").strip()
            raw_power = str(row.get("power") or "").strip()
            if not component_id or not NUMERIC_RE.fullmatch(raw_power):
                continue
            output[component_id] = float(raw_power)
        return output


@lru_cache(maxsize=8)
def ship_section_rules(game_root: Path) -> dict[str, dict[str, Any]]:
    """Index installed section slot templates used to validate components."""
    output: dict[str, dict[str, Any]] = {}
    root = game_root / "common/section_templates"
    for _wrapper, section_id, entries, path, line in _wrapped_definitions(
        root,
        {"ship_section_template"},
    ):
        explicit_slots: dict[str, str] = {}
        for slot in _child_blocks(entries, "component_slot"):
            name = _scalar_value(slot, "name")
            template = _scalar_value(slot, "template")
            if name and template:
                explicit_slots[name] = template

        def slot_count(
            field: str,
            source_entries: list[tuple[str, Any]] = entries,
        ) -> int:
            raw = _scalar_value(source_entries, field)
            try:
                return max(0, int(raw or 0))
            except ValueError:
                return 0

        output[section_id] = {
            "status": "available",
            "section_template": section_id,
            "ship_size": _scalar_value(entries, "ship_size"),
            "fits_on_slot": _scalar_value(entries, "fits_on_slot"),
            "component_slots": explicit_slots,
            "small_utility_slots": slot_count("small_utility_slots"),
            "medium_utility_slots": slot_count("medium_utility_slots"),
            "large_utility_slots": slot_count("large_utility_slots"),
            "aux_utility_slots": slot_count("aux_utility_slots"),
            "prerequisite_options": _technology_prerequisite_options(
                _child_blocks(entries, "prerequisites")[0]
                if _child_blocks(entries, "prerequisites")
                else []
            ),
            "source": {"path": str(path), "line": line},
        }
    return output


def ship_section_rule(
    game_root: Path,
    section_template: str,
) -> dict[str, Any]:
    rule = ship_section_rules(game_root).get(section_template)
    if rule is None:
        return {"status": "missing", "section_template": section_template}
    return rule


@lru_cache(maxsize=8)
def ship_component_catalog(game_root: Path) -> tuple[dict[str, Any], ...]:
    """Return source-backed component rules used by ship-design validation."""
    wrappers = {
        "weapon_component_template",
        "utility_component_template",
        "strike_craft_component_template",
    }
    output: list[dict[str, Any]] = []
    weapon_power = _weapon_power_table(game_root)
    root = game_root / "common/component_templates"
    for definition_type, component_id, entries, path, line in _wrapped_definitions(
        root, wrappers
    ):
        variables = variables_for_source(game_root, path)
        raw_power = _scalar_value(entries, "power")
        power = _resolved_number(raw_power, variables)
        if power is None and definition_type == "weapon_component_template":
            power = weapon_power.get(component_id)
        prerequisite_blocks = _child_blocks(entries, "prerequisites")
        prerequisite_options = _technology_prerequisite_options(
            prerequisite_blocks[0] if prerequisite_blocks else []
        )
        potential_blocks = _child_blocks(entries, "potential")
        output.append(
            {
                "component_id": component_id,
                "kind": (
                    "weapon"
                    if definition_type == "weapon_component_template"
                    else (
                        "strike_craft"
                        if definition_type == "strike_craft_component_template"
                        else "utility"
                    )
                ),
                "size": str(_scalar_value(entries, "size") or "").lower(),
                "component_type": _scalar_value(entries, "type"),
                "tags": _list_values(entries, "tags"),
                "component_set": _scalar_value(entries, "component_set"),
                "ship_behavior": _scalar_value(entries, "ship_behavior"),
                "upgrade_path": _scalar_value(entries, "upgrade_path"),
                "power": power,
                "initial": _scalar_value(entries, "initial") == "yes",
                "prerequisites": sorted(
                    {
                        technology
                        for option in prerequisite_options
                        for technology in option
                    }
                ),
                "prerequisite_options": prerequisite_options,
                "potential_policy": _ship_component_potential_policy(entries),
                "potential_blocks": potential_blocks,
                "potential_leaves": [
                    {"field": field, "value": value}
                    for block in potential_blocks
                    for field, value in _condition_leaves(block)
                ],
                "hidden": _scalar_value(entries, "hidden") == "yes",
                "source_family": (
                    "mutation"
                    if "mutation" in path.name.lower()
                    else (
                        "biological"
                        if "biogenesis" in path.name.lower()
                        else "standard"
                    )
                ),
                "source": {"path": str(path), "line": line},
            }
        )
    return tuple(output)


def _referenced_rule_texts(
    game_root: Path,
    root_block: str,
    root_source: Path,
) -> list[tuple[Path, str]]:
    output: list[tuple[Path, str]] = []
    pending = [(root_source, root_block)]
    seen = {root_source.resolve()}
    while pending:
        source, text = pending.pop(0)
        output.append((source, text))
        for script_name in re.findall(
            r'(?m)^\s*script\s*=\s*"?([A-Za-z0-9_./-]+)"?\s*$',
            text,
        ):
            script_path = game_root / "common/inline_scripts" / f"{script_name}.txt"
            resolved = script_path.resolve()
            if resolved in seen or not script_path.is_file():
                continue
            seen.add(resolved)
            pending.append(
                (
                    script_path,
                    script_path.read_text(
                        encoding="utf-8-sig",
                        errors="replace",
                    ),
                )
            )
    return output


def _resolved_field_values(
    texts: list[tuple[Path, str]],
    field: str,
    variables: dict[str, float],
) -> tuple[set[int], list[str]]:
    values: set[int] = set()
    sources: list[str] = []
    for source, text in texts:
        found = False
        for assignment in numeric_assignments(parse_clausewitz(text), variables):
            if assignment["path"].split(".")[-1] != field:
                continue
            value = assignment["resolved"]
            if isinstance(value, (int, float)) and float(value).is_integer():
                values.add(int(value))
                found = True
        if found:
            sources.append(str(source))
    return values, sources


@lru_cache(maxsize=256)
def zone_layout_rule(
    game_root: Path,
    zone_type: str,
) -> dict[str, Any]:
    source = find_definition_source(game_root, "zone", zone_type)
    if source is None:
        return {"status": "missing", "zone_type": zone_type}

    path, block, line = source
    variables = variables_for_source(game_root, path)
    texts = _referenced_rule_texts(game_root, block, path)
    slot_add_values, slot_sources = _resolved_field_values(
        texts,
        "zone_building_slots_add",
        variables,
    )
    max_values, max_sources = _resolved_field_values(
        [(path, block)],
        "max_buildings",
        variables,
    )
    positive_slot_values = {value for value in slot_add_values if value > 0}
    positive_max_values = {value for value in max_values if value > 0}

    capacity: int | None = None
    capacity_kind: str | None = None
    if len(positive_slot_values) == 1:
        capacity = next(iter(positive_slot_values))
        capacity_kind = "zone_building_slots_add"
    elif not positive_slot_values and len(positive_max_values) == 1:
        capacity = next(iter(positive_max_values))
        capacity_kind = "max_buildings"

    entries = _object_entries(block, zone_type)
    return {
        "status": "available" if capacity is not None else "ambiguous",
        "zone_type": zone_type,
        "building_slot_capacity": capacity,
        "capacity_kind": capacity_kind,
        "zone_sets": _list_values(entries, "zone_sets"),
        "source_path": str(path),
        "source_line": line,
        "capacity_sources": sorted(set(slot_sources + max_sources)),
    }


@lru_cache(maxsize=128)
def district_layout_rule(
    game_root: Path,
    district_type: str,
) -> dict[str, Any]:
    source = find_definition_source(game_root, "district", district_type)
    if source is None:
        return {"status": "missing", "district_type": district_type}
    path, block, line = source
    entries = _object_entries(block, district_type)
    return {
        "status": "available",
        "district_type": district_type,
        "zone_slot_ids": _list_values(entries, "zone_slots"),
        "prerequisites": _list_values(entries, "prerequisites"),
        "shared_capacity_modifier": _scalar_value(
            entries,
            "shared_capacity_modifier",
        ),
        "source_path": str(path),
        "source_line": line,
    }


@lru_cache(maxsize=512)
def building_upgrade_rule(
    game_root: Path,
    source_building_id: str,
    target_building_id: str,
) -> dict[str, Any]:
    """Verify one direct building upgrade against installed vanilla rules."""
    source = find_definition_source(
        game_root,
        "building",
        source_building_id,
    )
    target = find_definition_source(
        game_root,
        "building",
        target_building_id,
    )
    if source is None or target is None:
        return {
            "status": "missing",
            "source_building_id": source_building_id,
            "target_building_id": target_building_id,
        }

    source_path, source_block, source_line = source
    target_path, target_block, target_line = target
    declared_targets = _list_values(
        _object_entries(source_block, source_building_id),
        "upgrades",
    )
    if target_building_id not in declared_targets:
        return {
            "status": "not_direct",
            "source_building_id": source_building_id,
            "target_building_id": target_building_id,
            "declared_targets": declared_targets,
            "source_path": str(source_path),
            "source_line": source_line,
        }

    target_entries = _object_entries(target_block, target_building_id)
    return {
        "status": "available",
        "source_building_id": source_building_id,
        "target_building_id": target_building_id,
        "prerequisites": _list_values(target_entries, "prerequisites"),
        "requires_upgraded_capital": bool(
            re.search(r"\bhas_upgraded_capital\s*=\s*yes\b", target_block)
        ),
        "source_path": str(source_path),
        "source_line": source_line,
        "target_path": str(target_path),
        "target_line": target_line,
    }


def _ringworld_cost_condition(
    entries: list[tuple[str, Any]],
) -> bool | None:
    """Read the one planet condition that the first-run planner can prove."""
    for trigger in _child_blocks(entries, "trigger"):
        values = [(key, value) for key, value in trigger if key != "__value__"]
        if len(values) != 1:
            continue
        key, raw_value = values[0]
        if key != "has_ringworld_output_boost" or isinstance(raw_value, list):
            continue
        normalized = str(raw_value).lower()
        if normalized in {"yes", "no"}:
            return normalized == "yes"
    return None


def _inline_ringworld_cost_condition(value: str | None) -> bool | None:
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*has_ringworld_output_boost\s*=\s*(yes|no)\s*",
        value,
        flags=re.IGNORECASE,
    )
    return match.group(1).lower() == "yes" if match else None


@lru_cache(maxsize=512)
def construction_cost_rule(
    game_root: Path,
    kind: str,
    object_id: str,
) -> dict[str, Any]:
    """Read the regular-empire build cost without evaluating conditional branches."""
    source = find_definition_source(game_root, kind, object_id)
    if source is None:
        return {"status": "missing", "object_id": object_id}

    path, block, line = source
    variables = variables_for_source(game_root, path)
    entries = _object_entries(block, object_id)
    options: list[dict[str, float | int]] = []
    conditional_options: list[dict[str, Any]] = []
    unresolved_condition = False
    for resources in _child_blocks(entries, "resources"):
        unconditional: dict[str, float | int] = {}
        conditional: dict[bool, dict[str, float | int]] = {}
        for direct_cost in _child_blocks(resources, "cost"):
            cost: dict[str, float | int] = {}
            for resource, raw_value in direct_cost:
                if resource in {"__value__", "trigger"} or isinstance(raw_value, list):
                    continue
                resolved = _resolved_number(raw_value, variables)
                if resolved is not None:
                    cost[str(resource)] = resolved
            if cost:
                if _child_blocks(direct_cost, "trigger"):
                    ringworld_value = _ringworld_cost_condition(direct_cost)
                    if ringworld_value is None:
                        unresolved_condition = True
                    else:
                        conditional.setdefault(ringworld_value, {}).update(cost)
                else:
                    unconditional.update(cost)

        # Vanilla 4.4 buildings commonly delegate regular/nomadic currency
        # selection to this inline script.  Its call-site parameters contain
        # the regular empire resource and exact scripted-variable cost.
        for inline_script in _child_blocks(resources, "inline_script"):
            script = _scalar_value(inline_script, "script") or ""
            resource = _scalar_value(inline_script, "REGULAR_RESOURCE")
            raw_value = _scalar_value(inline_script, "COST")
            resolved = _resolved_number(raw_value, variables) if raw_value else None
            if not resource or resolved is None or float(resolved) < 0:
                continue
            if script.endswith("nomadic_cost_switcher"):
                unconditional[resource] = resolved
            elif script.endswith("nomadic_cost_switcher_with_additional_trigger"):
                ringworld_value = _inline_ringworld_cost_condition(
                    _scalar_value(inline_script, "TRIGGER")
                )
                if ringworld_value is None:
                    unresolved_condition = True
                else:
                    conditional.setdefault(ringworld_value, {})[resource] = resolved

        if conditional:
            for ringworld_value, branch in conditional.items():
                cost = {**unconditional, **branch}
                options.append(cost)
                conditional_options.append(
                    {
                        "condition": {
                            "has_ringworld_output_boost": ringworld_value,
                        },
                        "cost": cost,
                    }
                )
        elif unconditional:
            options.append(unconditional)

    unique_options = {tuple(sorted(option.items())) for option in options}
    if unresolved_condition:
        return {
            "status": "ambiguous",
            "object_id": object_id,
            "source_path": str(path),
            "source_line": line,
        }
    if conditional_options:
        return {
            "status": "conditional",
            "object_id": object_id,
            "conditional_costs": conditional_options,
            "source_path": str(path),
            "source_line": line,
        }
    if len(unique_options) != 1:
        return {
            "status": "unknown" if not unique_options else "ambiguous",
            "object_id": object_id,
            "source_path": str(path),
            "source_line": line,
        }

    return {
        "status": "available",
        "object_id": object_id,
        "cost": dict(next(iter(unique_options))),
        "source_path": str(path),
        "source_line": line,
    }


@lru_cache(maxsize=512)
def deposit_capacity_rule(
    game_root: Path,
    deposit_type: str,
) -> dict[str, Any]:
    """Read unconditional district-capacity modifiers from one deposit."""
    source = find_definition_source(game_root, "deposit", deposit_type)
    if source is None:
        return {"status": "missing", "deposit_type": deposit_type}

    path, block, line = source
    variables, _ = scripted_variables(game_root)
    contributions: dict[str, float | int] = {}
    contribution_sources: set[str] = set()
    for referenced_path, text in _referenced_rule_texts(
        game_root,
        block,
        path,
    ):
        entries = (
            _object_entries(text, deposit_type)
            if referenced_path == path
            else parse_clausewitz(text)
        )
        for key, value in entries:
            if key != "planet_modifier" or not isinstance(value, list):
                continue
            for assignment in numeric_assignments(value, variables):
                field = assignment["path"].split(".")[-1]
                if field not in DISTRICT_CAPACITY_FIELDS:
                    continue
                current = float(contributions.get(field, 0))
                updated = current + float(assignment["resolved"])
                contributions[field] = int(updated) if updated.is_integer() else updated
                contribution_sources.add(str(referenced_path))

    entries = _object_entries(block, deposit_type)
    category = _scalar_value(entries, "category") or ""
    return {
        "status": "available",
        "deposit_type": deposit_type,
        "capacity_modifiers": contributions,
        "blocks_district_slots": category.startswith("deposit_cat_blockers"),
        "source_path": str(path),
        "source_line": line,
        "capacity_sources": sorted(contribution_sources),
    }


@lru_cache(maxsize=256)
def static_modifier_capacity_rule(
    game_root: Path,
    modifier_type: str,
) -> dict[str, Any]:
    """Read direct capacity fields from one active static modifier."""
    source = find_definition_source(
        game_root,
        "static_modifier",
        modifier_type,
    )
    if source is None:
        return {"status": "missing", "modifier_type": modifier_type}

    path, block, line = source
    variables, _ = scripted_variables(game_root)
    contributions: dict[str, float | int] = {}
    for key, value in _object_entries(block, modifier_type):
        if key not in DISTRICT_CAPACITY_FIELDS or isinstance(value, list):
            continue
        resolved = _resolved_number(value, variables)
        if resolved is not None:
            contributions[key] = resolved

    return {
        "status": "available",
        "modifier_type": modifier_type,
        "capacity_modifiers": contributions,
        "source_path": str(path),
        "source_line": line,
    }


@lru_cache(maxsize=512)
def building_capacity_rule(
    game_root: Path,
    building_type: str,
) -> dict[str, Any]:
    """Read unconditional planet capacity fields from a built structure."""
    source = find_definition_source(game_root, "building", building_type)
    if source is None:
        return {"status": "missing", "building_type": building_type}

    path, block, line = source
    variables, _ = scripted_variables(game_root)
    contributions: dict[str, float | int] = {}
    contribution_sources: set[str] = set()
    for referenced_path, text in _referenced_rule_texts(game_root, block, path):
        entries = (
            _object_entries(text, building_type)
            if referenced_path == path
            else parse_clausewitz(text)
        )
        for key, value in entries:
            # Conditional triggered modifiers require trigger evaluation.  They
            # are deliberately excluded from this conservative capacity floor.
            if key != "planet_modifier" or not isinstance(value, list):
                continue
            for assignment in numeric_assignments(value, variables):
                field = assignment["path"].split(".")[-1]
                if field not in DISTRICT_CAPACITY_FIELDS:
                    continue
                current = float(contributions.get(field, 0))
                updated = current + float(assignment["resolved"])
                contributions[field] = int(updated) if updated.is_integer() else updated
                contribution_sources.add(str(referenced_path))

    return {
        "status": "available",
        "building_type": building_type,
        "capacity_modifiers": contributions,
        "source_path": str(path),
        "source_line": line,
        "capacity_sources": sorted(contribution_sources),
    }


def _planet_building_types(planet: dict[str, Any]) -> list[str]:
    """Return each built structure once, even if nested/flat zone views coexist."""
    building_types: list[str] = []
    seen_objects: set[object] = set()
    for zone in planet.get("zones", []):
        for building in zone.get("buildings", []):
            object_id = building.get("object_id")
            identity: object = (
                ("object", object_id)
                if object_id is not None
                else ("type", building.get("type"), len(building_types))
            )
            if identity in seen_objects:
                continue
            seen_objects.add(identity)
            building_type = str(building.get("type") or "")
            if building_type:
                building_types.append(building_type)
    return building_types


def district_capacity_profile(
    planet: dict[str, Any],
    district_types: set[str],
    deposit_rules: dict[str, dict[str, Any]],
    static_modifier_rules: dict[str, dict[str, Any]],
    building_rules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a conservative capacity floor from synchronized save evidence."""
    planet_size = planet.get("planet_size")
    if not isinstance(planet_size, int) or planet_size < 0:
        return {
            "status": "unavailable",
            "reason": "planet_size is absent from the synchronized save",
        }

    modifier_totals: dict[str, float] = {}
    modifier_sources: list[dict[str, Any]] = []
    blocked_slots = 0.0
    missing_deposits: list[str] = []
    for deposit_type in planet.get("deposit_types", []):
        rule = deposit_rules.get(str(deposit_type), {})
        if rule.get("status") != "available":
            missing_deposits.append(str(deposit_type))
            continue
        contributions = rule.get("capacity_modifiers", {})
        for field, value in contributions.items():
            numeric_value = float(value)
            if (
                rule.get("blocks_district_slots")
                and field == "planet_max_districts_add"
                and numeric_value < 0
            ):
                blocked_slots += -numeric_value
                continue
            modifier_totals[field] = modifier_totals.get(field, 0.0) + float(value)
        if contributions:
            modifier_sources.append(
                {
                    "kind": "deposit",
                    "id": str(deposit_type),
                    "capacity_modifiers": contributions,
                    "blocks_district_slots": bool(rule.get("blocks_district_slots")),
                }
            )

    missing_static_modifiers: list[str] = []
    for modifier_type in planet.get("active_modifiers", []):
        rule = static_modifier_rules.get(str(modifier_type), {})
        if rule.get("status") != "available":
            missing_static_modifiers.append(str(modifier_type))
            continue
        contributions = rule.get("capacity_modifiers", {})
        for field, value in contributions.items():
            modifier_totals[field] = modifier_totals.get(field, 0.0) + float(value)
        if contributions:
            modifier_sources.append(
                {
                    "kind": "static_modifier",
                    "id": str(modifier_type),
                    "capacity_modifiers": contributions,
                }
            )

    missing_buildings: list[str] = []
    for building_type in _planet_building_types(planet):
        rule = building_rules.get(building_type, {})
        if rule.get("status") != "available":
            missing_buildings.append(building_type)
            continue
        contributions = rule.get("capacity_modifiers", {})
        for field, value in contributions.items():
            modifier_totals[field] = modifier_totals.get(field, 0.0) + float(value)
        if contributions:
            modifier_sources.append(
                {
                    "kind": "building",
                    "id": building_type,
                    "capacity_modifiers": contributions,
                }
            )

    unsafe_multipliers = {
        field: value
        for field, value in modifier_totals.items()
        if field.endswith("_mult") and value < 0
    }
    if unsafe_multipliers:
        return {
            "status": "ambiguous",
            "reason": "negative multiplicative district-capacity modifiers need evaluation",
            "unsupported_modifiers": unsafe_multipliers,
        }
    ignored_positive_multipliers = {
        field: value
        for field, value in modifier_totals.items()
        if field.endswith("_mult") and value > 0
    }

    built_by_type: dict[str, int] = {}
    for district in planet.get("districts", []):
        district_type = str(district.get("type") or "")
        level = district.get("level")
        if district_type and isinstance(level, int) and level >= 0:
            built_by_type[district_type] = built_by_type.get(district_type, 0) + level

    pending_by_type: dict[str, int] = {}
    for item in planet.get("construction", {}).get("pending_items", []):
        if item.get("kind") != "district":
            continue
        district_type = str(item.get("district_type") or "")
        if district_type:
            pending_by_type[district_type] = pending_by_type.get(district_type, 0) + 1

    total_modifier_add = math.floor(modifier_totals.get("planet_max_districts_add", 0))
    total_maximum = max(planet_size + total_modifier_add, 0)
    blocked_slots_floor = max(math.ceil(blocked_slots), 0)
    usable_capacity = max(total_maximum - blocked_slots_floor, 0)
    built_total = sum(built_by_type.values())
    pending_total = sum(pending_by_type.values())
    total_remaining = max(usable_capacity - built_total - pending_total, 0)

    by_type: dict[str, dict[str, Any]] = {}
    for district_type in sorted(district_types):
        built = built_by_type.get(district_type, 0)
        pending = pending_by_type.get(district_type, 0)
        if district_type == "district_city":
            capacity_floor = total_maximum
            capacity_basis = "planet_total_capacity"
        else:
            capacity_floor = max(
                math.floor(modifier_totals.get(f"{district_type}_max_add", 0)),
                0,
            )
            capacity_basis = "save_evidenced_additive_modifiers"
        type_remaining = max(capacity_floor - built - pending, 0)
        by_type[district_type] = {
            "capacity_floor": capacity_floor,
            "capacity_basis": capacity_basis,
            "built": built,
            "pending": pending,
            "remaining_by_type_floor": type_remaining,
            "remaining_buildable_floor": min(
                type_remaining,
                total_remaining,
            ),
        }

    return {
        "status": "available",
        "basis": (
            "planet size, deposits, active static modifiers, and built "
            "structures resolved against version-matched installed rules"
        ),
        "missing_deposit_definitions": sorted(set(missing_deposits)),
        "missing_static_modifier_definitions": sorted(set(missing_static_modifiers)),
        "missing_building_definitions": sorted(set(missing_buildings)),
        "ignored_positive_multipliers": ignored_positive_multipliers,
        "modifier_sources": modifier_sources,
        "total": {
            "planet_size": planet_size,
            "known_modifier_add": total_modifier_add,
            "capacity": total_maximum,
            "blocked_slots": blocked_slots_floor,
            "usable_capacity": usable_capacity,
            "built": built_total,
            "pending": pending_total,
            "remaining": total_remaining,
        },
        "by_type": by_type,
    }


@lru_cache(maxsize=128)
def zone_slot_layout_rule(
    game_root: Path,
    slot_id: str,
) -> dict[str, Any]:
    source = find_definition_source(game_root, "zone_slot", slot_id)
    if source is None:
        return {"status": "missing", "slot_id": slot_id}
    path, block, line = source
    entries = _object_entries(block, slot_id)
    return {
        "status": "available",
        "slot_id": slot_id,
        "start_zone": _scalar_value(entries, "start"),
        "included_zone_sets": _list_values(entries, "included_zone_sets"),
        "definition_block": block,
        "source_path": str(path),
        "source_line": line,
    }


@lru_cache(maxsize=256)
def building_capital_tier(
    game_root: Path,
    building_type: str,
) -> int | None:
    source = find_definition_source(game_root, "building", building_type)
    if source is None:
        return None
    _, block, _ = source
    variables, _ = scripted_variables(game_root)
    values, _ = _resolved_field_values(
        [(Path("<definition>"), block)],
        "capital_tier",
        variables,
    )
    positive = {value for value in values if value >= 0}
    return next(iter(positive)) if len(positive) == 1 else None


def _planet_capital_tier(game_root: Path, planet: dict[str, Any]) -> int:
    tiers: list[int] = []
    for zone in planet.get("zones", []):
        for building in zone.get("buildings", []):
            building_type = str(building.get("type") or "")
            if not building_type:
                continue
            tier = building_capital_tier(game_root, building_type)
            if tier is not None:
                tiers.append(tier)
    return max(tiers, default=0)


def _zone_slot_unlock_status(
    slot_rule: dict[str, Any],
    *,
    planet: dict[str, Any],
    capital_tier: int,
    known_technologies: set[str],
) -> dict[str, Any]:
    if slot_rule.get("status") != "available":
        return {
            "unlocked": False,
            "reason": "zone_slot_definition_unavailable",
        }
    if slot_rule.get("start_zone"):
        return {
            "unlocked": False,
            "reason": "start_zone_is_created_automatically",
            "start_zone": slot_rule.get("start_zone"),
        }

    slot_id = str(slot_rule.get("slot_id") or "")
    required_technology = RESOURCE_ZONE_SLOT_TECHNOLOGIES.get(slot_id)
    if required_technology is not None:
        unlocked = required_technology in known_technologies
        return {
            "unlocked": unlocked,
            "reason": (
                "required_technology_known"
                if unlocked
                else "requires_technology_or_unmodeled_functional_civic"
            ),
            "required_technology": required_technology,
            "functional_civic_alternatives_modeled": False,
        }

    block = str(slot_rule.get("definition_block") or "")
    if "d_collapsed_spire" in block:
        blocked = "d_collapsed_spire" in set(planet.get("deposit_types", []))
        return {
            "unlocked": not blocked,
            "reason": (
                "blocked_by_d_collapsed_spire"
                if blocked
                else "d_collapsed_spire_absent"
            ),
        }
    if "has_upgraded_capital" in block:
        return {
            "unlocked": capital_tier >= 2,
            "reason": (
                "upgraded_capital_present"
                if capital_tier >= 2
                else "requires_upgraded_capital"
            ),
            "capital_tier": capital_tier,
            "minimum_capital_tier": 2,
        }
    unlocked = bool(re.search(r"\balways\s*=\s*yes\b", block))
    return {
        "unlocked": unlocked,
        "reason": "always_unlocked" if unlocked else "unlock_rule_unresolved",
    }


def enrich_snapshot_layout(
    config: dict[str, Any],
    capabilities: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Combine save occupancy with version-matched installed layout rules."""
    game_root = detect_game_root(config)
    if game_root is None:
        snapshot["layout_rules"] = {
            "status": "unavailable",
            "reason": "Stellaris installation was not found.",
        }
        return snapshot

    identity = install_identity(game_root)
    save_version = normalized_version(snapshot.get("source_save", {}).get("version"))
    local_version = identity.get("normalized_version")
    if (
        save_version is not None
        and local_version is not None
        and save_version != local_version
    ):
        snapshot["layout_rules"] = {
            "status": "version_mismatch",
            "save_version": save_version,
            "local_version": local_version,
        }
        return snapshot

    current_zone_types = {
        str(zone.get("type") or "")
        for planet in snapshot.get("planets", [])
        for zone in planet.get("zones", [])
        if zone.get("type")
    }
    target_zone_types = {
        str(zone_type)
        for zone_type, value in capabilities.get("zones", {}).items()
        if value.get("enabled")
    }
    zone_rules = {
        zone_type: zone_layout_rule(game_root, zone_type)
        for zone_type in sorted(current_zone_types | target_zone_types)
    }
    district_types = {
        str(district.get("type") or "")
        for planet in snapshot.get("planets", [])
        for district in planet.get("districts", [])
        if district.get("type")
    }
    district_types.update(
        str(district_type)
        for district_type, value in capabilities.get("districts", {}).items()
        if value.get("enabled")
    )
    district_rules = {
        district_type: district_layout_rule(game_root, district_type)
        for district_type in sorted(district_types)
    }
    deposit_types = {
        str(deposit_type)
        for planet in snapshot.get("planets", [])
        for deposit_type in planet.get("deposit_types", [])
        if deposit_type
    }
    deposit_rules = {
        deposit_type: deposit_capacity_rule(game_root, deposit_type)
        for deposit_type in sorted(deposit_types)
    }
    active_modifier_types = {
        str(modifier_type)
        for planet in snapshot.get("planets", [])
        for modifier_type in planet.get("active_modifiers", [])
        if modifier_type
    }
    static_modifier_rules = {
        modifier_type: static_modifier_capacity_rule(game_root, modifier_type)
        for modifier_type in sorted(active_modifier_types)
    }
    building_types = {
        building_type
        for planet in snapshot.get("planets", [])
        for building_type in _planet_building_types(planet)
    }
    building_rules = {
        building_type: building_capacity_rule(game_root, building_type)
        for building_type in sorted(building_types)
    }
    target_building_types = {
        str(building_type)
        for building_type, value in capabilities.get("buildings", {}).items()
        if value.get("enabled")
    }
    configured_upgrades = [
        value
        for value in capabilities.get("building_upgrades", [])
        if isinstance(value, dict)
        and value.get("enabled")
        and value.get("from_building_id")
        and value.get("to_building_id")
    ]
    upgrade_target_types = {
        str(value["to_building_id"]) for value in configured_upgrades
    }
    building_upgrade_rules: dict[str, dict[str, dict[str, Any]]] = {}
    for value in configured_upgrades:
        source_building_id = str(value["from_building_id"])
        target_building_id = str(value["to_building_id"])
        building_upgrade_rules.setdefault(source_building_id, {})[
            target_building_id
        ] = building_upgrade_rule(
            game_root,
            source_building_id,
            target_building_id,
        )
    construction_costs = {
        "build_building": {
            building_type: construction_cost_rule(
                game_root,
                "building",
                building_type,
            )
            for building_type in sorted(target_building_types)
        },
        "upgrade_building": {
            building_type: construction_cost_rule(
                game_root,
                "building",
                building_type,
            )
            for building_type in sorted(upgrade_target_types)
        },
        "replace_building": {
            building_type: construction_cost_rule(
                game_root,
                "building",
                building_type,
            )
            for building_type in sorted(target_building_types)
        },
        "build_district": {
            district_type: construction_cost_rule(
                game_root,
                "district",
                district_type,
            )
            for district_type in sorted(district_types)
        },
        "build_zone": {
            zone_type: construction_cost_rule(game_root, "zone", zone_type)
            for zone_type in sorted(target_zone_types)
        },
    }

    known_technologies = {
        str(technology)
        for technology in snapshot.get("known_technologies", [])
        if technology
    }

    for planet in snapshot.get("planets", []):
        flat_zones = {
            int(zone["zone_id"]): zone
            for zone in planet.get("zones", [])
            if isinstance(zone.get("zone_id"), int)
        }
        pending_zone_slots = {
            (
                int(item["district_id"]),
                int(item["slot_selector"]),
            ): item
            for item in planet.get("construction", {}).get(
                "pending_items",
                [],
            )
            if item.get("kind") == "zone"
            and isinstance(item.get("district_id"), int)
            and isinstance(item.get("slot_selector"), int)
        }
        capital_tier = _planet_capital_tier(game_root, planet)
        planet["capital_tier"] = capital_tier
        planet["district_capacity"] = district_capacity_profile(
            planet,
            district_types,
            deposit_rules,
            static_modifier_rules,
            building_rules,
        )

        for district in planet.get("districts", []):
            district_type = str(district.get("type") or "")
            district_rule = district_rules.get(district_type, {})
            slot_ids = list(district_rule.get("zone_slot_ids") or [])
            raw_references = list(district.get("zone_slot_references") or [])
            if len(raw_references) > len(slot_ids):
                slot_ids = []

            references = raw_references + [None] * max(
                len(slot_ids) - len(raw_references),
                0,
            )
            available_zone_slots: list[dict[str, Any]] = []
            locked_zone_slots: list[dict[str, Any]] = []
            zone_slot_statuses: list[dict[str, Any]] = []
            for selector, slot_id in enumerate(slot_ids):
                slot_rule = zone_slot_layout_rule(game_root, slot_id)
                unlock_status = _zone_slot_unlock_status(
                    slot_rule,
                    planet=planet,
                    capital_tier=capital_tier,
                    known_technologies=known_technologies,
                )
                reference = references[selector]
                pending_zone = pending_zone_slots.get(
                    (int(district["district_id"]), selector)
                )
                slot_status = {
                    "slot_selector": selector,
                    "slot_id": slot_id,
                    "zone_id": reference,
                    "is_start_zone": bool(slot_rule.get("start_zone")),
                    "included_zone_sets": slot_rule.get(
                        "included_zone_sets",
                        [],
                    ),
                    "unlock": unlock_status,
                }
                if reference is not None:
                    slot_status["state"] = "occupied"
                    zone_slot_statuses.append(slot_status)
                    continue
                if pending_zone is not None:
                    slot_status["state"] = "pending"
                    slot_status["pending_item_id"] = pending_zone.get("item_id")
                    slot_status["pending_zone_type"] = pending_zone.get("zone_type")
                    zone_slot_statuses.append(slot_status)
                    continue
                if bool(unlock_status.get("unlocked")):
                    slot_status["state"] = "available"
                    available_zone_slots.append(
                        {
                            "slot_selector": selector,
                            "slot_id": slot_id,
                            "included_zone_sets": slot_rule.get(
                                "included_zone_sets",
                                [],
                            ),
                        }
                    )
                else:
                    slot_status["state"] = "locked"
                    locked_zone_slots.append(
                        {
                            "slot_selector": selector,
                            "slot_id": slot_id,
                            "unlock": unlock_status,
                        }
                    )
                zone_slot_statuses.append(slot_status)
            district["zone_slot_ids"] = slot_ids
            district["zone_slot_capacity"] = len(slot_ids)
            district["zone_slot_statuses"] = zone_slot_statuses
            district["available_zone_slots"] = available_zone_slots
            district["locked_zone_slots"] = locked_zone_slots
            if district_type == "district_city" and slot_ids:
                specialization_slots = [
                    item for item in zone_slot_statuses if not item["is_start_zone"]
                ]
                district["zone_slot_count_is_fixed_by_district_type"] = True
                district["additional_district_levels_unlock_zone_slots"] = False
                district["specialization_zone_capacity"] = len(specialization_slots)
                district["specialization_zones_occupied"] = sum(
                    item["state"] == "occupied" for item in specialization_slots
                )
                district["specialization_zones_pending"] = sum(
                    item["state"] == "pending" for item in specialization_slots
                )
                district["specialization_zone_slots_available"] = sum(
                    item["state"] == "available" for item in specialization_slots
                )
                district["specialization_zone_slots_locked"] = sum(
                    item["state"] == "locked" for item in specialization_slots
                )

            for nested_zone in district.get("zones", []):
                zone_id = int(nested_zone["zone_id"])
                flat_zone = flat_zones.get(zone_id)
                targets = [nested_zone]
                if flat_zone is not None:
                    targets.append(flat_zone)

                zone_type = str(nested_zone.get("type") or "")
                zone_rule = zone_rules.get(zone_type, {})
                capacity = zone_rule.get("building_slot_capacity")

                occupied_positions = sorted(
                    {
                        int(building["position"])
                        for building in nested_zone.get("buildings", [])
                        if isinstance(building.get("position"), int)
                    }
                )
                available_positions = (
                    [
                        position
                        for position in range(capacity)
                        if position not in occupied_positions
                    ]
                    if isinstance(capacity, int) and capacity >= 0
                    else []
                )
                for target in targets:
                    target["building_slot_capacity"] = capacity
                    target["occupied_building_positions"] = occupied_positions
                    target["available_building_positions"] = available_positions
                    target["building_slot_capacity_source"] = {
                        "kind": zone_rule.get("capacity_kind"),
                        "paths": zone_rule.get("capacity_sources", []),
                    }

    snapshot["layout_rules"] = {
        "status": "available",
        "game_root": str(game_root),
        "save_version": save_version,
        "local_version": local_version,
        "zone_rules": zone_rules,
        "district_rules": district_rules,
        "deposit_rules": deposit_rules,
        "building_upgrade_rules": building_upgrade_rules,
        "construction_costs": construction_costs,
    }
    snapshot.setdefault("data_quality", {}).setdefault("precision", {})[
        "layout_slots"
    ] = (
        "occupied positions from save; capacities and zone-slot definitions "
        "from the matching installed Stellaris rules"
    )
    snapshot.setdefault("data_quality", {}).setdefault("precision", {})[
        "district_capacity"
    ] = (
        "conservative floor from save planet size and unconditional deposit "
        "modifiers in the matching installed Stellaris rules"
    )
    snapshot.setdefault("data_quality", {}).setdefault("precision", {})[
        "building_upgrades"
    ] = (
        "exact building object IDs and positions from the save; direct upgrade "
        "edges and prerequisites from matching installed Stellaris rules"
    )
    snapshot.setdefault("data_quality", {}).setdefault("precision", {})[
        "building_replacements"
    ] = (
        "exact source building object IDs and positions from the save; target "
        "costs and prerequisites from matching installed Stellaris rules"
    )
    return snapshot


def definition_evidence(
    game_root: Path,
    kind: str,
    object_id: str,
    variables: dict[str, float],
) -> dict[str, Any]:
    relative = DEFINITION_DIRS[kind]
    definition_root = game_root / relative
    source = find_definition_source(game_root, kind, object_id)
    if source is not None:
        path, block, line = source
        parsed = parse_clausewitz(block)
        references = sorted(
            {
                match
                for match in re.findall(
                    r"\b(?:building|zone|district|tech|jobs?)_[A-Za-z0-9_.-]+\b",
                    block,
                )
                if match != object_id
            }
        )
        return {
            "status": "available",
            "kind": kind,
            "object_id": object_id,
            "source_path": str(path),
            "source_line": line,
            "source_sha256": file_sha256(path),
            "numeric_assignments": numeric_assignments(parsed, variables),
            "references": references,
            "definition_excerpt": block[:6000],
            "excerpt_truncated": len(block) > 6000,
        }
    return {
        "status": "missing",
        "kind": kind,
        "object_id": object_id,
        "searched_under": str(definition_root),
    }


def build_local_rules_context(
    config: dict[str, Any],
    capabilities: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    game_root = detect_game_root(config)
    if game_root is None:
        return {
            "schema": "iag.local_rules.v1",
            "status": "unavailable",
            "warning": "Stellaris installation was not found.",
            "objects": [],
        }

    identity = install_identity(game_root)
    variables, _ = scripted_variables(game_root)
    requested: list[tuple[str, str]] = []
    requested.extend(
        ("building", object_id)
        for object_id, value in capabilities.get("buildings", {}).items()
        if value.get("enabled")
    )
    requested.extend(
        ("building", str(value[field]))
        for value in capabilities.get("building_upgrades", [])
        if isinstance(value, dict) and value.get("enabled")
        for field in ("from_building_id", "to_building_id")
        if value.get(field)
    )
    requested.extend(
        ("zone", object_id)
        for object_id, value in capabilities.get("zones", {}).items()
        if value.get("enabled")
    )
    requested.extend(
        (
            "district",
            str(value.get("district_type")),
        )
        for value in capabilities.get("zones", {}).values()
        if value.get("enabled") and value.get("district_type")
    )

    seen: set[tuple[str, str]] = set()
    objects: list[dict[str, Any]] = []
    for kind, object_id in requested:
        key = (kind, object_id)
        if key in seen:
            continue
        seen.add(key)
        objects.append(definition_evidence(game_root, kind, object_id, variables))

    save_version = normalized_version(snapshot.get("source_save", {}).get("version"))
    local_version = identity.get("normalized_version")
    version_matches = (
        save_version == local_version
        if save_version is not None and local_version is not None
        else None
    )
    warnings: list[str] = []
    if version_matches is False:
        warnings.append(
            f"Save version {save_version} differs from local rules {local_version}."
        )
    if any(item["status"] != "available" for item in objects):
        warnings.append("One or more legal object definitions were not found.")

    return {
        "schema": "iag.local_rules.v1",
        "status": "available" if not warnings else "warning",
        "install": identity,
        "save_version": save_version,
        "version_matches_save": version_matches,
        "scope": "installed vanilla rules; dynamic modifiers and active mod overrides are not evaluated",
        "extraction_notes": [
            "Literal numbers and @scripted_variables are resolved.",
            "Trigger branches and dynamic scripted values remain unevaluated.",
            "Raw workforce values must not be described as whole pops without conversion evidence.",
        ],
        "warnings": warnings,
        "objects": objects,
    }
