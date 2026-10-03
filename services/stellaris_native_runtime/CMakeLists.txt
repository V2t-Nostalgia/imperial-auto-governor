cmake_minimum_required(VERSION 3.20)

project(iag_stellaris_native_runtime LANGUAGES CXX)

file(
  GLOB IAG_NATIVE_RUNTIME_ACTION_SOURCES
  CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/actions/*.cpp"
)

if(WIN32)
  set(IAG_NATIVE_RUNTIME_PLATFORM_SOURCE runtime_windows.cpp)
  set(IAG_NATIVE_RUNTIME_VERSION_SOURCE versions/stellaris_4_4_6_windows.cpp)
else()
  set(IAG_NATIVE_RUNTIME_PLATFORM_SOURCE runtime.cpp)
  set(IAG_NATIVE_RUNTIME_VERSION_SOURCE versions/stellaris_4_4_6.cpp)
endif()

add_library(
  iag_stellaris_native_runtime
  SHARED
    ${IAG_NATIVE_RUNTIME_PLATFORM_SOURCE}
    game_api.cpp
    ${IAG_NATIVE_RUNTIME_ACTION_SOURCES}
    ${IAG_NATIVE_RUNTIME_VERSION_SOURCE}
)
target_compile_features(iag_stellaris_native_runtime PRIVATE cxx_std_20)
target_include_directories(
  iag_stellaris_native_runtime
  PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/actions
)
if(MSVC)
  target_compile_definitions(
    iag_stellaris_native_runtime
    PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
  )
  target_compile_options(
    iag_stellaris_native_runtime
    PRIVATE /W4 /WX /EHsc /permissive-
  )
else()
  target_compile_options(
    iag_stellaris_native_runtime
    PRIVATE -Wall -Wextra -Wpedantic -Werror -fvisibility=hidden
  )
  target_link_libraries(iag_stellaris_native_runtime PRIVATE dl pthread)
endif()
set_target_properties(
  iag_stellaris_native_runtime
  PROPERTIES
    OUTPUT_NAME "iag_stellaris_native_runtime"
)
if(WIN32)
  set_target_properties(iag_stellaris_native_runtime PROPERTIES PREFIX "")
else()
  set_target_properties(iag_stellaris_native_runtime PROPERTIES PREFIX "lib")
endif()

if(WIN32)
  add_executable(
    iag_stellaris_native_runtime_loader
    platform/windows/native_runtime_loader.cpp
  )
  target_compile_features(
    iag_stellaris_native_runtime_loader PRIVATE cxx_std_20
  )
  target_compile_definitions(
    iag_stellaris_native_runtime_loader
    PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
  )
  target_compile_options(
    iag_stellaris_native_runtime_loader
    PRIVATE /W4 /WX /EHsc /permissive-
  )
endif()

option(
  IAG_NATIVE_RUNTIME_BUILD_TESTS
  "Build native runtime contract tests"
  OFF
)
if(IAG_NATIVE_RUNTIME_BUILD_TESTS)
  enable_testing()
  add_executable(
    iag_native_runtime_action_contract_test
      tests/action_contract_test.cpp
      game_api.cpp
      ${IAG_NATIVE_RUNTIME_ACTION_SOURCES}
      ${IAG_NATIVE_RUNTIME_VERSION_SOURCE}
  )
  target_compile_features(
    iag_native_runtime_action_contract_test
    PRIVATE cxx_std_20
  )
  target_include_directories(
    iag_native_runtime_action_contract_test
    PRIVATE
      ${CMAKE_CURRENT_SOURCE_DIR}/include
      ${CMAKE_CURRENT_SOURCE_DIR}/actions
  )
  if(MSVC)
    target_compile_definitions(
      iag_native_runtime_action_contract_test
      PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
    )
    target_compile_options(
      iag_native_runtime_action_contract_test
      PRIVATE /W4 /WX /EHsc /permissive-
    )
  else()
    target_compile_options(
      iag_native_runtime_action_contract_test
      PRIVATE -Wall -Wextra -Wpedantic -Werror
    )
  endif()
  add_test(
    NAME native_runtime_action_contract
    COMMAND iag_native_runtime_action_contract_test
  )
endif()
