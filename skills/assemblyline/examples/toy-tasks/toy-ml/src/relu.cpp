#include "toy/tensor.h"

namespace toy {

Tensor relu(const Tensor& input) {
    return Tensor(input.shape(), input.values());
}

}  // namespace toy
