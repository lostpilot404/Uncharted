# Windows memory-management characterization plan

Status: **harness implemented; native Windows observations recorded from Runtime CI run
39843825619**. Every PR head is tested from its verified source SHA. No production reservation
or carving behavior has been changed.

Baseline verified before editing: `origin/main` and `HEAD` both resolved to
`5a54fd8d2b8190edb9fd4851f7c71e70e79ea506` (merged PR #6). The checkout was clean. The
mutex/condition-variable implementation is present in `src/runtime_mutex.c`, and
`tests/test_mutex_cond.c` is present. The audit file `Gemini.md` is not tracked in this checkout;
this plan and `docs/gemini-audit-triage.md` record the available evidence.

## Verified implementation facts

Verified directly in `src/runtime_memory.c` at the PR #7 baseline above (pre-fix):

- `USER_MIN` is `0x1000000000`; `USER_MAX` is `0xfc00000000`; the half-open interval is
  944 GiB.
- Windows `runtime_memory_reserve()` made one `VirtualAlloc2()` request for that exact interval
  with `MEM_RESERVE | MEM_RESERVE_PLACEHOLDER` and `PAGE_NOACCESS`.
- On failure, `win_fail()` printed `GetLastError()` and exited with status 21. The initial
  `HostRegion` was inserted only after the operating-system reservation succeeded.
- The initial Windows region table entry represented the entire reserved interval.

Mission 7.3 changes only the reservation/span layer (see "CRIT-05 fix (Mission 7.3)" below):
the full-span request is still attempted first; on `ERROR_INVALID_ADDRESS` the free
subranges are reserved individually and host-occupied intervals are omitted from the table.
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
the C runtime, Win32 APIs, the onecore import library for the Windows 10 memory APIs, and the
already-installed MSYS2 winpthreads library, but no GPU, SDL, FFmpeg, game data, full runtime
build, or test framework. This is a credible, dependency-minimal
standalone build. The complete `runtime_memory.c` is compiled, not a copied subset. The Linux
translation unit also compiles locally with GCC `-Wall -Wextra -Werror`; this environment cannot
compile its `_WIN32` branch.

Standalone command in MSYS2 CLANG64 (the test file defines its own test-only macro before
including the production source):

```sh
mkdir -p out
clang -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -Isrc \
  tests/test_windows_memory.c -lonecore -o out/windows-memory-test.exe
out/windows-memory-test.exe
```

The test-only callbacks in `runtime_memory.c` are guarded by `BB_WINDOWS_MEMORY_TEST`; they are
absent from ordinary runtime builds. The fixture seeds `regions` only after its own small
`VirtualAlloc2()` placeholder reservation succeeds. It does not call
`runtime_memory_reserve()` for the fixture and does not reserve or commit 944 GiB.

## Phase 1 — deliberate reservation collision (updated by Mission 7.3)

PR #7 characterization (superseded as the expected production outcome): a 64 KiB blocker at
`USER_MIN` made the monolithic 944 GiB `VirtualAlloc2()` fail with 487, production
`runtime_memory_reserve()` exited 21 via `win_fail()`, and the region table stayed empty.

Mission 7.3 expected behavior: the same blocker must now be **recovered from**. The parent
launches CRIT-05 children using `CreateProcessA`, merges and captures stdout/stderr, waits for
each child, and records exit status:

- `--child-reserve-low-blocker`: reserves a committed, canary-filled 64 KiB host blocker at
  exactly `USER_MIN`; confirms the monolithic probe still fails with 487; then requires
  production `runtime_memory_reserve()` to succeed, the first reserved subrange to start at
  `USER_MIN + 64 KiB`, the blocker to be absent from both tables, guest queries on it to return
  `ACCESS`, a non-fixed map to land at `USER_MIN + 64 KiB`, `MAP_FIXED`/reserve/crossing
  requests on the blocker to return `NO_MEMORY` with tables unchanged, and the blocker canary
  to survive cleanup.
- `--child-reserve-all-blocked` (legacy alias `--child-reservation-collision`): blocks every
  free subrange of `[USER_MIN, USER_MAX)`, so no guest subrange remains. Only then must
  `runtime_memory_reserve()` invoke the failure hook with `region_count=0` and exit 21. The
  parent requires markers:

```text
COLLISION_PROBE result=NULL error=487 expected=487
TEST_HOOK_RESERVATION_FAILURE error=487 expected_collision_error=487 region_count=0
STOP: reserving the PS4 address range (no free guest subrange) ... error 487
RESULT CRIT-05.all_blocked=PASS win_fail_exit=21 region_table_entries=0
```

Additional Mission 7.3 children: `--child-reserve-no-collision` (full-span success, aliasing,
query, release zeroing via unmap), `--child-reserve-middle-blocker` (84 KiB unaligned blocker;
hole is excluded through its 64 KiB-aligned end; boundary map/protect/query/clamp/unmap and
failed-crossing rollback), `--child-reserve-high-blocker` (blocker at `USER_MAX - 64 KiB`),
and `--child-reserve-multi-blocker` (three non-contiguous blockers, a 128 KiB pocket,
`find_free` skipping, `MAP_NO_OVERWRITE`, and `batch_map2` stopping at a hole-crossing entry
with `processed=1`). All children seed only a 4 MiB pagefile section after
`runtime_memory_reserve()` and verify cleanup returns runtime reservations to `MEM_FREE`
while host blockers stay byte-identical.

Final summary marker on success:

```text
RESULT CRIT-05=FIXED subrange_reservation=PASS host_holes_excluded_from_vmas=YES crossing_requests=NO_MEMORY full_exhaustion_exit=21
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

The local sandbox has no Windows runtime, Wine, or Windows cross-toolchain; native API
observations therefore come from Runtime CI. The CI step records the exact tested source commit,
Windows API errors, MBI state/protection, child outcomes and cleanup as grouped check annotations,
and uploads the full output log. The results section below contains measured Windows values, not
predictions. Runtime CI reruns against the verified exact PR-head source on each push; its check on
the final PR head is the commit-specific review evidence.

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

No production memory-manager rewrite was authorized by PR #7's mission. The candidate designs
required a separate follow-up review using the collected Windows evidence — that follow-up is
Mission 7.3 below.

## CRIT-05 fix (Mission 7.3)

**Selected design: reserve available address subranges while keeping host-owned occupied
ranges distinct from guest VMAs** (row 2 of the table above), with the no-collision fast path
preserved exactly. Rationale from the source: nothing requires the 944 GiB interval to be one
OS reservation — the guest VMA table (`find_free`/`covered`/`overlaps`) is the guest-address
contract, and `carve_host()` only needs each individual map/unmap request to lie inside one
contiguous placeholder span. Guest addresses are never translated; no retry at another host
base occurs; metadata is inserted only after the corresponding `VirtualAlloc2()` succeeds.

Production changes, all in `src/runtime_memory.c` (plus a comment in `src/runtime.h`):

- `HostRegion` gains a `root` field identifying its originating reservation, so cleanup/coalesce
  never merges or releases across a host-owned gap.
- `runtime_memory_reserve()` (now under the table write lock): fast path unchanged (one
  full-span placeholder). On `ERROR_INVALID_ADDRESS` it walks `[USER_MIN, USER_MAX)` with
  `VirtualQuery()`, reserves each 64 KiB-aligned `MEM_FREE` subrange exactly at its guest
  addresses, and records only those. Any other OS error, a query failure, or zero reservable
  subranges rolls back all partial reservations (`release_host_regions()`) and fails explicitly
  via `win_fail()`/exit 21 — no partially inconsistent model is published. Occupied intervals
  (including an unaligned blocker's tail up to the next 64 KiB boundary) become holes.
- `carve_host()` returns `-1` instead of `win_fail()` when a request is not fully inside one
  reserved span; `host_map()`/`host_unmap()` propagate it, so guest `place()`/`unmap_locked()`
  return `NO_MEMORY`/`INVALID`. Mapped state is published only after `MapViewOfFile3()` succeeds.
- `find_free()` skips holes between reserved spans; `place()` rejects fixed/out-of-range
  requests before any OS or table mutation and pre-reserves VMA capacity so insertion cannot
  fail after a successful map; guest protection/query/clamp/write-backing paths are
  hole-aware through the existing VMA table and hardened against overflow/over-read.
- `pool()` closes the section handle if the backing view fails; `reserve_range()` ensures the
  reservation exists before validating a fixed request (previously a fixed reserve before any
  pool use could reach the fatal carve path).

Guest-capacity consequence (explicit, not silent): a guest request contained in an available
subrange succeeds at its exact guest address; a request crossing a hole returns `NO_MEMORY`,
so a hole can make a very large contiguous guest mapping fail despite enough aggregate free
space. This degraded-capacity policy is the documented row-2 trade-off. The GNM
tessellation-factor ring at `0x7FF000000` (inside the guest interval) is unaffected in the
no-collision case; if a host hole covered it, dependent mappings would fail explicitly.

CRIT-06 is **not** fixed by this work: `carve_host()` still unmaps and rebuilds sibling views
with the transient gap and temporary `PAGE_READWRITE` window characterized below.

## Results

A successful native Runtime CI observation run completed at
[run 39843825619](https://github.com/lostpilot404/Uncharted/actions/runs/39843825619). The
workflow log recorded `TESTED_SOURCE_COMMIT=abc35da67bffffdc1d3883f066c91d16ca1b4b1d`; although
GitHub's default job checkout was the synthetic merge commit `4acc2c4ca6e163bd7cf162223fe3bba6a669496f`,
the harness was built from the separately fetched and SHA-verified PR-head archive. Because this report update is
documentation-only, the PR's current-head check is also required for final commit-specific
verification. The values below are observations from Windows, not predictions.

### CRIT-05 — 944 GiB reservation collision

- The child reserved a 64 KiB `MEM_RESERVE_PLACEHOLDER` blocker at the aligned production base
  `0x0000001000000000`; `VirtualQuery()` reported `State=0x2000` (`MEM_RESERVE`).
- The direct production-sized `VirtualAlloc2()` probe returned `NULL`; `GetLastError()` was
  `487` (`ERROR_INVALID_ADDRESS`), matching the expected overlap error.
- The subsequent production `runtime_memory_reserve()` attempt reached the test-only failure hook
  with `error=487` and `region_count=0`. `win_fail()` reported the same OS error and the child
  exited `0x00000015` (decimal 21).
- Result: **collision reproduced**. No host-unreserved interval was entered into the runtime
  region table. This validates the deliberate collision path; it does not estimate collision
  frequency on other processes or machines.

### CRIT-06 — small carve, transient gap, and writable window

- The 1 MiB placeholder fixture at `0x0000001100000000` was reserved with
  `AllocationProtect=0x1` (`PAGE_NOACCESS`); its initial section view was
  `State=0x1000` (`MEM_COMMIT`), `Type=0x40000` (`MEM_MAPPED`), `Protect=0x4`
  (`PAGE_READWRITE`).
- Production `carve_host()` removed `[0x0000001100030000, 0x0000001100040000)` (64 KiB) from
  that 1 MiB view. Afterward, `VirtualQuery()` reported committed/mapped left and right views with
  `Protect=0x2` (`PAGE_READONLY`), the carved center as `State=0x2000` (`MEM_RESERVE`), and the
  GPU-style tracked page as committed/mapped with `Protect=0x1` (`PAGE_NOACCESS`). The right
  sibling backing offset was `0x40000`; left/right marker and alias checks passed.

The reported `VirtualQuery()` MBI records were:

| Query point | `BaseAddress` | `RegionSize` | `State` | `Protect` | `Type` | `AllocationProtect` |
|---|---:|---:|---:|---:|---:|---:|
| Initial placeholder | `0x0000001100000000` | `0x100000` | `0x2000` | `0x0` | `0x20000` | `0x1` |
| Initial section view | `0x0000001100000000` | `0x100000` | `0x1000` | `0x4` | `0x40000` | `0x4` |
| Left after carve (query base+PAGE) | `0x0000001100004000` | `0x10000` | `0x1000` | `0x2` | `0x40000` | `0x4` |
| Carved center (query carve+PAGE) | `0x0000001100034000` | `0xc000` | `0x2000` | `0x0` | `0x20000` | `0x1` |
| Right after carve (query end-PAGE) | `0x00000011000fc000` | `0x4000` | `0x1000` | `0x2` | `0x40000` | `0x4` |
| Tracked no-access page | `0x0000001100014000` | `0x4000` | `0x1000` | `0x1` | `0x40000` | `0x4` |
| Left during unmap (query base+PAGE) | `0x0000001100004000` | `0xfc000` | `0x2000` | `0x0` | `0x20000` | `0x1` |
| Right during unmap (query end-PAGE) | `0x00000011000fc000` | `0x4000` | `0x2000` | `0x0` | `0x20000` | `0x1` |

- At the exact post-unmap/pre-reconstruction callback, both sibling queries reported
  `State=0x2000` (`MEM_RESERVE`), `Protect=0`, `Type=0x20000` (`MEM_PRIVATE` placeholder),
  while the production table still described the original region as mapped. Result:
  **transient gap directly observed** by `VirtualQuery()`.
- In the isolated concurrent child, the worker reached the gate and attempted a volatile read at
  `0x0000001100004000` while the mapping caller held `write_lock()`. The child exited with
  `0xC0000005` (`STATUS_ACCESS_VIOLATION`). Result: **controlled access violation reproduced**.
  This is one contained reproduction; it does not imply every carve crashes.
- In the separate protection child, left, tracked, and right pages all reported
  `State=0x1000` and `Protect=0x4` (`PAGE_READWRITE`) immediately before saved protections were
  restored. Result: **temporary writable protection observed**. Final checks confirmed
  `PAGE_READONLY` restoration and reinstatement of the tracked `PAGE_NOACCESS` page.
- Fixture cleanup passed: section handle closed, backing alias unmapped, placeholders released.
  The harness and existing Runtime CI build, standalone tests and Python suite all passed; the
  harness reported `RESULT SUMMARY failures=0`.

The Runtime CI check for each PR head is the commit-specific release evidence: it logs the exact
tested source SHA and emits API errors, MBI state/protection, child outcomes and cleanup evidence
as grouped check annotations; the full log is uploaded as an artifact. No production reservation
fallback or carving fix was applied by PR #7.

### Mission 7.3 — CRIT-05 fix verification (native Windows, this PR)

Native evidence: [Runtime CI run 39946258119](https://github.com/lostpilot404/Uncharted/actions/runs/39946258119)
on PR #8, `TESTED_SOURCE_COMMIT=19b3a36238063579a4c5cb1e87294bc71de4e8ed` (the harness was built
from the SHA-verified PR-head archive; the workflow checkout was the synthetic merge
`b728ead314f3fc35781dd9c1ee2565cdd19c0740`). All six CRIT-05 children passed
(`RESULT SUMMARY failures=0`); notably even the "no-collision" child found 12 pre-existing
occupied intervals in the CI process and reserved 2 subranges (966651 of 966656 MiB), the
low-blocker child recovered with 1 subrange starting at `USER_MIN + 64 KiB`, and the
all-blocked child still exited 21 with an empty table. An earlier run on `b91296a`
(run 39934002061) failed with the same three harness bugs fixed in `19b3a36` (under-sized
direct-memory allocations in the middle/high/multi test children — production code was not
the cause; the high child's access violation was the test reading an unmapped view after a
failed map). CRIT-06 markers in the passing run remain: gap `DIRECTLY_OBSERVED`, concurrent
`ACCESS_VIOLATION_REPRODUCED` (0xC0000005), temporary protection `WRITABLE_PROTECTION_OBSERVED`.
The updated harness reports, from the exact tested source SHA:

```text
RESULT CRIT-05.no_collision=PASS
RESULT CRIT-05.low_blocker=PASS recovered=YES fixed_on_blocker=NO_MEMORY
RESULT CRIT-05.middle_blocker=PASS unaligned_blocker=84KiB boundary_rollback=PASS
RESULT CRIT-05.high_blocker=PASS
RESULT CRIT-05.multi_blocker=PASS disjoint_subranges=PASS batch_partial_stop=PASS
RESULT CRIT-05.all_blocked=PASS win_fail_exit=21 region_table_entries=0
RESULT CRIT-05=FIXED subrange_reservation=PASS host_holes_excluded_from_vmas=YES crossing_requests=NO_MEMORY full_exhaustion_exit=21
RESULT CRIT-06.fixture=PASS ...
RESULT CRIT-06.gap=DIRECTLY_OBSERVED ...        (characterization, unchanged)
RESULT CRIT-06.concurrent_access=ACCESS_VIOLATION_REPRODUCED ...  (characterization, unchanged)
RESULT CRIT-06.temporary_protection=WRITABLE_PROTECTION_OBSERVED ... (characterization, unchanged)
RESULT SUMMARY failures=0
```

Local (Linux) validation for Mission 7.3: `src/runtime_memory.c` compiles with GCC
`-Wall -Wextra -Werror` (Linux branch only — this sandbox has no Windows toolchain, Wine, or
cross-compiler); the Python suite reports 98 tests / 28 skips; the standalone C/C++ tests
(content, file-mods, mutex-cond 32 checks, coroutine-frames, upscaler-support, dlss-sr) pass.
Linux results do not validate the `_WIN32` path; the Runtime CI run above does.
