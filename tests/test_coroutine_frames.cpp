// SPDX-License-Identifier: GPL-2.0-or-later
// Regression harness for the Liverpool::Task coroutine-frame lifetime contract
// (gpu/shadps4/video_core/amdgpu/liverpool.h Task/promise_type; leak sites in liverpool.cpp
// IndirectBufferConst/IndirectBuffer, fixed by destroying nested task frames after done()).
//
// The promise_type below mirrors Liverpool::Task::promise_type exactly in the three points
// that decide frame lifetime: initial_suspend = suspend_always, final_suspend = suspend_always
// (a finished task keeps its frame until handle.destroy()), and yield_value(empty&&) as the
// suspension point. Building the real liverpool.cpp needs the whole video core and a Windows
// toolchain; this harness validates the lifetime semantics the fix relies on and fails if a
// nested-task pattern like the command processor's leaks frames.
//
// Build/run (any C++20 compiler):
//   g++ -std=c++20 -O0 -g tests/test_coroutine_frames.cpp -o out/coroutine-frames-test
//   out/coroutine-frames-test
#include <cassert>
#include <coroutine>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <utility>

// Live-heap accounting: every allocation this process makes after startup is counted, so a
// leaked coroutine frame shows up as an ever-growing live count.
static std::size_t live_allocations = 0;
static std::size_t live_bytes = 0;

void* operator new(std::size_t size) {
    if (void* p = std::malloc(size + alignof(std::max_align_t))) {
        ++live_allocations;
        live_bytes += size;
        return p;
    }
    throw std::bad_alloc{};
}
void* operator new[](std::size_t size) {
    return operator new(size);
}
void operator delete(void* p) noexcept {
    if (p) {
        --live_allocations;
        std::free(p);
    }
}
void operator delete[](void* p) noexcept {
    operator delete(p);
}
void operator delete(void* p, std::size_t) noexcept {
    operator delete(p);
}
void operator delete[](void* p, std::size_t) noexcept {
    operator delete(p);
}

// Mirror of Liverpool::Task (liverpool.h). No destructor: the frame is freed only by
// handle.destroy(), exactly like the production type.
struct Task {
    struct promise_type {
        auto get_return_object() {
            Task task{};
            task.handle = std::coroutine_handle<promise_type>::from_promise(*this);
            return task;
        }
        static constexpr std::suspend_always initial_suspend() noexcept {
            return {};
        }
        static constexpr std::suspend_always final_suspend() noexcept {
            return {};
        }
        void unhandled_exception() {
            std::abort();
        }
        void return_void() {}
        struct empty {};
        std::suspend_always yield_value(empty&&) {
            return {};
        }
    };
    using Handle = std::coroutine_handle<promise_type>;
    Handle handle{};
};

// One nested indirect buffer, like ProcessCompute's IndirectBuffer case: create a child task,
// run it to done() by resume/yield ping-pong, then release the frame (the fixed behavior).
static Task NestedBuffer() {
    co_await std::suspend_always{};
    for (int i = 0; i < 3; ++i) {
        co_yield Task::promise_type::empty{};
    }
}

// The command-processor loop around a nested task (liverpool.cpp IndirectBuffer cases): the
// child is driven to completion, then the frame is kept (defect) or destroyed (fix).
static Task ProcessLoop(int nested_tasks, bool destroy_frames) {
    co_await std::suspend_always{};
    for (int n = 0; n < nested_tasks; ++n) {
        auto task = NestedBuffer();
        task.handle.resume();
        while (!task.handle.done()) {
            co_yield Task::promise_type::empty{};
            task.handle.resume();
        }
        if (destroy_frames) {
            task.handle.destroy(); // the fix: final_suspend is suspend_always
        }
    }
}

static void DriveToDone(Task::Handle handle) {
    handle.resume();
    while (!handle.done()) {
        handle.resume();
    }
}

int main() {
    // Warm up: first-touch allocations inside the standard library that are not the loop's.
    {
        auto warm = ProcessLoop(1, true);
        DriveToDone(warm.handle);
        warm.handle.destroy();
    }
    const std::size_t base_live = live_allocations;

    // 1. Reproduce the defect: nested frames kept after done() accumulate one per task.
    {
        auto leaky = ProcessLoop(50, false);
        DriveToDone(leaky.handle);
        leaky.handle.destroy();
        assert(live_allocations >= base_live + 50);
        std::printf("leak reproduced: %zu live allocations after 50 undestroyed nested frames\n",
                    live_allocations - base_live);
    }

    // The frames of the leaky run are unreachable: reclaim nothing here on purpose, the count
    // itself is the evidence. (A release build keeps them for the process lifetime.)

    // 2. The fixed pattern frees every nested frame: live count returns to the baseline.
    {
        const std::size_t before = live_allocations;
        auto fixed = ProcessLoop(50, true);
        DriveToDone(fixed.handle);
        fixed.handle.destroy();
        assert(live_allocations == before);
        std::printf("fixed pattern: no net allocations after 50 destroyed nested frames\n");
    }

    // 3. destroy() after done() is safe and stable across repeated driver runs.
    {
        const std::size_t before = live_allocations;
        for (int round = 0; round < 3; ++round) {
            auto t = ProcessLoop(5, true);
            DriveToDone(t.handle);
            t.handle.destroy();
        }
        assert(live_allocations == before);
        std::printf("repeat driver runs: live allocations unchanged\n");
    }
    std::printf("PASS: coroutine frame lifetime contract (destroy nested frames after done())\n");
    return 0;
}
