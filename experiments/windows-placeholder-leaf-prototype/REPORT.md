# Mission 7.8 — isolated Windows placeholder-leaf prototype

**Verdict: IMPLEMENTED, NATIVE EMPIRICAL VALIDATION BLOCKED / UNVALIDATED.**
There are no measured Windows leaf addresses, accepted API results, sibling-read
results, target AV results or timings. The experiment must not be described as a
successful lifecycle demonstration until its actual Windows logs exist. No
production implementation or design-review authorization follows from this work.

## 1. Verified baseline

Preflight and final read-only checks agree:

| Item | Verified value |
|---|---|
| Repository / branch | `lostpilot404/Uncharted`, `arena/96680190-uncharted` |
| Local HEAD and main / remote main | `3d07101d02b63f65f6026cc673e86e636d07ed59` |
| Remote session branch / PR9 head | `09bd5e7da2b54f8953e2edbb67a841627f8e4860` |
| PR9 | OPEN, unmerged, not altered |
| Existing worktree changes | Five unstaged files, +214/-24; preserved |
| Index | No staged changes |

The local/remote head difference was preexisting, not repaired by resetting,
switching, rebasing, committing or pushing. All five original modified file blob
hashes were preserved exactly; see [evidence/baseline.json](evidence/baseline.json).
The files are `.github/workflows/runtime.yml`, `docs/gemini-audit-triage.md`,
`docs/windows-memory-test-plan.md`, `src/runtime_memory.c`, and
`tests/test_windows_memory.c`. No tracked baseline file was edited by this mission.

Historical checks for PR9 remain passing: Runtime run `39985544942`, Package
`39985544822`, Rust `39985544827`. They tested the old characterization work,
**not this prototype**. The Runtime annotation observed an unaffected sibling in
MEM_RESERVE and the expected child AV; its green status is not proof of safety.

As requested, mainstream was **cloned, not forked**, in a separate shallow bare
cache, without altering this checkout. Inspected
`Supermedo/uncharted_pc@ac2bfbc1602623745d8b291a8ced639d6ede6369` (0.3.8):

* `fa64614044bb77e3aba72c3f155ef97d2d5af56f` advances the right-side backing
  offset for flexible as well as direct splits. This addresses the earlier
  flexible-offset source concern **upstream**, not in this protected checkout.
* `659dadf77ea22b071edd10d1618f49475fcae5d1` adds root-collision diagnostics /
  bounded relaunch attempts, not independent leaf remapping.
* Recent Linux file-read/build work does not supply this Windows primitive.
* Upstream still carves encompassing views and reconstructs siblings; it still
  uses `CreateFileMapping2`. No suitable leaf implementation was adopted.

## 2. Files added

All deliverables are new isolated files:

| File | Purpose |
|---|---|
| `placeholder_leaf_probe.c` | Independent x64 C11 Windows experiment and bounded benchmark |
| `CMakeLists.txt` | Standalone MSVC/clang-cl + SDK build, warning errors, source identity |
| `run_probe.ps1` | Full-output runner, expected exits/markers, JSON case results |
| `README.md` | Native commands, operation model, limits and evidence acceptance |
| `REPORT.md` | This eleven-section decision report |
| `evidence/baseline.json` | Protected hashes, source digest, baseline and unvalidated status |
| `evidence/local-checks.txt` | Complete local command output/exit codes; no native evidence |
| `../../.github/workflows/placeholder-leaf-prototype.yml` | Separate Windows 2022 build/run/artifact job |

Paths except the workflow are relative to this directory. No existing workflow,
production code, existing test, root build script or PR was changed. Generated
objects and caches are outside Git; native logs/builds use already-ignored `out/`.

## 3. Exact API sequence

**Implemented sequence, not observed Windows behavior:**

1. Resolve `VirtualAlloc2`, `MapViewOfFile3`, `UnmapViewOfFile2`; record exact
   OS via `RtlGetVersion`, native system information and build/source identity.
   Reject unsupported environment; never select a coarser mapping strategy.
2. `VirtualAlloc2(NULL, NULL, 0x10000, MEM_RESERVE |
   MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS, NULL, 0)`; require an allocation-
   granularity-aligned root. Record it as one owned placeholder extent.
3. Three calls to `VirtualFree(root + i*G, G, MEM_RELEASE |
   MEM_PRESERVE_PLACEHOLDER)` for `i=0,1,2`, `G=0x4000`. Record each prefix
   split and remainder separately. Check every host page's exact forward query
   boundaries and allocation base after reserve/split.
4. `CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_COMMIT,
   0, 5*G, NULL)`, then one ordinary offset-zero RW `MapViewOfFile` alias.
   Seed all five backing intervals with distinct full-interval patterns.
5. For each leaf `i=0..3`, `MapViewOfFile3(section, GetCurrentProcess(),
   root+i*G, i*G, G, MEM_REPLACE_PLACEHOLDER, PAGE_READWRITE, NULL, 0)`.
   Require the **exact** returned base, independently owned mapped-view extent,
   expected state/protection and full backing pattern. Forbid overlapping leaf
   backing intervals. Do not round offsets/sizes or enlarge views after failure.
6. Patch the backing alias; verify its shared visibility. Event-gate sibling
   readers. Unmap only exact target views using `UnmapViewOfFile2(current,
   target_base, MEM_PRESERVE_PLACEHOLDER)`. After each unmap block the mapper
   until both readers finish; query/check all unaffected leaves and probe the
   intentional reserved target for an expected, narrowly filtered read AV.
7. Replace exact target placeholders at the same offsets, then repeat an unmap
   of the first target and replace it at offset `4*G`; check bytes and unaffected
   offsets. The four-leaf single and multi-target cases use separate roots.
8. Join readers. Cleanup is explicitly scoped to the **entire owned root**, not
   a carve. Unmap only ledger-known whole views with preservation; coalesce only
   adjacent ledger-known placeholders with `MEM_RELEASE |
   MEM_COALESCE_PLACEHOLDERS`; finally `VirtualFree(root, 0, MEM_RELEASE)`.
   Check that the released free region covers the whole former root, then unmap
   the backing alias and close the section. A real mutator failure or ledger /
   query contradiction fail-stops rather than guessing a cleanup layout.

The installed SDK declarations are checked with `_Generic` against the actual
function pointer signatures. Queries do not grant lifetime protection; they
cross-check this experiment's own successful operations and ledger. Windows has
no `VirtualQuery` placeholder bit. This is not a claim of whole-sequence atomicity.

## 4. Alignment qualification

| Required empirical property | Current status |
|---|---|
| Fixed 16 KiB split accepted | UNVALIDATED; no Windows call made |
| Four independently replaceable exact-size views | UNVALIDATED |
| At least one accepted base **and** backing offset both non-64-KiB-aligned | UNVALIDATED |
| Exact address / size / page alignment / section-offset identity | Assertions implemented, not observed |
| No free/unowned range in successful lifecycle | Ledger + state checks implemented, not observed |

The intended leaf 1 base is `root+0x4000`, with section offset `0x4000`;
`ALIGNMENT=PASS` is emitted only after a successful map and verification with
both actual values non-64-KiB-aligned. No synthetic address is an observed result.
The documented placeholder replacement exception to ordinary 64 KiB base/offset
alignment motivates this experiment; it does not qualify the split or lifecycle.
A failed API is logged with arguments/error and stops qualification, never
changes G, rounds the request or activates a fallback.

## 5. Sibling-continuity evidence and destructive-operation log

**Evidence now: source-based operand review only; native operand/read log absent.**
The required structural invariant is enforced before calls: a selected mapped
view is exactly one ledger leaf; every carve/remap operand must be wholly inside
the declared target. Intended destructive operands are:

| Case | Target | Carve unmaps | Same-offset replacements | Alternate-offset unmap/replacement |
|---|---|---|---|---|
| Single | `[root+0x8000, root+0xC000)` | leaf 2 only | leaf 2 only | leaf 2 only |
| Multi | `[root+0x4000, root+0xC000)` | leaves 1 then 2 | leaves 1 then 2 | leaf 1 only |

Each has size `0x4000`. `UnmapViewOfFile2` has no size argument: the log gives
its previously established **whole view** size, not a fictitious byte-range
unmap capability. Initial root splitting and post-join whole-root cleanup are
separately labelled phases/scopes. Every mutation has an `OP` address/size/flags
entry (also in the benchmark); map calls log complete mapping arguments.
No tested carve path intentionally unmaps or replaces either outside sibling.

Readers at leaves 0 and 3 use ready/request/done events, not sleeps. The mapper
pauses after **each** target unmap. Both readers perform 16 full-leaf pattern /
backing / state checks in that deliberately open hole; the main thread checks
all other unaffected leaves, including leaf 1 in the single case. This combines
an operand-exclusion invariant with deterministic hole observations. It is not
free-running stress and is not a universal reader protocol.

No Windows `READER=PASS`, `QUERY=PASS`, `OS_OK`, `TARGET_INVALIDATION=PASS` or
`OP` execution log exists yet. A future report must retain those actual logs and
independently inspect the full operation sequence, not accept a final PASS alone.

## 6. Backing and protection evidence

**Implemented checks; all native outcomes UNVALIDATED.** One shared unnamed
pagefile section and one RW seed/inspection alias are used. Initial offsets are
nonoverlapping `0, G, 2G, 3G`; fifth interval `4G` is reserved for rebinding.
An interval-index byte prefix ensures distinct whole-interval patterns even in
the optional >256-leaf cases; every byte is verified, not just the prefix.
The alias patch at `G+17` must appear in any view of that interval. Same-offset
remap must restore that exact content. Fifth-offset remap must show that distinct
interval and leave all unaffected patterns/offsets unchanged. The alias remains
available while an intentional target leaf has no section view; the entire former
backing interval is checked again during that hole, with section handle, former
offset and alias address logged separately from target virtual state.

Mapped leaves must be MEM_COMMIT / MEM_MAPPED / PAGE_READWRITE, with the expected
allocation protection. Intentional holes must be MEM_RESERVE / MEM_PRIVATE,
not committed PAGE_NOACCESS. A one-byte read AV is caught only at the armed
exact target address and only for the expected read violation; an unrelated
exception is not accepted. A readable hole fails the test.

This RW-only experiment proves nothing about executable coherency, protection
transitions, copy-on-write, CRIT05 root/alias integration or GPU mappings. Hazard B
remains distinct: the prior mixed-sibling path could temporarily widen original
PAGE_NOACCESS to PAGE_READONLY, and later restoration does not erase that widening.

## 7. Failure-injection evidence

The default is **off**. These are deterministic **wrapper precall skips**, not
arbitrary kernel failures, allocation-pressure tests or proof of production rollback.

| Switch | Intended preserved state at simulated failure | Implemented outcome |
|---|---|---|
| `split:1` | First prefix split; known leaf plus remaining placeholder tail | Snapshot; coalesce/release known ranges; exit 3 |
| `map:2` | First two leaves mapped, other two placeholders | Snapshot of all extents/bytes; known-view cleanup; exit 3 |
| `unmap:1` | Multi case: leaf 1 reserved after reader pause, leaf 2 still mapped, outside siblings mapped | Snapshot and sibling checks; explicit failure then cleanup; exit 3 |
| `remap:0` | Single target reserved; outside mappings preserved | Snapshot and sibling checks; no invented transaction rollback; cleanup; exit 3 |
| `coalesce:0` | Readers joined, all known views converted to placeholders | Snapshot; consume injection once, retry only known cleanup; exit 3 |

Each selected call prints operation/index, `real_os_invoked=NO`, synthetic error,
full known ledger (including offset when mapped), query/pattern state and outcome.
The runner requires matching injection/pass/cleanup markers plus actual exit 3;
an unreached injection is failure. An earlier lifecycle failure cannot be hidden
by subsequently injecting a cleanup failure.

A real mutation API error sets uncertainty and fail-stops with exit 21; it is not
accepted as a simulated case. No guessed rollback/cleanup is attempted against an
uncertain layout. A timeout also fail-stops without permission to destroy memory
while a reader could dereference it. Process teardown is explicitly **not** a
successful recovery or production rollback protocol.

**Executed native injection cases: 0/5.** Thus neither the recovery logic nor the
failure-state ledger has empirical Windows validation.

## 8. Build, native Windows commands, compiler, OS and CI

Use the complete native PowerShell commands in [README.md](README.md). The
runner executes the basic process, five distinct injection processes, optionally
the bounded benchmark, and records full per-process output, exact exit codes,
required markers and `results.json`. CMake configures source SHA256, git SHA,
dirty status, compiler ID/version and configuration into the executable. Build
logs record the actual compiler commands; the probe records Windows build/page /
allocation granularity and machine summary.

Actual local validation, fully recorded in
[evidence/local-checks.txt](evidence/local-checks.txt):

* Host: Linux x86_64, kernel `6.1.158+`; **not Windows**. No Windows SDK, MSVC,
  PowerShell or native Windows execution environment was available.
* Zig `0.17.0`, Clang `22.1.8`: final Windows-GNU-target **object compilation**
  succeeded, exit **0**, with C11, SEH extensions, `-Wall -Wextra -Werror` and
  bundled MinGW headers. `objdump` identifies PE x86-64 with exception metadata.
  This catches syntax/type/code-generation issues for that cross environment;
  manually selected MSVC-compatibility macros do not turn it into MSVC or an
  official SDK build. No executable was linked or run.
* Earlier `-fsyntax-only` attempts encountered driver unused-argument handling,
  anonymous-union compatibility switches and Zig's temporary-object rename
  problem; these are not successful native builds. An initial timed-out command's
  final exit was not captured. The eventual object-build result is separate.
* CMake `4.4.4` on POSIX rejects configuration with the intended explicit native-
  Windows guard, exit **1**. That guard is not a Windows configure test.
* PyYAML `6.0.3` parsed the separate workflow and its basic structure successfully.
  `git diff --check` passed; all five baseline blob hashes match, index empty.
* PowerShell parsing/execution, MSVC / clang-cl + SDK configuration/build, runner
  exit-code semantics and the workflow itself have **not** been executed.

The new narrow workflow uses `windows-2022`, `/W4 /WX /sdl`, builds only the
standalone project, actually invokes the runner, and always uploads logs. It does
not reuse the runtime job or merely compile without execution. **It is local,
unpublished, unrun: there is no new run ID or artifact.** Manual dispatch is not
assumed available for an unregistered workflow. No push was made to alter PR9
for the sake of obtaining a CI result. Historical passing runtime CI is excluded.

## 9. Scaling

**No measurements.** The optional bounded implementation covers 4 / 64 / 256 /
1024 leaves, 1..10 repetitions (default 3). It emits raw per-repetition QPC
reserve, split, section-seed, map, target-unmap, target-remap, validation and
cleanup times, plus view / extent / backing-view / handle / fixed metadata
capacity/storage counts and zero failure count only for completed samples.
OS/build/processor-count/RAM/compiler/configuration/source identity precede them.
Failure aborts with its operation log rather than manufacturing a successful row.

The benchmark includes logging, page queries, ledger searches/moves and full-byte
checks. Split/coalesce validation and some bookkeeping can be quadratic. Its
results would describe **this instrumented prototype**, not pure syscall latency,
production overhead, memory-manager capacity or an amortized transaction model.
No extrapolation to guest heaps or live migration is authorized.

## 10. Unresolved hazards

1. **Primary blocker:** no native Windows empirical lifecycle, fine alignment,
   sibling, offset, AV, failure or cleanup evidence. The installed-SDK build is
   unvalidated, as are PowerShell and CI execution paths.
2. Strict query expectations (allocation bases, forward region boundaries,
   allocation protections) must be confirmed on the real OS, not silently
   weakened if this source-based model differs. A query is not a lock or an
   atomicity proof; no polling strategy protects stale pointers.
3. Placeholder ownership, unaffected-sibling continuity, intentional target
   invalidation and stale mapping-generation access are different properties.
   The target is deliberately inaccessible during replacement; stale native /
   async / GPU users are not made safe by independent sibling views.
4. The two-leaf operation is **not an atomic transaction**. Partial progress is
   explicit. Simulated precall errors do not establish real kernel rollback.
5. RW-only protection does not resolve Hazard B or the historical NOACCESS ->
   READONLY widening. Mixed executable/protection/cache behavior is untested.
6. No production HostRegion/carve/VMA, backing/alias roots, Linux behavior,
   universal rendezvous, native escape protocol or live-view migration was
   implemented. Locks, thread registries, sleeping/blocked status, queue lists
   and HLE call frequency still do not prove native quiescence.
7. Older OS behavior remains unqualified. Production `CreateFileMapping2`
   compatibility with the advertised 1903 baseline remains a separate unresolved
   concern; using `CreateFileMappingW` here does not fix that production import.
8. Scale, metadata burden and benchmark perturbation are unmeasured. A newer
   upstream flexible-offset fix is not authorization to modify protected code.

## 11. Next decision

**STOP SHORT OF PRODUCTION DESIGN REVIEW / IMPLEMENTATION AUTHORIZATION.**

The next permitted evidence step is to build and run this exact isolated project
on native supported x64 Windows, preserving the source digest, compiler / SDK /
configuration, exact OS and complete output/exit codes. Obtain the basic single
and multi cases plus all five injection runs; review every destructive operand,
actual non-64-KiB base/offset acceptance, exact view size, ownership/state,
backing/alias/protection checks, true target invalidation and post-join cleanup.
Optionally obtain bounded scaling data with its instrumentation caveat. Record
unsupported/failing OSs honestly; do not resize leaves or fall back to coarse
views to make the test pass.

All native gates must pass reproducibly before a **production design review**
can be considered. Even that would not fix Hazard A / CRIT06 or stale-generation
access by itself, nor authorize a rewrite. This mission provides an executable
experiment and reproducible evidence path, not the missing native observations.
