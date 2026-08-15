#!/usr/bin/env python3
"""Built-in model configuration templates."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from iag.applications.economy_governance.planner import read_json


TEMPLATES_PATH = Path(__file__).resolve().parent / "model_templates.json"


def load_model_templates() -> list[dict[str, Any]]:
    value = read_json(TEMPLATES_PATH)
    if value.get("schema") != "iag.model_templates.v1":
        raise ValueError("Unsupported model template schema.")
    templates = value.get("templates")
    if not isinstance(templates, list):
        raise ValueError("Model templates must be an array.")
    return [copy.deepcopy(item) for item in templates if isinstance(item, dict)]


def public_model_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": template.get("id"),
            "label": template.get("label"),
            "description": template.get("description"),
            "config": template.get("config", {}),
        }
        for template in load_model_templates()
    ]


def apply_model_template(
    config: dict[str, Any],
    template_id: str,
) -> dict[str, Any]:
    template_map = {
        str(item.get("id")): item for item in load_model_templates()
    }
    template = template_map.get(template_id)
    if template is None:
        raise ValueError(f"Unknown model template: {template_id}")
    merged = copy.deepcopy(config)
    if template_id != "custom":
        merged.update(copy.deepcopy(template.get("config", {})))
    merged["model_template_id"] = template_id
    return merged
