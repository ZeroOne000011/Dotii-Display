cmake_minimum_required(VERSION 3.16)

set(IDF_TARGET "esp32s3")
set(PROJECT_VER "1.0.0")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(state_display)
