from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path

from iag.applications.registry import builtin_application_registry


ROOT = Path(__file__).resolve().parents[4]
APPLICATION_ROOT = ROOT / "src" / "iag" / "applications"
PROHIBITED_PREFIXES = (
    "iag.stellaris.execution.broker",
    "iag.stellaris.execution.carrier_click",
    "iag.stellaris.execution.fixed_click",
    "iag.stellaris.execution.host_executor_protocol",
    "iag.stellaris.execution.iag_supervisor",
    "iag.stellaris.execution.packet",
    "iag.stellaris.execution.runtime",
    "iag.stellaris.execution.session_proxy",
    "iag.stellaris.execution.session_proxy_controller",
    "iag.stellaris.execution.windows_fixed_click",
    "iag.stellaris.execution.x11_fixed_click",
    "iag.stellaris.runtime",
    "stellaris.execution.broker",
    "stellaris.execution.carrier_click",
    "stellaris.execution.fixed_click",
    "stellaris.execution.host_executor_protocol",
    "stellaris.execution.iag_supervisor",
    "stellaris.execution.packet",
    "stellaris.execution.runtime",
    "stellaris.execution.session_proxy",
    "stellaris.execution.session_proxy_controller",
    "stellaris.execution.windows_fixed_click",
    "stellaris.execution.x11_fixed_click",
    "stellaris.runtime",
)
PROHIBITED_SYMBOLS = {
    "CarrierClickBackend",
    "NativeRuntimeBackend",
    "SessionProxyBackend",
    "SessionProxyController",
    "SessionProxyError",
}

# Phase-one debt is frozen at exact symbols. New direct dependencies fail this
# test; each later migration removes entries until the allowlist is empty.
EXPECTED_LEGACY_IMPORTS = {
    (
        "economy_governance/agent_tools.py",
        "iag.stellaris.execution.iag_supervisor",
        "StaleSourceSaveError",
    ),
    (
        "economy_governance/agent_tools.py",
        "iag.stellaris.execution.iag_supervisor",
        "execute_run",
    ),
    (
        "economy_governance/agent_tools.py",
        "iag.stellaris.execution.session_proxy_controller",
        "SessionProxyController",
    ),
    (
        "economy_governance/agent_tools.py",
        "iag.stellaris.execution.session_proxy_controller",
        "SessionProxyError",
    ),
    (
        "fleet_operations/agent_tools.py",
        "iag.stellaris.execution.session_proxy_controller",
        "SessionProxyController",
    ),
    (
        "fleet_operations/agent_tools.py",
        "iag.stellaris.execution.session_proxy_controller",
        "SessionProxyError",
    ),
}


def prohibited_application_imports() -> set[tuple[str, str, str]]:
    imports: set[tuple[str, str, str]] = set()
    for path in APPLICATION_ROOT.rglob("*.py"):
        relative_path = path.relative_to(APPLICATION_ROOT)
        if "tests" in relative_path.parts:
            continue
        relative = relative_path.as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                for item in node.names:
                    if module.startswith(PROHIBITED_PREFIXES) or (
                        item.name in PROHIBITED_SYMBOLS
                    ):
                        imports.add((relative, module, item.name))
            elif isinstance(node, ast.Import):
                for item in node.names:
                    if item.name.startswith(PROHIBITED_PREFIXES):
                        imports.add((relative, item.name, "*"))
    return imports


class ExecutionArchitectureTests(unittest.TestCase):
    def test_direct_transport_imports_are_limited_to_frozen_migration_debt(
        self,
    ) -> None:
        self.assertEqual(
            prohibited_application_imports(),
            EXPECTED_LEGACY_IMPORTS,
        )

    def test_migrated_research_application_has_no_wire_target_vocabulary(self) -> None:
        source = (APPLICATION_ROOT / "research_strategy" / "agent_tools.py").read_text(
            encoding="utf-8"
        )
        for private_name in (
            "session_proxy",
            "context_822c",
            "destination_tag_hex",
            "source_fleet_object",
            "command_serial",
        ):
            self.assertNotIn(private_name, source)

    def test_application_manifests_require_semantic_action_capabilities(self) -> None:
        forbidden = {"session_proxy_v1", "host_inbound_rewrite_v1"}
        for manifest in builtin_application_registry().all():
            capabilities = set(manifest.required_platform_capabilities)
            self.assertTrue(forbidden.isdisjoint(capabilities))
            self.assertIn("stellaris.execution.v1", capabilities)
            for action_type in manifest.action_types:
                self.assertIn(
                    f"stellaris.action.{action_type}.v1",
                    capabilities,
                    msg=f"{manifest.application_id} lacks {action_type} capability",
                )

    def test_toml_manifests_match_semantic_capability_rule(self) -> None:
        registry = builtin_application_registry()
        for path in APPLICATION_ROOT.glob("*/application.toml"):
            document = tomllib.loads(path.read_text(encoding="utf-8"))
            manifest = registry.get(str(document["application_id"]))
            capabilities = set(document["required_platform_capabilities"])
            self.assertEqual(tuple(document["action_types"]), manifest.action_types)
            self.assertEqual(
                tuple(document["required_platform_capabilities"]),
                manifest.required_platform_capabilities,
            )
            self.assertNotIn("session_proxy_v1", capabilities)
            self.assertNotIn("host_inbound_rewrite_v1", capabilities)
            self.assertIn("stellaris.execution.v1", capabilities)
            for action_type in document["action_types"]:
                self.assertIn(
                    f"stellaris.action.{action_type}.v1",
                    capabilities,
                    msg=f"{path} lacks {action_type} capability",
                )


if __name__ == "__main__":
    unittest.main()
