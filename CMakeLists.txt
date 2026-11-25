cmake_minimum_required(VERSION 3.20)
project(quant LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(CURL REQUIRED)
find_package(ZLIB REQUIRED)

option(QUANT_WITH_KDBX
  "Enable KDB-X/q integration via the official KX C API" ON)
set(QUANT_PYTHON_EXECUTABLE "" CACHE STRING
  "Python interpreter with PyKX installed")
set(KDBX_ENABLED FALSE)
set(KDBX_INCLUDE_DIR "")
set(KDBX_LIBRARY "")

if(QUANT_WITH_KDBX)
  if(QUANT_PYTHON_EXECUTABLE STREQUAL "")
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/data/venv/bin/python")
      set(QUANT_PYTHON_EXECUTABLE
        "${CMAKE_CURRENT_SOURCE_DIR}/data/venv/bin/python")
    else()
      set(QUANT_PYTHON_EXECUTABLE "python3")
    endif()
  endif()
  # PyKX prints a Community Edition banner on import, so write the path to a
  # file rather than relying on stdout parsing.
  execute_process(
    COMMAND ${QUANT_PYTHON_EXECUTABLE} -c
      [=[
import pykx, os, sys
open(sys.argv[1], "w").write(os.path.dirname(pykx.__file__))
]=]
      "${CMAKE_BINARY_DIR}/pykx_path.txt"
    OUTPUT_QUIET ERROR_QUIET RESULT_VARIABLE PYKX_RESULT)
  set(PYKX_DIR "")
  if(PYKX_RESULT EQUAL 0 AND EXISTS "${CMAKE_BINARY_DIR}/pykx_path.txt")
    file(READ "${CMAKE_BINARY_DIR}/pykx_path.txt" PYKX_DIR)
    string(STRIP "${PYKX_DIR}" PYKX_DIR)
  endif()
  if(EXISTS "${PYKX_DIR}/include/k.h")
    # libe exports khpun/kclose/k for external C clients; libq alone does not.
    if(APPLE)
      set(KDBX_LIB_CANDIDATES
        "${PYKX_DIR}/lib/m64arm/libe.so"
        "${PYKX_DIR}/lib/m64/libe.so")
    else()
      set(KDBX_LIB_CANDIDATES
        "${PYKX_DIR}/lib/l64arm/libe.so"
        "${PYKX_DIR}/lib/l64/libe.so")
    endif()
    foreach(candidate ${KDBX_LIB_CANDIDATES})
      if(EXISTS "${candidate}")
        set(KDBX_LIBRARY "${candidate}")
        break()
      endif()
    endforeach()
    if(NOT KDBX_LIBRARY STREQUAL "")
      get_filename_component(KDBX_LIB_DIR "${KDBX_LIBRARY}" DIRECTORY)
      set(KDBX_INCLUDE_DIR "${PYKX_DIR}/include")
      set(KDBX_ENABLED TRUE)
      set(CMAKE_BUILD_RPATH "${KDBX_LIB_DIR}")
      set(CMAKE_INSTALL_RPATH "${KDBX_LIB_DIR}")
    endif()
  endif()
endif()

add_library(quant_core
  src/market_data.cpp
  src/engine.cpp
  src/zerodha.cpp
  src/kdbx.cpp
)
target_include_directories(quant_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(quant_core PUBLIC CURL::libcurl ZLIB::ZLIB)
target_compile_options(quant_core PRIVATE -Wall -Wextra -Wpedantic)
if(KDBX_ENABLED)
  target_compile_definitions(quant_core PUBLIC QUANT_HAS_KDBX=1)
  target_compile_definitions(quant_core PUBLIC
    QUANT_PYKX_PYTHON="${QUANT_PYTHON_EXECUTABLE}")
  target_compile_definitions(quant_core PUBLIC QUANT_PYKX_DIR="${PYKX_DIR}")
  target_include_directories(quant_core PRIVATE ${KDBX_INCLUDE_DIR})
  target_link_libraries(quant_core PUBLIC "${KDBX_LIBRARY}")
endif()

add_executable(quant src/main.cpp)
target_link_libraries(quant PRIVATE quant_core)
target_compile_options(quant PRIVATE -Wall -Wextra -Wpedantic)

add_executable(quant_tests tests/tests.cpp)
target_link_libraries(quant_tests PRIVATE quant_core)
target_compile_options(quant_tests PRIVATE -Wall -Wextra -Wpedantic)

if(APPLE AND KDBX_ENABLED)
  # PyKX's libe.so carries a bare leaf install name. Rewrite only the
  # executable load commands to @rpath so the CMake rpath resolves it.
  foreach(target quant quant_tests)
    add_custom_command(TARGET ${target} POST_BUILD
      COMMAND install_name_tool -change libe.so @rpath/libe.so
        $<TARGET_FILE:${target}>)
  endforeach()
endif()

enable_testing()
add_test(NAME quant_unit_tests COMMAND quant_tests
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME quant_doctor COMMAND quant doctor
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
add_test(NAME quant_validate COMMAND quant validate
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})

option(QUANT_SANITIZERS "Build with ASan/UBSan" OFF)
if(QUANT_SANITIZERS)
  foreach(target quant_core quant quant_tests)
    target_compile_options(${target} PRIVATE
      -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(${target} PRIVATE -fsanitize=address,undefined)
  endforeach()
endif()

message(STATUS "QUANT KDB-X integration: ${KDBX_ENABLED}")
if(QUANT_WITH_KDBX AND NOT KDBX_ENABLED)
  message(STATUS "KDB-X unavailable: install PyKX or pass "
    "-DQUANT_PYTHON_EXECUTABLE=/path/to/python")
endif()
