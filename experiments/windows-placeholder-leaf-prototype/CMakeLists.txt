cmake_minimum_required(VERSION 3.21)
project(windows_placeholder_leaf_prototype LANGUAGES C)
if(NOT WIN32)
    message(FATAL_ERROR "Native Windows qualification required. No POSIX fallback is provided.")
endif()
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "Use an x64 generator/toolchain (e.g. -A x64).")
endif()
if(NOT MSVC)
    message(FATAL_ERROR "Use x64 MSVC or clang-cl with the Windows SDK; the probe uses narrow SEH filters.")
endif()
find_package(Git QUIET)
set(PROBE_GIT_SHA "unrecorded")
set(PROBE_TREE_STATE "unrecorded")
if(GIT_FOUND)
    execute_process(COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}" OUTPUT_VARIABLE PROBE_GIT_SHA
        OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    execute_process(COMMAND "${GIT_EXECUTABLE}" status --porcelain=v1
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}" OUTPUT_VARIABLE dirty ERROR_QUIET)
    if(dirty)
        set(PROBE_TREE_STATE "dirty-use-probe-sha256")
    else()
        set(PROBE_TREE_STATE "clean")
    endif()
endif()
file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/placeholder_leaf_probe.c" PROBE_SOURCE_SHA256)
# Reconfigure if the source changes so the embedded digest cannot silently go stale.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS placeholder_leaf_probe.c)
add_executable(placeholder_leaf_probe placeholder_leaf_probe.c)
set_target_properties(placeholder_leaf_probe PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED YES C_EXTENSIONS NO)
target_compile_definitions(placeholder_leaf_probe PRIVATE
    WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00 NTDDI_VERSION=0x0A000007
    "PROBE_GIT_SHA=\"${PROBE_GIT_SHA}\""
    "PROBE_SOURCE_SHA256=\"${PROBE_SOURCE_SHA256}\""
    "PROBE_TREE_STATE=\"${PROBE_TREE_STATE}\""
    "PROBE_CONFIGURATION=\"$<CONFIG>\""
    "PROBE_COMPILER=\"${CMAKE_C_COMPILER_ID}-${CMAKE_C_COMPILER_VERSION}\"")
target_compile_options(placeholder_leaf_probe PRIVATE /W4 /WX /sdl)
# The modern APIs are dynamically resolved. No onecore / CreateFileMapping2 import.
target_link_libraries(placeholder_leaf_probe PRIVATE kernel32)
include(CTest)
if(BUILD_TESTING)
    add_test(NAME placeholder-leaf-basic COMMAND placeholder_leaf_probe)
    set_tests_properties(placeholder-leaf-basic PROPERTIES TIMEOUT 120)
endif()
