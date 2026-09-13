# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
import pytest

from dimos.eval.codegen_loop.waypoints import (
    cumulative_arclength,
    rdp_simplify,
    uniform_by_arclength,
)


def test_empty_trajectory_is_preserved() -> None:
    empty = np.empty((0, 4), dtype=np.float64)

    assert cumulative_arclength(empty[:, 1:3]).shape == (0,)
    assert uniform_by_arclength(empty).shape == (0, 4)
    assert rdp_simplify(empty).shape == (0, 4)


def test_single_point_is_not_duplicated() -> None:
    point = np.array([[10.0, 1.0, 2.0, 0.0]])

    sampled = uniform_by_arclength(point)

    assert sampled.shape == (1, 4)
    np.testing.assert_array_equal(sampled, point)


def test_uniform_sampling_rejects_non_positive_spacing() -> None:
    with pytest.raises(ValueError, match="d_m must be positive"):
        uniform_by_arclength(np.empty((0, 4)), d_m=0)


def test_uniform_sampling_always_includes_route_endpoint() -> None:
    trajectory = np.column_stack(
        (
            np.arange(11, dtype=np.float64),
            np.arange(11, dtype=np.float64),
            np.zeros(11),
            np.zeros(11),
        )
    )

    sampled = uniform_by_arclength(trajectory, d_m=3.0)

    np.testing.assert_array_equal(sampled[0], trajectory[0])
    np.testing.assert_array_equal(sampled[-1], trajectory[-1])
    np.testing.assert_allclose(sampled[:, 1], [0.0, 3.0, 6.0, 9.0, 10.0])
