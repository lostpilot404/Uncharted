/* Windows-native verification for CRIT-05 and characterization for CRIT-06.
 *
 * This deliberately includes the production implementation in this one translation unit so
 * the test can call both its guest-facing Orbis memory exports and its static host-mapping
 * boundary (carve_host/view_map/host_map/release_host_regions) without copying those
 * algorithms or pulling in the GPU/SDL/FFmpeg dependency graph. BB_WINDOWS_MEMORY_TEST enables
 * narrowly scoped observation callbacks in runtime_memory.c and is never defined in release
 * builds.
 */
#define BB_WINDOWS_MEMORY_TEST 1
#include "../src/runtime_memory.c"

#ifndef _WIN32
#error "test_windows_memory.c requires a native Windows target"
#endif

#include <stdint.h>

#define FIXTURE_BASE ((uintptr_t)(USER_MIN + UINT64_C(0x100000000))) /* USER_MIN + 4 GiB */
#define FIXTURE_SIZE UINT64_C(0x00100000)                            /* 1 MiB, for CRIT-06 fixture */
#define TEST_POOL_SIZE UINT64_C(0x00400000)                          /* 4 MiB pagefile section for CRIT-05 */
#define GRANULARITY UINT64_C(0x00010000)                             /* Windows allocation granularity (64 KiB) */
#define CARVE_START_OFF UINT64_C(0x00030000)
#define CARVE_END_OFF   UINT64_C(0x00040000)                         /* remove just one 64 KiB unit */
#define MID_BLOCKER_BASE ((uintptr_t)(USER_MIN + (UINT64_C(256) << 30)))
#define MID_BLOCKER_SIZE UINT64_C(0x00015000)                        /* 84 KiB: intentionally not 64 KiB aligned */
#define MID_BLOCKER_ALIGNED_END ((uintptr_t)(MID_BLOCKER_BASE + UINT64_C(0x00020000)))
#define MULTI_MID_BASE ((uintptr_t)(USER_MIN + (UINT64_C(512) << 30)))
#define CHILD_TIMEOUT_MS 20000
#define STATUS_ACCESS_VIOLATION_CODE 0xC0000005UL
#define CHILD_TIMEOUT_CODE 0xE0000001UL

_Static_assert(sizeof(void *) == 8, "Windows memory tests require x64");
_Static_assert((USER_MIN % GRANULARITY) == 0, "production base must be allocation-granularity aligned");
_Static_assert((USER_MAX % GRANULARITY) == 0, "production end must be allocation-granularity aligned");
_Static_assert((FIXTURE_BASE % GRANULARITY) == 0, "fixture base must be allocation-granularity aligned");
_Static_assert((FIXTURE_SIZE % GRANULARITY) == 0, "fixture span must be allocation-granularity aligned");
_Static_assert((PAGE % UINT64_C(4096)) == 0, "guest pages must be system-page aligned");

typedef enum { HOOK_NONE, HOOK_GAP, HOOK_GAP_CONCURRENT, HOOK_PROTECTION } HookMode;

typedef struct {
    DWORD exit_code;
    BOOL timed_out;
    BOOL output_truncated;
    char output[65536];
} ChildResult;

typedef struct {
    uintptr_t start;
    uintptr_t end;
} AddrSpan;

typedef struct {
    uintptr_t base;
    SIZE_T size;
    uint64_t canary;
    int active;
} HostBlocker;

static int failures;
static HookMode hook_mode;
static int hook_query_failed;
static int gap_observed;
static int temporary_write_observed;
static int protection_hook_calls;
static HANDLE concurrent_start_event;
static HANDLE concurrent_ready_event;
static HANDLE concurrent_done_event;
static HANDLE concurrent_thread;
static uintptr_t concurrent_probe_address;
static volatile LONG concurrent_value;

/* runtime_memory.c's full translation unit has only these two project-level link dependencies
 * on Windows. Neither is involved in the paths under test. */
uintptr_t runtime_lookup(const RuntimeExport *table, size_t count, const char *nid) {
    (void)table; (void)count; (void)nid;
    return 0;
}
void compat_sleep_ns(uint64_t ns) { (void)ns; }

static void check(int condition, const char *what) {
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", what);
        ++failures;
    } else {
        printf("PASS: %s\n", what);
    }
}

static int api_failure(const char *what, DWORD error) {
    fprintf(stderr, "FAIL: %s (Windows error %lu)\n", what, (unsigned long)error);
    ++failures;
    return 0;
}

static int query_mbi(uintptr_t address, MEMORY_BASIC_INFORMATION *mbi, const char *what) {
    SIZE_T n = VirtualQuery((const void *)address, mbi, sizeof(*mbi));
    if (!n) {
        api_failure(what, GetLastError());
        return 0;
    }
    return 1;
}

static void print_mbi(const char *label, const MEMORY_BASIC_INFORMATION *mbi) {
    printf("%s base=%p size=0x%zx state=0x%lx protect=0x%lx type=0x%lx allocation_protect=0x%lx\n",
           label, mbi->BaseAddress, (size_t)mbi->RegionSize, (unsigned long)mbi->State,
           (unsigned long)mbi->Protect, (unsigned long)mbi->Type,
           (unsigned long)mbi->AllocationProtect);
}

static uint64_t marker_for(uint64_t offset) {
    return UINT64_C(0x554e434841525445) ^ (offset * UINT64_C(0x9e3779b97f4a7c15));
}

static uint64_t blocker_canary_for(uintptr_t base, size_t word_index) {
    return UINT64_C(0x484f5354424c4f43) ^ ((uint64_t)base * UINT64_C(0x9e3779b97f4a7c15)) ^
           ((uint64_t)word_index * UINT64_C(0xbf58476d1ce4e5b9));
}

/* Walks [USER_MIN, USER_MAX) via VirtualQuery before runtime_memory_reserve() to record the
 * exact 64 KiB-aligned MEM_FREE subranges and count any non-free host intervals. */
static int scan_expected_subranges(AddrSpan *out, size_t max_out, size_t *out_count,
                                   size_t *occupied_count) {
    size_t n_free = 0, n_occ = 0;
    uintptr_t cursor = USER_MIN;
    while (cursor < USER_MAX) {
        MEMORY_BASIC_INFORMATION mbi;
        if (!query_mbi(cursor, &mbi, "VirtualQuery(scan_expected_subranges)")) return 0;
        if (!mbi.RegionSize || (uintptr_t)mbi.BaseAddress > cursor) {
            fprintf(stderr, "FAIL: invalid VirtualQuery progress at %p\n", (void *)cursor);
            ++failures;
            return 0;
        }
        uintptr_t base = (uintptr_t)mbi.BaseAddress;
        uintptr_t end = (mbi.RegionSize > USER_MAX - base) ? USER_MAX : base + mbi.RegionSize;
        if (end <= cursor) {
            fprintf(stderr, "FAIL: non-advancing VirtualQuery at %p\n", (void *)cursor);
            ++failures;
            return 0;
        }
        if (mbi.State != MEM_FREE) {
            ++n_occ;
        } else {
            uintptr_t s = align_up(cursor, GRANULARITY);
            uintptr_t e = end & ~(uintptr_t)(GRANULARITY - 1);
            if (s >= cursor && s < e) {
                if (n_free >= max_out) {
                    fprintf(stderr, "FAIL: too many free subranges (%zu)\n", n_free);
                    ++failures;
                    return 0;
                }
                out[n_free++] = (AddrSpan){s, e};
            }
        }
        cursor = end;
    }
    *out_count = n_free;
    *occupied_count = n_occ;
    return 1;
}

static int verify_range_is_free_os(uintptr_t start, uintptr_t end, const char *label) {
    uintptr_t cursor = start;
    while (cursor < end) {
        MEMORY_BASIC_INFORMATION mbi;
        if (!query_mbi(cursor, &mbi, label)) return 0;
        if (mbi.State != MEM_FREE) {
            fprintf(stderr, "FAIL: %s expected MEM_FREE at %p, got state=0x%lx\n",
                    label, (void *)cursor, (unsigned long)mbi.State);
            ++failures;
            return 0;
        }
        uintptr_t mbi_base = (uintptr_t)mbi.BaseAddress;
        if (!mbi.RegionSize || mbi_base > cursor) {
            fprintf(stderr, "FAIL: %s bad MBI geometry at %p\n", label, (void *)cursor);
            ++failures;
            return 0;
        }
        uintptr_t next = (mbi.RegionSize > end - mbi_base) ? end : mbi_base + mbi.RegionSize;
        if (next <= cursor) {
            fprintf(stderr, "FAIL: %s non-advancing MBI at %p\n", label, (void *)cursor);
            ++failures;
            return 0;
        }
        cursor = next;
    }
    return 1;
}

static int create_host_blocker(uintptr_t base, SIZE_T size, HostBlocker *out) {
    memset(out, 0, sizeof(*out));
    if (!verify_range_is_free_os(base, base + size, "pre-blocker MEM_FREE check")) return 0;
    void *mem = VirtualAlloc((void *)base, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (mem != (void *)base) {
        DWORD err = GetLastError();
        if (mem) VirtualFree(mem, 0, MEM_RELEASE);
        return api_failure("VirtualAlloc(host blocker)", err);
    }
    uint64_t *words = (uint64_t *)mem;
    size_t count = size / sizeof(uint64_t);
    for (size_t i = 0; i < count; ++i) words[i] = blocker_canary_for(base, i);
    out->base = base;
    out->size = size;
    out->canary = blocker_canary_for(base, 0);
    out->active = 1;
    return 1;
}

static int verify_host_blocker_intact(const HostBlocker *b, const char *when) {
    if (!b->active) return 0;
    MEMORY_BASIC_INFORMATION mbi;
    if (!query_mbi(b->base, &mbi, when)) return 0;
    if (mbi.State != MEM_COMMIT || mbi.Protect != PAGE_READWRITE ||
        (uintptr_t)mbi.AllocationBase != b->base || mbi.RegionSize < b->size) {
        fprintf(stderr, "FAIL: host blocker at %p altered (%s): state=0x%lx protect=0x%lx size=0x%zx\n",
                (void *)b->base, when, (unsigned long)mbi.State,
                (unsigned long)mbi.Protect, (size_t)mbi.RegionSize);
        ++failures;
        return 0;
    }
    const uint64_t *words = (const uint64_t *)b->base;
    size_t count = b->size / sizeof(uint64_t);
    for (size_t i = 0; i < count; ++i) {
        uint64_t want = blocker_canary_for(b->base, i);
        if (words[i] != want) {
            fprintf(stderr, "FAIL: host blocker canary corrupted at %p+%zu (%s)\n",
                    (void *)b->base, i * sizeof(uint64_t), when);
            ++failures;
            return 0;
        }
    }
    return 1;
}

static int release_host_blocker(HostBlocker *b) {
    if (!b->active) return 1;
    if (!VirtualFree((void *)b->base, 0, MEM_RELEASE)) {
        return api_failure("VirtualFree(host blocker)", GetLastError());
    }
    b->active = 0;
    return verify_range_is_free_os(b->base, b->base + b->size, "post-blocker release MEM_FREE");
}

static int range_overlaps_any_host_region(uintptr_t start, uintptr_t end) {
    for (size_t i = 0; i < region_count; ++i) {
        if (start < regions[i].end && regions[i].start < end) return 1;
    }
    return 0;
}

static int range_overlaps_any_vma(uintptr_t start, uintptr_t end) {
    for (size_t i = 0; i < vma_count; ++i) {
        if (start < vmas[i].end && vmas[i].start < end) return 1;
    }
    return 0;
}

/* Verifies that regions[] matches expected[] 1-to-1 and that every entry is backed by a real
 * OS PAGE_NOACCESS placeholder. */
static int verify_initial_reservations(const AddrSpan *expected, size_t expected_count) {
    if (region_count != expected_count) {
        fprintf(stderr, "FAIL: region_count=%zu expected=%zu\n", region_count, expected_count);
        ++failures;
        return 0;
    }
    int ok = 1;
    for (size_t i = 0; i < expected_count; ++i) {
        const HostRegion *r = &regions[i];
        if (r->start != expected[i].start || r->end != expected[i].end ||
            r->root != expected[i].start || r->mapped || r->phys != 0) {
            fprintf(stderr, "FAIL: regions[%zu]=[%p,%p) root=%p mapped=%d expected=[%p,%p)\n",
                    i, (void *)r->start, (void *)r->end, (void *)r->root, r->mapped,
                    (void *)expected[i].start, (void *)expected[i].end);
            ++failures;
            ok = 0;
            continue;
        }
        if (i > 0 && regions[i - 1].end >= r->start) {
            fprintf(stderr, "FAIL: adjacent initial reservations [%zu] and [%zu] were not separated\n",
                    i - 1, i);
            ++failures;
            ok = 0;
        }
        MEMORY_BASIC_INFORMATION mbi;
        if (!query_mbi(r->start, &mbi, "VirtualQuery(reserved subrange)")) {
            ok = 0;
            continue;
        }
        if (mbi.State != MEM_RESERVE || mbi.AllocationProtect != PAGE_NOACCESS ||
            (uintptr_t)mbi.BaseAddress != r->start || mbi.RegionSize != (SIZE_T)(r->end - r->start)) {
            fprintf(stderr, "FAIL: OS placeholder mismatch for regions[%zu]=[%p,%p): base=%p size=0x%zx state=0x%lx alloc_prot=0x%lx\n",
                    i, (void *)r->start, (void *)r->end, mbi.BaseAddress, (size_t)mbi.RegionSize,
                    (unsigned long)mbi.State, (unsigned long)mbi.AllocationProtect);
            ++failures;
            ok = 0;
        }
    }
    return ok;
}

/* Seeds a small 4 MiB pagefile-backed section after runtime_memory_reserve() so tests can call
 * direct_allocate/direct_map/batch_map2 without committing 6 GiB of pagefile in CI. */
static int init_small_backing_pool(void) {
    if (section || backing_base) {
        fprintf(stderr, "FAIL: backing pool already initialized\n");
        ++failures;
        return 0;
    }
    section = CreateFileMapping2(INVALID_HANDLE_VALUE, NULL, FILE_MAP_ALL_ACCESS,
                                 PAGE_EXECUTE_READWRITE, SEC_COMMIT, TEST_POOL_SIZE, NULL, NULL, 0);
    if (!section) return api_failure("CreateFileMapping2(TEST_POOL_SIZE)", GetLastError());
    backing_base = MapViewOfFile3(section, GetCurrentProcess(), NULL, 0, (SIZE_T)TEST_POOL_SIZE,
                                  0, PAGE_READWRITE, NULL, 0);
    if (!backing_base) {
        DWORD err = GetLastError();
        CloseHandle(section);
        section = NULL;
        return api_failure("MapViewOfFile3(TEST_POOL_SIZE backing)", err);
    }
    for (uint64_t off = 0; off < TEST_POOL_SIZE; off += PAGE) {
        uint64_t m = marker_for(off);
        memcpy(backing_base + off, &m, sizeof(m));
    }
    return 1;
}

static int cleanup_runtime_state(const AddrSpan *expected, size_t expected_count) {
    int ok = 1;
    free(vmas);
    vmas = NULL;
    vma_count = vma_capacity = 0;
    memset(blocks, 0, sizeof(blocks));
    memset(flex_bitmap, 0, sizeof(flex_bitmap));
    live_bytes = flexible_bytes = 0;

    if (release_host_regions() != 0 || region_count != 0) {
        fprintf(stderr, "FAIL: release_host_regions failed (remaining region_count=%zu)\n", region_count);
        ++failures;
        ok = 0;
    }
    if (backing_base) {
        if (!UnmapViewOfFile(backing_base)) {
            api_failure("UnmapViewOfFile(test backing_base)", GetLastError());
            ok = 0;
        }
        backing_base = NULL;
    }
    if (section) {
        if (!CloseHandle(section)) {
            api_failure("CloseHandle(test section)", GetLastError());
            ok = 0;
        }
        section = NULL;
    }
    for (size_t i = 0; i < expected_count; ++i) {
        if (!verify_range_is_free_os(expected[i].start, expected[i].end,
                                     "VirtualQuery(subrange after cleanup)")) {
            ok = 0;
        }
    }
    return ok;
}

static int check_markers_step(uintptr_t address, uint64_t length, uint64_t phys,
                              uint64_t step, const char *label) {
    int ok = 1;
    for (uint64_t local = 0; local < length; local += step) {
        uint64_t got = 0;
        memcpy(&got, (const void *)(address + local), sizeof(got));
        uint64_t expected = marker_for(phys + local);
        if (got != expected) {
            fprintf(stderr, "FAIL: %s marker at guest+0x%" PRIx64
                    " (backing+0x%" PRIx64 "): got 0x%" PRIx64 " expected 0x%" PRIx64 "\n",
                    label, local, phys + local, got, expected);
            ++failures;
            ok = 0;
        }
    }
    return ok;
}

/* ---- CRIT-05 child 1: no-collision full-span reservation ---- */
static int reserve_no_collision_child(void) {
    printf("CRIT05_CASE: no-collision reservation\n");
    AddrSpan expected[512];
    size_t expected_count = 0, preexisting_occupied = 0;
    if (!scan_expected_subranges(expected, 512, &expected_count, &preexisting_occupied)) return 10;
    printf("NO_COLLISION_PRECHECK free_subranges=%zu preexisting_occupied=%zu\n",
           expected_count, preexisting_occupied);

    runtime_memory_reserve();
    check(vma_count == 0, "no guest VMAs exist immediately after runtime_memory_reserve");
    check(verify_initial_reservations(expected, expected_count),
          "runtime_memory_reserve matches OS free subranges and creates real placeholders");
    if (preexisting_occupied == 0) {
        check(region_count == 1 && regions[0].start == USER_MIN && regions[0].end == USER_MAX,
              "clean address space reserves the full 944 GiB [USER_MIN, USER_MAX) in one placeholder");
    }
    /* Idempotency: a second call must keep the exact same reservation table. */
    runtime_memory_reserve();
    check(region_count == expected_count, "second runtime_memory_reserve call is a no-op");

    if (!init_small_backing_pool()) {
        cleanup_runtime_state(expected, expected_count);
        return 11;
    }
    int64_t phys = -1;
    check(direct_allocate(0, (int64_t)TEST_POOL_SIZE, 2 * GRANULARITY, GRANULARITY, 3, &phys) == 0 &&
          phys == 0,
          "direct_allocate allocates 128 KiB from direct pool");
    /* Re-write markers because direct_allocate leaves initial zeroed/seeded bytes intact; seed
     * deterministic markers after allocation so direct_release zeroing is observable too. */
    for (uint64_t off = 0; off < 2 * GRANULARITY; off += PAGE) {
        uint64_t m = marker_for(off);
        memcpy(backing_base + off, &m, sizeof(m));
    }

    void *first_view = NULL;
    check(direct_map(&first_view, GRANULARITY, 3, 0, (int64_t)(phys + GRANULARITY), GRANULARITY) == 0 &&
          (uintptr_t)first_view == expected[0].start,
          "non-fixed direct_map places view at the start of the reserved guest range");
    void *alias_view = (void *)(expected[0].start + 2 * GRANULARITY);
    check(direct_map(&alias_view, GRANULARITY, 3, MAP_FIXED_FLAG,
                     (int64_t)(phys + GRANULARITY), GRANULARITY) == 0 &&
          (uintptr_t)alias_view == expected[0].start + 2 * GRANULARITY,
          "MAP_FIXED direct_map creates second guest alias to same physical offset");
    check(check_markers_step((uintptr_t)first_view, GRANULARITY, GRANULARITY, PAGE, "no-collision-view"),
          "non-zero physical backing offset maps expected markers");
    uint64_t alias_val = UINT64_C(0xfeedfacecafebeef);
    memcpy(first_view, &alias_val, sizeof(alias_val));
    uint64_t alias_read = 0;
    memcpy(&alias_read, alias_view, sizeof(alias_read));
    check(alias_read == alias_val, "writes through one guest view are visible through its section alias");

    VirtualQueryInfo vq;
    check(virtual_query(first_view, 0, &vq, sizeof(vq)) == 0 &&
          vq.start == (uintptr_t)first_view && vq.end == (uintptr_t)first_view + GRANULARITY &&
          vq.offset == GRANULARITY && strcmp(vq.name, "direct") == 0,
          "sceKernelVirtualQuery reports exact guest VMA bounds and physical offset");
    check(direct_unmap(alias_view, GRANULARITY) == 0, "direct_unmap unmaps alias view");
    check(direct_release((uint64_t)phys, 2 * GRANULARITY) == 0 && vma_count == 0,
          "direct_release unmaps remaining direct VMA and clears VMA table");

    int cleaned = cleanup_runtime_state(expected, expected_count);
    check(cleaned, "no-collision reservations and section handles cleaned up to MEM_FREE");
    printf("NO_COLLISION_SUMMARY regions=%zu preexisting_occupied=%zu cleanup=%s\n",
           expected_count, preexisting_occupied, cleaned ? "PASS" : "FAIL");
    return failures || !cleaned ? 1 : 0;
}

/* ---- CRIT-05 child 2: deliberate blocker at USER_MIN (PR #7 collision scenario) ---- */
static int reserve_low_blocker_child(void) {
    printf("CRIT05_CASE: low-boundary blocker recovery at USER_MIN\n");
    HostBlocker blocker;
    if (!create_host_blocker(USER_MIN, (SIZE_T)GRANULARITY, &blocker)) return 20;
    printf("COLLISION_BLOCKER=RESERVED base=%p size=0x%zx\n",
           (void *)blocker.base, (size_t)blocker.size);

    /* Confirm the monolithic 944 GiB VirtualAlloc2 request still fails with 487. */
    SetLastError(ERROR_SUCCESS);
    void *probe = VirtualAlloc2(NULL, (void *)(uintptr_t)USER_MIN, (SIZE_T)(USER_MAX - USER_MIN),
                                MEM_RESERVE | MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS, NULL, 0);
    DWORD probe_error = probe ? ERROR_SUCCESS : GetLastError();
    if (probe) VirtualFree(probe, 0, MEM_RELEASE);
    printf("COLLISION_PROBE result=%s error=%lu expected=%lu\n",
           probe ? "non-null" : "NULL", (unsigned long)probe_error,
           (unsigned long)ERROR_INVALID_ADDRESS);
    check(probe == NULL && probe_error == ERROR_INVALID_ADDRESS,
          "monolithic 944 GiB VirtualAlloc2 probe still fails with ERROR_INVALID_ADDRESS (487)");

    AddrSpan expected[512];
    size_t expected_count = 0, occupied_count = 0;
    if (!scan_expected_subranges(expected, 512, &expected_count, &occupied_count)) {
        release_host_blocker(&blocker);
        return 21;
    }

    /* Production runtime_memory_reserve() must now succeed by reserving available subranges. */
    runtime_memory_reserve();
    check(expected_count >= 1 && verify_initial_reservations(expected, expected_count),
          "runtime_memory_reserve recovers from low-base collision and reserves free subranges");
    check(regions[0].start == USER_MIN + GRANULARITY,
          "first reserved subrange begins immediately after the 64 KiB blocker at USER_MIN");
    check(!range_overlaps_any_host_region(blocker.base, blocker.base + blocker.size),
          "host-owned blocker at USER_MIN is absent from HostRegion table");
    check(vma_count == 0 && !range_overlaps_any_vma(blocker.base, blocker.base + blocker.size),
          "host-owned blocker at USER_MIN is never recorded as a guest VMA");

    VirtualQueryInfo vq;
    check(virtual_query((const void *)(uintptr_t)USER_MIN, 0, &vq, sizeof(vq)) == ACCESS,
          "sceKernelVirtualQuery rejects host-owned blocker at USER_MIN with ACCESS");
    void *qp_s = NULL, *qp_e = NULL;
    uint32_t qp_prot = 0;
    check(query_protection((void *)(uintptr_t)USER_MIN, &qp_s, &qp_e, &qp_prot) == ACCESS,
          "sceKernelQueryMemoryProtection rejects host-owned blocker at USER_MIN with ACCESS");

    if (!init_small_backing_pool()) {
        cleanup_runtime_state(expected, expected_count);
        release_host_blocker(&blocker);
        return 22;
    }
    int64_t phys = -1;
    check(direct_allocate(0, (int64_t)TEST_POOL_SIZE, 2 * GRANULARITY, GRANULARITY, 1, &phys) == 0,
          "direct_allocate succeeds after degraded-capacity reservation");
    for (uint64_t off = 0; off < 2 * GRANULARITY; off += PAGE) {
        uint64_t m = marker_for(off);
        memcpy(backing_base + off, &m, sizeof(m));
    }

    /* Non-fixed guest mapping skips USER_MIN and lands at USER_MIN + 64 KiB. */
    void *guest = NULL;
    check(direct_map(&guest, GRANULARITY, 3, 0, phys, GRANULARITY) == 0 &&
          (uintptr_t)guest == USER_MIN + GRANULARITY,
          "non-fixed direct_map skips blocked USER_MIN and maps at USER_MIN + 64 KiB");

    size_t regions_before = region_count, vmas_before = vma_count;
    /* MAP_FIXED directly on the blocked range fails cleanly without exit(21) or table mutation. */
    void *fixed_blocked = (void *)(uintptr_t)USER_MIN;
    check(direct_map(&fixed_blocked, GRANULARITY, 3, MAP_FIXED_FLAG, phys, GRANULARITY) == NO_MEMORY,
          "MAP_FIXED direct_map on blocked USER_MIN returns NO_MEMORY");
    void *res_blocked = (void *)(uintptr_t)USER_MIN;
    check(reserve_range(&res_blocked, GRANULARITY, MAP_FIXED_FLAG, GRANULARITY) == NO_MEMORY,
          "MAP_FIXED sceKernelReserveVirtualRange on blocked USER_MIN returns NO_MEMORY");
    /* MAP_FIXED crossing from blocked USER_MIN into reserved USER_MIN + 64 KiB also fails cleanly. */
    void *cross_low = (void *)(uintptr_t)USER_MIN;
    check(direct_map(&cross_low, 2 * GRANULARITY, 3, MAP_FIXED_FLAG, phys, GRANULARITY) == NO_MEMORY,
          "MAP_FIXED direct_map crossing from blocked USER_MIN into reserved subrange returns NO_MEMORY");
    check(region_count == regions_before && vma_count == vmas_before &&
          vmas[0].start == USER_MIN + GRANULARITY && vmas[0].end == USER_MIN + 2 * GRANULARITY,
          "failed blocked/crossing MAP_FIXED calls leave HostRegion and VMA tables unchanged");
    check(check_markers_step((uintptr_t)guest, GRANULARITY, 0, PAGE, "low-boundary-survivor"),
          "adjacent mapped guest view at USER_MIN + 64 KiB remains intact after failed crossing map");
    check(verify_host_blocker_intact(&blocker, "after low-blocker guest operations"),
          "host-owned blocker memory and protection at USER_MIN remain untouched");

    check(direct_unmap(guest, GRANULARITY) == 0 && vma_count == 0,
          "direct_unmap releases adjacent view at USER_MIN + 64 KiB");
    int cleaned = cleanup_runtime_state(expected, expected_count);
    check(verify_host_blocker_intact(&blocker, "after runtime cleanup"),
          "runtime cleanup leaves host-owned blocker at USER_MIN intact");
    int blocker_freed = release_host_blocker(&blocker);
    check(cleaned && blocker_freed, "low-blocker child cleaned up all reservations and blocker");
    return failures || !cleaned || !blocker_freed ? 1 : 0;
}

/* ---- CRIT-05 child 3: non-64KiB-aligned middle blocker + boundary operations + rollback ---- */
static int reserve_middle_blocker_child(void) {
    printf("CRIT05_CASE: middle blocker (84 KiB unaligned) and boundary semantics\n");
    if (!verify_range_is_free_os(MID_BLOCKER_BASE - 4 * GRANULARITY,
                                 MID_BLOCKER_ALIGNED_END + 4 * GRANULARITY,
                                 "middle test window precheck")) {
        return 30;
    }
    HostBlocker blocker;
    if (!create_host_blocker(MID_BLOCKER_BASE, (SIZE_T)MID_BLOCKER_SIZE, &blocker)) return 31;

    AddrSpan expected[512];
    size_t expected_count = 0, occupied_count = 0;
    if (!scan_expected_subranges(expected, 512, &expected_count, &occupied_count)) {
        release_host_blocker(&blocker);
        return 32;
    }

    runtime_memory_reserve();
    check(expected_count >= 2 && verify_initial_reservations(expected, expected_count),
          "runtime_memory_reserve splits around middle blocker with 64 KiB granularity alignment");
    check(!range_overlaps_any_host_region(MID_BLOCKER_BASE, MID_BLOCKER_ALIGNED_END) &&
          !range_overlaps_any_vma(MID_BLOCKER_BASE, MID_BLOCKER_ALIGNED_END),
          "middle blocker and its unaligned 64 KiB tail are absent from HostRegion and VMA tables");

    /* Confirm exact boundary split around [MID_BLOCKER_BASE, MID_BLOCKER_ALIGNED_END). */
    size_t left_idx = region_index(MID_BLOCKER_BASE - GRANULARITY);
    size_t right_idx = region_index(MID_BLOCKER_ALIGNED_END);
    check(left_idx < region_count && regions[left_idx].end == MID_BLOCKER_BASE &&
          right_idx == left_idx + 1 && regions[right_idx].start == MID_BLOCKER_ALIGNED_END,
          "adjacent HostRegions end at MID_BLOCKER_BASE and resume at 64 KiB-aligned MID_BLOCKER_ALIGNED_END");

    if (!init_small_backing_pool()) {
        cleanup_runtime_state(expected, expected_count);
        release_host_blocker(&blocker);
        return 33;
    }
    int64_t phys = -1;
    check(direct_allocate(0, (int64_t)TEST_POOL_SIZE, 16 * GRANULARITY, GRANULARITY, 2, &phys) == 0,
          "direct_allocate succeeds for middle-boundary views");
    for (uint64_t off = 0; off < 16 * GRANULARITY; off += PAGE) {
        uint64_t m = marker_for(off);
        memcpy(backing_base + off, &m, sizeof(m));
    }

    uintptr_t left_addr = MID_BLOCKER_BASE - 2 * GRANULARITY;
    uintptr_t right_addr = MID_BLOCKER_ALIGNED_END;
    uint64_t left_phys = GRANULARITY;
    uint64_t right_phys = 3 * GRANULARITY;
    void *left_ptr = (void *)left_addr;
    void *right_ptr = (void *)right_addr;
    check(direct_map(&left_ptr, 2 * GRANULARITY, 3, MAP_FIXED_FLAG, (int64_t)left_phys, GRANULARITY) == 0,
          "MAP_FIXED direct_map succeeds immediately below middle hole [MID-128KiB, MID)");
    check(direct_map(&right_ptr, 2 * GRANULARITY, 3, MAP_FIXED_FLAG, (int64_t)right_phys, GRANULARITY) == 0,
          "MAP_FIXED direct_map succeeds immediately above middle hole [MID_END, MID_END+128KiB)");
    check(check_markers_step(left_addr, 2 * GRANULARITY, left_phys, PAGE, "mid-left") &&
          check_markers_step(right_addr, 2 * GRANULARITY, right_phys, PAGE, "mid-right"),
          "both boundary views preserve distinct non-zero physical backing offsets");

    /* Backing write helper works inside mapped boundary view and refuses across hole. */
    uint64_t patch_word = UINT64_C(0x1122334455667788);
    check(runtime_memory_write_backing(left_addr + PAGE, &patch_word, sizeof(patch_word)) == 1 &&
          *(const uint64_t *)(left_addr + PAGE) == patch_word,
          "runtime_memory_write_backing updates guest view through backing alias");
    uint64_t hole_buf[4] = {1, 2, 3, 4};
    check(runtime_memory_write_backing(MID_BLOCKER_BASE - PAGE, hole_buf, 2 * PAGE) == 0,
          "runtime_memory_write_backing rejects range crossing into middle hole without partial write");
    /* Restore marker at left_addr + PAGE. */
    uint64_t orig_marker = marker_for(left_phys + PAGE);
    memcpy(backing_base + left_phys + PAGE, &orig_marker, sizeof(orig_marker));

    /* Query, clamp, region, and is_mapped behavior across the hole. */
    void *qp_start = NULL, *qp_end = NULL;
    uint32_t qp_prot = 0;
    check(query_protection((void *)left_addr, &qp_start, &qp_end, &qp_prot) == 0 &&
          (uintptr_t)qp_start == left_addr && (uintptr_t)qp_end == MID_BLOCKER_BASE && qp_prot == 3,
          "sceKernelQueryMemoryProtection stops at the hole boundary");
    check(query_protection((void *)MID_BLOCKER_BASE, &qp_start, &qp_end, &qp_prot) == ACCESS,
          "sceKernelQueryMemoryProtection returns ACCESS on the middle hole");

    VirtualQueryInfo vq;
    check(virtual_query((const void *)MID_BLOCKER_BASE, 0, &vq, sizeof(vq)) == ACCESS,
          "exact sceKernelVirtualQuery returns ACCESS on the middle hole");
    check(virtual_query((const void *)MID_BLOCKER_BASE, 1, &vq, sizeof(vq)) == 0 &&
          vq.start == right_addr && vq.end == right_addr + 2 * GRANULARITY && vq.offset == right_phys,
          "VQ_FIND_NEXT skips the middle hole and reports the right-hand guest VMA");
    check(runtime_memory_clamp(left_addr, 6 * GRANULARITY) == 2 * GRANULARITY,
          "runtime_memory_clamp stops at the end of the left VMA before the hole");
    check(runtime_memory_is_mapped(left_addr, 2 * GRANULARITY) == 1 &&
          runtime_memory_is_mapped(right_addr, 2 * GRANULARITY) == 1 &&
          runtime_memory_is_mapped(left_addr, (right_addr + 2 * GRANULARITY) - left_addr) == 0,
          "runtime_memory_is_mapped rejects ranges spanning the middle hole");
    uintptr_t reg_s = 0, reg_e = 0;
    int reg_mapped = -1;
    check(runtime_memory_region(MID_BLOCKER_BASE, &reg_s, &reg_e, &reg_mapped) == 1 &&
          reg_s == MID_BLOCKER_BASE && reg_e == right_addr && reg_mapped == 0,
          "runtime_memory_region reports the hole between VMAs as an unmapped gap");

    /* Protection changes near and across the boundary, plus GPU page tracking. */
    check(kernel_mprotect((const void *)left_addr, (right_addr + GRANULARITY) - left_addr, 1) == INVALID,
          "sceKernelMprotect across the middle hole returns INVALID without touching OS protections");
    check(kernel_mprotect((const void *)left_addr, 2 * GRANULARITY, 1) == 0,
          "sceKernelMprotect to PROT_READ succeeds on left boundary VMA");
    runtime_memory_gpu_protect(right_addr, PAGE, 0, 0);
    MEMORY_BASIC_INFORMATION mbi_left, mbi_gpu;
    if (query_mbi(left_addr, &mbi_left, "VirtualQuery(left PROT_READ)") &&
        query_mbi(right_addr, &mbi_gpu, "VirtualQuery(right GPU PAGE_NOACCESS)")) {
        check(mbi_left.Protect == PAGE_READONLY, "left boundary OS view updated to PAGE_READONLY");
        check(mbi_gpu.Protect == PAGE_NOACCESS,
              "GPU page tracking sets PAGE_NOACCESS on first page above middle hole");
    }
    runtime_memory_gpu_protect(right_addr, PAGE, 1, 1);

    /* Rollback check: MAP_FIXED overwrite crossing the hole while live views exist on both sides. */
    size_t rc_before = region_count, vc_before = vma_count;
    void *cross = (void *)(MID_BLOCKER_BASE - GRANULARITY);
    check(direct_map(&cross, (MID_BLOCKER_ALIGNED_END + GRANULARITY) - (MID_BLOCKER_BASE - GRANULARITY),
                     3, MAP_FIXED_FLAG, (int64_t)(5 * GRANULARITY), GRANULARITY) == NO_MEMORY,
          "MAP_FIXED overwrite crossing middle hole fails with NO_MEMORY");
    check(region_count == rc_before && vma_count == vc_before,
          "failed crossing overwrite leaves HostRegion and VMA counts unchanged");
    if (query_mbi(left_addr, &mbi_left, "VirtualQuery(left after failed crossing overwrite)")) {
        check(mbi_left.State == MEM_COMMIT && mbi_left.Protect == PAGE_READONLY,
              "left boundary view state and PAGE_READONLY protection survive failed crossing overwrite");
    }
    check(check_markers_step(left_addr, 2 * GRANULARITY, left_phys, PAGE, "mid-left-after-rollback") &&
          check_markers_step(right_addr, 2 * GRANULARITY, right_phys, PAGE, "mid-right-after-rollback"),
          "both boundary views retain their exact data and backing offsets after failed crossing overwrite");
    check(verify_host_blocker_intact(&blocker, "after failed crossing overwrite"),
          "middle host blocker remains untouched after failed crossing overwrite");

    /* Unmap spanning across the hole unmaps both guest VMAs without touching the host blocker. */
    check(direct_unmap((void *)left_addr, (right_addr + 2 * GRANULARITY) - left_addr) == 0 &&
          vma_count == 0,
          "sceKernelMunmap spanning middle hole unmaps both guest VMAs cleanly");
    check(verify_host_blocker_intact(&blocker, "after spanning munmap"),
          "spanning sceKernelMunmap does not unmap or alter the middle host blocker");

    int cleaned = cleanup_runtime_state(expected, expected_count);
    check(verify_host_blocker_intact(&blocker, "after middle-blocker runtime cleanup"),
          "middle host blocker remains intact after runtime cleanup");
    int blocker_freed = release_host_blocker(&blocker);
    check(cleaned && blocker_freed, "middle-blocker child cleaned up all reservations and blocker");
    return failures || !cleaned || !blocker_freed ? 1 : 0;
}

/* ---- CRIT-05 child 4: high-boundary blocker at USER_MAX - 64 KiB ---- */
static int reserve_high_blocker_child(void) {
    printf("CRIT05_CASE: high-boundary blocker at USER_MAX - 64 KiB\n");
    uintptr_t high_blocker_base = USER_MAX - GRANULARITY;
    if (!verify_range_is_free_os(USER_MAX - 4 * GRANULARITY, USER_MAX, "high window precheck"))
        return 40;
    HostBlocker blocker;
    if (!create_host_blocker(high_blocker_base, (SIZE_T)GRANULARITY, &blocker)) return 41;

    AddrSpan expected[512];
    size_t expected_count = 0, occupied_count = 0;
    if (!scan_expected_subranges(expected, 512, &expected_count, &occupied_count)) {
        release_host_blocker(&blocker);
        return 42;
    }

    runtime_memory_reserve();
    check(expected_count >= 1 && verify_initial_reservations(expected, expected_count),
          "runtime_memory_reserve reserves up to USER_MAX - 64 KiB when high end is occupied");
    check(regions[region_count - 1].end == high_blocker_base &&
          !range_overlaps_any_host_region(high_blocker_base, USER_MAX) &&
          !range_overlaps_any_vma(high_blocker_base, USER_MAX),
          "high-end host blocker is excluded from HostRegion and VMA tables");

    if (!init_small_backing_pool()) {
        cleanup_runtime_state(expected, expected_count);
        release_host_blocker(&blocker);
        return 43;
    }
    int64_t phys = -1;
    check(direct_allocate(0, (int64_t)TEST_POOL_SIZE, 4 * GRANULARITY, GRANULARITY, 1, &phys) == 0,
          "direct_allocate succeeds for high-boundary test");
    for (uint64_t off = 0; off < 4 * GRANULARITY; off += PAGE) {
        uint64_t m = marker_for(off);
        memcpy(backing_base + off, &m, sizeof(m));
    }

    uintptr_t near_top = high_blocker_base - 2 * GRANULARITY;
    void *top_view = (void *)near_top;
    int32_t top_rc = direct_map(&top_view, 2 * GRANULARITY, 3, MAP_FIXED_FLAG,
                                (int64_t)GRANULARITY, GRANULARITY);
    check(top_rc == 0, "MAP_FIXED direct_map succeeds right up to USER_MAX - 64 KiB");
    if (top_rc == 0) {
        check(check_markers_step(near_top, 2 * GRANULARITY, GRANULARITY, PAGE, "high-boundary"),
              "high-boundary view preserves backing offset and markers");
    }

    void *cross_top = (void *)(high_blocker_base - GRANULARITY);
    check(direct_map(&cross_top, 2 * GRANULARITY, 3, MAP_FIXED_FLAG, 0, GRANULARITY) == NO_MEMORY,
          "MAP_FIXED direct_map crossing into high-end blocker returns NO_MEMORY");
    void *hint_top = (void *)high_blocker_base;
    check(direct_map(&hint_top, GRANULARITY, 3, 0, 0, GRANULARITY) == NO_MEMORY,
          "hinted non-fixed direct_map at blocked USER_MAX - 64 KiB returns NO_MEMORY when exhausted");
    if (top_rc == 0) {
        check(check_markers_step(near_top, 2 * GRANULARITY, GRANULARITY, PAGE, "high-after-rollback"),
              "high-boundary guest view survives failed high-end requests");
    }
    check(verify_host_blocker_intact(&blocker, "after high-boundary operations"),
          "high-end host blocker survives failed high-end requests");

    check(direct_unmap((void *)near_top, 2 * GRANULARITY) == 0 && vma_count == 0,
          "direct_unmap cleans up high-boundary guest view");
    int cleaned = cleanup_runtime_state(expected, expected_count);
    check(verify_host_blocker_intact(&blocker, "after high-blocker runtime cleanup"),
          "high host blocker remains intact after runtime cleanup");
    int blocker_freed = release_host_blocker(&blocker);
    check(cleaned && blocker_freed, "high-blocker child cleaned up all reservations and blocker");
    return failures || !cleaned || !blocker_freed ? 1 : 0;
}

/* ---- CRIT-05 child 5: multiple non-contiguous blockers + pocket skipping + batch_map2 ---- */
static int reserve_multi_blocker_child(void) {
    printf("CRIT05_CASE: multiple non-contiguous blockers and fragmented placement\n");
    if (!verify_range_is_free_os(USER_MIN, USER_MIN + 8 * GRANULARITY, "multi low window") ||
        !verify_range_is_free_os(MULTI_MID_BASE, MULTI_MID_BASE + 4 * GRANULARITY, "multi mid window")) {
        return 50;
    }
    HostBlocker blockers[3];
    memset(blockers, 0, sizeof(blockers));
    /* Blocker 0 at USER_MIN (64 KiB), pocket [USER_MIN+64KiB, USER_MIN+192KiB) = 128 KiB,
     * Blocker 1 at USER_MIN+192KiB (128 KiB), Blocker 2 at MULTI_MID_BASE (64 KiB). */
    if (!create_host_blocker(USER_MIN, (SIZE_T)GRANULARITY, &blockers[0]) ||
        !create_host_blocker(USER_MIN + 3 * GRANULARITY, (SIZE_T)(2 * GRANULARITY), &blockers[1]) ||
        !create_host_blocker(MULTI_MID_BASE, (SIZE_T)GRANULARITY, &blockers[2])) {
        for (int i = 0; i < 3; ++i) release_host_blocker(&blockers[i]);
        return 51;
    }

    AddrSpan expected[512];
    size_t expected_count = 0, occupied_count = 0;
    if (!scan_expected_subranges(expected, 512, &expected_count, &occupied_count)) {
        for (int i = 0; i < 3; ++i) release_host_blocker(&blockers[i]);
        return 52;
    }

    runtime_memory_reserve();
    check(expected_count >= 3 && verify_initial_reservations(expected, expected_count),
          "runtime_memory_reserve reserves all disjoint subranges around 3 non-contiguous blockers");
    check(regions[0].start == USER_MIN + GRANULARITY &&
          regions[0].end == USER_MIN + 3 * GRANULARITY &&
          regions[1].start == USER_MIN + 5 * GRANULARITY,
          "small 128 KiB pocket and subsequent span are accurately represented in HostRegion table");
    for (int i = 0; i < 3; ++i) {
        check(!range_overlaps_any_host_region(blockers[i].base, blockers[i].base + blockers[i].size) &&
              !range_overlaps_any_vma(blockers[i].base, blockers[i].base + blockers[i].size),
              "non-contiguous host blocker is excluded from HostRegion and VMA tables");
    }

    if (!init_small_backing_pool()) {
        cleanup_runtime_state(expected, expected_count);
        for (int i = 0; i < 3; ++i) release_host_blocker(&blockers[i]);
        return 53;
    }
    int64_t phys = -1;
    check(direct_allocate(0, (int64_t)TEST_POOL_SIZE, 16 * GRANULARITY, GRANULARITY, 1, &phys) == 0,
          "direct_allocate succeeds for multi-blocker test");
    for (uint64_t off = 0; off < 16 * GRANULARITY; off += PAGE) {
        uint64_t m = marker_for(off);
        memcpy(backing_base + off, &m, sizeof(m));
    }

    /* 256 KiB non-fixed request does not fit in the 128 KiB pocket; find_free must place it at
     * USER_MIN + 5 * GRANULARITY. */
    void *large = NULL;
    check(direct_map(&large, 4 * GRANULARITY, 3, 0, phys, GRANULARITY) == 0 &&
          (uintptr_t)large == USER_MIN + 5 * GRANULARITY,
          "find_free skips 128 KiB pocket for 256 KiB request and places it in next subrange");

    /* 64 KiB guest reservation fits in the earlier 128 KiB pocket at USER_MIN + 64 KiB. */
    void *pocket_res = NULL;
    check(reserve_range(&pocket_res, GRANULARITY, 0, GRANULARITY) == 0 &&
          (uintptr_t)pocket_res == USER_MIN + GRANULARITY,
          "non-fixed sceKernelReserveVirtualRange uses the 128 KiB pocket at USER_MIN + 64 KiB");
    check(direct_map(&pocket_res, GRANULARITY, 3, MAP_FIXED_FLAG | MAP_NO_OVERWRITE,
                     (int64_t)(4 * GRANULARITY), GRANULARITY) == 0,
          "MAP_FIXED | MAP_NO_OVERWRITE replaces guest KIND_RESERVED VMA in pocket");
    check(direct_map(&pocket_res, GRANULARITY, 3, MAP_FIXED_FLAG | MAP_NO_OVERWRITE,
                     (int64_t)(4 * GRANULARITY), GRANULARITY) == NO_MEMORY,
          "MAP_FIXED | MAP_NO_OVERWRITE rejects overwriting an already-mapped KIND_DIRECT VMA");

    /* batch_map2: entry 0 maps second half of pocket, entry 1 crosses Blocker 1 -> stops at 1. */
    BatchEntry batch[2];
    memset(batch, 0, sizeof(batch));
    batch[0].start = (void *)(USER_MIN + 2 * GRANULARITY);
    batch[0].offset = 5 * GRANULARITY;
    batch[0].length = GRANULARITY;
    batch[0].prot = 3;
    batch[0].operation = 0;
    batch[1].start = (void *)(USER_MIN + 2 * GRANULARITY);
    batch[1].offset = 6 * GRANULARITY;
    batch[1].length = 4 * GRANULARITY; /* crosses Blocker 1 [3*GRAN, 5*GRAN) */
    batch[1].prot = 3;
    batch[1].operation = 0;
    int processed = -1;
    int32_t batch_rc = batch_map2(batch, 2, &processed, MAP_FIXED_FLAG);
    check(batch_rc == NO_MEMORY && processed == 1,
          "sceKernelBatchMap2 stops on hole-crossing entry and reports processed=1");
    if (processed == 1) {
        check(check_markers_step(USER_MIN + 2 * GRANULARITY, GRANULARITY, 5 * GRANULARITY, PAGE,
                                 "batch-entry-0"),
              "completed batch entry 0 remains mapped with intact backing offset after entry 1 fails");
    }

    check(direct_unmap((void *)(USER_MIN + GRANULARITY), 8 * GRANULARITY) == 0 && vma_count == 0,
          "unmap across multiple pockets/blockers clears guest VMAs cleanly");
    int cleaned = cleanup_runtime_state(expected, expected_count);
    int blockers_ok = 1;
    for (int i = 0; i < 3; ++i) {
        if (!verify_host_blocker_intact(&blockers[i], "after multi-blocker cleanup"))
            blockers_ok = 0;
        if (!release_host_blocker(&blockers[i]))
            blockers_ok = 0;
    }
    check(cleaned && blockers_ok,
          "multi-blocker child cleaned up all disjoint reservations and left all 3 blockers intact");
    return failures || !cleaned || !blockers_ok ? 1 : 0;
}

/* ---- CRIT-05 child 6: entire [USER_MIN, USER_MAX) blocked -> explicit fatal failure ---- */
static int reserve_all_blocked_child(void) {
    if (region_count != 0) {
        fprintf(stderr, "SETUP_ERROR: production region table was not empty\n");
        return 70;
    }
    AddrSpan free_spans[512];
    size_t free_count = 0, occupied_count = 0;
    if (!scan_expected_subranges(free_spans, 512, &free_count, &occupied_count) || !free_count) {
        fprintf(stderr, "SETUP_ERROR: no free spans found before full-interval block\n");
        return 71;
    }
    for (size_t i = 0; i < free_count; ++i) {
        void *b = VirtualAlloc((void *)free_spans[i].start,
                               (SIZE_T)(free_spans[i].end - free_spans[i].start),
                               MEM_RESERVE, PAGE_NOACCESS);
        if (b != (void *)free_spans[i].start) {
            fprintf(stderr, "SETUP_ERROR: cannot block span [%p,%p) error=%lu\n",
                    (void *)free_spans[i].start, (void *)free_spans[i].end,
                    (unsigned long)GetLastError());
            return 73;
        }
    }
    printf("ALL_BLOCKED_SPANS=%zu\n", free_count);
    fflush(stdout);

    SetLastError(ERROR_SUCCESS);
    void *probe = VirtualAlloc2(NULL, (void *)(uintptr_t)USER_MIN, (SIZE_T)(USER_MAX - USER_MIN),
                                MEM_RESERVE | MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS, NULL, 0);
    DWORD probe_error = probe ? ERROR_SUCCESS : GetLastError();
    printf("COLLISION_PROBE result=%s error=%lu expected=%lu\n",
           probe ? "non-null" : "NULL", (unsigned long)probe_error,
           (unsigned long)ERROR_INVALID_ADDRESS);
    fflush(stdout);
    if (probe || probe_error != ERROR_INVALID_ADDRESS) return 77;

    SetLastError(ERROR_SUCCESS);
    /* With zero reservable subranges left in [USER_MIN, USER_MAX), runtime_memory_reserve()
     * must invoke the failure hook with region_count=0 and terminate via win_fail (exit 21). */
    runtime_memory_reserve();
    fprintf(stderr, "FAIL: runtime_memory_reserve unexpectedly returned when 100%% of guest space was blocked\n");
    return 78;
}

/* ---- CRIT-06 characterization fixture (preserved from PR #7) ---- */
static int prepare_fixture(void) {
    MEMORY_BASIC_INFORMATION before;
    if (region_count || section || backing_base) {
        fprintf(stderr, "FAIL: fixture process is not in a clean memory-test state\n");
        ++failures;
        return 0;
    }
    if (!query_mbi(FIXTURE_BASE, &before, "VirtualQuery(fixture base before reservation)")) return 0;
    if (before.State != MEM_FREE) {
        fprintf(stderr, "FAIL: fixed fixture base %p is not free before the test (state=0x%lx)\n",
                (void *)FIXTURE_BASE, (unsigned long)before.State);
        ++failures;
        return 0;
    }

    void *placeholder = VirtualAlloc2(NULL, (void *)FIXTURE_BASE, (SIZE_T)FIXTURE_SIZE,
                                      MEM_RESERVE | MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS, NULL, 0);
    if (placeholder != (void *)FIXTURE_BASE) {
        DWORD error = GetLastError();
        if (placeholder) VirtualFree(placeholder, 0, MEM_RELEASE);
        api_failure("VirtualAlloc2(test-only 1 MiB fixture placeholder)", error);
        return 0;
    }
    region_insert(0, (HostRegion){FIXTURE_BASE, FIXTURE_BASE + FIXTURE_SIZE, FIXTURE_BASE, 0, 0});
    check(region_count == 1 && regions[0].start == FIXTURE_BASE &&
          regions[0].end == FIXTURE_BASE + FIXTURE_SIZE &&
          regions[0].root == FIXTURE_BASE && !regions[0].mapped,
          "region table seeded only after the OS placeholder reservation succeeded");

    MEMORY_BASIC_INFORMATION reserved;
    if (query_mbi(FIXTURE_BASE, &reserved, "VirtualQuery(initial placeholder)")) {
        print_mbi("FIXTURE_PLACEHOLDER", &reserved);
        check(reserved.State == MEM_RESERVE, "fixture interval is an actual reserved placeholder");
        check(reserved.AllocationProtect == PAGE_NOACCESS,
              "fixture placeholder was reserved with PAGE_NOACCESS allocation protection");
    }

    section = CreateFileMapping2(INVALID_HANDLE_VALUE, NULL, FILE_MAP_ALL_ACCESS,
                                 PAGE_EXECUTE_READWRITE, SEC_COMMIT, FIXTURE_SIZE, NULL, NULL, 0);
    if (!section) {
        api_failure("CreateFileMapping2(test section)", GetLastError());
        return 0;
    }
    backing_base = MapViewOfFile3(section, GetCurrentProcess(), NULL, 0, (SIZE_T)FIXTURE_SIZE,
                                  0, PAGE_READWRITE, NULL, 0);
    if (!backing_base) {
        api_failure("MapViewOfFile3(test backing alias)", GetLastError());
        return 0;
    }
    for (uint64_t offset = 0; offset < FIXTURE_SIZE; ++offset)
        backing_base[offset] = (unsigned char)((offset * 131u + (offset >> 8) * 17u + 0x5du) & 0xffu);
    for (uint64_t offset = 0; offset < FIXTURE_SIZE; offset += GRANULARITY) {
        uint64_t marker = marker_for(offset);
        memcpy(backing_base + offset, &marker, sizeof(marker));
    }

    if (host_map(FIXTURE_BASE, FIXTURE_SIZE, PROT_READ | PROT_WRITE, 0, 0)) {
        fprintf(stderr, "FAIL: production host_map(test view) returned failure\n");
        ++failures;
        return 0;
    }
    check(region_count == 1 && regions[0].mapped && regions[0].phys == 0,
          "production host_map replaces the placeholder and records the mapped view");
    MEMORY_BASIC_INFORMATION mapped;
    if (query_mbi(FIXTURE_BASE, &mapped, "VirtualQuery(initial section view)")) {
        print_mbi("FIXTURE_INITIAL_VIEW", &mapped);
        check(mapped.State == MEM_COMMIT && mapped.Type == MEM_MAPPED,
              "section view is committed and section-backed");
        check(mapped.Protect == PAGE_READWRITE, "initial view protection is PAGE_READWRITE");
    }

    /* Prove that the backing view and guest-address view alias the same section bytes. */
    uint64_t changed = marker_for(UINT64_C(0x28000)) ^ UINT64_C(0xa55aa55aa55aa55a);
    memcpy(backing_base + UINT64_C(0x28000), &changed, sizeof(changed));
    uint64_t observed = 0;
    memcpy(&observed, (const void *)(FIXTURE_BASE + UINT64_C(0x28000)), sizeof(observed));
    check(observed == changed, "backing alias writes are visible through the mapped guest view");

    if (host_protect(FIXTURE_BASE, FIXTURE_SIZE, PROT_READ)) {
        api_failure("VirtualProtect(test parent view to PAGE_READONLY)", GetLastError());
        return 0;
    }
    /* A distinct GPU-style tracked page makes save/restore verify multiple protection runs. */
    if (host_protect(FIXTURE_BASE + UINT64_C(0x14000), PAGE, PROT_NONE)) {
        api_failure("VirtualProtect(test tracked page to PAGE_NOACCESS)", GetLastError());
        return 0;
    }
    return 1;
}

static int cleanup_fixture(void) {
    int ok = 1;
    if (release_host_regions() != 0 || region_count != 0) {
        fprintf(stderr, "FAIL: release_host_regions(fixture) failed\n");
        ++failures;
        ok = 0;
    }
    if (backing_base) {
        if (!UnmapViewOfFile(backing_base)) {
            fprintf(stderr, "FAIL: UnmapViewOfFile(backing alias) error %lu\n",
                    (unsigned long)GetLastError());
            ++failures;
            ok = 0;
        }
        backing_base = NULL;
    }
    if (section) {
        if (!CloseHandle(section)) {
            fprintf(stderr, "FAIL: CloseHandle(section) error %lu\n",
                    (unsigned long)GetLastError());
            ++failures;
            ok = 0;
        }
        section = NULL;
    }

    MEMORY_BASIC_INFORMATION released;
    if (ok && query_mbi(FIXTURE_BASE, &released, "VirtualQuery(after fixture cleanup)")) {
        check(released.State == MEM_FREE, "cleanup released the placeholder reservation");
        if (released.State != MEM_FREE) ok = 0;
    } else {
        ok = 0;
    }
    if (ok) printf("FIXTURE_CLEANUP=PASS section_handle=closed backing_view=unmapped placeholder=released\n");
    return ok;
}

static int test_fixture_child(void) {
    printf("PHASE2: section-backed partial carve fixture\n");
    hook_mode = HOOK_NONE;
    if (!prepare_fixture()) {
        cleanup_fixture();
        return 10;
    }

    uintptr_t carve_start = FIXTURE_BASE + CARVE_START_OFF;
    uintptr_t carve_end = FIXTURE_BASE + CARVE_END_OFF;
    size_t index = SIZE_MAX;
    write_lock();
    int carve_rc = carve_host(carve_start, carve_end, &index);
    write_unlock();
    check(carve_rc == 0 && index == 1, "carve_host returns the central placeholder region index");
    check(region_count == 3, "region table has left view, carved placeholder and right view");
    if (region_count == 3) {
        check(regions[0].start == FIXTURE_BASE && regions[0].end == carve_start &&
              regions[0].mapped && regions[0].phys == 0,
              "left table entry retains its original section offset");
        check(regions[1].start == carve_start && regions[1].end == carve_end &&
              !regions[1].mapped,
              "carved table entry is exactly the requested placeholder range");
        check(regions[2].start == carve_end && regions[2].end == FIXTURE_BASE + FIXTURE_SIZE &&
              regions[2].mapped && regions[2].phys == CARVE_END_OFF,
              "right table entry advances its backing offset by the carved prefix");
    }

    MEMORY_BASIC_INFORMATION left, middle, right, tracked;
    int ql = query_mbi(FIXTURE_BASE + PAGE, &left, "VirtualQuery(left after carve)");
    int qm = query_mbi(carve_start + PAGE, &middle, "VirtualQuery(carved placeholder)");
    int qr = query_mbi(FIXTURE_BASE + FIXTURE_SIZE - PAGE, &right, "VirtualQuery(right after carve)");
    int qt = query_mbi(FIXTURE_BASE + UINT64_C(0x14000), &tracked,
                       "VirtualQuery(tracked PAGE_NOACCESS page after carve)");
    if (ql) print_mbi("CARVE_LEFT", &left);
    if (qm) print_mbi("CARVE_MIDDLE", &middle);
    if (qr) print_mbi("CARVE_RIGHT", &right);
    if (qt) print_mbi("CARVE_TRACKED_NOACCESS", &tracked);
    if (ql) {
        check(left.State == MEM_COMMIT && left.Type == MEM_MAPPED,
              "left side remains a mapped section view");
        check(left.Protect == PAGE_READONLY, "left side protection is restored to PAGE_READONLY");
    }
    if (qm) {
        check(middle.State == MEM_RESERVE && middle.AllocationProtect == PAGE_NOACCESS,
              "carved center remains an unmapped PAGE_NOACCESS placeholder");
    }
    if (qr) {
        check(right.State == MEM_COMMIT && right.Type == MEM_MAPPED,
              "right side remains a mapped section view");
        check(right.Protect == PAGE_READONLY, "right side protection is restored to PAGE_READONLY");
    }
    if (qt) {
        check(tracked.State == MEM_COMMIT && tracked.Protect == PAGE_NOACCESS,
              "per-page GPU-style PAGE_NOACCESS tracking is restored after carve");
    }
    if (region_count == 3) {
        check_markers_step(regions[0].start, regions[0].end - regions[0].start,
                           regions[0].phys, GRANULARITY, "left");
        check_markers_step(regions[2].start, regions[2].end - regions[2].start,
                           regions[2].phys, GRANULARITY, "right");
    }
    printf("PHASE2_RESULT small_carve=0x%" PRIx64
           " parent_view=0x%" PRIx64 " left_offset=0x0 right_offset=0x%" PRIx64 "\n",
           CARVE_END_OFF - CARVE_START_OFF, FIXTURE_SIZE, CARVE_END_OFF);
    int cleaned = cleanup_fixture();
    return failures || !cleaned ? 1 : 0;
}

static int query_pair(uintptr_t lo, uintptr_t hi,
                      MEMORY_BASIC_INFORMATION *left, MEMORY_BASIC_INFORMATION *right) {
    SIZE_T nl = VirtualQuery((const void *)(lo + PAGE), left, sizeof(*left));
    DWORD error = nl ? ERROR_SUCCESS : GetLastError();
    SIZE_T nr = VirtualQuery((const void *)(hi - PAGE), right, sizeof(*right));
    if (!nr && error == ERROR_SUCCESS) error = GetLastError();
    if (!nl || !nr) {
        fprintf(stderr, "FAIL: VirtualQuery at instrumentation point (error %lu)\n",
                (unsigned long)error);
        hook_query_failed = 1;
        ++failures;
        return 0;
    }
    return 1;
}

void runtime_memory_test_after_unmap(uintptr_t lo, uintptr_t a, uintptr_t b, uintptr_t hi) {
    if (hook_mode == HOOK_NONE || hook_mode == HOOK_PROTECTION) return;
    MEMORY_BASIC_INFORMATION left, right;
    if (!query_pair(lo, hi, &left, &right)) {
        printf("GAP_DIRECTLY_OBSERVED=INCONCLUSIVE query_failed=1\n");
    } else {
        int table_still_mapped = region_count == 1 && regions[0].start == lo &&
                                 regions[0].end == hi && regions[0].mapped;
        gap_observed = left.State != MEM_COMMIT && right.State != MEM_COMMIT;
        print_mbi("GAP_LEFT_DURING_UNMAP", &left);
        print_mbi("GAP_RIGHT_DURING_UNMAP", &right);
        printf("GAP_DIRECTLY_OBSERVED=%s left_state=0x%lx right_state=0x%lx"
               " left_protect=0x%lx right_protect=0x%lx table_still_says_mapped=%d"
               " carve=[%p,%p) parent=[%p,%p)\n",
               gap_observed ? "YES" : "NO", (unsigned long)left.State,
               (unsigned long)right.State, (unsigned long)left.Protect,
               (unsigned long)right.Protect, table_still_mapped,
               (void *)a, (void *)b, (void *)lo, (void *)hi);
    }
    fflush(stdout);

    if (hook_mode == HOOK_GAP_CONCURRENT) {
        printf("CONCURRENT_ACCESS_GATE=OPEN target=%p\n", (void *)concurrent_probe_address);
        fflush(stdout);
        if (!SetEvent(concurrent_start_event)) {
            fprintf(stderr, "FAIL: SetEvent(concurrent start) error %lu\n",
                    (unsigned long)GetLastError());
            fflush(stderr);
            ExitProcess(88);
        }
        DWORD wait = WaitForSingleObject(concurrent_done_event, 5000);
        if (wait != WAIT_OBJECT_0) {
            printf("CONCURRENT_ACCESS=INCONCLUSIVE wait_result=0x%lx error=%lu\n",
                   (unsigned long)wait, (unsigned long)GetLastError());
            fflush(stdout);
            ExitProcess(87);
        }
    }
}

void runtime_memory_test_before_protection_restore(uintptr_t lo, uintptr_t a,
                                                    uintptr_t b, uintptr_t hi) {
    if (hook_mode != HOOK_PROTECTION) return;
    MEMORY_BASIC_INFORMATION left, right, tracked;
    if (!query_pair(lo, hi, &left, &right)) {
        printf("TEMP_WRITABLE_PROTECTION=INCONCLUSIVE query_failed=1\n");
        fflush(stdout);
        return;
    }
    /* The GPU-tracked PAGE_NOACCESS page lives at a fixed fixture offset; query it absolutely
     * so repeated carves whose span starts elsewhere are still observed against it. */
    SIZE_T nt = VirtualQuery((const void *)(FIXTURE_BASE + UINT64_C(0x14000)), &tracked, sizeof(tracked));
    if (!nt) {
        fprintf(stderr, "FAIL: VirtualQuery(tracked page before protection restore) error %lu\n",
                (unsigned long)GetLastError());
        hook_query_failed = 1;
        ++failures;
        printf("TEMP_WRITABLE_PROTECTION=INCONCLUSIVE tracked_query_failed=1\n");
        fflush(stdout);
        return;
    }
    /* Accumulate across repeated carves: any single writable reconstruction window fails
     * CRIT-06 protection safety; the marker must never be reset by a later clean carve.
     * Every saved fixture protection is PAGE_READONLY or PAGE_NOACCESS, so PAGE_READWRITE
     * on any committed queried page during reconstruction is itself the violation. */
    if ((left.State == MEM_COMMIT && left.Protect == PAGE_READWRITE) ||
        (right.State == MEM_COMMIT && right.Protect == PAGE_READWRITE) ||
        (tracked.State == MEM_COMMIT && tracked.Protect == PAGE_READWRITE))
        temporary_write_observed = 1;
    ++protection_hook_calls;
    printf("TEMP_WRITABLE_PROTECTION=%s left_state=0x%lx left_protect=0x%lx"
           " tracked_state=0x%lx tracked_protect=0x%lx right_state=0x%lx right_protect=0x%lx"
           " original_expected=PAGE_READONLY+PAGE_NOACCESS carve=[%p,%p) parent=[%p,%p)\n",
           temporary_write_observed ? "OBSERVED" : "BEHAVIOR_NOT_OBSERVED",
           (unsigned long)left.State, (unsigned long)left.Protect,
           (unsigned long)tracked.State, (unsigned long)tracked.Protect,
           (unsigned long)right.State, (unsigned long)right.Protect,
           (void *)a, (void *)b, (void *)lo, (void *)hi);
    fflush(stdout);
}

void runtime_memory_test_reservation_failure(DWORD error, size_t recorded_regions) {
    fprintf(stderr, "TEST_HOOK_RESERVATION_FAILURE error=%lu expected_collision_error=%lu"
            " region_count=%zu\n", (unsigned long)error,
            (unsigned long)ERROR_INVALID_ADDRESS, recorded_regions);
    fflush(stderr);
}

static DWORD WINAPI concurrent_access_worker(void *unused) {
    (void)unused;
    if (!SetEvent(concurrent_ready_event)) return 2;
    if (WaitForSingleObject(concurrent_start_event, INFINITE) != WAIT_OBJECT_0) return 3;
    printf("CONCURRENT_ACCESS_ATTEMPT=YES address=%p\n", (void *)concurrent_probe_address);
    fflush(stdout);
    volatile unsigned char value = *(volatile unsigned char *)concurrent_probe_address;
    InterlockedExchange(&concurrent_value, value);
    printf("CONCURRENT_ACCESS_COMPLETED=YES value=0x%02lx\n",
           (unsigned long)(unsigned char)concurrent_value);
    fflush(stdout);
    SetEvent(concurrent_done_event);
    return 0;
}

static int test_gap_child(int concurrent) {
    printf("PHASE3: exact post-unmap/pre-reconstruction VirtualQuery observation\n");
    hook_mode = concurrent ? HOOK_GAP_CONCURRENT : HOOK_GAP;
    hook_query_failed = gap_observed = 0;
    if (!prepare_fixture()) {
        cleanup_fixture();
        return 10;
    }
    if (concurrent) {
        SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
        concurrent_start_event = CreateEventA(NULL, TRUE, FALSE, NULL);
        concurrent_ready_event = CreateEventA(NULL, TRUE, FALSE, NULL);
        concurrent_done_event = CreateEventA(NULL, TRUE, FALSE, NULL);
        if (!concurrent_start_event || !concurrent_ready_event || !concurrent_done_event) {
            api_failure("CreateEventA(concurrent access gates)", GetLastError());
            cleanup_fixture();
            return 12;
        }
        concurrent_probe_address = FIXTURE_BASE + PAGE;
        concurrent_value = 0;
        concurrent_thread = CreateThread(NULL, 0, concurrent_access_worker, NULL, 0, NULL);
        if (!concurrent_thread) {
            api_failure("CreateThread(concurrent access probe)", GetLastError());
            cleanup_fixture();
            return 13;
        }
        if (WaitForSingleObject(concurrent_ready_event, 5000) != WAIT_OBJECT_0) {
            fprintf(stderr, "FAIL: concurrent worker did not reach its gate\n");
            ++failures;
            cleanup_fixture();
            return 14;
        }
    }

    uintptr_t a = FIXTURE_BASE + CARVE_START_OFF;
    uintptr_t b = FIXTURE_BASE + CARVE_END_OFF;
    size_t index = SIZE_MAX;
    write_lock();
    int carve_rc = carve_host(a, b, &index);
    write_unlock();
    check(carve_rc == 0 && index == 1 && region_count == 3,
          "gap observation carve completed and region table was split");
    if (concurrent) {
        if (WaitForSingleObject(concurrent_thread, 5000) != WAIT_OBJECT_0) {
            fprintf(stderr, "FAIL: concurrent access worker did not complete after carve\n");
            ++failures;
        }
        if (concurrent_thread) CloseHandle(concurrent_thread);
        concurrent_thread = NULL;
        if (concurrent_start_event) CloseHandle(concurrent_start_event);
        if (concurrent_ready_event) CloseHandle(concurrent_ready_event);
        if (concurrent_done_event) CloseHandle(concurrent_done_event);
        concurrent_start_event = concurrent_ready_event = concurrent_done_event = NULL;
    }
    if (!gap_observed && !hook_query_failed)
        printf("CRIT06_GAP_RESULT=BEHAVIOR_NOT_OBSERVED\n");
    else if (gap_observed)
        printf("CRIT06_GAP_RESULT=DIRECTLY_OBSERVED\n");
    int cleaned = cleanup_fixture();
    return failures || hook_query_failed || !cleaned ? 1 : 0;
}

static int test_protection_child(void) {
    printf("PHASE3: temporary protection change, independently observed\n");
    hook_mode = HOOK_PROTECTION;
    hook_query_failed = temporary_write_observed = protection_hook_calls = 0;
    if (!prepare_fixture()) {
        cleanup_fixture();
        return 10;
    }
    uintptr_t a = FIXTURE_BASE + CARVE_START_OFF;
    uintptr_t b = FIXTURE_BASE + CARVE_END_OFF;
    size_t index = SIZE_MAX;
    write_lock();
    int carve_rc = carve_host(a, b, &index);
    write_unlock();
    check(carve_rc == 0 && index == 1 && region_count == 3,
          "protection observation carve completed and region table was split");
    /* Repeat: carve a second 64 KiB hole out of the already-rebuilt right sibling. A writable
     * reconstruction window must not be able to hide behind a single clean carve. */
    uintptr_t a2 = FIXTURE_BASE + UINT64_C(0x50000);
    uintptr_t b2 = FIXTURE_BASE + UINT64_C(0x60000);
    index = SIZE_MAX;
    write_lock();
    int carve2_rc = carve_host(a2, b2, &index);
    write_unlock();
    check(carve2_rc == 0 && region_count == 5,
          "second carve of a rebuilt sibling completed and region table was split again");
    check(protection_hook_calls >= 2,
          "protection hook observed every reconstruction, not just the first");
    check(!temporary_write_observed,
          "no rebuilt view was ever PAGE_READWRITE during reconstruction (CRIT-06 hazard B)");
    /* Direct PAGE_NOACCESS mapping: host_map on the first hole with PROT_NONE must commit a
     * PAGE_NOACCESS section view straight from view_map, with no writable intermediate. */
    if (host_map(a, b - a, PROT_NONE, 0, CARVE_START_OFF)) {
        fprintf(stderr, "FAIL: production host_map(PROT_NONE over carved hole) returned failure\n");
        ++failures;
    } else {
        MEMORY_BASIC_INFORMATION noaccess;
        if (query_mbi(a + PAGE, &noaccess, "VirtualQuery(direct PAGE_NOACCESS view)"))
            check(noaccess.State == MEM_COMMIT && noaccess.Type == MEM_MAPPED &&
                  noaccess.Protect == PAGE_NOACCESS,
                  "view_map commits a PAGE_NOACCESS section view directly");
    }
    MEMORY_BASIC_INFORMATION left, right, tracked;
    int ql = query_mbi(FIXTURE_BASE + PAGE, &left, "VirtualQuery(left after protection restoration)");
    int qr = query_mbi(FIXTURE_BASE + FIXTURE_SIZE - PAGE, &right,
                       "VirtualQuery(right after protection restoration)");
    int qt = query_mbi(FIXTURE_BASE + UINT64_C(0x14000), &tracked,
                       "VirtualQuery(tracked page after protection restoration)");
    if (ql) check(left.State == MEM_COMMIT && left.Protect == PAGE_READONLY,
                  "left original protection restored after carve");
    if (qr) check(right.State == MEM_COMMIT && right.Protect == PAGE_READONLY,
                  "right original protection restored after carve");
    if (qt) check(tracked.State == MEM_COMMIT && tracked.Protect == PAGE_NOACCESS,
                  "tracked PAGE_NOACCESS restored after the writable reconstruction window");
    if (!temporary_write_observed && !hook_query_failed)
        printf("CRIT06_TEMP_PROTECTION_RESULT=BEHAVIOR_NOT_OBSERVED\n");
    else if (temporary_write_observed)
        printf("CRIT06_TEMP_PROTECTION_RESULT=WRITABLE_WINDOW_OBSERVED\n");
    int cleaned = cleanup_fixture();
    return failures || hook_query_failed || !cleaned ? 1 : 0;
}

static BOOL run_child(const char *argument, DWORD timeout_ms, ChildResult *result) {
    SECURITY_ATTRIBUTES sa;
    memset(&sa, 0, sizeof(sa));
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    HANDLE read_pipe = NULL, write_pipe = NULL;
    if (!CreatePipe(&read_pipe, &write_pipe, &sa, 65536)) {
        fprintf(stderr, "FAIL: CreatePipe error %lu\n", (unsigned long)GetLastError());
        return FALSE;
    }
    SetHandleInformation(read_pipe, HANDLE_FLAG_INHERIT, 0);

    char executable[MAX_PATH * 4];
    DWORD path_len = GetModuleFileNameA(NULL, executable, (DWORD)sizeof(executable));
    if (!path_len || path_len >= sizeof(executable)) {
        fprintf(stderr, "FAIL: GetModuleFileNameA error %lu\n", (unsigned long)GetLastError());
        CloseHandle(read_pipe);
        CloseHandle(write_pipe);
        return FALSE;
    }
    char command_line[MAX_PATH * 4 + 128];
    int command_len = snprintf(command_line, sizeof(command_line), "\"%s\" %s", executable, argument);
    if (command_len < 0 || (size_t)command_len >= sizeof(command_line)) {
        fprintf(stderr, "FAIL: child command line is too long\n");
        CloseHandle(read_pipe);
        CloseHandle(write_pipe);
        return FALSE;
    }

    STARTUPINFOA startup;
    PROCESS_INFORMATION process;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdOutput = write_pipe;
    startup.hStdError = write_pipe;
    startup.hStdInput = write_pipe;
    BOOL created = CreateProcessA(executable, command_line, NULL, NULL, TRUE,
                                  CREATE_NO_WINDOW, NULL, NULL, &startup, &process);
    DWORD create_error = created ? ERROR_SUCCESS : GetLastError();
    CloseHandle(write_pipe);
    if (!created) {
        fprintf(stderr, "FAIL: CreateProcessA(%s) error %lu\n", argument,
                (unsigned long)create_error);
        CloseHandle(read_pipe);
        return FALSE;
    }

    memset(result, 0, sizeof(*result));
    DWORD wait = WaitForSingleObject(process.hProcess, timeout_ms);
    if (wait == WAIT_TIMEOUT) {
        result->timed_out = TRUE;
        TerminateProcess(process.hProcess, CHILD_TIMEOUT_CODE);
        WaitForSingleObject(process.hProcess, INFINITE);
    } else if (wait == WAIT_FAILED) {
        fprintf(stderr, "FAIL: WaitForSingleObject(child) error %lu\n",
                (unsigned long)GetLastError());
        TerminateProcess(process.hProcess, CHILD_TIMEOUT_CODE);
        WaitForSingleObject(process.hProcess, INFINITE);
        result->timed_out = TRUE;
    }
    if (!GetExitCodeProcess(process.hProcess, &result->exit_code)) {
        fprintf(stderr, "FAIL: GetExitCodeProcess error %lu\n", (unsigned long)GetLastError());
        result->timed_out = TRUE;
    }

    size_t used = 0;
    for (;;) {
        char chunk[4096];
        DWORD got = 0;
        if (!ReadFile(read_pipe, chunk, sizeof(chunk), &got, NULL) || !got) break;
        size_t available = sizeof(result->output) - 1 - used;
        size_t take = got < available ? got : available;
        if (take) {
            memcpy(result->output + used, chunk, take);
            used += take;
        }
        if (take != got) result->output_truncated = TRUE;
    }
    result->output[used] = '\0';
    CloseHandle(read_pipe);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return TRUE;
}

static void show_child(const char *label, const ChildResult *result) {
    printf("\n--- %s: child_exit=0x%08lx%s%s ---\n%s%s",
           label, (unsigned long)result->exit_code,
           result->timed_out ? " timed_out" : "",
           result->output_truncated ? " output_truncated" : "",
           result->output,
           result->output[0] && result->output[strlen(result->output) - 1] != '\n' ? "\n" : "");
}

static int run_child_success(const char *label, const char *argument, ChildResult *result) {
    if (!run_child(argument, CHILD_TIMEOUT_MS, result)) {
        ++failures;
        return 0;
    }
    show_child(label, result);
    if (result->timed_out || result->exit_code != 0) {
        fprintf(stderr, "FAIL: %s child failed (exit=0x%08lx)\n", label,
                (unsigned long)result->exit_code);
        ++failures;
        return 0;
    }
    return 1;
}

static int run_crit05_suite(void) {
    int ok = 1;
    ChildResult r;
    memset(&r, 0, sizeof(r));
    r.exit_code = 0xFFFFFFFFUL;

    if (run_child_success("CRIT-05 no-collision reservation", "--child-reserve-no-collision", &r) &&
        strstr(r.output, "NO_COLLISION_SUMMARY") && strstr(r.output, "cleanup=PASS")) {
        printf("RESULT CRIT-05.no_collision=PASS\n");
    } else {
        printf("RESULT CRIT-05.no_collision=FAIL\n");
        if (r.exit_code == 0 && !r.timed_out) ++failures;
        ok = 0;
    }

    if (run_child_success("CRIT-05 low-boundary blocker recovery", "--child-reserve-low-blocker", &r) &&
        strstr(r.output, "COLLISION_PROBE result=NULL error=487")) {
        printf("RESULT CRIT-05.low_blocker=PASS recovered=YES fixed_on_blocker=NO_MEMORY\n");
    } else {
        printf("RESULT CRIT-05.low_blocker=FAIL\n");
        if (r.exit_code == 0 && !r.timed_out) ++failures;
        ok = 0;
    }

    if (run_child_success("CRIT-05 middle blocker and boundaries", "--child-reserve-middle-blocker", &r)) {
        printf("RESULT CRIT-05.middle_blocker=PASS unaligned_blocker=84KiB boundary_rollback=PASS\n");
    } else {
        printf("RESULT CRIT-05.middle_blocker=FAIL\n");
        ok = 0;
    }

    if (run_child_success("CRIT-05 high-boundary blocker", "--child-reserve-high-blocker", &r)) {
        printf("RESULT CRIT-05.high_blocker=PASS\n");
    } else {
        printf("RESULT CRIT-05.high_blocker=FAIL\n");
        ok = 0;
    }

    if (run_child_success("CRIT-05 multiple non-contiguous blockers", "--child-reserve-multi-blocker", &r)) {
        printf("RESULT CRIT-05.multi_blocker=PASS disjoint_subranges=PASS batch_partial_stop=PASS\n");
    } else {
        printf("RESULT CRIT-05.multi_blocker=FAIL\n");
        ok = 0;
    }

    if (!run_child("--child-reserve-all-blocked", CHILD_TIMEOUT_MS, &r)) {
        ++failures;
        printf("RESULT CRIT-05.all_blocked=TEST_INCONCLUSIVE\n");
        ok = 0;
    } else {
        show_child("CRIT-05 completely blocked guest interval (fatal exhaustion)", &r);
        int all_blocked_ok = !r.timed_out && r.exit_code == 21 &&
                             strstr(r.output, "COLLISION_PROBE result=NULL") &&
                             strstr(r.output, "TEST_HOOK_RESERVATION_FAILURE error=487") &&
                             strstr(r.output, "region_count=0") &&
                             strstr(r.output, "STOP: reserving the PS4 address range (no free guest subrange)");
        if (!all_blocked_ok) {
            fprintf(stderr, "FAIL: CRIT-05 full-interval exhaustion did not exit 21 cleanly\n");
            ++failures;
            printf("RESULT CRIT-05.all_blocked=FAIL\n");
            ok = 0;
        } else {
            printf("RESULT CRIT-05.all_blocked=PASS win_fail_exit=21 region_table_entries=0\n");
        }
    }

    if (ok) {
        printf("RESULT CRIT-05=FIXED subrange_reservation=PASS host_holes_excluded_from_vmas=YES"
               " crossing_requests=NO_MEMORY full_exhaustion_exit=21\n");
    } else {
        printf("RESULT CRIT-05=FAIL\n");
    }
    return ok;
}

static int run_fixture_test(void) {
    ChildResult result;
    if (!run_child_success("CRIT-06 section-backed carve", "--child-fixture", &result)) return 0;
    if (!strstr(result.output, "PHASE2_RESULT") || !strstr(result.output, "FIXTURE_CLEANUP=PASS")) {
        fprintf(stderr, "FAIL: fixture child exited successfully without all required result markers\n");
        ++failures;
        return 0;
    }
    printf("RESULT CRIT-06.fixture=PASS small_partial_carve=64KiB parent_view=1MiB"
           " cleanup=PASS\n");
    return 1;
}

static int run_gap_test(void) {
    ChildResult result;
    if (!run_child_success("CRIT-06 direct gap observation", "--child-gap", &result)) {
        printf("RESULT CRIT-06.gap=TEST_INCONCLUSIVE\n");
        return 0;
    }
    if (strstr(result.output, "GAP_DIRECTLY_OBSERVED=YES"))
        printf("RESULT CRIT-06.gap=DIRECTLY_OBSERVED (VirtualQuery sees both sibling views absent before reconstruction)\n");
    else if (strstr(result.output, "GAP_DIRECTLY_OBSERVED=NO"))
        printf("RESULT CRIT-06.gap=BEHAVIOR_NOT_OBSERVED\n");
    else {
        printf("RESULT CRIT-06.gap=TEST_INCONCLUSIVE\n");
        ++failures;
        return 0;
    }
    return 1;
}

static int run_concurrent_test(void) {
    ChildResult result;
    if (!run_child("--child-concurrent-gap", CHILD_TIMEOUT_MS, &result)) {
        printf("RESULT CRIT-06.concurrent_access=TEST_INCONCLUSIVE\n");
        ++failures;
        return 0;
    }
    show_child("CRIT-06 isolated concurrent access", &result);
    if (!result.timed_out && result.exit_code == STATUS_ACCESS_VIOLATION_CODE &&
        strstr(result.output, "CONCURRENT_ACCESS_GATE=OPEN") &&
        strstr(result.output, "CONCURRENT_ACCESS_ATTEMPT=YES")) {
        printf("RESULT CRIT-06.concurrent_access=ACCESS_VIOLATION_REPRODUCED"
               " child_status=0xC0000005 isolated_child=YES\n");
        return 1;
    }
    if (!result.timed_out && result.exit_code == 0 &&
        strstr(result.output, "CONCURRENT_ACCESS_COMPLETED=YES")) {
        printf("RESULT CRIT-06.concurrent_access=NOT_REPRODUCED (probe completed inside the instrumented interval)\n");
        return 1;
    }
    printf("RESULT CRIT-06.concurrent_access=TEST_INCONCLUSIVE child_status=0x%08lx\n",
           (unsigned long)result.exit_code);
    fprintf(stderr, "FAIL: concurrent child neither reproduced nor completed the guarded access\n");
    ++failures;
    return 0;
}

static int run_protection_test(void) {
    ChildResult result;
    memset(&result, 0, sizeof(result));
    int child_ok = run_child_success("CRIT-06 temporary protection window", "--child-protection", &result);
    /* CRIT-06 hazard B is a regression gate, not characterization: a writable reconstruction
     * window at any observed carve fails the suite (the child also exits nonzero). */
    if (strstr(result.output, "TEMP_WRITABLE_PROTECTION=OBSERVED")) {
        printf("RESULT CRIT-06.temporary_protection=WRITABLE_WINDOW_OBSERVED"
               " restored_after_carve=PAGE_READONLY\n");
        if (child_ok) ++failures;
        return 0;
    }
    if (!child_ok) {
        printf("RESULT CRIT-06.temporary_protection=TEST_INCONCLUSIVE\n");
        return 0;
    }
    if (strstr(result.output, "TEMP_WRITABLE_PROTECTION=BEHAVIOR_NOT_OBSERVED") &&
        strstr(result.output, "CRIT06_TEMP_PROTECTION_RESULT=BEHAVIOR_NOT_OBSERVED")) {
        printf("RESULT CRIT-06.temporary_protection=FIXED"
               " (no PAGE_READWRITE reconstruction window across repeated carves;"
               " PAGE_NOACCESS restored; direct PAGE_NOACCESS view_map verified)\n");
        return 1;
    }
    printf("RESULT CRIT-06.temporary_protection=TEST_INCONCLUSIVE\n");
    ++failures;
    return 0;
}

static int child_entry(const char *mode) {
    if (!strcmp(mode, "--child-reserve-no-collision")) return reserve_no_collision_child();
    if (!strcmp(mode, "--child-reserve-low-blocker")) return reserve_low_blocker_child();
    if (!strcmp(mode, "--child-reserve-middle-blocker")) return reserve_middle_blocker_child();
    if (!strcmp(mode, "--child-reserve-high-blocker")) return reserve_high_blocker_child();
    if (!strcmp(mode, "--child-reserve-multi-blocker")) return reserve_multi_blocker_child();
    if (!strcmp(mode, "--child-reserve-all-blocked") ||
        !strcmp(mode, "--child-reservation-collision"))
        return reserve_all_blocked_child();
    if (!strcmp(mode, "--child-fixture")) return test_fixture_child();
    if (!strcmp(mode, "--child-gap")) return test_gap_child(0);
    if (!strcmp(mode, "--child-concurrent-gap")) return test_gap_child(1);
    if (!strcmp(mode, "--child-protection")) return test_protection_child();
    return 2;
}

int main(int argc, char **argv) {
    if (argc == 2) return child_entry(argv[1]);
    if (argc != 1) {
        fprintf(stderr, "usage: %s [--child-{reserve-no-collision,reserve-low-blocker,"
                        "reserve-middle-blocker,reserve-high-blocker,reserve-multi-blocker,"
                        "reserve-all-blocked,fixture,gap,concurrent-gap,protection}]\n",
                argv[0]);
        return 2;
    }

    printf("Windows memory tests: production USER_MIN=0x%" PRIx64
           " USER_MAX=0x%" PRIx64 " interval=0x%" PRIx64 " bytes\n",
           (uint64_t)USER_MIN, (uint64_t)USER_MAX, (uint64_t)(USER_MAX - USER_MIN));
    SYSTEM_INFO system_info;
    GetSystemInfo(&system_info);
    printf("Windows system_page_size=0x%lx allocation_granularity=0x%lx\n",
           (unsigned long)system_info.dwPageSize,
           (unsigned long)system_info.dwAllocationGranularity);
    if (system_info.dwAllocationGranularity != GRANULARITY || !system_info.dwPageSize ||
        (PAGE % system_info.dwPageSize) != 0) {
        fprintf(stderr, "FAIL: Windows page/allocation granularity is incompatible with fixture alignment\n");
        return 1;
    }
    printf("Harness dependencies: Win32 APIs + onecore + C runtime + winpthreads; no GPU/SDL/FFmpeg/runtime build\n");
    run_crit05_suite();
    run_fixture_test();
    run_gap_test();
    run_concurrent_test();
    run_protection_test();
    printf("RESULT SUMMARY failures=%d\n", failures);
    if (failures) {
        fprintf(stderr, "FAIL: Windows memory tests had %d unexpected failure(s)\n", failures);
        return 1;
    }
    printf("PASS: Windows memory CRIT-05 verification, CRIT-06 hazard-B regression gate, and"
           " CRIT-06 hazard-A characterization completed\n");
    return 0;
}
