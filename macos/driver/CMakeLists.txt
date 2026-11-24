# AG Podcast Mixer, the CoreAudio driver (AGPodcastMixer.cpp): built by build.sh, not on its own.
cmake_minimum_required(VERSION 3.15)

project(AGPodcastMixer CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

set(DRIVER_NAME "AGPodcastMixer")
set(DRIVER_VERSION "1.3.0")      # CFBundleVersion: AG CuteMusic installs a newer one (not a CACHE variable: a cached one stays at its first value)
set(DRIVER_IDENTIFIER "com.ag-projects.podcast-mixer.driver")
set(DRIVER_FACTORY_UUID "082740A4-C2E5-44A0-BD1F-6FA672E9358D")
set(DRIVER_ENTRYPOINT "AGPodcastMixer_Create")
set(DRIVER_COPYRIGHT "Copyright (C) 2026 AG Projects")
option(AGPM_TRACE "libASPL's tracing to syslog" OFF)

# libASPL, vendored (libASPL/VERSION): its sources compiled into the driver
set(ASPL ${CMAKE_CURRENT_SOURCE_DIR}/libASPL)
file(GLOB ASPL_SOURCES ${ASPL}/src/*.cpp)

add_library(${DRIVER_NAME} MODULE AGPodcastMixer.cpp ${ASPL_SOURCES})
target_include_directories(${DRIVER_NAME} PRIVATE ${ASPL}/include ${ASPL}/src)
target_compile_options(${DRIVER_NAME} PRIVATE -Wall -Wno-unused-parameter -Wno-invalid-offsetof -fblocks)
if(AGPM_TRACE)
  target_compile_definitions(${DRIVER_NAME} PRIVATE AGPM_TRACE)
endif()

find_library(CORE_FOUNDATION CoreFoundation REQUIRED)
find_library(CORE_AUDIO CoreAudio REQUIRED)
target_link_libraries(${DRIVER_NAME} PRIVATE ${CORE_FOUNDATION} ${CORE_AUDIO})

# a loadable bundle: AGPodcastMixer.driver/Contents/{Info.plist,MacOS/AGPodcastMixer}
set_target_properties(${DRIVER_NAME} PROPERTIES
  OUTPUT_NAME "${DRIVER_NAME}"
  BUNDLE TRUE
  BUNDLE_EXTENSION "driver"
  PREFIX ""
  SUFFIX ""
  MACOSX_BUNDLE ON
  MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/Info.plist.in"
  MACOSX_BUNDLE_BUNDLE_NAME "${DRIVER_NAME}"
  MACOSX_BUNDLE_BUNDLE_VERSION "${DRIVER_VERSION}"
  MACOSX_BUNDLE_SHORT_VERSION_STRING "${DRIVER_VERSION}"
  MACOSX_BUNDLE_GUI_IDENTIFIER "${DRIVER_IDENTIFIER}"
  MACOSX_BUNDLE_COPYRIGHT "${DRIVER_COPYRIGHT}"
)
