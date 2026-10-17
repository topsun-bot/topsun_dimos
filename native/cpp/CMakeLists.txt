# Copyright 2026 Dimensional Inc.
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.14)
project(dimos_native CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# PFR (standalone Boost.PFR): compile-time struct reflection for config
# parsing. Header-only. nix builds inject it via FETCHCONTENT_SOURCE_DIR_PFR.
include(FetchContent)
FetchContent_Declare(pfr
  GIT_REPOSITORY https://github.com/apolukhin/pfr_non_boost.git
  GIT_TAG b1a9798677adc191e21dd7904fb42cbe2f2889a7  # 2.3.2
)
FetchContent_MakeAvailable(pfr)

# JSON parser. config.hpp includes it as a public SDK header, so it is a usage
# requirement of this target rather than something each consumer repeats. nix
# provides it via buildInputs.
find_package(nlohmann_json QUIET)
if(NOT nlohmann_json_FOUND)
  FetchContent_Declare(nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG 9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03  # v3.11.3
  )
  FetchContent_MakeAvailable(nlohmann_json)
endif()

# module.hpp runs the dispatch loop and publish workers on std::thread.
find_package(Threads REQUIRED)

# Header-only SDK. Consumers link this INTERFACE target and inherit the include
# path, the C++ standard, and the SDK's own dependencies. A consumer still sets
# CMAKE_CXX_STANDARD for the targets it builds that do not link this one.
add_library(dimos_native INTERFACE)
target_include_directories(dimos_native INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(dimos_native INTERFACE cxx_std_20)
target_link_libraries(dimos_native INTERFACE
  pfr
  nlohmann_json::nlohmann_json
  Threads::Threads
)

# Zenoh transport. zenoh-cpp is header-only over zenoh-c, which is a compiled
# rust library, so unlike the rest of the SDK it cannot be vendored cheaply. It
# is required anyway: every module has to be able to speak the transport the
# coordinator picks.
#
# zenohcxxConfig.cmake only defines its backend target when zenohc::lib already
# exists, and hard-errors when no backend does, so zenoh-c goes first.
# Older zenoh-c spells the locality enum ZC_LOCALITY_*, so accepting one trades
# this version message for a page of compiler errors.
# pkg-config before the CMake config: nixpkgs splits zenoh-c into out/dev and its
# zenohcConfig.cmake points the library at the dev output, which has none, while
# its .pc is right. The upstream release zips ship only the CMake config.
set(zenoh_min_version 1.9.0)
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
  pkg_check_modules(ZENOHC QUIET IMPORTED_TARGET zenohc>=${zenoh_min_version})
endif()
if(ZENOHC_FOUND)
  add_library(zenohc::lib ALIAS PkgConfig::ZENOHC)
else()
  find_package(zenohc ${zenoh_min_version} QUIET)
  if(NOT zenohc_FOUND)
    include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/FetchZenohStandalone.cmake")
    dimos_fetch_zenoh_standalone("${CMAKE_BINARY_DIR}/zenoh-standalone")
    find_package(zenohc ${zenoh_min_version} REQUIRED)
  endif()
endif()
find_package(zenohcxx ${zenoh_min_version} REQUIRED)
if(DIMOS_ZENOH_STANDALONE_PREFIX)
  # The standalone .so is not on the default loader path (/usr/local is).
  list(APPEND CMAKE_BUILD_RPATH "${DIMOS_ZENOH_STANDALONE_PREFIX}/lib")
endif()

# Off in an upstream source build and on in every published binary, so it is
# detected rather than assumed.
include(CheckCXXSourceCompiles)
get_target_property(zenohc_includes zenohc::lib INTERFACE_INCLUDE_DIRECTORIES)
set(CMAKE_REQUIRED_INCLUDES ${zenohc_includes})
check_cxx_source_compiles("
  #include <zenoh_configure.h>
  #if !defined(Z_FEATURE_UNSTABLE_API)
  #error unstable api off
  #endif
  int main() { return 0; }
" DIMOS_NATIVE_ZENOH_UNSTABLE_API)
unset(CMAKE_REQUIRED_INCLUDES)
if(NOT DIMOS_NATIVE_ZENOH_UNSTABLE_API)
  message(FATAL_ERROR "dimos_native: zenoh-c is built without its unstable API, which the zenoh \
transport needs, rebuild it with -DZENOHC_BUILD_WITH_UNSTABLE_API=ON")
endif()
target_link_libraries(dimos_native INTERFACE zenohcxx::zenohc)

# LCM transport. transport_factory.hpp includes lcm_transport.hpp
# unconditionally, so liblcm is a compile dependency of the SDK headers, not
# something a consumer opts into.
#
# Prefer pkg-config so runners with libglib2.0-dev keep today's PkgConfig::LCM
# link. lcm.pc Requires: glib-2.0; a required-workflow pin may install
# liblcm-dev without that .pc even though headers and liblcm.so are present.
# Runtime glib is already a NEEDED of the LCM shared library.
find_package(PkgConfig REQUIRED)
pkg_check_modules(LCM QUIET IMPORTED_TARGET lcm)
if(LCM_FOUND)
  target_link_libraries(dimos_native INTERFACE PkgConfig::LCM)
else()
  find_path(LCM_INCLUDE_DIR NAMES lcm/lcm.h)
  find_library(LCM_LIBRARY NAMES lcm)
  if(NOT LCM_INCLUDE_DIR OR NOT LCM_LIBRARY)
    message(FATAL_ERROR "dimos_native: liblcm is required (lcm/lcm.h and liblcm). \
pkg-config could not resolve lcm (often because glib-2.0.pc is missing) and \
find_path/find_library also failed")
  endif()
  add_library(LCM::lcm INTERFACE IMPORTED)
  set_target_properties(LCM::lcm PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${LCM_INCLUDE_DIR}"
    INTERFACE_LINK_LIBRARIES "${LCM_LIBRARY}"
  )
  find_library(GLIB_LIBRARY NAMES glib-2.0)
  if(GLIB_LIBRARY)
    set_property(TARGET LCM::lcm APPEND PROPERTY INTERFACE_LINK_LIBRARIES "${GLIB_LIBRARY}")
  endif()
  message(STATUS "dimos_native: using LCM via ${LCM_LIBRARY} (pkg-config unavailable)")
  target_link_libraries(dimos_native INTERFACE LCM::lcm)
endif()

option(DIMOS_NATIVE_BUILD_TESTS "Build the dimos native SDK tests" OFF)
if(DIMOS_NATIVE_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
