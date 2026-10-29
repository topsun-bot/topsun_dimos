# Copyright 2026 Dimensional Inc.
# SPDX-License-Identifier: Apache-2.0

# Fetch the same pinned zenoh-c / zenoh-cpp standalone zips the hosted native
# job installs. Used when neither pkg-config nor find_package can see them —
# for example when a required-workflow pin runs an older ci.yml that never
# installed Zenoh, while this tree already requires it.

set(_DIMOS_ZENOH_FETCH_VERSION 1.10.0)
set(_DIMOS_ZENOHC_ZIP_SHA256 1168b3dffa7f4f48ffabfd640a3878ec0527c0a612ce825aa6f93e2cd05762d1)
set(_DIMOS_ZENOHCXX_ZIP_SHA256 7a50a74e98fd1e1e7ed8461b8490b30d67f878649f0727dea2cf0936501780af)

macro(dimos_fetch_zenoh_standalone prefix)
  if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "dimos_native: zenoh-c ${zenoh_min_version}+ is required and \
no automatic fetch is available on ${CMAKE_SYSTEM_NAME}")
  endif()
  if(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|amd64|AMD64)$")
    message(FATAL_ERROR "dimos_native: zenoh-c ${zenoh_min_version}+ is required and \
no automatic fetch is available for ${CMAKE_SYSTEM_PROCESSOR}")
  endif()

  set(_dimos_zenoh_c_cfg "${prefix}/lib/cmake/zenohc/zenohcConfig.cmake")
  set(_dimos_zenoh_cxx_cfg "${prefix}/lib/cmake/zenohcxx/zenohcxxConfig.cmake")
  if(NOT EXISTS "${_dimos_zenoh_c_cfg}" OR NOT EXISTS "${_dimos_zenoh_cxx_cfg}")
    set(_dimos_zenoh_base "https://github.com/eclipse-zenoh")
    set(_dimos_zenoh_ver "${_DIMOS_ZENOH_FETCH_VERSION}")
    set(_dimos_zenoh_c_zip "${prefix}/zenoh-c-${_dimos_zenoh_ver}.zip")
    set(_dimos_zenoh_cxx_zip "${prefix}/zenoh-cpp-${_dimos_zenoh_ver}.zip")
    file(MAKE_DIRECTORY "${prefix}")
    message(STATUS "dimos_native: fetching zenoh ${_dimos_zenoh_ver} standalone into ${prefix}")
    file(DOWNLOAD
      "${_dimos_zenoh_base}/zenoh-c/releases/download/${_dimos_zenoh_ver}/zenoh-c-${_dimos_zenoh_ver}-x86_64-unknown-linux-gnu-standalone.zip"
      "${_dimos_zenoh_c_zip}"
      EXPECTED_HASH SHA256=${_DIMOS_ZENOHC_ZIP_SHA256}
      TLS_VERIFY ON
      STATUS _dimos_zenoh_c_status
    )
    list(GET _dimos_zenoh_c_status 0 _dimos_zenoh_c_code)
    if(NOT _dimos_zenoh_c_code EQUAL 0)
      message(FATAL_ERROR "dimos_native: failed to download zenoh-c: ${_dimos_zenoh_c_status}")
    endif()
    file(DOWNLOAD
      "${_dimos_zenoh_base}/zenoh-cpp/releases/download/${_dimos_zenoh_ver}/zenohcpp-${_dimos_zenoh_ver}-standalone.zip"
      "${_dimos_zenoh_cxx_zip}"
      EXPECTED_HASH SHA256=${_DIMOS_ZENOHCXX_ZIP_SHA256}
      TLS_VERIFY ON
      STATUS _dimos_zenoh_cxx_status
    )
    list(GET _dimos_zenoh_cxx_status 0 _dimos_zenoh_cxx_code)
    if(NOT _dimos_zenoh_cxx_code EQUAL 0)
      message(FATAL_ERROR "dimos_native: failed to download zenoh-cpp: ${_dimos_zenoh_cxx_status}")
    endif()
    execute_process(
      COMMAND "${CMAKE_COMMAND}" -E tar xzf "${_dimos_zenoh_c_zip}"
      WORKING_DIRECTORY "${prefix}"
      RESULT_VARIABLE _dimos_zenoh_c_extract
    )
    if(NOT _dimos_zenoh_c_extract EQUAL 0)
      message(FATAL_ERROR "dimos_native: failed to extract zenoh-c standalone zip")
    endif()
    execute_process(
      COMMAND "${CMAKE_COMMAND}" -E tar xzf "${_dimos_zenoh_cxx_zip}"
      WORKING_DIRECTORY "${prefix}"
      RESULT_VARIABLE _dimos_zenoh_cxx_extract
    )
    if(NOT _dimos_zenoh_cxx_extract EQUAL 0)
      message(FATAL_ERROR "dimos_native: failed to extract zenoh-cpp standalone zip")
    endif()
  endif()

  list(PREPEND CMAKE_PREFIX_PATH "${prefix}")
  set(DIMOS_ZENOH_STANDALONE_PREFIX "${prefix}")
endmacro()
