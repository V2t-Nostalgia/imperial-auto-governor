cmake_minimum_required(VERSION 3.20)

project(iag_stellaris_windows_native_probe LANGUAGES CXX)

if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "The Windows native probe requires 64-bit Windows")
endif()

add_library(iag_stellaris_windows_native_probe SHARED windows_move_probe.cpp)
target_compile_features(iag_stellaris_windows_native_probe PRIVATE cxx_std_20)
target_compile_definitions(
  iag_stellaris_windows_native_probe
  PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
)
target_compile_options(
  iag_stellaris_windows_native_probe
  PRIVATE /W4 /WX /EHsc /permissive-
)
set_target_properties(
  iag_stellaris_windows_native_probe
  PROPERTIES PREFIX "" OUTPUT_NAME "iag_stellaris_windows_native_probe"
)

add_executable(iag_stellaris_windows_probe_tool windows_probe_tool.cpp)
target_compile_features(iag_stellaris_windows_probe_tool PRIVATE cxx_std_20)
target_compile_definitions(
  iag_stellaris_windows_probe_tool
  PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
)
target_compile_options(
  iag_stellaris_windows_probe_tool
  PRIVATE /W4 /WX /EHsc /permissive-
)
