# Windows placeholder-leaf prototype (Mission 7.8)

**Implemented experiment; native Windows empirical gate NOT VALIDATED.** No Windows
execution has occurred in this workspace. This is not a production memory manager,
not a fix for Hazard A / CRIT06, and not permission to implement one. See
[REPORT.md](REPORT.md) for the eleven-section evidence report and decision.

## Isolation and prerequisites

No runtime headers, production implementation, root build, existing tests or
existing workflows are dependencies. Only this directory and a separate
`placeholder-leaf-prototype.yml` workflow were added. The preexisting dirty files
and PR9 are deliberately preserved. No commit, push, fork or PR change was made.

Primary supported build: **native AMD64 Windows, x64 MSVC (C11-capable Visual
Studio 2022) or clang-cl with a current installed Windows SDK**, CMake >= 3.21.
`_Generic` assertions compare function-pointer types to the installed SDK's
actual API declarations. The primary build treats warnings as errors. MinGW / a
Linux cross-build is not SDK or runtime qualification. ARM64 emulation, Wine,
POSIX, missing APIs, incompatible page/root granularity and OS builds older than
Windows 10 1903 (18362) are deliberately unsupported. A successful Server 2022
run would qualify only that recorded build, **not every advertised older OS**.

No `CreateFileMapping2`, coarse-map fallback, alternate leaf size, VirtualProtect
substitution, runtime reader registry, thread suspension or live-view migration.
Modern APIs are dynamically resolved from KernelBase / Kernel32. Backing uses
`CreateFileMappingW` with an unnamed pagefile-backed **RW** committed section.
Executable mappings and mixed-protection Hazard B are not tested.

## Native build and run (from repository root, PowerShell)

Use a Visual Studio x64 developer shell when choosing Ninja or clang-cl. The
Visual Studio generator below selects x64 explicitly:

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
New-Item -ItemType Directory -Force out/placeholder-leaf-logs | Out-Null
git rev-parse HEAD | Tee-Object out/placeholder-leaf-logs/source-sha.txt
Get-FileHash experiments/windows-placeholder-leaf-prototype/placeholder_leaf_probe.c -Algorithm SHA256 |
  Format-List | Out-File out/placeholder-leaf-logs/source-digest.txt
cmake -S experiments/windows-placeholder-leaf-prototype -B out/placeholder-leaf-prototype -A x64 2>&1 |
  Tee-Object out/placeholder-leaf-logs/configure.log
if ($LASTEXITCODE -ne 0) { throw 'Configure failed' }
cmake --build out/placeholder-leaf-prototype --config Release --verbose 2>&1 |
  Tee-Object out/placeholder-leaf-logs/build.log
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
& experiments/windows-placeholder-leaf-prototype/run_probe.ps1 `
  -Executable out/placeholder-leaf-prototype/Release/placeholder_leaf_probe.exe `
  -OutputDirectory out/placeholder-leaf-logs -Benchmark -Repetitions 3
```

For clang-cl use a **separate** build directory and configure in a VS developer
shell with `-G Ninja -DCMAKE_C_COMPILER=clang-cl -DCMAKE_BUILD_TYPE=Release`
(instead of `-A x64`). Do not reuse a different toolchain's CMake cache. A plain
`ctest --test-dir out/placeholder-leaf-prototype -C Release --output-on-failure`
runs only the basic case, **not** the required injection suite or benchmark.

Keep complete output, `results.json`, configure/build logs, source SHA256, git
SHA and dirty status. CMake embeds the C source hash and reconfigures on source
changes; the executable records compiler, configuration, OS build, architecture,
page size, allocation granularity, logical processor count, RAM and QPC frequency.
Copy artifacts outside Git; `out/` is already ignored. Do not infer an empirical
result from the example log markers below or from historical runtime CI.

## What runs

Default execution uses two separate four-leaf roots, each 0x10000 bytes:

* single: `LEFT1 | LEFT2 | TARGET | RIGHT`, target leaf 2;
* multi: `LEFT | TARGET1 | TARGET2 | RIGHT`, target leaves 1 and 2.

Each exact 0x4000 placeholder is replaced with one independently returned view
at offset `leaf * 0x4000` in the same section. A fifth interval permits rebinding.
Distinct interval-index prefixes and deterministic per-byte patterns cover full
intervals, not just sentinel addresses. The backing alias changes one byte at
`G + 17`; every relevant mapped interval is checked against both its expected
pattern and the alias. Nonoverlapping initial offsets are prescribed explicitly.

Two reader threads read the outside leaves. Auto-reset request/done events and
startup events make the mapping thread wait after **each** target unmap, while
both readers perform 16 full-leaf checks. The main thread also checks every
unaffected leaf (including LEFT2 in the single case). This is gated repeated
reading during deliberately paused holes, **not continuous free-running stress**.
Readers are joined before cleanup. A timeout fail-stops the process, never grants
permission to destroy storage still reachable by a live reader.

Queries walk every host page and check exact forward region boundaries,
allocation bases, reserved/private versus committed/mapped state, and protection.
`VirtualQuery` has no placeholder bit: successful API calls plus the ledger
establish placeholder provenance. This intentionally strict query model must be
confirmed on Windows; a mismatch is a failure, not a silently relaxed assertion.

An intentional one-byte target read uses SEH. Its filter accepts only a read AV
at that exact armed address. A mapped-leaf check catches only a read AV within
the leaf it is checking and records failure; unrelated exceptions propagate.
Target state must be MEM_RESERVE rather than a committed PAGE_NOACCESS view.
Remapping to the same offset restores bytes; a second unmap and replacement at
the fifth interval changes just the target and preserves unaffected offsets.

Every split, map, unmap, coalesce and root release is audited with an exact base
and size; the root release separately logs its API-required size argument zero.
Map calls additionally log handles, offset, size, flags and protection. No carve
or remap wrapper can operate on a view outside the declared target. Cleanup has
an explicitly different lifetime/target: the entire owned root after readers join.

## Deterministic, default-off precall injection

Each invocation below is a separate process. The synthetic error is
`ERROR_NOT_ENOUGH_MEMORY`; **the selected OS call is skipped**, not forced to fail
inside the kernel. A successful injection test still returns **3**, not 0.

```powershell
$exe = 'out/placeholder-leaf-prototype/Release/placeholder_leaf_probe.exe'
& $exe --inject split:1     # earlier prefix split succeeded
& $exe --inject map:2       # two earlier leaf maps succeeded
& $exe --inject unmap:1     # multi-target; first target unmapped, siblings checked
& $exe --inject remap:0    # intentional target hole exists
& $exe --inject coalesce:0 # known placeholders during cleanup
& $exe --benchmark 3      # optional, repetitions 1..10
```

A matching `SIMULATED_PRECALL_FAILURE ... real_os_invoked=NO`, complete known
ledger / query / mapped-pattern snapshot, `SIMULATION_CHECK=PASS`, completed
cleanup and `RESULT=EXPECTED_SIMULATED_FAILURE` are required by the runner.
Coalesce injection is one-shot: after snapshot it retries only ledger-known
cleanup, reporting that it did **not** roll back the mapping transaction. For
other injections it returns the explicit operation failure after ledger-only
cleanup. A real mutating API error or contradictory layout fail-stops (exit 21),
without guessed layout rollback. Process teardown is not recovery evidence.

Exit codes: 0 normal success; 1 failed check/unreached injection; 2 unsupported
or bad arguments; 3 expected simulated failure; 21 fail-stop. The runner accepts
only the particular expected exit plus markers and rejects real OS failures.

## Optional bounded scaling

Sizes 4/64/256/1024 leaves, default three raw repetitions. Logs retain every
operation and one raw `BENCH` record per successful size/repetition. Timings
cover reserve, split, section creation/seed, map, one interior unmap/remap, final
validation, cleanup. **These are wrapper timings including queries, pattern
checks, logging and ledger maintenance**, not pure syscall latency. Split and
coalesce verification can be quadratic; there is no production extrapolation.
Counts distinguish leaf views, the additional backing view, section handle,
active/peak extent count, fixed metadata capacity and `sizeof(Probe)` storage.
Failures stop the run and preserve preceding operations; no failed sample is
silently turned into a successful timing row. No scaling measurements exist yet.

## CI and qualification

`.github/workflows/placeholder-leaf-prototype.yml` targets `windows-2022`, builds
only this project, runs the default and five injection processes plus the optional
benchmark, and uploads full logs even on failure. It does not build the runtime.
**The file has not been published or executed.** A newly added manual workflow
is not dispatchable just because it exists in this local checkout. This session
must not push the branch merely to obtain CI, since that would alter PR9.

Before any production design review: obtain actual supported native Windows
build/run artifacts with exact source/compiler/OS/exit codes; verify 16 KiB base,
size and non-64-KiB offset acceptance; independently read the complete operand
log; confirm single/multi sibling continuity, backing/alias behavior, true target
invalidation, each failure injection and ownership-preserving cleanup. Review
older-OS coverage separately. None of these native gates is currently passed.
