from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
RUNTIME_ROOT = ROOT / "services" / "stellaris_native_runtime"


class NativeRuntimeLayoutTests(unittest.TestCase):
    def test_action_sources_are_auto_built_and_self_registered(self) -> None:
        cmake = (RUNTIME_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        registry = (RUNTIME_ROOT / "actions" / "action_registry.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("GLOB IAG_NATIVE_RUNTIME_ACTION_SOURCES", cmake)
        self.assertIn("CONFIGURE_DEPENDS", cmake)
        self.assertIn("${IAG_NATIVE_RUNTIME_ACTION_SOURCES}", cmake)
        action_sources = sorted(
            path
            for path in (RUNTIME_ROOT / "actions").glob("*.cpp")
            if path.name != "action_registry.cpp"
        )
        self.assertTrue(action_sources)
        for source in action_sources:
            content = source.read_text(encoding="utf-8")
            self.assertIn("ActionRegistration", content, msg=source.name)
            self.assertIn(".application_id", content, msg=source.name)
            self.assertIn(".parameters", content, msg=source.name)
            self.assertNotIn(f'#include "{source.stem}.h"', registry)
        self.assertNotIn("AttackFleetDescriptor", registry)
        self.assertNotIn("MoveFleetDescriptor", registry)
        self.assertNotIn("StopResearchDescriptor", registry)

    def test_version_profiles_are_explicit_build_inputs(self) -> None:
        cmake = (RUNTIME_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        profiles = sorted((RUNTIME_ROOT / "versions").glob("*.cpp"))
        self.assertTrue(profiles)
        for profile in profiles:
            self.assertIn(f"versions/{profile.name}", cmake)

    def test_windows_builder_discovers_actions_and_packages_runtime(self) -> None:
        builder = (
            ROOT / "scripts" / "build" / "Build-StellarisNativeRuntime.ps1"
        ).read_text(encoding="utf-8")
        agent_builder = (
            ROOT / "scripts" / "build" / "Build-IAGWindowsAgent.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('Get-ChildItem -LiteralPath $ActionsRoot -Filter "*.cpp"', builder)
        self.assertIn("iag_native_runtime_action_contract_test", builder)
        self.assertIn("_internal\\native_runtime", agent_builder)

    def test_external_action_example_uses_etc_and_declares_parameters(self) -> None:
        example = (
            RUNTIME_ROOT / "examples" / "example_action.cpp.example"
        ).read_text(encoding="utf-8")
        self.assertIn('.application_id = "etc"', example)
        self.assertIn("ActionParameterDescriptor", example)
        self.assertIn("ActionRegistration", example)

    def test_runtime_core_contains_no_action_specific_implementation(self) -> None:
        core = (RUNTIME_ROOT / "runtime.cpp").read_text(encoding="utf-8")
        for private_detail in (
            "move_fleet",
            "stop_research",
            "CFleetFlyToCoordinatesCommand",
            "CCancelResearchCommand",
        ):
            self.assertNotIn(private_detail, core)

    def test_only_common_game_api_submits_native_commands(self) -> None:
        game_api = (RUNTIME_ROOT / "game_api.cpp").read_text(encoding="utf-8")
        self.assertIn("GameApi::PostCommand", game_api)
        for source in (RUNTIME_ROOT / "actions").glob("*.cpp"):
            content = source.read_text(encoding="utf-8")
            self.assertNotIn("PostCommandToSession", content)
            self.assertNotIn(".PostCommand(", content)


if __name__ == "__main__":
    unittest.main()
