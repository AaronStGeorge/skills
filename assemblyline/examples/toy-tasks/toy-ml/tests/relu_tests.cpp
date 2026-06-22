#include "toy/tensor.h"

#include <cmath>
#include <iostream>
#include <string>
#include <vector>

namespace {

int failures = 0;

void expect_true(bool condition, const std::string& message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << "\n";
    }
}

void expect_shape(
    const std::vector<std::size_t>& actual,
    const std::vector<std::size_t>& expected,
    const std::string& message
) {
    expect_true(actual == expected, message);
}

void expect_values(
    const std::vector<float>& actual,
    const std::vector<float>& expected,
    const std::string& message
) {
    if (actual.size() != expected.size()) {
        ++failures;
        std::cerr << "FAIL: " << message << " size mismatch\n";
        return;
    }
    for (std::size_t i = 0; i < actual.size(); ++i) {
        if (std::fabs(actual[i] - expected[i]) > 0.0001F) {
            ++failures;
            std::cerr << "FAIL: " << message << " at index " << i << ": expected "
                      << expected[i] << ", got " << actual[i] << "\n";
            return;
        }
    }
}

void test_shape_preservation() {
    const toy::Tensor input({2, 3}, {-3.0F, -1.0F, 0.0F, 1.0F, 2.0F, 3.0F});
    const toy::Tensor output = toy::relu(input);

    expect_shape(output.shape(), {2, 3}, "relu preserves shape");
    expect_true(output.size() == input.size(), "relu preserves element count");
}

void test_negative_zero_positive_values() {
    const toy::Tensor input({5}, {-4.0F, -0.5F, 0.0F, 2.5F, 9.0F});
    const toy::Tensor output = toy::relu(input);

    expect_values(
        output.values(),
        {0.0F, 0.0F, 0.0F, 2.5F, 9.0F},
        "relu clamps negative values and keeps non-negative values"
    );
}

void test_input_immutability() {
    toy::Tensor input({2, 2}, {-8.0F, 0.0F, 3.0F, -1.0F});
    const std::vector<float> before = input.values();

    const toy::Tensor output = toy::relu(input);

    expect_values(input.values(), before, "relu does not mutate input values");
    expect_true(&output.values() != &input.values(), "relu returns an independent tensor");
}

}  // namespace

int main() {
    test_shape_preservation();
    test_negative_zero_positive_values();
    test_input_immutability();

    if (failures != 0) {
        std::cerr << failures << " ReLU test failure(s)\n";
        return 1;
    }
    return 0;
}
