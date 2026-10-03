cmake_minimum_required(VERSION 3.20)

project(iag_stellaris_native_ui_bridge LANGUAGES CXX)

add_library(iag_stellaris_native_ui_bridge SHARED native_ui_bridge.cpp)
target_compile_features(iag_stellaris_native_ui_bridge PRIVATE cxx_std_20)
target_compile_options(
  iag_stellaris_native_ui_bridge
  PRIVATE -Wall -Wextra -Wpedantic -Werror -fno-omit-frame-pointer
)
target_link_libraries(iag_stellaris_native_ui_bridge PRIVATE dl pthread)

set_target_properties(
  iag_stellaris_native_ui_bridge
  PROPERTIES
    OUTPUT_NAME "iag_stellaris_native_ui_bridge"
    PREFIX "lib"
)
