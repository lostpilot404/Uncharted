# Gemini audit — triage, root-cause verification and first improvement

Status: read-only audit complete, one confirmed defect fixed and tested.
Branch: `arena/3219e6b1-uncharted`
Baseline commit: `5fe65e3697ee2f4a739a8793cd21ed8fdc2a42af`
Fix commit: `05647f1ccfc137ac7ecc4fc0e41c5a93f83ae40b` (CI notice follow-up: `bf2f7db`;
test-harness follow-up: `595aea4`)
Pull request: [#6](https://github.com/lostpilot404/Uncharted/pull/6) — merged into `main` at `5a54fd8d2b8190edb9fd4851f7c71e70e79ea506`

This document triages every entry of the supplied `Gemini.md` (3,696 lines, 11,750 words,
24 ledger entries, 4 supplemental fix sections) against the **current** source, the historical
claims of the previous mission, current upstream `Supermedo/uncharted_pc`, and the tests that
could actually be executed here. No patch from the audit was applied as given: each one is
reviewed in §7, and most of them are either stale against this tree, unsafe, or unverifiable
without hardware or game data.

---

## 1. Baseline established before any edit

### 1.1 Repository, branch, history

| Fact | Value |
|---|---|
| Repository | `https://github.com/lostpilot404/Uncharted` (fork, public) |
| Remote | `origin` → `https://github.com/lostpilot404/Uncharted.git` (fetch/push). No other remote. |
| HEAD | `5fe65e3697ee2f4a739a8793cd21ed8fdc2a42af` |
| Branch | `arena/3219e6b1-uncharted` (== `main` == `origin/main` at start) |
| Working tree | clean (`git status --short` empty) |
| Clone depth | shallow — `.git/shallow` contains `5fe65e3`, so one commit is visible locally |
| Submodules | 6, all uninitialised (`gpu/third_party/{fsr-vulkan,imgui,magic_enum,miniz,xbyak}`, `third_party/LibAtrac9`) |

The single visible commit is the squash-merge of PR #5. Commit ancestry was therefore
established through the GitHub API rather than the local object database.

### 1.2 Historical claims — verified, corrected

| Claim from the previous report | Verified state |
|---|---|
| Commit `722f1ef` "corrected the DLSS depth-inversion default" | Exists; it is a head of PR #5's branch (`gh run list` shows CI on `722f1ef6511364d762f1dcc9792b71437ae77eed`). Ancestry not visible locally (shallow clone). |
| PR #5 "open and unmerged at head `4d971ca`" | **Stale.** PR #5 is **MERGED** at `2026-10-22T12:03:05Z`, head `4d971ca62a0369377d2966c3f0d2ef24e8ed1bca`, based on `main`. |
| "Runtime/memory reliability work" | PR #2 `runtime/memory-reliability`, **merged** (`cb56e08`). |
| "Rust/egui launcher prototype" | PR #3 `launcher/rust-prototype`, **merged** (`d6329fd`). |
| "Successful runtime, Rust and Windows packaging CI" | Confirmed for `5fe65e3` (see §1.3). |
| PR #1 (`arena/e40bb63b-uncharted`) | **CLOSED**, not merged — superseded by PRs #2/#3. |
| Upstream import | PR #4 "Import upstream uncharted_pc v0.3.6 with full history", **merged** (`d4a8032`). |

The DLSS work is therefore already on `main`; there is no open PR #5 to preserve, and the
"keep unrelated changes out of PR #5" constraint is satisfied by working on this branch instead.

### 1.3 CI associated with the baseline commit

All three workflows ran on `5fe65e3` and succeeded:

| Workflow | Run ID | Conclusion | URL |
|---|---|---|---|
| Runtime CI | 39775239734 | success | https://github.com/lostpilot404/Uncharted/actions/runs/39775239734 |
| Windows package | 39775239607 | success | https://github.com/lostpilot404/Uncharted/actions/runs/39775239607 |
| Rust launcher CI | 39775239557 | success | https://github.com/lostpilot404/Uncharted/actions/runs/39775239557 |

### 1.4 Toolchain available in this environment

`gcc/g++ 12.2.0`, `make 4.3`, `python3 3.11.2`, 2 cores, 3 GB RAM, Debian 12.
**Missing:** `clang`, `cmake`, `ninja`, `pkg-config`, `cargo`/`rustc`/`rustfmt`,
`glslangValidator`, Vulkan headers/loader, SDL3, FFmpeg, fmt, vma.
Consequence: `build.sh` cannot run at all here (it requires pkg-config + vulkan + sdl3 + cmake +
ninja), so `bb-probe`, `libbbgpu` and the Rust launcher **cannot be built or exercised** in this
environment. SDL3 headers were fetched (public `libsdl-org/SDL` release tarball, headers only) to
syntax-check `tests/test_pad.c`; no SDL3 library is available to link it.

### 1.5 Baseline test results (recorded before any edit)

```
$ python3 -m unittest discover -s tests -p 'test_*.py'
Ran 98 tests in 9.210s
OK (skipped=28)
```

The 28 skips are `test_probe`/`test_runtime`/`test_sema`/`test_content` (`out/bb-probe`,
`out/runtime-test`, `out/sema-test`, `out/content-test` absent) plus platform skips. After
`out/content-test` was built (see §9) the same suite reports `OK (skipped=25)`; both numbers are
recorded so future regressions are not confused with this pre-existing skips.

Standalone C/C++ tests that the environment supports, all green before the change:

| Test | Result |
|---|---|
| `tests/test_content.c` | PASS |
| `tests/test_coroutine_frames.cpp` | PASS |
| `tests/test_upscaler_support.cpp` | PASS |
| `tests/test_dlss_sr.cpp` | PASS |
| `tests/test_file_mods.c` | PASS |

Cannot be built here (missing dependencies, not failures): `test_pad.c` (SDL3 library),
`test_runtime.c`/`test_sema.c` (need SDL3+FFmpeg+Vulkan via the full `runtime` source set),
`test_scene_resolution.cpp` (fmt), `test_motion_history.cpp`/`test_ui_composition.cpp`
(missing headers in `gpu/shadps4/video_core/renderer_vulkan/`), `test_camera_motion.cpp`/
`test_taa_shader.cpp`/`test_motion_shaders.cpp` (Vulkan + spirv-val).

### 1.6 DLSS work verified present (not touched)

- Non-inverted depth default and the depth convention: `gpu/shim/bbport_settings.h`,
  `gpu/dlss_bridge/bbport_dlss_bridge.cpp` — unchanged by this work.
- `sr_velocity` / `VelocityScaledPreset`: `gpu/shim/bbport_settings.h:34`.
- Preset → render-resolution mapping: `PresetScale()` `gpu/shim/bbport_settings.cpp:232-235`
  `{1.0, 1.5, 1.7, 2.0, 3.0}` for `NativeAA/Quality/Balanced/Performance/UltraPerformance`;
  the Python side `scripts/patches.py:21` `PRESET_SCALES` is identical, and
  `render_size()`/`scaled_sizes()` (`:109-146`) agree with it. Cross-checked, consistent.
- Frame-generation independence from SR: `tests/test_dlss_sr.cpp` asserts it and still passes.

---

## 2. Structural findings about the audit itself

These matter because they determine how much of the document can be trusted.

1. **The audit was not written against this tree.** Several "current implementation" quotes do
   not exist here. Examples: it quotes `probe.c :: apply_patches` without the `slots[]` array
   that lines 493-506 do have; it quotes `cond_destroy` with a `(uintptr_t)*cond == 2` guard that
   line 161 did not have; it quotes `if ((d->buttons & BTN_R2) && !d->r2) d->r2 = 255;` in a
   place where this tree already has an unconditional `d->r2 = 255;` at `runtime_pad.c:578`;
   it says mode 2 of `camera_motion.comp` lacks a NaN/Inf guard, but lines 99-101 have one.
2. **The local `src/`, `scripts/` and `gpu/` files are byte-identical to current upstream
   `Supermedo/uncharted_pc@main` (v0.3.6).** Verified file by file over `src/*.c`,
   `scripts/*.py`, `gpu/shim/bbport_nan_guard.h`, `gpu/shadps4/.../camera_motion.comp` and
   `gpu/dlss_bridge/bbport_dlss_bridge.cpp`; only the CI workflows, `build.sh`, the Rust
   launcher and the DLSS additions differ, which is what PRs #2/#3/#5 changed.
   So a source-level finding present locally is present upstream too — with one exception,
   §7.E (CRIT-09), where the file is *dead code*.
3. **One entire ledger entry points at dead code.** `CRIT-09` accuses
   `gpu/shadps4/common/bounded_threadsafe_queue.h` of corrupting every `SPSCQueue` in the video
   core. The header is included by **nothing** (`grep -rn bounded_threadsafe` over the tree
   matches only the header itself) and is not referenced by any CMake target. There are zero
   instances, so there is no reachable defect.
4. **The "layer" severity labels are not calibrated to observability.** Several CRITICAL items
   are in Windows-only code paths that cannot be exercised here, while some of the genuinely
   reachable defects (e.g. `condition-variable` handle lifetime) are labelled MEDIUM.

---

## 3. Ledger triage — all 24 entries

Statuses used: `CONFIRMED`, `PARTIALLY CONFIRMED`, `ALREADY FIXED HERE`, `FIXED UPSTREAM ONLY`,
`NOT REPRODUCED`, `INCORRECT OR UNSUPPORTED`, `DUPLICATE`, `HARDWARE VALIDATION REQUIRED`,
`BLOCKED`.

| ID | Audit severity | Status | Fixed upstream? | Disposition in this session |
|---|---|---|---|---|
| CRIT-01 | CRITICAL | PARTIALLY CONFIRMED | no (identical) | Deferred — see §4.1 |
| HIGH-01 | HIGH | PARTIALLY CONFIRMED (half is INCORRECT) | no | Deferred, low value |
| HIGH-02 | HIGH | CONFIRMED | no | Deferred — needs a module with TLS relocs |
| HIGH-03 | HIGH | CONFIRMED | no | Deferred — needs the game's eboot/patches |
| MED-01 | MEDIUM | PARTIALLY CONFIRMED | no | Deferred, low impact |
| CRIT-02 | CRITICAL | PARTIALLY CONFIRMED (Linux only) | no | Deferred — needs per-thread altstacks |
| CRIT-03 | CRITICAL | INCORRECT OR UNSUPPORTED (as described) | no | Deferred — see §4.2 |
| HIGH-04 | HIGH | PARTIALLY CONFIRMED | no | Deferred, diagnostics-only path |
| CRIT-04 | CRITICAL | PARTIALLY CONFIRMED | no | Deferred — guest-side precondition unproven |
| CRIT-05 | CRITICAL | PARTIALLY CONFIRMED / HARDWARE VALIDATION REQUIRED | no | Deferred — Windows-only, untestable here |
| CRIT-06 | CRITICAL | CONFIRMED (source) / HARDWARE VALIDATION REQUIRED (race) | no | Deferred — Windows-only, explicitly risky |
| HIGH-05 | HIGH | PARTIALLY CONFIRMED | no | Deferred |
| CRIT-07 | CRITICAL | PARTIALLY CONFIRMED | no | Deferred — coroutine core, no test possible |
| HIGH-06 | HIGH | PARTIALLY CONFIRMED (two of three claims are wrong) | no | Deferred |
| MED-02 | MEDIUM | **CONFIRMED** | **no** | **FIXED in `05647f1`** |
| HIGH-07 | HIGH | NOT REPRODUCED / ALREADY FIXED HERE (as described) | no | No change — see §4.3 |
| HIGH-08 | HIGH | NOT REPRODUCED | no | No change — see §4.4 |
| CRIT-08 | CRITICAL | PARTIALLY CONFIRMED | no | Deferred — proposed fix is not a fix (§7.F) |
| CRIT-09 | CRITICAL | NOT REPRODUCED (dead code) | n/a | No change — see §7.E |
| HIGH-09 | HIGH | INCORRECT OR UNSUPPORTED (design trade-off) | no | No change — needs GPU evidence |
| CRIT-10 | CRITICAL | PARTIALLY CONFIRMED | no | Deferred — needs GPU validation |
| HIGH-10 | HIGH | CONFIRMED (leaks, identity matrices) | no | Deferred — needs the NGX SDK to test |
| CRIT-11 | CRITICAL | PARTIALLY CONFIRMED | no | Deferred — needs GPU validation |
| HIGH-11 | HIGH | CONFIRMED (job object, CI coverage) | no | Partially addressed — pad/sema/runtime still uncovered, see §4.5 |

Counts: 4 confirmed and actionable (HIGH-02, HIGH-03, MED-02, HIGH-11-partial), 1 fixed here
(MED-02), 2 not reproduced (HIGH-08, CRIT-09), 2 incorrect (CRIT-03, HIGH-09), 1 already fixed in
place (HIGH-07), the rest partially confirmed or hardware-blocked.

### 3.1 Evidence per entry (Layer 1)

**CRIT-01 — `%fs:[0]` pattern handling — PARTIALLY CONFIRMED.**
`scripts/link_modules.py:22` `FS_LOAD = 64488b042500000000` and `patch_fs_loads()` at
`:107-119` only match ModRM `0x04`, i.e. `mov rax, fs:[0]`. The Windows mirror
`src/probe.c:348-359` matches the byte sequence `65 48 8b 04 25 00 00 00 00` — again rax only.
The ISA claim in the audit is correct: `mov r64, fs:[0]` encodes as `64 48 8b modrm 25 00000000`
with modrm ∈ {04,0c,14,1c,24,2c,34,3c} and REX `48`, or `64 4c 8b modrm 25 …` for `r8..r15`.
The audit's proposed ModRM set and its C mask `(at[3] & 0xc7) == 0x04` are both correct.
What cannot be established here: whether this title's `eboot.bin`, `libc.prx` or
`libSceFios2.prx` actually contains a non-`rax` form — the game is not present, and `link.json`
(which records `fs_loads_patched`) is not committed. See §4.1 for why I did not apply it anyway.

**HIGH-01 — SELF/ELF buffer sizing — PARTIALLY CONFIRMED; half is INCORRECT.**
`scripts/prepare.py:32-55`. The audit's mechanism is wrong: `elf = bytearray(end)` followed by
`elf[:header_end] = span(data, base, header_end)` **cannot** invalidate segment offsets, because
a slice assignment to `elf[:n]` with `n > len(elf)` replaces the whole bytearray with the new
bytes — it grows at the end and never shifts anything. `span()` is bounds-checked and raises
`ValueError` before any of this. The second half of the finding is real but cosmetic:
`prepare()` (`:114-140`) writes `(vaddr, memsz, flags)` into `boot.bin` without checking
page alignment or `flags <= 7`, while `src/probe.c` rejects such segments with a generic
`fail("bad segment")`. Impact is a worse error message, not a crash in the port.

**HIGH-02 — TLS relocations — CONFIRMED.**
`scripts/link_modules.py:183-188`: `kind == 16` (`R_X86_64_DTPMOD64`) raises unless
`symbol['name']` is empty and `symbol['type'] == 3`; `kind == 17/18` fall through to
`raise ValueError('unsupported relocation type')` at `:204`. `scripts/link_libc.py` has the same
shape. Relocation *support* is genuinely incomplete. Reachability again needs a module that
actually carries such a relocation — an Uncharted module dump was not available. See §7.A for
why the audit's replacement code must not be applied.

**HIGH-03 — split patch writes and NULL pointer patches — CONFIRMED.**
`scripts/patches.py:199-207` emits one record per `<Line>` with no coalescing, and
`:327-329` writes them verbatim. `src/probe.c:483-...` rejects a write that only partially
covers an 8-byte relocation slot (`relocs[r].kind || target < offset || target + 8 > offset + length`,
`:493-495`) and rejects `value == 0` because `0 < base` (`:496`). Both claimed aborts are real
code paths. Whether they fire depends on a patch file this repository does not ship for this
title (the bundled `patches/Bloodborne.xml`, 3,184 `<Line>`s, belongs to the port's origin and
its `60 FPS++` set uses `bytes32` groups; I could not prove any of them straddles a relocation
slot without the matching `eboot.bin`).

**MED-01 — mod overlay state and cleanup — PARTIALLY CONFIRMED.**
`scripts/mods.py:107` `LINKED = {}` is never cleared between `build_overlay()` calls, and
`:215` calls `shutil.rmtree(overlay)` with no `onerror`, so a read-only hardlinked mod file makes
the cleanup raise `PermissionError` on Windows and mask the original exception. The impact is
smaller than stated: `LINKED` is keyed by the absolute path under a fresh `mkdtemp()` overlay, so
stale keys cannot alias a later overlay; they only accumulate.

### 3.2 Evidence per entry (Layer 2)

**CRIT-02 — no `sigaltstack`/`SA_ONSTACK` — PARTIALLY CONFIRMED (Linux only).**
`src/probe.c:891` `sa.sa_flags = SA_SIGINFO` with no `SA_ONSTACK`, and no `sigaltstack()` call
anywhere. `fault()` (`:362`) is the GPU page-tracking handler and it *returns* to the guest, so a
`rt_sigframe` written at the guest's `rsp` does overwrite the 128-byte SysV red zone on every
handled fault. Two corrections to the finding: (a) this is Linux-only — the Windows build uses
SEH (`SetUnhandledExceptionFilter`, first-chance handlers) and never installs a POSIX handler,
so it cannot affect the RTX 2050 target machine; (b) the audit's patch installs the alternate
stack on the *calling* thread only, while `sigaltstack` is per-thread and guest code also runs on
the audio, GPU and fiber threads, so the patch would fix at most one of them. Any real fix needs
one altstack per guest thread, plus a decision about recursive faults on an exhausted altstack.

**CRIT-03 — callback thunks — INCORRECT OR UNSUPPORTED as described.**
`src/probe.c:674-690` `near_image_memory()` returns memory on **both** platforms (`#ifdef _WIN32`
`VirtualAlloc` / `#else` `mmap(...MAP_FIXED_NOREPLACE...)`), so the "always returns NULL on
Linux" claim is false. The thunk generator at `:691-...` already begins with
`lea rsp,[rsp-128]` (bytes `48 8d a4 24 80 ff ff ff`) before `pushfq`, saves
`rax rcx rdx rsi rdi r8-r11 rbx`, and saves/restores all 16 XMM registers around the call.
What remains true: it uses legacy SSE `movdqu` rather than VEX `vmovdqu`, so it does not save the
upper 128 bits of `ymm0-15`. Whether that matters depends on whether the guest or the callback
uses AVX state across the hook; that needs a runtime A/B test. The audit's replacement thunk is
not an improvement — see §4.2.

**HIGH-04 — W^X and instruction cache — PARTIALLY CONFIRMED.**
`install_guest_hooks` (`src/probe.c:608-672`) allocates `PAGE_EXECUTE_READWRITE` and, unlike
`install_game_callbacks`, never calls `protect(thunks, …, 5)`. It is `BB_GUEST_HOOKS`-only
diagnostic code, so the impact is a W^X violation on an opt-in debug path. Note that
`protect()` (`:109-122`) does call `FlushInstructionCache` on Windows but does **not** call
`__builtin___clear_cache` on POSIX — irrelevant on x86-64, relevant only if the port is ever
built for ARM.

### 3.3 Evidence per entry (Layer 3)

**CRIT-04 — host allocations exposed to the guest above 1 TiB — PARTIALLY CONFIRMED.**
The contract is real and documented at `src/runtime_memory.c:1-9` ("Guest addresses are placed
below 1 TiB … GPU descriptors encode 40-bit addresses"). `src/runtime_mutex.c` does hand
`malloc()`ed `GuestMutex`/`GuestCond`/`GuestAttr` pointers to the guest, and on both platforms
those are far above 1 TiB. What is *not* established is that the guest ever truncates a
synchronization handle to 40 bits — these handles are opaque tokens passed back to libkernel, and
the 40-bit constraint is documented for *GPU descriptor* addresses. Claiming a crash needs game
data. `src/runtime_rwlock.c` has the same exposure.

**CRIT-05 — monolithic 944 GiB `VirtualAlloc2` — PARTIALLY CONFIRMED / HARDWARE VALIDATION REQUIRED.**
`src/runtime_memory.c:139-143` is exactly one `VirtualAlloc2` over
`[0x10_0000_0000, 0xfc_0000_0000)` with `MEM_RESERVE_PLACEHOLDER`, and `win_fail` exits with 21
on failure. The failure message even tells the user to relink with `--disable-high-entropy-va`,
which shows the maintainers know the precondition. Whether a given Windows 11 installation has
anything mapped in that range depends on the loaded modules and cannot be determined on Linux.
This is the highest-impact unfixed item for the target machine and is **BLOCKED** here.

**CRIT-06 — `carve_host` unmaps sibling pages — CONFIRMED at source level.**
`src/runtime_memory.c:160-212`: for a carve inside one mapped region it calls
`UnmapViewOfFile2(..., regions[i].start, MEM_PRESERVE_PLACEHOLDER)` on the **whole** region
(`:172-175`), then `VirtualFree`s, re-inserts placeholders, and only afterwards re-creates the
kept left/right parts with `view_map()` (`:186-196`) — i.e. `[lo,a)` and `[b,hi)` are
`PAGE_NOACCESS` placeholders for the duration, and any concurrently executing guest thread
touching them takes an access violation. `view_map` also maps them `PAGE_READWRITE` first and
restores `PAGE_READONLY` at `:198-201`, so for that window GPU write-tracking is bypassed. The
audit's `view_map_runs`/`carve_host` rewrite is not safe to take as given (§7.D), and the
subsystem is Windows-only: **no test is possible in this environment**, so I did not touch it.

**HIGH-05 — `touch_for_write` RMW race and `ReadFile` short reads — PARTIALLY CONFIRMED.**
`src/runtime_file.c:304-311` still does a non-atomic read-modify-write (`*b = *b`) per page, which
can clobber a concurrent writer; `read_full` (`:317-...)` touches the whole remaining range in one
go rather than in chunks before the host read. The failure mode requires the guest to race a
load against another writer to the same page — plausible for streaming asset loads, but not
demonstrable without the game.

### 3.4 Evidence per entry (Layer 4)

**CRIT-07 — fiber TLS caching and contextless stack placement — PARTIALLY CONFIRMED.**
`this_thread()` (`src/runtime_fiber.c:80-85`) is `noinline` with an `asm volatile("" : "+r"(t))`
barrier but has neither `ABI` (`sysv_abi`) nor a `"memory"` clobber; because it is `noinline`,
the TLS address is recomputed in the callee, which is what makes fiber migration work — the
audit's "compiler CSE across `fiber_swap`" claim is the weaker half. `context_of()`
(`:128-147`) does build a fresh frame at `below - 512` whenever the fiber has no
`addr_context`, so two such fibers sharing `t->thread_rsp` would collide; whether this title ever
runs a contextless fiber *from* a contextless fiber is unknown. `switches`/`fibers_started` are
already `_Atomic uint64_t` (`:78`) but incremented with plain `++` (seq_cst) — the audit's
"SEQ_CST lock inc per switch" complaint is a performance observation, not a defect.

**HIGH-06 — TSC/process_time/version-locked U2 patch — PARTIALLY CONFIRMED.**
- `process_time()` `src/runtime_kernel.c:79-81`: the negative-nanosecond truncation is real
  (`(uint64_t)((t.tv_nsec - process_start.tv_nsec)/1000)` truncates toward zero), but I traced
  the arithmetic over all four sign combinations of the two nanosecond fields: the unsigned
  wrap-around cancels the sign error, the result is monotonically non-decreasing, and the error
  is bounded by **1 µs**. The audit's "non-monotonic step" is **INCORRECT**.
- `tsc_frequency()` `:89-104`: the lazy `if (!tsc_hz)` is an unsynchronised read (benign in
  practice on x86-64 for an aligned 8-byte load) and the 20 ms `nanosleep` is a one-time cost.
  The claimed `(t1-t0)*1000000000` overflow needs > 1.8e10 ticks — 6 seconds at 3 GHz, not 20 ms.
  **INCORRECT in practice.**
- The U2 leg-IK / focus patches (`src/runtime_pad.c:463-513`) are not "hardcoded offsets": they
  are byte signatures verified with `memcmp` before patching (see `install_game_callbacks`), so on
  a non-EU-1.00 dump they are simply **not applied** (no corruption, no patch). The comment says
  "U2 (EU 1.00)". So the limitation is real, the mechanism description is not, and the
  consequence is "guard absent", not "port corrupts the game".

**MED-02 — destroyed condition variable re-initialised, non-atomic counters — CONFIRMED (FIXED).**
`src/runtime_mutex.c:151-157` stored `NULL` on destroy, and `ensure_cond()` `:138-150` treated
every value `< 2` as "statically initialised, create it", so any use after
`pthread_cond_destroy` silently allocated a fresh host condvar that the destroying thread will
never signal. `mutex_destroy()` `:107-112` already uses the sentinel `2` for exactly this case.
`created/locks/unlocks/conds/waits/wakeups` were plain `size_t` (`:12`, `:134`) incremented with
`++` from every guest thread while `runtime_mutex_report()` read them without a lock.
Both are fixed in `05647f1`; see §8.

### 3.5 Evidence per entry (Layer 5)

**HIGH-07 — U1 R2 trigger saturation — NOT REPRODUCED / ALREADY FIXED HERE (as described).**
`trigger()` (`src/runtime_pad.c:74`) is indeed `v >> 7`, which reaches 255 only at raw ≥ 32640.
But the failure the audit describes cannot happen in this tree: `sample_host()` sets the button
bit from the alias branch (`:553-557`), and then, unconditionally and for every input source,
`:577-578` force `d->l2 = 255` / `d->r2 = 255` whenever the bit is set. So a pad that saturates
at, say, 30000 still produces `r2 == 255` and Uncharted 1's firing threshold is crossed. The
residual issues are (a) the guest never sees *analog* trigger values at all — L2/R2 are always
0 or 255 — and (b) `hold_after_capture` (`:782-785`) zeroes `buttons` but not `l2`/`r2`. Both are
behaviour questions for the game, not the reported bug; changing (a) would alter pad semantics
for every title, so it is not a safe first fix. **No change made.**

### 3.6 Evidence per entry (Layers 5–6 continued)

**HIGH-08 — ATRAC9 accounting, MP3 channels, decode serialisation — NOT REPRODUCED.**
- The ATRAC9 underflow cannot happen: the batch loop only calls `at9_frame()` after
  `if (in_size < in->superframe_remain) break;` (`src/runtime_ajm.c:526-528`), and
  `superframe_remain` is reset to `superframeSize` whenever `frames % framesInSuperframe == 0`
  (`:229-233`), so `superframe_remain` is always a positive multiple of the codec frame size on
  entry. The gapless drain loop (`:235-238`) guards on `*input_size != 0`, but its precondition
  (`superframe_remain > 0`) already implies `*input_size >= frame size`, for the same reason.
- `mp3_sample()` (`:341-349`) does index `f->extended_data[channel]`, and `mp3_frame()` falls
  back to the *header's* channel count when `f->ch_layout.nb_channels == 0` (`:378`), which would
  be an out-of-bounds plane read. It is defensible to harden, but FFmpeg always populates
  `ch_layout` for a successfully received MP3 frame, so there is no reproduction path; and the
  fix needs FFmpeg, which is unavailable here.
- The single global recursive `lock` (`:79`) serialising AJM batches is real but is a
  performance property, not a correctness defect.

**CRIT-08 — `HandleTable` use-after-free and TCB leak — PARTIALLY CONFIRMED.**
`gpu/shim/core/file_sys/fs.h:56-59`: `GetFile()` releases `m_mutex` and returns a raw `File*`;
`:49-55` `DeleteHandle()` `delete`s it under the lock. The pattern is a genuine use-after-free
race. Reachability for this title: the racing pair needs two guest threads closing and using the
same descriptor — realistic for sockets/epoll, which a single-player Uncharted session does not
exercise much. The TCB half is confirmed and documented as intentional in
`src/runtime_thread.c:130-135` ("the record lives until process exit"), i.e. a deliberate
bounded-looking leak that becomes unbounded with every HLE thread. The proposed fix is not a fix
(§7.F).

**CRIT-09 — `SPSCQueue` placement-new over live objects — NOT REPRODUCED (dead code); ordering
claim INCORRECT.**
`gpu/shadps4/common/bounded_threadsafe_queue.h` is included by nothing and instantiated nowhere
(§2.3), so no `SPSCQueue`/`MPSCQueue`/`MPMCQueue` instance exists in the video core or the HLE
libraries despite the audit's claim. The lifetime defect in `Emplace` (`:95`) is real if the file
is ever used — `std::array<T, Capacity> m_data` default-constructs and placement-new is applied
without `~T()` — but the "missing release ordering" half is **INCORRECT**: `++m_write_index` and
`++m_read_index` are seq_cst read-modify-writes on `std::atomic_size_t`, which already provide
release ordering; the audit's replacement (`store(idx+1, release)`) *weakens* it. Changing dead
code is not a reviewable improvement, so nothing was changed. See §7.E.

**HIGH-09 — ±Inf in `BbNanGuard` mode 1 — INCORRECT OR UNSUPPORTED.**
`gpu/shim/bbport_nan_guard.h:9-19` documents the choice explicitly, including *why* mode 2 broke
Uncharted 1 ("large finite float32 sentinel values > 65504 to R32 targets, blue trees"), and
`emit_spirv_context_get_set.cpp:319-323` implements mode 1 as `IsNan → 0` and mode 2 as that plus
`±65504` clamping. Deliberately not clamping Inf in mode 1 is a design trade-off made against
observed evidence from this project. The 16×16 tile artefact in Uncharted 3 is one unreproduced
observation; changing the default on that basis would re-break Uncharted 1's lighting.
Requires GPU evidence before any change; **no change made**, marked HARDWARE VALIDATION REQUIRED.

### 3.7 Evidence per entry (Layer 9)

**CRIT-10 — velocity pass mode 2 — PARTIALLY CONFIRMED.**
`gpu/shadps4/video_core/host_shaders/camera_motion.comp:88-101`. The division
`clip.x / clip.z` and `prev_proj.y / (depth - prev_proj.x)` are unguarded, so a pixel that was
behind the previous near plane produces a *huge finite* vector — but the result **is** already
sanitised for NaN/Inf at `:99-101`, which the audit denies. So the residual defect is the finite
magnitude case and the missing object-motion pass, not a NaN leak. The stencil-`0xc0` object
velocity is indeed not implemented (the constants are logged and dropped). Any change here needs
GPU frame-capture validation on the target card; a source-only edit proves nothing. Deferred.

**HIGH-10 — NGX parameter lifetime and frame-generation matrices — CONFIRMED.**
`gpu/dlss_bridge/bbport_dlss_bridge.cpp`: `CreateFeature`/`FrameGenCreate` return 0 on failure
without destroying `state.parameters` / `state.fg_parameters`; `FrameGenEvaluate` passes identity
`clipToPrevClip`/`prevClipToClip` and the world basis vectors; the global `state` has no mutex.
The leaks are real and the fix is small, but it can only be compiled and exercised with the
proprietary NGX SDK, which is not in this environment and must not be redistributed. Deferred;
the leak is one-shot per failed feature creation, so the impact is bounded.

**CRIT-11 — swapchain semaphore reuse / `present_done` deadlock — PARTIALLY CONFIRMED.**
`vk_presenter.cpp` does index `image_acquired` per frame index and only signals `present_done`
on the final `PresentImage`; the multi-generated-frame loop is present (`:192-235`, `:319`,
`:523`). Whether `frame_index` can wrap onto an in-flight semaphore depends on the generated
frame count, the image count and the scheduler's fence waits — a GPU-dependent race that cannot
be established statically. Requires the hardware test matrix (resize, device loss, 2×/3×/4× FG).
Deferred.

### 3.8 Evidence per entry (Layer 10)

**HIGH-11 — launcher/process lifetime and CI coverage — CONFIRMED (with a caveat).**
- `packaging/windows/play.c:18-20` ignores both `swprintf` return values. The strings are
  bounded by the `GetModuleFileNameW` check at `:12-14`, so truncation is practically impossible —
  a robustness nit, not the reported hazard. `CreateProcessW` *is* checked (`:24`).
- `launcher/uncharted_launcher.py:1658-1672` `GameJob` creates a job object and calls
  `AssignProcessToJobObject` **without checking the result** and never calls
  `SetInformationJobObject(..., JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)`. Confirmed.
  But the audit's proposed fix (add kill-on-close) would **break the documented
  `close_on_play` setting**: `:1394-1395` deliberately schedules `root.destroy()` 5 s after the
  game starts, i.e. the launcher is *supposed* to exit while the game keeps running. Killing the
  job on handle close would terminate `run.py` and `bb-probe` at that moment. The audit's claim
  that `Launcher.close()` "orphans `bb-probe.exe`" is therefore a description of intended
  behaviour in the `close_on_play` case, and an ambiguous one otherwise. See §7.J.
- CI coverage: `.github/workflows/runtime.yml` "Standalone tests" runs content, coroutine-frames,
  upscaler-support and dlss-sr (and now mutex-cond). It does **not** run `test_pad.c`,
  `test_sema.c` or `test_runtime.c`, although MSYS2 CLANG64 provides SDL3, FFmpeg and Vulkan and
  `build.sh --test` builds all three on Linux. `test_motion_history.cpp` and
  `test_ui_composition.cpp` are not built by any workflow *or* by `build.sh --test`, and need a
  Vulkan device, so the audit's insistence on them is unsupported. The `pad`/`sema`/`runtime`
  gap is real and is the reason the pad and synchronization subsystems have no Windows CI
  signal at all.

---

## 4. Items I deliberately did not "fix"

### 4.1 CRIT-01 — why not broaden the byte pattern now

The audit's patch is *correct* (I checked the ModRM/REX encodings and both the Python set and the
C mask), and it is behaviour-preserving for the existing `rax` form. I still did not apply it:

- Both implementations are **heuristic byte scanners**, not disassemblers. `image.find()` /
  `memcmp` will match the same 9-byte sequence inside an immediate, a jump table or the tail of an
  unrelated instruction whose displacement byte happens to be `0x64`. The existing `rax`-only
  pattern already has that hazard; the patch multiplies the number of rewritten byte sequences by
  up to 16× (`{48,4c}` × 8 registers). Rewriting `0x64`→`0x65` inside data corrupts it silently.
- The change only matters if this title's binary actually contains a non-`rax` form, and there is
  no game data, no `link.json` and no `bb-probe` in this environment to check. Applying it would
  be a change I could not validate on either end.
- The safe version of this fix is a small instruction-length decoder (or a relocation-aware scan
  restricted to `.text`), plus a test fixture that proves both the old and new forms are matched
  and that a data look-alike is not. That is a follow-up, not a first patch.

Recorded follow-up: extend `patch_fs_loads`/`patch_tcb_loads` with the full ModRM set **and** an
instruction-boundary guard, with fixtures for `mov rcx/rdx/r8/fs:[0]` and for a
`mov rax, fs:[0x10]` look-alike that must stay untouched.

### 4.2 CRIT-03 — the proposed thunk is not an improvement

The audit's replacement `install_game_callbacks` injects `mov rcx, rdi` / `mov rdx, rsi` before
the call, i.e. it **changes the callback ABI** from "Image, r14 as the first two arguments" to
"rdi, rsi" — while every existing `u1_*`/`u2_*`/`u3_*` callback in `src/runtime_pad.c` is declared
`__attribute__((ms_abi))` and reads its arguments in `rcx, rdx, r8, r9`. It would also drop the
`mov r9, r14` argument that `u1_joypad_sticks` depends on (`r14-0x62 != joypad_state`), and it
replaces the existing `movdqu` saves with a 16×`movdqu` loop that is functionally identical. The
current thunk already skips the red zone and preserves XMM0-15. Applying this patch would break
the Uncharted 1 mouse-camera hooks. **Rejected.**

### 4.3 HIGH-07 — nothing to fix as described

See §3.5. The reported "R2 never reaches 255" cannot occur because `src/runtime_pad.c:577-578`
force `l2`/`r2` to 255 whenever the button bit is set, for every input path. I considered the two
residual oddities (analog values never reach the guest; `hold_after_capture` leaves `l2`/`r2`
latched) and rejected both as first fixes: the first changes trigger semantics for all three
titles and needs gameplay validation, the second is a one-frame overlay artefact.

### 4.4 HIGH-08 — the invariant already holds

See §3.6. A "bounds check" would be dead code; adding it would suggest the code is unsafe when
the caller's guard already establishes the invariant.

### 4.5 HIGH-11 — partial

`GameJob` genuinely does not set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and does not check
`AssignProcessToJobObject`. But the audit's remedy conflicts with `close_on_play`, so this needs
a *product decision* (should closing the launcher stop the game?) before code. What I did do is
close the CI half for the subsystem I touched: the new `tests/test_mutex_cond.c` runs in
`runtime.yml`. Adding `pad-test`/`sema-test`/`runtime-test` to Windows CI is a separate,
reviewable change and should be done with the ability to see the first CI run, since those three
have never executed on Windows and may fail for reasons unrelated to any fix.

---

## 5. Supplemental sections (audit §3) — triage

| § | Recommendation | Maps to | Status |
|---|---|---|---|
| 3.1 | U1 R2 deadzone `[0..1024]`, saturate at 28672, always sample triggers, clear `l2`/`r2` in `hold_after_capture` | HIGH-07 | **NOT REPRODUCED** — the firing failure is already prevented by `runtime_pad.c:577-578`. Changing `trigger()` would only change the button-bit threshold and the (currently discarded) analog value. |
| 3.2 | CPUID 0x15 TSC calibration + version-independent U2 leg-IK signature scanner in `probe.c` | HIGH-06 | Partially valid; **the proposed scanner is unsafe** (§7.G). TSC part: overflow claim wrong, race benign. |
| 3.3 | Stencil `0xc0` object velocity + real camera matrices into DLSS-G | CRIT-10, HIGH-10 | Valid goal, **unimplemented and unverifiable here** (needs GPU capture). The quoted shader patch duplicates a NaN/Inf guard that already exists at `camera_motion.comp:99-101`. |
| 3.4 | `VirtualQuery`-guided multi-range placeholder reservation | CRIT-05 | Same finding as CRIT-05, i.e. a **DUPLICATE**. The proposed code also marks pre-existing *occupied* ranges as `KIND_RESERVED` VMAs, which is exactly the "record memory as reserved when the OS does not support the claim" hazard this mission warns about. Rejected as given. |

Duplicates and dependencies in the audit:

- CRIT-05 ≡ §3.4 (exact duplicate).
- HIGH-07 ≡ §3.1 (exact duplicate).
- HIGH-06 ⊃ §3.2 (the supplement adds the U2 scanner to the timing finding).
- CRIT-10 + HIGH-10 are merged into §3.3, which additionally depends on a working
  `CameraMotion` reprojection export that does not exist yet.
- HIGH-09 is *not* a duplicate of CRIT-10: the former is a shader-recompiler output guard, the
  latter is the velocity shader's own arithmetic.

---

## 6. Additional concerns found that the audit does not mention

1. `patch_fs_loads()` and `patch_tcb_loads()` can match bytes that are not instruction starts
   (§4.1). Any broadening must address this.
2. `src/probe.c::protect()` flushes the instruction cache on Windows but not on POSIX
   (`__builtin___clear_cache` is absent). Harmless on x86-64, wrong on any other host.
3. `src/runtime_mutex.c::cond_wait()` requires an already-materialised mutex
   (`(uintptr_t)*mutex < 3` → EINVAL) while `mutex_lock()` lazily materialises a static one;
   a guest that does `pthread_cond_wait(&c, &static_mutex)` before ever locking it gets EINVAL.
   Inconsistent, but changing it alters guest-visible error behaviour, so it is recorded rather
   than changed.
4. `scripts/patches.py::game_app_version()` silently returns `None` on any unreadable/invalid
   `param.sfo`, which disables the version guard at `:297-303` and lets version-specific patches
   be written into a different game build. Failing closed would be safer. Recorded, not changed.
5. `tests/test_motion_history.cpp` and `tests/test_ui_composition.cpp` include headers that do not
   exist in this tree (`video_core/renderer_vulkan/motion_history.h`, `ui_composition.h`), so they
   cannot be compiled by anyone. Either the headers are missing from the import or the tests are
   stale; this is a CI-integrity issue independent of the audit.

---

## 7. Critical review of the proposed patches (A–J)

**A. TLS relocations (HIGH-02).** Handles `DTPMOD64` (16) and `DTPOFF64` (17) but **not**
`TPOFF64` (18), which the same finding claims to address. Worse, it treats a module's own TLS
symbol as `symbol['value'] + addend` for 17 *and would need* `symbol['value'] + addend -
tls_memsz` (negative, thread-pointer-relative) for 18 — the two models are different and the
patch conflates them. It also relaxes `DTPMOD64` to `symbol['type'] in (3, 6)`, accepting
`STT_SECTION` symbols that have no TLS they can refer to. Not applied.

**B. Signal handling and callback thunks (CRIT-02/CRIT-03).** The alternate-stack helper is
correct C but installed on one thread only, and `SA_ONSTACK` without a per-thread stack does
nothing on the others (§3.2). The thunk rewrite changes the callback calling convention and
would break the existing `ms_abi` callbacks (§4.2). Not applied. A test would have to run guest
code under fault pressure, which needs the game.

**C. Low-address allocator (CRIT-04).** The proposed slab duplicates the existing
`runtime_low_map()` facility in `runtime_memory.c`, and its free path requires the caller to pass
the *same* size it allocated with (no allocator-side metadata), so a mismatch silently corrupts
the wrong bin; allocations > 2048 bytes go to a mapping that is never freed. It also does not
solve the actual question — whether the guest masks these handles — which is unproven. Not
applied.

**D. Virtual-address reservation and view carving (CRIT-05/CRIT-06).** The `carve_host` rewrite
does not remove the fundamental problem: to split a *mapped* section view you must unmap it, and
`MapViewOfFile3` re-creation leaves the same transient hole. It adds `save_protections()` for the
kept ranges (an improvement over the current `PAGE_READWRITE`-then-restore), but it also
restructures the region table and failure paths in a Windows-only subsystem with no tests
available here. The CRIT-05 fallback additionally records occupied ranges as reserved VMAs, which
would make later queries claim memory the OS does not back. Not applied; explicitly deferred
pending a Windows test rig.

**E. Queue lifetime and ordering (CRIT-09).** The finding targets dead code (§2.3), and half of it
(the ordering claim) is wrong: `++m_write_index`/`++m_read_index` are seq_cst RMWs, and the
proposed `store(…, release)` is a weakening. The `m_data[pos] = T(args…)` / `= T{}` replacement
would also impose *assignable* and *default-constructible* requirements that the placement-new
version does not, narrowing the type contract for any future user. Not applied; if the header is
ever wired up, the right fix is `std::optional<T>`/`std::construct_at`+`std::destroy_at` with a
dedicated test (wraparound, move-only `T`, destructor counting).

**F. Networking handle lifetime (CRIT-08).** The proposed `m_retired` list of `shared_ptr<File>`
capped at 256 entries is **not** a use-after-free fix: after 256 deletions the oldest entry is
dropped while a caller may still hold — and dereference — the raw pointer `GetFile()` returned.
The task's own warning applies exactly. A correct design keeps the object alive for the duration
of the operation (e.g. return a `shared_ptr`/handle-based reference, or take the lock for the
whole operation and forbid close-during-use). Not applied.

**G. Timing and numerical correctness (HIGH-06).** The `process_time` rewrite is correct
(split seconds and nanoseconds, normalise the borrow) but fixes a 1 µs bias, not a
non-monotonic step (§3.4). The CPUID leaf-0x15 path is a good idea; `pthread_once` is portable
here only because the file is compiled on Windows too, where `runtime_kernel.c` uses
`compat_*` shims — the patch does not show the Windows branch. The U2 leg-IK scanner
(`§3.2`) is unsafe: it scans from offset 32 upward for a 10-byte pattern, then writes a 51-byte
stub at `c` and `0xcc`-fills to 51 bytes, which can overrun the matched instruction sequence and
the following instructions, and it hardcodes a `jmp 0x2d` that is only valid for the EU-1.00
layout it claims to make version-independent. Not applied.

**H. Shader/upscaler/motion vectors (CRIT-10, HIGH-09).** The mode-2 patch re-adds the NaN/Inf
guard that already exists at `camera_motion.comp:99-101` and adds the `clip.z <= 1e-4` case — the
latter is a real improvement (huge finite vectors), but the patch does **not** implement the
object-motion handling it promises: no stencil input, no `c[56..58]` transform. A denominator
clamp alone does not make reconstructed motion vectors valid. The DLSS depth convention and the
SR/frame-generation independence were preserved and re-verified (§1.6).

**I. NGX lifetime and swapchain synchronisation (HIGH-10, CRIT-11).** The parameter-leak fix is
correct in intent but incomplete: it destroys `state.parameters` only on the two failure paths it
patches, and leaves `state.feature`/`state.fg_feature` and the success path's ownership rules
implicit. Adding `std::recursive_mutex` to `State` is not sufficient by itself, as the mission
notes: every read of `state` in `FrameGenEvaluate`, `Evaluate` and the NGX log callback would have
to take it, and the log callback can be invoked from driver threads, which is a reentrancy
hazard the patch does not analyse. The CRIT-11 rewrite is plausible but unverifiable here.

**J. Launcher and process handling (HIGH-11).** `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` changes the
meaning of `close_on_play` (launcher exits by design 5 s after the game starts) — the audit does
not consider that, and the mission explicitly forbids silently changing that setting. The
`swprintf` truncation checks are harmless but the strings are already bounded. Not applied.

---

## 8. Selected fix

### 8.1 Why this finding

Ranking the unresolved set by (evidence × impact × reproducibility ÷ regression risk):

| Item | Evidence | Impact | Reproducible here | Risk of fix | Verdict |
|---|---|---|---|---|---|
| MED-02 cond lifetime + counters | read + tested + TSan | medium (silent guest hang, UB) | **yes** | very low | **chosen** |
| HIGH-08 hardening | no reproduction | low | no (FFmpeg) | low | skip |
| CRIT-08 `HandleTable` | pattern real | medium (UAF) | no | medium (API redesign) | defer |
| HIGH-02 TLS relocs | confirmed | high *if* reachable | no (game data) | low-medium | defer |
| HIGH-03 patch loader | confirmed | high *if* reachable | no (eboot) | low | defer |
| CRIT-05/CRIT-06 | confirmed source | **highest for the target PC** | no (Windows) | high | defer, documented |
| CRIT-10 / HIGH-10 / CRIT-11 | partial | high (rendering) | no (GPU) | medium-high | defer |

No CRITICAL entry could be implemented *and validated* in this environment. MED-02 is the highest
item that could be confirmed, reproduced, fixed and regression-tested without game data, a GPU or
Windows — and it is `assert`-level wrong in a way that turns a guest programming error into a
hang, which is exactly the class of defect the project's own conventions
(`mutex_destroy`'s sentinel, `runtime_rwlock.c`'s registry) already guard against elsewhere.

### 8.2 Root cause and invariant

Invariant: *a guest handle that has been destroyed must never be silently replaced by a new host
object; using it must return the guest's EINVAL.*

`src/runtime_mutex.c` encoded destroyed mutexes as the sentinel `2` and checked for it in
`ensure_mutex`/`mutex_destroy`/`mutex_unlock`, but encoded destroyed condition variables as
`NULL`, and `ensure_cond` treated `NULL` as "statically initialised, create it lazily". The two
halves of the same file disagreed, so `pthread_cond_signal`/`_wait`/`_broadcast` after a destroy
allocated a fresh condvar — which nothing will ever signal.

Second, independent defect in the same file: six statistics counters were plain `size_t`
incremented from every guest thread and read by `runtime_mutex_report()` from another thread.

### 8.3 The change

`src/runtime_mutex.c` (only file of production code changed):

| Before | After |
|---|---|
| `if (__atomic_load_n(...) >= 2) return 0;` in `ensure_cond` | explicit `== 2 → EINVAL`, `> 2 → 0`, else lazily create |
| `cond_destroy`: `*cond = NULL` | `*cond = (GuestCond *)(uintptr_t)2`, and a second destroy returns EINVAL |
| `static size_t created, locks, unlocks;` | `static _Atomic size_t …` + `counted()` helper |
| `++locks`, `++waits`, `++wakeups`, … | `counted(&locks)`, … (relaxed) |
| `runtime_mutex_report()` plain reads | `atomic_load_explicit(…, memory_order_relaxed)` |

`tests/test_mutex_cond.c` (new, 162 lines) covers: a real wait/signal round trip across threads;
the destroyed-handle contract for signal/broadcast/wait/timedwait/double-destroy; the static
`0`/`1` handles still coming up on first use; and 8 threads × 20 000 lock/unlock with an exact
counter assertion. It includes `src/runtime_mutex.c` the way `tests/test_pad.c` includes
`src/runtime_pad.c`, so it needs no GPU, SDL, FFmpeg or game data.

`build.sh --test` and `.github/workflows/runtime.yml` gained the two lines that build and run it,
so the regression is guarded on the target platform (Windows CI) as well.

### 8.4 Commit

```
05647f1ccfc137ac7ecc4fc0e41c5a93f83ae40b
branch arena/3219e6b1-uncharted
Guest mutex/cond lifetime: report destroyed condvars, unrace the statistics
 1 file changed in src/ (runtime_mutex.c), 1 new test, 2 test-entry lines
 4 files changed, 202 insertions(+), 17 deletions(-)
```

The diff is confined to `src/runtime_mutex.c`, `tests/test_mutex_cond.c`, `build.sh` and
`.github/workflows/runtime.yml`. The DLSS implementation, the launcher, the loader, the memory
manager and every `gpu/` file are untouched.

---

## 9. Validation

### 9.1 Native probe cannot be built here

`build.sh` needs `pkg-config` with `vulkan` and `sdl3`, plus `cmake` and `ninja`; none is present
in this sandbox. Therefore `bb-probe`, `libbbgpu`, the packaged ZIP and the Rust launcher were
**not** built, and no CI run could be triggered from here (see §10).

### 9.2 Commands actually run, with their real output

```
$ python3 -m unittest discover -s tests -p 'test_*.py'          # before the change
Ran 98 tests in 9.210s
OK (skipped=28)

$ python3 -m unittest discover -s tests -p 'test_*.py'          # after the change
Ran 98 tests in 9.747s
OK (skipped=25)
```
(The skip count drops from 28 to 25 only because `out/content-test` now exists, which un-skips
the three `test_content` cases; no test was added or removed from the Python suite. All 25
remaining skips are pre-existing: missing `out/bb-probe`, `out/runtime-test`, `out/sema-test`,
and the platform skips in `test_run_settings.py`.)

```
$ gcc -std=c11 -D_GNU_SOURCE -O2 -g -Wall -Wextra -Werror -I. -Isrc \
      tests/test_content.c src/runtime_content.c -o out/content-test && ./out/content-test
PASS: AppContent lifecycle, profile and output boundaries

$ gcc -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -Isrc tests/test_file_mods.c -o out/file-mods-test && ./out/file-mods-test
Guest mod files: reads, stat, merged listing, readonly assets and writable saves PASS

$ gcc -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -Isrc tests/test_mutex_cond.c -o out/mutex-cond-test && ./out/mutex-cond-test     # NEW
Runtime: mutexes created=4, locks=160003, unlocks=160003
Runtime: condition variables created=3, waits=1, wakeups=3
PASS: mutex/cond handle lifetime, destroyed-sentinel reporting and shared statistics (32 checks)

$ g++ -std=c++20 -O0 -g -Wall -Wextra -Werror tests/test_coroutine_frames.cpp -o out/coroutine-frames-test && ./out/coroutine-frames-test
PASS: coroutine frame lifetime contract (destroy nested frames after done())

$ g++ -std=c++20 -O2 -g -Wall -Wextra -Werror -Igpu/shim -Igpu/shadps4 \
      tests/test_upscaler_support.cpp gpu/shim/bbport_settings.cpp -o out/upscaler-support-test && ./out/upscaler-support-test
Upscaler support: saved choices and FSR 3.1 fallback PASS

$ g++ -std=c++20 -O2 -g -Wall -Wextra -Werror -Igpu/shim -Igpu/shadps4 \
      tests/test_dlss_sr.cpp gpu/shim/bbport_settings.cpp -o out/dlss-sr-test && ./out/dlss-sr-test
DLSS SR: presets, resolution plan, velocity-mode gate, provider selection and frame-generation independence PASS

$ bash -n build.sh            # syntax of the edited test block
ok

$ gcc -std=c11 -fsyntax-only -Wall -Wextra -Werror -I. -Isrc \
      -I<sdl3-headers> tests/test_pad.c
ok    # unchanged file still parses; the SDL3 library is absent, so it cannot be linked/run here
```

Every changed line compiles with `-Wall -Wextra -Werror` on gcc 12. No new warnings.

### 9.3 Mutation check — the new test really detects the old behaviour

The pre-change `runtime_mutex.c` was taken verbatim from the baseline commit
(`git show 5fe65e3:src/runtime_mutex.c`, sha256 `b1d46a0a7c5e051…`) into a scratch tree, with the
new test compiled against it (only the counter *reads* adapted to plain `size_t`, so the TU
compiles). Re-run after the harness fix in §9.4(b), with the same result:

```
$ ./pre-fix
FAIL: handle is the destroyed sentinel: got 0, want 0x1
exit=1
```

A direct behavioural comparison, same program against both files:

```
--- PRE-FIX ---                                 --- FIXED ---
signal after destroy -> 0                        signal after destroy -> 0x80020016
destroy twice        -> 0                        destroy twice        -> 0x80020016
counted locks=400000 of 400000 expected          counted locks=400000 of 400000 expected
```

The pre-fix `0` on `signal after destroy` is the defect: a *new* condvar was allocated for a
handle the guest believes is dead.

### 9.4 Concurrency evidence (ThreadSanitizer)

TSan is **not** wired into CI; all of the following are one-off runs, and each build ran to
completion, so the reports are attributable to the sources rather than to an aborted run.

**(a) The production counter race.** A probe that does what a live process does — guest threads
locking while `runtime_mutex_report()` prints from the main thread — compiled against the exact
pre-fix source (`git show 5fe65e3:src/runtime_mutex.c`, sha256 `b1d46a0a…`):

```
$ gcc -std=c11 -O1 -g -pthread -fsanitize=thread -Isrc prodrace.c -o prodrace-prefix
$ ./prodrace-prefix ; echo $?
WARNING: ThreadSanitizer: data race
SUMMARY: ThreadSanitizer: data race src/runtime_mutex.c:98 in mutex_unlock
SUMMARY: ThreadSanitizer: data race src/runtime_mutex.c:260 in runtime_mutex_report
66                      # 2 races

$ (same probe, fixed source)
$ ./prodrace-fixed ; echo $?
0                       # 0 races
```

So the sanitizer detects the original race in the production file, both the writer side
(`mutex_unlock`) and the reader side (`runtime_mutex_report`), and it is gone after the fix.

**(b) The test harness — a defect found in review and corrected.** Run against the test as first
committed, TSan reported a *second*, independent race: `check_pair()`'s tally was a plain
`static long` written by both `waiter_thread()` and `main`, because the signaller's
`unlock` genuinely overlaps the waiter's `wait`/`unlock`.

```
$ gcc -std=c11 -O1 -g -pthread -fsanitize=thread -Isrc tests/test_mutex_cond.c -o tmc-tsan
$ ./tmc-tsan ; echo $?
WARNING: ThreadSanitizer: data race
  Read of size 8 by thread T1:   #0 check_pair tests/test_mutex_cond.c:33
                                 #1 waiter_thread tests/test_mutex_cond.c:49
  Previous write by main thread: #0 check_pair tests/test_mutex_cond.c:33
                                 #1 test_wait_signal_round_trip tests/test_mutex_cond.c:67
66                      # 1 race
```

The tally is now `_Atomic long` with `atomic_fetch_add_explicit(…, relaxed)` and an atomic load
for the report (`%ld` unchanged). Nothing else in the test changed: no assertion, case or coverage
removed. After the change the identical TSan build reports no race and exits 0. This was a
harness-only defect — it did not affect the printed count (300/300 runs printed `32` before,
200/200 after), so it never masked a production result.

### 9.5 What this validation does and does not establish

- Establishes: the destroyed-handle contract now matches the mutex half of the same file; the
  statistics are race-free *and* so is the test that checks them; the header/CI wiring is
  syntactically valid; the pre-existing suites are unchanged; the fixed code compiles
  warning-free.
- Does **not** establish: that the port builds or runs (no Vulkan/SDL/cmake here), or anything
  about DLSS, motion vectors, frame generation, VRAM behaviour or frame times. (Windows CI with
  the new test step is covered in §10, on the run for the exact head.)
- TSan remains a local, one-off check. Nothing in CI runs it, so a future race in this file would
  not be caught automatically.

---

## 10. CI

The branch was pushed and PR #6 opened (`https://github.com/lostpilot404/Uncharted/pull/6`,
base `main`, head `arena/3219e6b1-uncharted`) so the new test step could be exercised on real
Windows rather than merely predicted. Nothing was merged.

This table is updated to whatever the branch head is at the end of a session; every run below
reported the SHA it is listed against.

Final head `bd933ad85a7e57582ef0c4c2df3ad8236e92107e`, all three running that exact SHA
(2026-10-22T16:46:37Z):

| Workflow | Run | Result |
|---|---|---|
| Runtime CI | https://github.com/lostpilot404/Uncharted/actions/runs/39816803333 | **success**, 8m3s |
| Rust launcher CI | https://github.com/lostpilot404/Uncharted/actions/runs/39816803304 | **success**, 3m56s |
| Windows package | https://github.com/lostpilot404/Uncharted/actions/runs/39816803394 | **success**, 12m43s — the packaged ZIP was rebuilt and checked with the fix in the tree |

The runtime job's annotation for that run reads
`notice: mutex-cond-test ran and passed (destroyed cond handles reported, statistics race-free)`.

Every earlier head of this branch was also fully green, so no run is being relied on out of
sequence: `a17a4bf` (39813852060 / 39813852104 / 39813851971),
`be9d9c1` (39811702837 / 39811702830 / 39811702866),
`b80d143` (39807667506 / 39807670668 / 39807667460) and `32f49aa`
(39805755984 / 39805755756 / 39805757419).

The first push (`52ccbfb`) also produced a fully green Runtime CI
(https://github.com/lostpilot404/Uncharted/actions/runs/39801999073) and Rust launcher CI
(https://github.com/lostpilot404/Uncharted/actions/runs/39801998881) before the notice-only
follow-up, and the Windows package job of that push was still running when the follow-up landed.

The runtime job's "Build the Windows runtime (MSYS2 CLANG64)" step succeeded, so `src/runtime_mutex.c`
**compiles on the target toolchain** with the project's `-Werror` policy. The "Standalone tests"
step then reported, as API-readable annotations:

```
notice: mutex-cond-test ran and passed (destroyed cond handles reported, statistics race-free)
notice: content-test ran and passed
notice: coroutine-frames-test ran and passed
notice: dlss-sr-test ran and passed (presets, resolution plan, velocity-mode gate, provider selection and frame-generation independence)
notice: upscaler-support-test ran and passed
notice: test_file_mods skipped: POSIX fixture (Linux/Nix only)
```

and the "Python test suite" step passed as well (the workflow fails the job unless the suite exits
0 and prints `OK`). This is the first time the mutex/condition-variable subsystem has had a Windows
CI signal; before this change the step did not build any test for it.

Still absent and unchanged by this work: `pad-test`, `sema-test` and `runtime-test` are built by
`build.sh --test` on Linux but not by the Windows workflow (§4.5).

---

## 11. Remaining work, ranked

### 11.1 Highest priority (confirmed, unfixed)

1. **CRIT-05 — monolithic 944 GiB placeholder reservation** (`runtime_memory.c:139-143`).
   The only audited defect that can prevent the port from starting at all on a real Windows 11
   install, and the current failure message concedes the precondition. Needs a Windows test rig:
   occupied-address cases, partial ranges, failed reservation calls, protection restoration and
   concurrent access (§7.D). Not attempted: no Windows, no test.
2. **CRIT-06 — `carve_host` transient unmap** (`runtime_memory.c:160-212`). Same subsystem, same
   prerequisite. Confirmed by reading; the race window is provable but its consequences need
   Windows.
3. **HIGH-10 — NGX parameter leaks** (`bbport_dlss_bridge.cpp`). Small, bounded, and safe to fix
   *if* the NGX SDK is available to compile against. Blocked here by the SDK, not by design.
4. **CRIT-08 — `HandleTable` lifetime** (`gpu/shim/core/file_sys/fs.h:32-66`). Needs an ownership
   redesign, not the retired-list patch.
5. **HIGH-02 — TLS relocation kinds 17/18** (`scripts/link_modules.py:177-204`). Needs one real
   module dump to know whether it bites, and a correct `TPOFF64` implementation if it does.

### 11.2 Medium

6. **CRIT-10 / §3.3 — velocity pass `clip.z` guard and stencil object motion.** Needs GPU frame
   captures before and after.
7. **CRIT-11 — frame-generation semaphore/fence reuse.** Needs the hardware matrix in §11.3.
8. **HIGH-11 — Job object semantics.** Needs a product decision on `close_on_play`, then
   `SetInformationJobObject` + checked `AssignProcessToJobObject`.
9. **HIGH-11 (CI) — add `pad-test`, `sema-test`, `runtime-test` to the Windows workflow.** Do it
   with the ability to watch the first run.
10. **CRIT-02 — per-thread alternate signal stacks** (Linux only).
11. **MED-01 — `LINKED.clear()` + `shutil.rmtree(onerror=…)`** in `scripts/mods.py`.
12. **§6 item 4 — `patches.py` should fail closed when `param.sfo` cannot be read.**

### 11.3 Hardware-validation checklist — all `NOT TESTED`

Nothing below has been executed; the machine is unavailable and the port does not build here.

| # | Check | Status |
|---|---|---|
| 1 | Long-session host-memory growth (≥ 2 h, same scene, record RSS every 5 min) | NOT TESTED |
| 2 | VRAM pressure and allocation failures at 1080p with DLSS SR Performance/Ultra Performance on 4 GB | NOT TESTED |
| 3 | DLSS SR preset selection and *effective* render resolution (log + capture) | NOT TESTED |
| 4 | Motion-vector artifacts: ghosting, streaks, camera pan, near-camera geometry | NOT TESTED |
| 5 | Frame-generation correctness and stability (2×/3×), window resize, alt-tab, device loss | NOT TESTED |
| 6 | Frame time / FPS / VRAM before-and-after, identical scene, settings, cache state | NOT TESTED |
| 7 | Launcher install, start, settings persistence, `close_on_play`, game launch, Stop | NOT TESTED |
| 8 | Startup on a Windows 11 system with third-party overlay DLLs loaded (CRIT-05 precondition) | NOT TESTED |
| 9 | Uncharted 1 firing (`R2`) with a worn/limited-travel pad | NOT TESTED |
| 10 | Uncharted 2/3 leg-IK assertion on a non-EU-1.00 dump | NOT TESTED |

Reproduction instructions for the CI-visible half of the fix, once pushed:

```
# Windows (MSYS2 CLANG64), from the repository root:
python -m unittest discover -s tests -p 'test_*.py'
clang -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -Isrc tests/test_mutex_cond.c -o out/mutex-cond-test
out/mutex-cond-test.exe
```

---

## 12. Change isolation

- All work is on `arena/3219e6b1-uncharted`, branched from the verified `5fe65e3`.
- No force-push, no reset, no branch deletion, no submodule initialisation, no push to
  `Supermedo/uncharted_pc`.
- PR #5 is already merged; the DLSS implementation is untouched by this change and was re-verified
  green (`test_dlss_sr`, `test_upscaler_support`) after the edit.
- The fix and its test form one commit; the triage report is a separate documentation commit, so
  the behavioural diff can be reviewed on its own.

---

## 13. Mission 7.2 — Windows memory characterization follow-up

This section supersedes the earlier §3.3/§11 statements that CRIT-05/CRIT-06 had no Windows test
harness. It does **not** claim either finding is fixed.

### Verified base and source

A fresh fetch resolved `origin/main` to `5a54fd8d2b8190edb9fd4851f7c71e70e79ea506`, and local
`HEAD` was exactly the same SHA with a clean working tree before edits. This is the merged PR #6
base specified for Mission 7.2. Work is on the active Arena session branch
`arena/6c7fcfd7-uncharted`, which began at this verified base; the earlier merged PR branch was
not reused. The mutex/condition-variable implementation and
`tests/test_mutex_cond.c` are present; the latter was rebuilt with GCC `-Wall -Wextra -Werror` and
ran successfully here (32 checks). The supplied `Gemini.md` is not tracked in this checkout; the
existing triage is `docs/gemini-audit-triage.md`.

Reinspection of `src/runtime_memory.c` confirmed the production parameters and sequence:
`USER_MIN=0x1000000000`, `USER_MAX=0xfc00000000`, one full-range
`VirtualAlloc2(MEM_RESERVE | MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS)`, failure via `win_fail()`
(exit 21), and insertion into the Windows `HostRegion` table only after reservation success.
`carve_host()` unmaps mapped views before coalescing/splitting placeholders and reconstructing
survivors; reconstruction uses `PAGE_READWRITE` and saved protection runs are restored later.

### Harness and current evidence state

`tests/test_windows_memory.c` includes the complete production `runtime_memory.c` to test the
actual static `host_map()`, `view_map()` and `carve_host()` implementations without copying them.
The minimal standalone link boundary is Win32 + onecore + C runtime + winpthreads, with inert
stubs for the unexercised `runtime_lookup()` and `compat_sleep_ns()` project references. The
fixture reserves only 1 MiB, records the test range only after `VirtualAlloc2()` succeeds, and
exercises a 64 KiB
interior carve. Test-only callbacks in `runtime_memory.c` are guarded by
`BB_WINDOWS_MEMORY_TEST` and are absent from normal builds.

Runtime CI compiles and executes the Windows-native harness from the exact PR-head SHA (it
verifies GitHub's PR-head ref) and retains its output log, without replacing existing steps. The
harness reports collision errors/child exits/table state, section-backed mapping and offsets,
direct `VirtualQuery()` observations, concurrent access, temporary protections, restoration and
cleanup. Access-violation classification is independent of direct gap observation; a gap does
not imply every execution crashes.

A successful native Windows run is recorded at [Runtime CI run 39843825619](https://github.com/lostpilot404/Uncharted/actions/runs/39843825619).
The log confirms the harness was built from the exact PR-head source commit
`abc35da67bffffdc1d3883f066c91d16ca1b4b1d` (the workflow's synthetic merge checkout was
`4acc2c4ca6e163bd7cf162223fe3bba6a669496f`). Observed outcomes:

- CRIT-05: the 64 KiB placeholder blocker at `USER_MIN` was reserved (`VirtualQuery` state
  `0x2000`); the exact 944 GiB `VirtualAlloc2` probe returned `NULL` with `GetLastError()=487`
  (`ERROR_INVALID_ADDRESS`). Production failure hook also captured 487 with `region_count=0`;
  `win_fail()` emitted 487 and child exit was decimal 21 (`0x15`).
- CRIT-06 fixture: the 64 KiB interior carve in a 1 MiB section view passed. Left/right states
  were `MEM_COMMIT`/`MEM_MAPPED` with `PAGE_READONLY` (`0x2`); the center was `MEM_RESERVE`
  (`0x2000`); the tracked page was restored to `PAGE_NOACCESS` (`0x1`). The right section offset
  was `0x40000`; alias/data and cleanup checks passed.
- Direct gap: both surviving sibling queries reported `MEM_RESERVE` (`0x2000`), `Protect=0`,
  `Type=MEM_PRIVATE` (`0x20000`) before reconstruction, while the region table still said
  mapped. The isolated worker reached the gate and its read reproduced
  `STATUS_ACCESS_VIOLATION` (`0xC0000005`).
- Temporary protection: left, tracked, and right pages each reported `MEM_COMMIT` with
  `PAGE_READWRITE` (`0x4`) before saved protections were restored. Final read-only and tracked
  no-access protection assertions passed.
- Overall Windows harness: `failures=0`; section handle closed, backing alias unmapped, fixture
  placeholders released. Runtime build, existing standalone tests, and Python suite passed.

The detailed `VirtualQuery()` bases, sizes, state/protection values, and child output are retained
in `docs/windows-memory-test-plan.md` and the check annotations. The first CI attempt on `241ab1da126c893ae80b29d50628b3dbd0e6f51b` ([run
39838263644](https://github.com/lostpilot404/Uncharted/actions/runs/39838263644)) passed the runtime
build and existing tests but failed to link the harness because `VirtualAlloc2` required
`-lonecore`; no API calls ran on that attempt. The standalone link was corrected and the
successful observation run followed.

Local validation remains separate: the strict GCC compile covers only the Linux branch of
`runtime_memory.c`; the local Python suite reports 98 tests, 28 skips, and the mutex/condition test
reports 32 passing checks. Each push triggers an exact-source Runtime CI check; the result on the
current PR head is the commit-specific verification for review.

### Fix disposition

No production reservation fallback or carving rewrite was applied. A retry at a different host
base is not acceptable if it violates guest address identity. In particular, the Gemini proposal
to record host-occupied addresses as guest-reserved VMAs is rejected: those bytes are owned by
host modules and are not backed by the runtime placeholder, so guest query/unmap/protect behavior
would be false and potentially destructive. Candidate CRIT-05/CRIT-06 designs and constraints
(address-space fragmentation, 64 KiB Windows mapping granularity versus the 16 KiB guest page,
section offsets/aliasing, GPU page tracking, raw guest-thread access, API failure handling and
performance) are documented in `docs/windows-memory-test-plan.md` for separate review in light of
the native observations.

---

## 14. Mission 7.3 — CRIT-05 fix

This section supersedes the §3.3/§11.1/§13 disposition of CRIT-05 as merely deferred. CRIT-06
remains open and is not claimed fixed.

Base verified by fresh fetch: `origin/main` == local `HEAD` == session branch start ==
`d49e0c6cf289099d433ba552dc5961fec05bd8d7` (merge of PR #7), with that commit verified as an
ancestor of `origin/main`. All PR #7 files and the `BB_WINDOWS_MEMORY_TEST` hooks were present.

Root cause (confirmed against source): Windows `runtime_memory_reserve()` made exactly one
944 GiB `VirtualAlloc2(MEM_RESERVE|MEM_RESERVE_PLACEHOLDER)` over `[0x1000000000, 0xfc00000000)`
and exited 21 on any failure. Any host allocation inside that interval — measured in PR #7
with a deliberate 64 KiB blocker returning `ERROR_INVALID_ADDRESS` (487) — prevented startup.
Nothing in the runtime requires one contiguous OS reservation: the guest contract is the VMA
table (`find_free`/`covered`/`overlaps`, all `[USER_MIN, USER_MAX)`-bounded), and
`carve_host()` operates on one contiguous placeholder span per request.

Fix (production: `src/runtime_memory.c`, comment in `src/runtime.h`): reserve available
subranges, keeping host-owned holes distinct from guest VMAs. The full-span fast path is
unchanged. On 487, a `VirtualQuery()` walk reserves each 64 KiB-aligned free subrange at its
exact guest address and records only successful reservations (new `HostRegion.root` keeps
cleanup/coalescing inside one originating reservation). Non-487 errors, query failures,
mid-scan races that cannot be reconciled, and the zero-subrange case roll back every partial
reservation and exit 21 explicitly. Hole-crossing guest map/unmap requests now return
`NO_MEMORY`/`INVALID` instead of reaching `win_fail()`; mapped metadata is published only
after `MapViewOfFile3()` succeeds; `find_free()` skips holes; fixed `sceKernelReserveVirtualRange`
before any pool use ensures the reservation instead of hitting the fatal carve path. The
rejected Gemini §3.4 approach (recording occupied ranges as guest-reserved VMAs) remains
rejected and is not implemented.

Tests: `tests/test_windows_memory.c` now has six native CRIT-05 children — no-collision,
low blocker at `USER_MIN` (PR #7 scenario, now expecting recovery), unaligned 84 KiB middle
blocker with boundary/rollback semantics, high blocker at `USER_MAX - 64 KiB`, three
non-contiguous blockers with pocket/`batch_map2` behavior, and full-interval exhaustion
(still exit 21, `region_count=0`). CRIT-06 fixture/gap/concurrent/protection children are
preserved as characterization. Results are recorded in
`docs/windows-memory-test-plan.md` §"Mission 7.3" once the exact-head Windows CI run is
inspected; Linux-only results are not evidence for the `_WIN32` path.

Remaining: CRIT-06 (transient missing-view interval, concurrent access violation, temporary
writable protections during `carve_host()`) is unresolved and needs its own safepoint/carve
design. Degraded-capacity limitation: a guest mapping larger than every available subrange
fails with `NO_MEMORY` even when aggregate free space suffices.

Mission 7.3 verification update: the fix is implemented in `b91296a` with harness corrections
in `19b3a36`. Runtime CI run 39946258119 tested exact source
`19b3a36238063579a4c5cb1e87294bc71de4e8ed` and reported all six CRIT-05 children PASS with
`RESULT SUMMARY failures=0` (`RESULT CRIT-05=FIXED ...`), while CRIT-06 remained
characterized-not-fixed (gap directly observed, concurrent access violation reproduced,
temporary writable protection observed). The first run on `b91296a` failed on three harness
bugs (under-sized direct allocations in the test children), not on production code; they are
fixed and re-verified in `19b3a36`. PR: https://github.com/lostpilot404/Uncharted/pull/8
(not merged automatically).
