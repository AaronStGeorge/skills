#pragma once

#include <cstddef>
#include <vector>

namespace toy {

class Tensor {
public:
    Tensor(std::vector<std::size_t> shape, std::vector<float> values);

    const std::vector<std::size_t>& shape() const;
    const std::vector<float>& values() const;
    std::vector<float>& values();

    std::size_t size() const;
    float operator[](std::size_t index) const;
    float& operator[](std::size_t index);

private:
    std::vector<std::size_t> shape_;
    std::vector<float> values_;
};

Tensor relu(const Tensor& input);

}  // namespace toy
