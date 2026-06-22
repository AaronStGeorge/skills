#include "toy/tensor.h"

#include <stdexcept>
#include <utility>

namespace toy {
namespace {

std::size_t element_count(const std::vector<std::size_t>& shape) {
    if (shape.empty()) {
        throw std::invalid_argument("tensor rank must be positive");
    }

    std::size_t count = 1;
    for (std::size_t dim : shape) {
        if (dim == 0) {
            throw std::invalid_argument("tensor dimensions must be positive");
        }
        count *= dim;
    }
    return count;
}

}  // namespace

Tensor::Tensor(std::vector<std::size_t> shape, std::vector<float> values)
    : shape_(std::move(shape)), values_(std::move(values)) {
    const std::size_t expected = element_count(shape_);
    if (values_.size() != expected) {
        throw std::invalid_argument("tensor value count must match shape");
    }
}

const std::vector<std::size_t>& Tensor::shape() const {
    return shape_;
}

const std::vector<float>& Tensor::values() const {
    return values_;
}

std::vector<float>& Tensor::values() {
    return values_;
}

std::size_t Tensor::size() const {
    return values_.size();
}

float Tensor::operator[](std::size_t index) const {
    return values_.at(index);
}

float& Tensor::operator[](std::size_t index) {
    return values_.at(index);
}

}  // namespace toy
