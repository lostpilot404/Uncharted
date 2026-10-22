# Windows memory-management characterization plan

Status: **harness implemented; native Windows execution pending Runtime CI**. No production reservation or carving behavior has been changed.

Baseline verified before editing: `origin/main` and `HEAD` both resolved to
`5a54fd8d2b8190edb9fd4851f7c71e70e79ea506` (merged PR #6). The checkout was clean. The
mutex/condition-variable implementation is present in `src/runtime_mutex.c`, and
`tests/test_mutex_cond.c` is present. The audit file `Gemini.md` is not tracked in this checkout;
this plan and `docs/gemini-audit-triage.md` record the available evidence.

## Verified implementation facts

Verified directly in `src/runtime_memory.c` at the baseline above:

- `USER_MIN` is `0x1000000000`; `USER_MAX` is `0xfc00000000`; the half-open interval is
  944 GiB.
- Windows `runtime_memory_reserve()` makes one `VirtualAlloc2()` request for that exact interval
  with `MEM_RESERVE | MEM_RESERVE_PLACEHOLDER` and `PAGE_NOACCESS`.
- On failure, `win_fail()` prints `GetLastError()` and exits with status 21. The initial
  `HostRegion` is inserted only after the operating-system reservation succeeds.
- The initial Windows region table entry represents the entire reserved interval.
- `carve_host()` saves protections, unmaps mapped views using `UnmapViewOfFile2(...,
  MEM_PRESERVE_PLACEHOLDER)`, coalesces/splits placeholders, rebuilds surviving section views,
  then restores saved protection runs.
- During carving, surviving views are rebuilt by `view_map(..., PAGE_READWRITE)` before the
  saved protections are reapplied. `view_map()` itself maps a requested `PAGE_NOACCESS` view as
  `PAGE_READWRITE` first, then changes it with `VirtualProtect()`.

These facts confirm the code path; they do not establish how often a user machine encounters a
collision or whether every concurrent carve causes a crash.

## Smallest testable boundary and standalone build

The harness is `tests/test_windows_memory.c`. It includes the complete production
`src/runtime_memory.c` in one translation unit. That is intentional: the target functions
`carve_host()`, `view_map()`, `host_map()`, and the `HostRegion` table are `static`. Including the
file exposes those exact production functions to the fixture without copying or reimplementing
them. Linking the runtime/GPU target would pull in unrelated SDL, Vulkan, FFmpeg and game-runtime
dependencies, so it is not the smallest boundary.

On the Windows branch the remaining project-level link references are `runtime_lookup()` and
`compat_sleep_ns()`; the test supplies inert stubs because neither is exercised. The build needs
the C runtime, Win32 APIs, and the already-installed MSYS2 winpthreads library, but no GPU, SDL,
FFmpeg, game data, full runtime build, or test framework. This is a credible, dependency-minimal
standalone build. The complete `runtime_memory.c` is compiled, not a copied subset. The Linux
translation unit also compiles locally with GCC `-Wall -Wextra -Werror`; this environment cannot
compile its `_WIN32` branch.

Standalone command in MSYS2 CLANG64 (the test file defines its own test-only macro before
including the production source):

```sh
mkdir -p out
clang -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -Isrc \
  tests/test_windows_memory.c -o out/windows-memory-test.exe
out/windows-memory-test.exe
```

The test-only callbacks in `runtime_memory.c` are guarded by `BB_WINDOWS_MEMORY_TEST`; they are
absent from ordinary runtime builds. The fixture seeds `regions` only after its own small
`VirtualAlloc2()` placeholder reservation succeeds. It does not call
`runtime_memory_reserve()` for the fixture and does not reserve or commit 944 GiB.

## Phase 1 — deliberate reservation collision

The parent launches `--child-reservation-collision` using `CreateProcessA`, merges and captures
its stdout/stderr, waits for the child, and records the exit status. The child:

1. Confirms the fixed, allocation-granularity-aligned production base `USER_MIN` is initially
   free. If it is not, that is a setup failure—not evidence of a collision.
2. Reserves one 64 KiB `MEM_RESERVE_PLACEHOLDER` at exactly `USER_MIN` with `VirtualAlloc2()`.
   This creates a deliberate overlap; the test does not rely on a system DLL collision.
3. Probes the exact production 944 GiB `VirtualAlloc2()` request and requires
   `ERROR_INVALID_ADDRESS` (487). Any other Windows error is classified as an unrelated error
   and fails the characterization.
4. Calls production `runtime_memory_reserve()`. Its test-only failure callback records the error
   and `region_count` before `win_fail()` exits the child. The parent requires the production
   error to remain 487, the table count to remain zero, and the child exit status to be 21.

This captures the deliberate blocker, API failure, Windows error and production fatal exit
separately. The 944 GiB request is attempted only after the intentional overlap guarantees that
it fails; the fixture phases below use a 1 MiB reservation.

Expected markers (actual values will be filled in after Windows CI):

```text
COLLISION_BLOCKER=RESERVED ...
COLLISION_PROBE result=NULL error=487 expected=487
TEST_HOOK_RESERVATION_FAILURE error=487 expected_collision_error=487 region_count=0
STOP: reserving the PS4 address range ... error 487
RESULT CRIT-05=COLLISION_REPRODUCED ... win_fail_exit=21 region_table_entries=0
```

## Phase 2 — production section-view carving fixture

Each fixture runs in a fresh child and uses a fixed 1 MiB interval at `USER_MIN + 4 GiB`. It
creates an actual placeholder with `VirtualAlloc2()`, a small pagefile-backed section with
`CreateFileMapping2()`, a backing alias with `MapViewOfFile3()`, then maps the guest-address view
through production `host_map()`/`view_map()`. The test writes distinct 64 KiB backing markers
and verifies alias visibility.

It carves just `[base + 0x30000, base + 0x40000)`—64 KiB out of the 1 MiB parent view—and checks:

- the table before mapping, after mapping, and after carving;
- the center placeholder and the left/right committed `MEM_MAPPED` views via `VirtualQuery()`;
- the right sibling's backing offset advances by `0x40000`, with recognizable markers preserved
  on both sides;
- original `PAGE_READONLY` protections are restored, including a separate 16 KiB
  `PAGE_NOACCESS` GPU-style tracked page, so the test covers multiple saved protection runs;
- backing aliasing remains shared; and
- mapped views are unmapped, placeholders coalesced and released, the backing alias unmapped,
  and the section handle closed. `VirtualQuery()` confirms the fixture range is free afterward.

The offsets and carve edges are 64 KiB aligned for Windows allocation-granularity/placeholder
operations. The test does not claim to validate production `MapViewOfFile3()` offsets that are
only 16 KiB aligned; that constraint is called out in the design review below.

## Phase 3 — transient gap and temporary protection, measured separately

Two test-only callbacks are compiled only into the standalone harness:

- The first executes immediately after production `carve_host()` has unmapped its existing views
  and **before** placeholder coalescing/splitting or reconstruction. It calls `VirtualQuery()` on
  both surviving sibling ranges and records the state/protection while the region table still
  describes the original mapped region.
- The second executes after the surviving views have been remapped and **before** saved
  protections are restored. It separately queries the left side, the previously tracked
  `PAGE_NOACCESS` page, and the right side to detect temporary `PAGE_READWRITE` access.

The direct-gap and temporary-protection checks run in different child processes, so one does not
stand in for the other. A third isolated child starts a worker thread before carving. The carving caller holds the
production metadata `write_lock()`; the worker never takes that lock. At the post-unmap callback
it opens a gate; the worker attempts a volatile read from a sibling page before the callback
permits reconstruction. An unhandled `STATUS_ACCESS_VIOLATION` (exit status
`0xC0000005`) is interpreted as a reproduced access violation only if the child emitted the
pre-read gate/attempt markers. A successful read is reported as **not reproduced**. A timeout,
query failure or unrelated child status is **test inconclusive** and fails CI. An access violation
is contained to that disposable child; Windows reclaims its address space and handles at process
exit.

The output reports independent results for:

- `CRIT-06.gap`: directly observed / behavior not observed / test inconclusive;
- `CRIT-06.concurrent_access`: access violation reproduced / not reproduced / test inconclusive;
- `CRIT-06.temporary_protection`: writable protection observed / behavior not observed /
  test inconclusive; and
- fixture cleanup and final protection restoration.

A directly observed transient placeholder proves that interval occurred in that run. Even a
reproduced controlled access violation does not prove every production execution will crash.

## Phase 4 — Windows CI and evidence policy

The standalone target is intentionally independent of the full runtime/GPU target. Runtime CI
will compile and execute it on `windows-latest` using the existing MSYS2 CLANG64 toolchain,
without removing or replacing any current build, standalone-test, Python-test or artifact steps.
For pull requests, the characterization step fetches `refs/pull/<number>/head`, verifies that SHA
against GitHub's `pull_request.head.sha`, and archives/builds that exact source commit rather than
the default synthetic merge checkout. Push runs use `GITHUB_SHA`. The test prints its observations
and Windows errors into the job log; a compile error, expected collision that yields another
error, broken fixture, failed cleanup, or inconclusive concurrent probe fails the job with
captured output.

**No real Windows execution has occurred in the local sandbox.** It has no Windows runtime,
Wine, or Windows cross-toolchain. Until the new Windows Runtime CI job completes on the exact
final commit SHA, all Windows API outcomes below remain pending; no expected value is reported as
an observation. The result section will be updated with the workflow run and exact commit before
the pull request is presented for review.

## Phase 5 — candidate fix review (no fix implemented)

### CRIT-05: address-space reservation

| Strategy | Guest-address contract | Collision truthfulness | Costs / risks |
|---|---|---|---|
| Keep one full placeholder, but control process layout before startup | Preserves the current contiguous address contract when the reservation succeeds | Truthful; still fails fast if any host allocation occupies the interval | Link/runtime policy can keep the executable, heap and stacks low (the current diagnostic mentions `--disable-high-entropy-va`), but cannot generically relocate a third-party DLL or overlay already loaded in the range. Must reserve before other runtime allocations and test real injected-module configurations. |
| Reserve actual free subranges and represent host-occupied holes separately | Keeps guest numeric addresses for available ranges, but explicitly reduces the usable address set; requests crossing an occupied hole must return a guest allocation error | Truthful if a host-occupied interval is never recorded as a guest reservation and cannot be unmapped/protected as one | More complex interval accounting and allocator/query semantics; fragmented address space; a hole may make a large guest request fail despite enough aggregate free bytes. This is a degraded-capacity policy, not the full 944 GiB contract. |
| Reserve exact guest VMAs on demand | Preserves requested guest addresses only for ranges the OS actually grants | Truthful if metadata is published only after the exact reservation succeeds | A later collision is an ordinary allocation failure; requires transactional table updates and careful interactions with mapped views, guest `VirtualQuery`, and frees. Does not promise that every guest address is available. |
| Translate guest virtual addresses to a separate host address space | Could preserve the logical guest address set while host mappings live elsewhere | Can be truthful if all accesses are translated | Architectural change: raw guest pointers, callbacks, generated code, GPU descriptors and direct-address assumptions all need translation. Not a small fallback. |
| Retry at another host base or mark occupied addresses as guest-reserved | Breaks the guest pointer/address contract or reports false guest state | **Rejected.** A host DLL/heap range is not a guest reservation and is not backed by the runtime's placeholder | Gemini's proposed fallback that inserts host-occupied ranges into guest-reserved VMAs is explicitly unsafe: guest queries lie, later guest allocations conflict, and guest unmap/protect operations could target host-owned memory. Do not implement it. |

### CRIT-06: replacing section-backed views while guest threads can access them

- A metadata lock alone is insufficient. Guest instructions dereference raw guest pointers without
  calling `write_lock()`, and worker/GPU readers or callbacks may also hold those pointers.
- A process-wide guest safepoint/rendezvous is the clearest way to permit wholesale unmap/rebuild,
  but it must include every guest execution thread and asynchronous reader, define lock ordering,
  avoid suspending a thread while it owns a needed lock, and resume only after page protections,
  GPU tracking and metadata agree. Stopping only the mapping caller is not sufficient.
- Splitting views into smaller independently replaceable pieces can reduce how much neighboring
  memory is unavailable at once, but cannot make the unmap/re-map interval atomic. Windows view
  addresses, placeholder splitting and section offsets have 64 KiB allocation-granularity
  constraints, while this runtime's guest page constant is 16 KiB. More pieces also mean more
  region metadata, system calls and fragmentation. It is a mitigation, not by itself a proof of
  safe concurrent raw access.
- Keeping an old view mapped while preparing a new view does not generally solve an in-place
  replacement: `MEM_REPLACE_PLACEHOLDER` requires a placeholder, and the existing virtual address
  is occupied by the old view. Copying into a private replacement breaks section aliasing and
  backing-store semantics. Any alternative must preserve all aliases and exact physical offsets.
- A least-privilege temporary mapping can reduce the current writable window, but it does not
  remove the missing-view interval. Conversely, mapping survivors `PAGE_READWRITE` temporarily
  can bypass GPU page tracking; each saved protection run (including `PAGE_NOACCESS`) must be
  restored before guest execution resumes.
- `MapViewOfFile3` section offsets and view bases must respect Windows allocation granularity.
  The current guest-page checks accept 16 KiB alignment. Rounding an offset down without a
  correctly biased view and adjusted region metadata would map the wrong bytes. Any fix must
  address the 16 KiB/64 KiB mismatch explicitly, as well as placeholder split/coalesce rules.
- Every `UnmapViewOfFile2`, `VirtualFree`, `MapViewOfFile3` and `VirtualProtect` failure path must
  leave a coherent table and OS mapping or fail in a deliberately contained way. Current
  `win_fail()` terminates the process on these errors. A future patch needs failure/rollback
  tests; this characterization does not alter that policy.

No production memory-manager rewrite is authorized by this mission or included here. The
candidate designs require a separate review after Windows evidence is collected.

## Results

Pending native Windows Runtime CI on the exact final commit. Fill in the run URL, commit SHA,
actual `GetLastError()` values, child exit codes, `VirtualQuery()` states/protections, concurrent
probe outcome, and cleanup result here after the workflow completes. Do not replace `pending`
with predictions.
