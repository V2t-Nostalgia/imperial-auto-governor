cmake_minimum_required(VERSION 3.20)

project(iag_stellaris_command_probe LANGUAGES CXX)

add_library(iag_stellaris_command_probe SHARED command_probe.cpp)
target_compile_features(iag_stellaris_command_probe PRIVATE cxx_std_20)
target_compile_options(
  iag_stellaris_command_probe
  PRIVATE -Wall -Wextra -Wpedantic -Werror -fno-omit-frame-pointer
)
target_link_libraries(iag_stellaris_command_probe PRIVATE dl pthread)

add_library(iag_stellaris_payload_probe SHARED payload_probe.cpp)
target_compile_features(iag_stellaris_payload_probe PRIVATE cxx_std_20)
target_compile_options(
  iag_stellaris_payload_probe
  PRIVATE -Wall -Wextra -Wpedantic -Werror -fno-omit-frame-pointer
)
target_link_libraries(iag_stellaris_payload_probe PRIVATE dl pthread)

add_library(
  iag_stellaris_observer_takeover_probe
  SHARED
    observer_takeover_probe.cpp
    ../game_api.cpp
    ../actions/action_registry.cpp
    ../actions/move_fleet.cpp
    ../versions/stellaris_4_4_6.cpp
)
target_compile_features(
  iag_stellaris_observer_takeover_probe
  PRIVATE cxx_std_20
)
target_include_directories(
  iag_stellaris_observer_takeover_probe
  PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../include
    ${CMAKE_CURRENT_SOURCE_DIR}/../actions
)
target_compile_options(
  iag_stellaris_observer_takeover_probe
  PRIVATE -Wall -Wextra -Wpedantic -Werror -fno-omit-frame-pointer
)
target_link_libraries(
  iag_stellaris_observer_takeover_probe
  PRIVATE dl pthread
)

set_target_properties(
  iag_stellaris_command_probe
  PROPERTIES
    OUTPUT_NAME "iag_stellaris_command_probe"
    PREFIX "lib"
)

set_target_properties(
  iag_stellaris_payload_probe
  PROPERTIES
    OUTPUT_NAME "iag_stellaris_payload_probe"
    PREFIX "lib"
)

set_target_properties(
  iag_stellaris_observer_takeover_probe
  PROPERTIES
    OUTPUT_NAME "iag_stellaris_observer_takeover_probe"
    PREFIX "lib"
)
