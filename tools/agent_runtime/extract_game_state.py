#!/usr/bin/env python3
"""Extract an auditable Stellaris 4.x empire snapshot from a save.

The parser intentionally reads only stable save structures already observed in
Pegasus saves. Missing fields become null/empty values instead of invented
defaults. The result is planning input; it never edits a save.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAVE_STATE_DIR = Path(__file__).resolve().parents[1] / "save_state"
if str(SAVE_STATE_DIR) not in sys.path:
    sys.path.insert(0, str(SAVE_STATE_DIR))

from extract_planet_profiles import (  # noqa: E402
    extract_profiles,
    find_braced_section,
    integer_list,
    parse_numeric_map,
    planet_name,
    quoted_scalar,
    scalar,
)


RESOURCE_LINE_RE = re.compile(
    r"(?m)^\s*([a-z_][a-z0-9_]*)=(-?\d+(?:\.\d+)?)\s*$"
)
DATE_RE = re.compile(r'(?m)^date="([^"]+)"\s*$')
TECH_RE = re.compile(r'(?m)^\s*technology="([^"]+)"\s*$')
AMOUNT_RE = re.compile(r"(?m)^\s*amount=(\d+)\s*$")

PRIMARY_RESOURCES = (
    "energy",
    "minerals",
    "food",
    "consumer_goods",
    "alloys",
    "unity",
    "trade",
    "physics_research",
    "society_research",
    "engineering_research",
    "volatile_motes",
    "exotic_gases",
    "rare_crystals",
    "sr_living_metal",
    "sr_zro",
    "sr_dark_matter",
    "nanites",
)

SPECIAL_PLANET_MARKERS = (
    "habitat",
    "ringworld",
    "ecumenopolis",
    "relic",
    "hive",
    "machine",
    "resort",
    "thrall",
)
CARRIER_PLANET_NAME_KEYS = (
    "NAME_IAG_Carrier_Enclave",
    "NAME_IAG_Carrier_Zone_Enclave",
)


def roman_numeral(value: int) -> str:
    numerals = (
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    output: list[str] = []
    remaining = max(value, 0)
    for amount, numeral in numerals:
        while remaining >= amount:
            output.append(numeral)
            remaining -= amount
    return "".join(output)


def planet_display_name_hint(
    name_key: str,
    name_variables: dict[str, str],
) -> str:
    """Render a stable, non-localised hint for generated system planet names."""
    match = re.fullmatch(r"NEW_COLONY_NAME_(\d+)", name_key)
    system_name = name_variables.get("NAME")
    if not match or not system_name:
        return name_key
    suffix = roman_numeral(int(match.group(1)))
    return f"{system_name}-{suffix}" if suffix else system_name


def is_iag_carrier_planet(
    planet_block: str, colony_block: str, name: str
) -> bool:
    return (
        "iag_carrier_enclave" in planet_block
        or "iag_carrier_enclave" in colony_block
        or any(key in planet_block or name == key for key in CARRIER_PLANET_NAME_KEYS)
    )




def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_gamestate(save_path: Path) -> str:
    try:
        with zipfile.ZipFile(save_path) as archive:
            return archive.read("gamestate").decode(
                "utf-8-sig", errors="replace"
            )
    except zipfile.BadZipFile:
        return save_path.read_text(encoding="utf-8-sig", errors="replace")


def load_save_metadata(save_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(save_path) as archive:
            text = archive.read("meta").decode("utf-8-sig", errors="replace")
    except (zipfile.BadZipFile, KeyError):
        return {}
    return {
        "version": quoted_scalar(text, "version"),
        "name": quoted_scalar(text, "name"),
        "date": quoted_scalar(text, "date"),
        "version_control_revision": scalar(text, "version_control_revision"),
    }

def optional_section(block: str, name: str) -> str:
    try:
        return find_braced_section(block, name, allow_indent=True)
    except ValueError:
        return ""


def float_scalar(block: str, key: str) -> float | None:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}=(-?\d+(?:\.\d+)?)\s*$",
        block,
    )
    return float(match.group(1)) if match else None


def bool_scalar(block: str, key: str) -> bool | None:
    match = re.search(
        rf"(?m)^\s*{re.escape(key)}=(yes|no)\s*$",
        block,
    )
    if not match:
        return None
    return match.group(1) == "yes"


def loose_quoted_scalar(block: str, key: str) -> str | None:
    match = re.search(
        rf'(?m)^\s*{re.escape(key)}=\s*"([^"]*)"\s*$',
        block,
    )
    return match.group(1) if match else None


def resource_values(block: str) -> dict[str, float]:
    """Return direct resource values from one resource object."""
    values: dict[str, float] = {}
    for key, raw_value in RESOURCE_LINE_RE.findall(block):
        values[key] = float(raw_value)
    return values


def aggregate_resource_values(block: str) -> dict[str, float]:
    """Sum categorized resource entries, such as budget balance rows."""
    totals: defaultdict[str, float] = defaultdict(float)
    for key, raw_value in RESOURCE_LINE_RE.findall(block):
        totals[key] += float(raw_value)
    return dict(sorted(totals.items()))


def player_identity(text: str) -> dict[str, Any]:
    player_block = find_braced_section(text, "player")
    country_match = re.search(r"(?m)^\s*country=(\d+)\s*$", player_block)
    name_match = re.search(r'(?m)^\s*name="([^"]*)"\s*$', player_block)
    if not country_match:
        raise ValueError("The save does not expose a player country.")
    return {
        "name": name_match.group(1) if name_match else None,
        "country_id": int(country_match.group(1)),
    }


def extract_country_state(
    text: str,
    country_id: int,
) -> tuple[dict[str, Any], set[str]]:
    countries = parse_numeric_map(find_braced_section(text, "country").strip())
    country_block = countries.get(country_id)
    if not country_block:
        raise ValueError(f"Country {country_id} was not found in the save.")

    budget = optional_section(country_block, "budget")
    current_month = optional_section(budget, "current_month")
    monthly_balance = aggregate_resource_values(
        optional_section(current_month, "balance")
    )

    modules = optional_section(country_block, "modules")
    economy_module = optional_section(modules, "standard_economy_module")
    stockpile = resource_values(optional_section(economy_module, "resources"))

    technologies = set(TECH_RE.findall(optional_section(country_block, "tech_status")))
    wars = integer_list(country_block, "wars")
    last_war = loose_quoted_scalar(country_block, "last_date_at_war")

    research_total = sum(
        monthly_balance.get(key, 0.0)
        for key in (
            "physics_research",
            "society_research",
            "engineering_research",
        )
    )
    monthly_balance["research_total"] = research_total

    country_state = {
        "country_id": country_id,
        "stockpile": {
            key: stockpile.get(key)
            for key in PRIMARY_RESOURCES
            if key in stockpile
        },
        "monthly_balance": {
            key: monthly_balance.get(key)
            for key in (*PRIMARY_RESOURCES, "research_total")
            if key in monthly_balance
        },
        "power": {
            key: float_scalar(country_block, key)
            for key in ("military_power", "economy_power", "tech_power")
        },
        "fleet": {
            "fleet_size": scalar(country_block, "fleet_size"),
            "used_naval_capacity": scalar(
                country_block, "used_naval_capacity"
            ),
        },
        "empire_size": scalar(country_block, "empire_size"),
        "num_sapient_pops_raw": scalar(
            country_block, "num_sapient_pops"
        ),
        "war": {
            "active_war_ids": wars,
            "is_at_war": bool(wars)
            or bool_scalar(country_block, "is_at_war") is True,
            "last_date_at_war": last_war,
        },
    }
    return country_state, technologies


def construction_queues(text: str) -> dict[int, str | None]:
    construction = optional_section(text, "construction")
    queue_manager = optional_section(construction, "queue_mgr")
    queues = optional_section(queue_manager, "queues")
    return parse_numeric_map(queues.strip()) if queues else {}


def construction_items(text: str) -> dict[int, str | None]:
    construction = optional_section(text, "construction")
    item_manager = optional_section(construction, "item_mgr")
    items = optional_section(item_manager, "items")
    return parse_numeric_map(items.strip()) if items else {}


def construction_item_profile(
    item_id: int,
    item_block: str | None,
) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "item_id": item_id,
        "kind": "unknown",
        "queue_id": None,
        "progress": None,
        "progress_needed": None,
        "resources": {},
    }
    if not item_block:
        return profile

    profile.update(
        {
            "queue_id": scalar(item_block, "queue"),
            "progress": float_scalar(item_block, "progress"),
            "progress_needed": float_scalar(item_block, "progress_needed"),
            "resources": resource_values(
                optional_section(item_block, "resources")
            ),
        }
    )

    building = optional_section(item_block, "buildable_planet_building")
    if building:
        profile.update(
            {
                "kind": "building",
                "building_id": quoted_scalar(building, "building"),
                "colony_id": scalar(building, "planet"),
                "zone_id": scalar(building, "zone"),
            }
        )
        return profile

    zone = optional_section(item_block, "buildable_zone")
    if zone:
        profile.update(
            {
                "kind": "zone",
                "zone_type": quoted_scalar(zone, "zone"),
                "colony_id": scalar(zone, "planet"),
                "district_id": scalar(zone, "district"),
                "slot_selector": scalar(zone, "zone_slot"),
            }
        )
        return profile

    district = optional_section(item_block, "buildable_district")
    if district:
        profile.update(
            {
                "kind": "district",
                "district_type": quoted_scalar(district, "district"),
                "colony_id": scalar(district, "planet"),
            }
        )
    return profile


def colony_metrics(
    colony_block: str,
    pop_jobs: dict[int, str | None],
) -> dict[str, Any]:
    employable = scalar(colony_block, "employable_pops")
    employed_raw = 0
    workforce_raw = 0
    max_workforce_raw = 0
    active_jobs: list[dict[str, Any]] = []

    for job_id in integer_list(colony_block, "pop_jobs"):
        job_block = pop_jobs.get(job_id)
        if not job_block:
            continue
        max_workforce = scalar(job_block, "max_workforce")
        workforce = scalar(job_block, "workforce")
        amount = sum(int(value) for value in AMOUNT_RE.findall(job_block))
        if max_workforce is not None and max_workforce >= 0:
            employed_raw += amount
            workforce_raw += max(workforce or 0, 0)
            max_workforce_raw += max_workforce
            if max_workforce > 0 or amount > 0:
                active_jobs.append(
                    {
                        "job_id": job_id,
                        "type": quoted_scalar(job_block, "type"),
                        "employed_raw": amount,
                        "workforce_raw": workforce,
                        "max_workforce_raw": max_workforce,
                    }
                )

    unemployed_raw = (
        max((employable or 0) - employed_raw, 0)
        if employable is not None
        else None
    )
    free_workforce_raw = max(max_workforce_raw - workforce_raw, 0)
    free_amenities = float_scalar(colony_block, "free_amenities")
    free_housing = float_scalar(colony_block, "free_housing")

    return {
        "stability": float_scalar(colony_block, "stability"),
        "crime": float_scalar(colony_block, "crime"),
        "amenities": float_scalar(colony_block, "amenities"),
        "amenities_usage": float_scalar(colony_block, "amenities_usage"),
        "free_amenities_raw": free_amenities,
        "free_housing_raw": free_housing,
        "total_housing_raw": float_scalar(colony_block, "total_housing"),
        "housing_usage_raw": float_scalar(colony_block, "housing_usage"),
        "employable_pops_raw": employable,
        "sapient_pops_raw": scalar(colony_block, "num_sapient_pops"),
        "employed_raw": employed_raw,
        "unemployed_raw": unemployed_raw,
        "free_workforce_raw": free_workforce_raw,
        "free_jobs_estimate": round(free_workforce_raw / 100.0, 2),
        "unemployed_pops_estimate": (
            round(unemployed_raw / 100.0, 2)
            if unemployed_raw is not None
            else None
        ),
        "designation": quoted_scalar(colony_block, "designation"),
        "final_designation": quoted_scalar(
            colony_block, "final_designation"
        ),
        "ascension_tier": scalar(colony_block, "ascension_tier"),
        "automation_enabled": (
            bool_scalar(optional_section(colony_block, "automation"), "enabled")
        ),
        "resource_upkeep": resource_values(
            optional_section(colony_block, "upkeep")
        ),
        "resource_production": resource_values(
            optional_section(colony_block, "produces")
        ),
        "resource_profit": resource_values(
            optional_section(colony_block, "profits")
        ),
        "active_jobs": active_jobs,
    }


def colony_is_establishing(colony_block: str) -> bool:
    """Return whether initial colonization growth is still in progress."""
    return (
        float_scalar(colony_block, "colonizing_species") is not None
        or "GROWTH_CAT_COLONIZATION" in colony_block
    )


def planet_ownership(
    planet_block: str,
    current_country_id: int,
) -> dict[str, int | bool | None]:
    """Return explicit ownership evidence without inferring missing fields."""
    owner_id = scalar(planet_block, "owner")
    original_owner_id = scalar(planet_block, "original_owner")
    is_inherited = (
        owner_id == current_country_id
        and original_owner_id is not None
        and original_owner_id != owner_id
    )
    return {
        "owner_id": owner_id,
        "original_owner_id": original_owner_id,
        "is_inherited_colony": is_inherited,
    }


def extract_game_state(
    text: str,
    *,
    save_path: Path | None = None,
    owner: int | None = None,
) -> dict[str, Any]:
    identity = player_identity(text)
    owner_id = identity["country_id"] if owner is None else owner
    country_state, technologies = extract_country_state(text, owner_id)

    profiles = extract_profiles(text, owner_id)
    colonies = parse_numeric_map(find_braced_section(text, "colony").strip())
    pop_jobs = parse_numeric_map(find_braced_section(text, "pop_jobs").strip())
    queues = construction_queues(text)
    items = construction_items(text)

    planet_blocks = parse_numeric_map(
        find_braced_section(
            find_braced_section(text, "planets"),
            "planet",
            allow_indent=True,
        ).strip()
    )

    planets: list[dict[str, Any]] = []
    for profile in profiles["planets"]:
        planet_id = int(profile["planet_id"])
        colony_id = profile.get("colony_id")
        colony_block = colonies.get(colony_id) if colony_id is not None else None
        if not colony_block:
            continue

        queue_id = int(profile["build_queue_id"])
        queue_block = queues.get(queue_id) or ""
        queued_items = integer_list(queue_block, "items")
        pending_items = [
            construction_item_profile(item_id, items.get(item_id))
            for item_id in queued_items
        ]
        planet_block = planet_blocks.get(planet_id) or ""
        planet_class = profile.get("planet_class")
        name = str(profile.get("name") or planet_name(planet_block))
        special_marked = any(
            marker in str(planet_class or "").lower()
            or marker in str(colony_block).lower()
            for marker in SPECIAL_PLANET_MARKERS
        )
        is_carrier = is_iag_carrier_planet(planet_block, colony_block, name)
        is_colonizing = colony_is_establishing(colony_block)
        ownership = planet_ownership(planet_block, owner_id)

        zones_flat: list[dict[str, Any]] = []
        for district in profile["districts"]:
            for zone in district["zones"]:
                zones_flat.append(
                    {
                        "zone_id": zone["zone_id"],
                        "slot_selector": zone.get("slot_selector"),
                        "type": zone["type"],
                        "district_id": district["district_id"],
                        "district_type": district["type"],
                        "district_level": district["level"],
                        "buildings": zone["buildings"],
                    }
                )

        planets.append(
            {
                "name_key": name,
                "name_variables": profile.get("name_variables", {}),
                "display_name_hint": planet_display_name_hint(
                    name,
                    profile.get("name_variables", {}),
                ),
                "planet_id": planet_id,
                "colony_id": colony_id,
                "owner_id": ownership["owner_id"],
                "original_owner_id": ownership["original_owner_id"],
                "planet_class": planet_class,
                "planet_size": profile.get("planet_size"),
                "active_modifiers": profile.get("active_modifiers", []),
                "build_queue_id": queue_id,
                "deposit_types": profile.get("deposit_types", []),
                "construction": {
                    "has_pending_construction": bool(queued_items),
                    "queued_item_ids": queued_items,
                    "queue_depth": len(queued_items),
                    "pending_items": pending_items,
                },
                "safety": {
                    "is_iag_carrier": is_carrier,
                    "is_colonizing": is_colonizing,
                    "is_inherited_colony": ownership["is_inherited_colony"],
                    "special_planet_requires_manual_review": special_marked,
                    "eligible_for_first_run": (
                        not is_carrier
                        and not is_colonizing
                        and not special_marked
                    ),
                },
                "metrics": colony_metrics(colony_block, pop_jobs),
                "districts": profile["districts"],
                "zones": zones_flat,
            }
        )

    date_match = DATE_RE.search(text)
    result: dict[str, Any] = {
        "schema": "iag.game_state.v1",
        "generated_at": now_iso(),
        "game_date": date_match.group(1) if date_match else None,
        "player": identity,
        "owner_filter": owner_id,
        "country": country_state,
        "known_technologies": sorted(technologies),
        "planet_count": len(planets),
        "planets": sorted(planets, key=lambda item: item["planet_id"]),
        "data_quality": {
            "source_kind": "synchronized_save",
            "fact_priority": "authoritative_for_the_save_timestamp",
            "precision": {
                "country_stockpile": "direct save value",
                "country_monthly_balance": "direct current-month budget aggregation",
                "planet_stability_crime_housing_amenities": "direct save values",
                "free_jobs_estimate": "derived from raw workforce divided by 100",
                "unemployed_pops_estimate": "derived from raw workforce divided by 100",
            },
            "limitations": [
                "Values describe the saved simulation state, not unsaved live ticks.",
                "UI rounding may differ from stored floating-point values.",
            ],
        },
    }
    if save_path is not None:
        raw = save_path.read_bytes()
        modified_at = datetime.fromtimestamp(
            save_path.stat().st_mtime,
            tz=timezone.utc,
        )
        metadata = load_save_metadata(save_path)
        result["source_save"] = {
            "path": str(save_path.resolve()),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "modified_at": modified_at.astimezone().isoformat(timespec="seconds"),
            "age_seconds_at_extraction": max(
                (datetime.now(timezone.utc) - modified_at).total_seconds(),
                0,
            ),
            **metadata,
        }
    return result


def newest_save(root: Path) -> Path:
    candidates = sorted(
        root.rglob("*.sav"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No .sav files found below {root}")
    return candidates[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--save", type=Path)
    source.add_argument("--latest-under", type=Path)
    parser.add_argument("--owner", type=int)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    save_path = args.save or newest_save(args.latest_under)
    result = extract_game_state(
        load_gamestate(save_path),
        save_path=save_path,
        owner=args.owner,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
