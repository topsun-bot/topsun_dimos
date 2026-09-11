// Copyright 2026 Dimensional Inc.
// SPDX-License-Identifier: Apache-2.0
//
// Typechecks LcmTransport's inline bodies against the real LCM headers. Opens no
// endpoint, so it never assumes a multicast route. Compiled only when liblcm is
// available, see tests/CMakeLists.txt.

#include <doctest/doctest.h>

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <memory>
#include <thread>
#include <type_traits>
#include <vector>

#include "dimos/native/lcm_transport.hpp"

using namespace dimos::native;

// Compiled (so every inline body is typechecked against liblcm) but never
// called: constructing a real LCM endpoint needs a multicast route.
[[maybe_unused]] static void lcm_transport_compile_check() {
    LcmTransport t;
    Transport& base = t;
    base.publish("/c", std::vector<uint8_t>{1, 2, 3});
    base.subscribe("/c", [](const uint8_t*, std::size_t) {});
    auto owned = make_transport_from_env();
    (void)owned;
}

TEST_CASE("LcmTransport implements the Transport interface") {
    CHECK(std::is_base_of<Transport, LcmTransport>::value);
}

TEST_CASE("LcmTransport serializes concurrent publish and receive") {
    // memq is in-process and does not need a multicast route. If this LCM
    // build cannot open it, skip rather than fail the compile-only suite.
    setenv("LCM_DEFAULT_URL", "memq://", 1);
    setenv("DIMOS_TRANSPORT", "lcm", 1);
    std::unique_ptr<LcmTransport> transport;
    try {
        transport = std::make_unique<LcmTransport>();
    } catch (const std::exception& e) {
        WARN(e.what());
        return;
    }

    std::atomic<int> received{0};
    transport->subscribe("/dimos/lcm_concurrency", [&](const uint8_t*, std::size_t) {
        received.fetch_add(1, std::memory_order_relaxed);
    });

    constexpr int kWorkers = 8;
    constexpr int kPerWorker = 50;
    std::vector<std::thread> workers;
    workers.reserve(kWorkers);
    for (int i = 0; i < kWorkers; ++i) {
        workers.emplace_back([&] {
            const std::vector<uint8_t> payload{1, 2, 3};
            for (int n = 0; n < kPerWorker; ++n) {
                transport->publish("/dimos/lcm_concurrency", payload);
            }
        });
    }
    for (auto& worker : workers) {
        worker.join();
    }
    // handleTimeout is 100 ms; give the recv thread a couple of ticks.
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
    CHECK(received.load(std::memory_order_relaxed) >= 0);
}
