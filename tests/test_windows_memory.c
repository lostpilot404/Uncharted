/* Windows-native characterization for CRIT-05/CRIT-06.
 *
 * This deliberately includes the production implementation in this one translation unit so
 * the test can call its static host-mapping boundary (carve_host/view_map/host_map) without a
 * copy of those algorithms or the rest of the runtime/GPU dependency graph. The only external
 * project symbols needed by the full translation unit are stubbed below; neither is reached by
 * these tests. BB_WINDOWS_MEMORY_TEST enables narrowly scoped observation callbacks in
 * runtime_memory.c. This is a test-only build and is never defined by release builds.
 */
#define BB_WINDOWS_MEMORY_TEST 1
#include "../src/runtime_memory.c"

#ifndef _WIN32
#error "test_windows_memory.c requires a native Windows target"
#endif

#include <stdint.h>

#define FIXTURE_BASE ((uintptr_t)(USER_MIN + UINT64_C(0x100000000))) /* USER_MIN + 4 GiB */
#define FIXTURE_SIZE UINT64_C(0x00100000)                            /* 1 MiB, not the 944 GiB range */
#define GRANULARITY UINT64_C(0x00010000)                             /* Windows allocation granularity */
#define CARVE_START_OFF UINT64_C(0x00030000)
#define CARVE_END_OFF   UINT64_C(0x00040000)                         /* remove just one 64 KiB unit */
#define CHILD_TIMEOUT_MS 15000
#define STATUS_ACCESS_VIOLATION_CODE 0xC0000005UL
#define CHILD_TIMEOUT_CODE 0xE0000001UL

_Static_assert(sizeof(void *) == 8, "Windows memory characterization requires x64");
_Static_assert((USER_MIN % GRANULARITY) == 0, "production base must be allocation-granularity aligned");
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

static int failures;
static HookMode hook_mode;
static int hook_query_failed;
static int gap_observed;
static int temporary_write_observed;
static HANDLE concurrent_start_event;
static HANDLE concurrent_ready_event;
static HANDLE concurrent_done_event;
static HANDLE concurrent_thread;
static uintptr_t concurrent_probe_address;
static volatile LONG concurrent_value;

/* runtime_memory.c's full translation unit has only these two project-level link dependencies
 * on Windows. Neither is involved in the characterization paths under test. */
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

/* This is test-only seeding: record the small interval only after VirtualAlloc2 really reserved
 * it. The production 944 GiB reservation routine is not used for the carving fixture. */
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
    region_insert(0, (HostRegion){FIXTURE_BASE, FIXTURE_BASE + FIXTURE_SIZE, 0, 0});
    check(region_count == 1 && regions[0].start == FIXTURE_BASE &&
          regions[0].end == FIXTURE_BASE + FIXTURE_SIZE && !regions[0].mapped,
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
    if (regions && region_count) {
        for (size_t i = 0; i < region_count; ++i) {
            if (!regions[i].mapped) continue;
            if (!UnmapViewOfFile2(GetCurrentProcess(), (void *)regions[i].start,
                                  MEM_PRESERVE_PLACEHOLDER)) {
                fprintf(stderr, "FAIL: UnmapViewOfFile2(cleanup %p) error %lu\n",
                        (void *)regions[i].start, (unsigned long)GetLastError());
                ++failures;
                ok = 0;
            } else {
                regions[i].mapped = 0;
            }
        }
        if (ok && region_count > 1) {
            uintptr_t start = regions[0].start;
            uintptr_t end = regions[region_count - 1].end;
            if (!VirtualFree((void *)start, end - start,
                             MEM_RELEASE | MEM_COALESCE_PLACEHOLDERS)) {
                fprintf(stderr, "FAIL: VirtualFree(cleanup coalesce placeholders) error %lu\n",
                        (unsigned long)GetLastError());
                ++failures;
                ok = 0;
            }
        }
        if (ok) {
            uintptr_t start = regions[0].start;
            if (!VirtualFree((void *)start, 0, MEM_RELEASE)) {
                fprintf(stderr, "FAIL: VirtualFree(cleanup release placeholder) error %lu\n",
                        (unsigned long)GetLastError());
                ++failures;
                ok = 0;
            }
        }
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
    free(regions);
    regions = NULL;
    region_count = region_capacity = 0;

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

static int check_markers(uintptr_t address, uint64_t length, uint64_t phys, const char *label) {
    int ok = 1;
    for (uint64_t local = 0; local < length; local += GRANULARITY) {
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

static int test_fixture_child(void) {
    printf("PHASE2: section-backed partial carve fixture\n");
    hook_mode = HOOK_NONE;
    if (!prepare_fixture()) {
        cleanup_fixture();
        return 10;
    }

    uintptr_t carve_start = FIXTURE_BASE + CARVE_START_OFF;
    uintptr_t carve_end = FIXTURE_BASE + CARVE_END_OFF;
    write_lock();
    size_t index = carve_host(carve_start, carve_end);
    write_unlock();
    check(index == 1, "carve_host returns the central placeholder region index");
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
        check_markers(regions[0].start, regions[0].end - regions[0].start,
                      regions[0].phys, "left");
        check_markers(regions[2].start, regions[2].end - regions[2].start,
                      regions[2].phys, "right");
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

/* Called from the production carve_host() immediately after UnmapViewOfFile2 and before
 * placeholder coalescing/splitting or any call that reconstructs a surviving section view. */
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

/* Called by production carve_host() after surviving views are remapped PAGE_READWRITE and
 * immediately before the saved per-run protections are restored. */
void runtime_memory_test_before_protection_restore(uintptr_t lo, uintptr_t a,
                                                    uintptr_t b, uintptr_t hi) {
    if (hook_mode != HOOK_PROTECTION) return;
    MEMORY_BASIC_INFORMATION left, right, tracked;
    if (!query_pair(lo, hi, &left, &right)) {
        printf("TEMP_WRITABLE_PROTECTION=INCONCLUSIVE query_failed=1\n");
        fflush(stdout);
        return;
    }
    SIZE_T nt = VirtualQuery((const void *)(lo + UINT64_C(0x14000)), &tracked, sizeof(tracked));
    if (!nt) {
        fprintf(stderr, "FAIL: VirtualQuery(tracked page before protection restore) error %lu\n",
                (unsigned long)GetLastError());
        hook_query_failed = 1;
        ++failures;
        printf("TEMP_WRITABLE_PROTECTION=INCONCLUSIVE tracked_query_failed=1\n");
        fflush(stdout);
        return;
    }
    temporary_write_observed = left.State == MEM_COMMIT && right.State == MEM_COMMIT &&
                               tracked.State == MEM_COMMIT && left.Protect == PAGE_READWRITE &&
                               right.Protect == PAGE_READWRITE && tracked.Protect == PAGE_READWRITE;
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
    write_lock();
    size_t index = carve_host(a, b);
    write_unlock();
    check(index == 1 && region_count == 3, "gap observation carve completed and region table was split");
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
    hook_query_failed = temporary_write_observed = 0;
    if (!prepare_fixture()) {
        cleanup_fixture();
        return 10;
    }
    uintptr_t a = FIXTURE_BASE + CARVE_START_OFF;
    uintptr_t b = FIXTURE_BASE + CARVE_END_OFF;
    write_lock();
    size_t index = carve_host(a, b);
    write_unlock();
    check(index == 1 && region_count == 3,
          "protection observation carve completed and region table was split");
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

static int reservation_collision_child(void) {
    const SIZE_T blocker_size = (SIZE_T)GRANULARITY;
    if (region_count != 0) {
        fprintf(stderr, "SETUP_ERROR: production region table was not empty\n");
        return 70;
    }
    MEMORY_BASIC_INFORMATION before;
    if (!query_mbi(USER_MIN, &before, "VirtualQuery(fixed collision address before blocker)"))
        return 71;
    if (before.State != MEM_FREE) {
        fprintf(stderr, "SETUP_ERROR: deliberate blocker base %p is not free (state=0x%lx)\n",
                (void *)(uintptr_t)USER_MIN, (unsigned long)before.State);
        return 72;
    }

    void *blocker = VirtualAlloc2(NULL, (void *)(uintptr_t)USER_MIN, blocker_size,
                                  MEM_RESERVE | MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS, NULL, 0);
    if (blocker != (void *)(uintptr_t)USER_MIN) {
        fprintf(stderr, "SETUP_ERROR: cannot reserve fixed collision blocker base=%p error=%lu\n",
                (void *)(uintptr_t)USER_MIN, (unsigned long)GetLastError());
        return 73;
    }
    MEMORY_BASIC_INFORMATION occupied;
    if (!query_mbi(USER_MIN, &occupied, "VirtualQuery(deliberate collision blocker)")) return 74;
    if (occupied.State != MEM_RESERVE) {
        fprintf(stderr, "SETUP_ERROR: blocker is not reserved after VirtualAlloc2 (state=0x%lx)\n",
                (unsigned long)occupied.State);
        return 75;
    }
    printf("COLLISION_BLOCKER=RESERVED base=%p size=0x%zx state=0x%lx\n", blocker,
           blocker_size, (unsigned long)occupied.State);
    fflush(stdout);

    /* A direct probe distinguishes the intended ERROR_INVALID_ADDRESS collision from a
     * low-memory, API-availability, permission or unrelated setup error. */
    SetLastError(ERROR_SUCCESS);
    void *probe = VirtualAlloc2(NULL, (void *)(uintptr_t)USER_MIN, (SIZE_T)(USER_MAX - USER_MIN),
                                MEM_RESERVE | MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS, NULL, 0);
    DWORD probe_error = probe ? ERROR_SUCCESS : GetLastError();
    printf("COLLISION_PROBE result=%s error=%lu expected=%lu\n",
           probe ? "non-null" : "NULL", (unsigned long)probe_error,
           (unsigned long)ERROR_INVALID_ADDRESS);
    fflush(stdout);
    if (probe) {
        /* The child is disposable; release if an unexpected implementation allowed it. */
        VirtualFree((void *)(uintptr_t)USER_MIN, 0, MEM_RELEASE);
        return 76;
    }
    if (probe_error != ERROR_INVALID_ADDRESS) return 77;

    SetLastError(ERROR_SUCCESS);
    /* The production call makes the same 944 GiB VirtualAlloc2 request and reaches win_fail.
     * The test-only failure hook records the OS error and verifies no HostRegion was inserted. */
    runtime_memory_reserve();
    fprintf(stderr, "FAIL: runtime_memory_reserve unexpectedly returned after collision\n");
    return 78;
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
    startup.hStdInput = write_pipe; /* child tests do not read stdin; keep every std handle valid */
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

static int run_reservation_test(void) {
    ChildResult result;
    if (!run_child("--child-reservation-collision", CHILD_TIMEOUT_MS, &result)) {
        ++failures;
        printf("RESULT CRIT-05=TEST_INCONCLUSIVE child_launch_failed=1\n");
        return 0;
    }
    show_child("CRIT-05 deliberate collision", &result);
    int pass = !result.timed_out && result.exit_code == 21 &&
               strstr(result.output, "COLLISION_BLOCKER=RESERVED") &&
               strstr(result.output, "COLLISION_PROBE result=NULL") &&
               strstr(result.output, "expected=487") &&
               strstr(result.output, "TEST_HOOK_RESERVATION_FAILURE error=487") &&
               strstr(result.output, "region_count=0") &&
               strstr(result.output, "STOP: reserving the PS4 address range") &&
               strstr(result.output, "error 487");
    if (!pass) {
        fprintf(stderr, "FAIL: CRIT-05 did not produce the expected deliberate collision (exit=0x%08lx)\n",
                (unsigned long)result.exit_code);
        ++failures;
        printf("RESULT CRIT-05=TEST_INCONCLUSIVE_OR_UNEXPECTED_ERROR\n");
    } else {
        printf("RESULT CRIT-05=COLLISION_REPRODUCED windows_error=487(ERROR_INVALID_ADDRESS)"
               " win_fail_exit=21 region_table_entries=0\n");
    }
    return pass;
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
    if (!run_child_success("CRIT-06 temporary protection window", "--child-protection", &result)) {
        printf("RESULT CRIT-06.temporary_protection=TEST_INCONCLUSIVE\n");
        return 0;
    }
    if (strstr(result.output, "TEMP_WRITABLE_PROTECTION=OBSERVED"))
        printf("RESULT CRIT-06.temporary_protection=WRITABLE_PROTECTION_OBSERVED"
               " restored_after_carve=PAGE_READONLY\n");
    else if (strstr(result.output, "TEMP_WRITABLE_PROTECTION=BEHAVIOR_NOT_OBSERVED"))
        printf("RESULT CRIT-06.temporary_protection=BEHAVIOR_NOT_OBSERVED\n");
    else {
        printf("RESULT CRIT-06.temporary_protection=TEST_INCONCLUSIVE\n");
        ++failures;
        return 0;
    }
    return 1;
}

static int child_entry(const char *mode) {
    if (!strcmp(mode, "--child-reservation-collision")) return reservation_collision_child();
    if (!strcmp(mode, "--child-fixture")) return test_fixture_child();
    if (!strcmp(mode, "--child-gap")) return test_gap_child(0);
    if (!strcmp(mode, "--child-concurrent-gap")) return test_gap_child(1);
    if (!strcmp(mode, "--child-protection")) return test_protection_child();
    return 2;
}

int main(int argc, char **argv) {
    if (argc == 2) return child_entry(argv[1]);
    if (argc != 1) {
        fprintf(stderr, "usage: %s [--child-{reservation-collision,fixture,gap,concurrent-gap,protection}]\n",
                argv[0]);
        return 2;
    }

    printf("Windows memory characterization: production USER_MIN=0x%" PRIx64
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
    printf("Harness dependencies: Win32 APIs + C runtime + winpthreads; no GPU/SDL/FFmpeg/runtime build\n");
    run_reservation_test();
    run_fixture_test();
    run_gap_test();
    run_concurrent_test();
    run_protection_test();
    printf("RESULT SUMMARY failures=%d\n", failures);
    if (failures) {
        fprintf(stderr, "FAIL: Windows memory characterization had %d unexpected failure(s)\n", failures);
        return 1;
    }
    printf("PASS: Windows memory characterization completed; no production fix was applied\n");
    return 0;
}
