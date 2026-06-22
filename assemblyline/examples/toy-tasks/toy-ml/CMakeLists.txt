cmake_minimum_required(VERSION 3.16)
project(toy_ml LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()

add_library(toy_ml
    src/relu.cpp
    src/tensor.cpp
)
target_include_directories(toy_ml PUBLIC include)

add_executable(toy_ml_tests
    tests/relu_tests.cpp
)
target_link_libraries(toy_ml_tests PRIVATE toy_ml)

add_test(NAME relu_tests COMMAND toy_ml_tests)
