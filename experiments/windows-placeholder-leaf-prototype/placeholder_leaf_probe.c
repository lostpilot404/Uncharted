/* Standalone experiment. No Uncharted runtime headers or production fallback.
 * Build with a current Windows SDK and x64 MSVC or clang-cl (C11).
 * See README.md for the intentionally limited proof and evidence requirements. */
#ifndef _WIN32
#error This probe must be built for Windows; a POSIX run cannot qualify it.
#endif
#ifndef _WIN64
#error An x64 build is required.
#endif
#ifndef _M_X64
#error Build with an x64 MSVC-compatible frontend (MSVC or clang-cl).
#endif
#include <windows.h>
#include <memoryapi.h>
#include <winternl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#ifndef PROBE_GIT_SHA
#define PROBE_GIT_SHA "unrecorded"
#endif
#ifndef PROBE_SOURCE_SHA256
#define PROBE_SOURCE_SHA256 "unrecorded"
#endif
#ifndef PROBE_CONFIGURATION
#define PROBE_CONFIGURATION "unrecorded"
#endif
#ifndef PROBE_COMPILER
#define PROBE_COMPILER "unrecorded"
#endif
#ifndef PROBE_TREE_STATE
#define PROBE_TREE_STATE "unrecorded"
#endif
#define G ((SIZE_T)0x4000)
#define MAX_LEAVES 1024u
#define WAIT_MS 30000u
#define READER_PASSES 16u
#define SIMULATED_EXIT 3
#define FAIL_STOP_EXIT 21

/* These are function pointer types, NOT substitute API declarations. _Generic
 * checks them against the installed SDK without emitting static API imports. */
typedef PVOID (WINAPI *Alloc2Fn)(HANDLE, PVOID, SIZE_T, ULONG, ULONG,
                                MEM_EXTENDED_PARAMETER *, ULONG);
typedef PVOID (WINAPI *Map3Fn)(HANDLE, HANDLE, PVOID, ULONG64, SIZE_T, ULONG,
                              ULONG, MEM_EXTENDED_PARAMETER *, ULONG);
typedef BOOL (WINAPI *Unmap2Fn)(HANDLE, PVOID, ULONG);
_Static_assert(_Generic(&VirtualAlloc2, Alloc2Fn: 1, default: 0), "VirtualAlloc2 SDK signature");
_Static_assert(_Generic(&MapViewOfFile3, Map3Fn: 1, default: 0), "MapViewOfFile3 SDK signature");
_Static_assert(_Generic(&UnmapViewOfFile2, Unmap2Fn: 1, default: 0), "UnmapViewOfFile2 SDK signature");
_Static_assert(sizeof(void *) == 8, "64-bit pointers required");

static Alloc2Fn alloc2;
static Map3Fn map3;
static Unmap2Fn unmap2;
static SYSTEM_INFO sysinfo;
static LARGE_INTEGER frequency;
static const char *injection_stage;
static size_t injection_index;

typedef enum { PLACEHOLDER, MAPPED } State;
typedef struct { size_t first, count; State state; } Extent;
typedef struct {
    unsigned char *root, *backing;
    HANDLE section;
    size_t count, extent_count;
    Extent extents[MAX_LEAVES];
    SIZE_T offsets[MAX_LEAVES];
    size_t target_first, target_end;
    size_t operation;
    int injected, uncertain, quiet, patch;
    int non64_verified;
} Probe;
typedef struct {
    Probe *probe;
    size_t leaf;
    HANDLE request, done, ready, thread;
    volatile LONG stop;
    int ok;
    unsigned rounds;
} Reader;

static void fail_stop(const char *reason) {
    printf("FAIL_STOP reason=%s exit=%d cleanup=NOT_ATTEMPTED_ON_UNCERTAIN_LAYOUT\n",
           reason, FAIL_STOP_EXIT);
    fflush(NULL);
    ExitProcess(FAIL_STOP_EXIT);
}
static int check(int condition, const char *property) {
    if (!condition) printf("CHECK=FAIL property=%s\n", property);
    return condition;
}
static FARPROC resolve(const char *name) {
    HMODULE module = GetModuleHandleW(L"kernelbase.dll");
    FARPROC fn = module ? GetProcAddress(module, name) : NULL;
    if (!fn) {
        module = GetModuleHandleW(L"kernel32.dll");
        fn = module ? GetProcAddress(module, name) : NULL;
    }
    if (!fn) printf("UNSUPPORTED missing_api=%s error=%lu\n", name, GetLastError());
    return fn;
}
static int environment(void) {
    typedef LONG (WINAPI *VersionFn)(PRTL_OSVERSIONINFOW);
    VersionFn version_fn = NULL;
    FARPROC fn;
    HMODULE nt = GetModuleHandleW(L"ntdll.dll");
    RTL_OSVERSIONINFOW version;
    MEMORYSTATUSEX memory;
    memset(&version, 0, sizeof(version));
    version.dwOSVersionInfoSize = sizeof(version);
    memset(&memory, 0, sizeof(memory));
    memory.dwLength = sizeof(memory);
    setvbuf(stdout, NULL, _IONBF, 0);
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    printf("SOURCE git_sha=%s tree=%s probe_sha256=%s\n",
           PROBE_GIT_SHA, PROBE_TREE_STATE, PROBE_SOURCE_SHA256);
    printf("BUILD compiler=%s configuration=%s msvc_compat=%d date=%s time=%s\n",
           PROBE_COMPILER, PROBE_CONFIGURATION, _MSC_VER, __DATE__, __TIME__);
#ifdef __clang__
    printf("BUILD clang=%s\n", __clang_version__);
#endif
    if (nt && GetProcAddress(nt, "wine_get_version")) {
        printf("UNSUPPORTED Wine is not native Windows qualification evidence\n");
        return 0;
    }
    fn = nt ? GetProcAddress(nt, "RtlGetVersion") : NULL;
    if (!fn) return check(0, "RtlGetVersion available for exact OS recording");
    _Static_assert(sizeof(fn) == sizeof(version_fn), "function pointer size");
    memcpy(&version_fn, &fn, sizeof(fn));
    if (version_fn(&version) != 0) return check(0, "RtlGetVersion succeeds");
    GetNativeSystemInfo(&sysinfo);
    if (!QueryPerformanceFrequency(&frequency)) return check(0, "QPC available");
    if (!GlobalMemoryStatusEx(&memory)) return check(0, "memory status available");
    printf("OS major=%lu minor=%lu build=%lu native_arch=%u processors=%lu "
           "page_size=0x%lx allocation_granularity=0x%lx physical_bytes=%llu qpc_hz=%lld\n",
           version.dwMajorVersion, version.dwMinorVersion, version.dwBuildNumber,
           sysinfo.wProcessorArchitecture, sysinfo.dwNumberOfProcessors,
           sysinfo.dwPageSize, sysinfo.dwAllocationGranularity,
           (unsigned long long)memory.ullTotalPhys, (long long)frequency.QuadPart);
    if (version.dwMajorVersion < 10 || version.dwBuildNumber < 18362)
        return check(0, "advertised Windows 10 1903+ baseline");
    if (sysinfo.wProcessorArchitecture != PROCESSOR_ARCHITECTURE_AMD64 ||
        !sysinfo.dwPageSize || G % sysinfo.dwPageSize ||
        !sysinfo.dwAllocationGranularity || (4 * G) % sysinfo.dwAllocationGranularity)
        return check(0, "native x64 page/root granularity compatible with 16KiB leaves");
    fn = resolve("VirtualAlloc2");
    if (!fn) return 0;
    _Static_assert(sizeof(fn) == sizeof(alloc2), "function pointer size");
    memcpy(&alloc2, &fn, sizeof(fn));
    fn = resolve("MapViewOfFile3");
    if (!fn) return 0;
    _Static_assert(sizeof(fn) == sizeof(map3), "function pointer size");
    memcpy(&map3, &fn, sizeof(fn));
    fn = resolve("UnmapViewOfFile2");
    if (!fn) return 0;
    _Static_assert(sizeof(fn) == sizeof(unmap2), "function pointer size");
    memcpy(&unmap2, &fn, sizeof(fn));
    return 1;
}
static unsigned char *address(const Probe *p, size_t leaf) { return p->root + leaf * G; }
static size_t extent_at(const Probe *p, size_t leaf) {
    size_t i;
    for (i = 0; i < p->extent_count; ++i)
        if (leaf >= p->extents[i].first && leaf < p->extents[i].first + p->extents[i].count)
            return i;
    fail_stop("ledger does not cover leaf");
    return 0;
}
static int target_contains(const Probe *p, size_t first, size_t count) {
    return first >= p->target_first && first <= p->target_end && count <= p->target_end - first;
}
/* Called BEFORE every split/map/unmap/coalesce. All destructive view operations
 * in phase=carve/remap must be wholly within the declared target. */
static int before(Probe *p, const char *stage, size_t index, size_t first,
                  size_t count, SIZE_T offset, ULONG flags, const char *phase) {
    int target_phase = !strcmp(phase, "carve") || !strcmp(phase, "remap");
    if (!count || first > p->count || count > p->count - first ||
        (target_phase && !target_contains(p, first, count)))
        fail_stop("STRUCTURAL_INVARIANT violated before OS call");
    /* Keep the complete destructive-operation audit even in benchmark mode. */
    {
        printf("OP seq=%zu phase=%s stage=%s index=%zu base=%p size=0x%zx "
               "section_offset=0x%zx flags=0x%lx operand_scope=%s target=[%p,%p) invariant=PASS\n",
               p->operation, phase, stage, index, (void *)address(p, first), count * G,
               offset, (unsigned long)flags,
               target_phase ? "DECLARED_CARVE_TARGET" : "ENTIRE_OWNED_ROOT",
               (void *)address(p, target_phase ? p->target_first : 0),
               (void *)address(p, target_phase ? p->target_end : p->count));
    }
    ++p->operation;
    if (injection_stage && !p->injected && !strcmp(stage, injection_stage) && index == injection_index) {
        p->injected = 1;
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        printf("SIMULATED_PRECALL_FAILURE stage=%s index=%zu real_os_invoked=NO "
               "synthetic_error=%lu\n", stage, index, GetLastError());
        return 0;
    }
    return 1;
}
static int actual_failure(Probe *p, const char *api, DWORD error) {
    p->uncertain = 1;
    printf("REAL_OS_FAILURE api=%s error=%lu real_os_invoked=YES ledger_uncertain=YES\n", api, error);
    /* No guessed rollback after a real mutator failure. Process teardown is the
     * explicit fail-stop, not evidence of a successful recovery. */
    return 0;
}
static unsigned char pattern(const Probe *p, SIZE_T off) {
    SIZE_T interval = off / G, within = off % G;
    /* An interval-index prefix keeps all 1025 bounded backing intervals
     * distinct, including indices that differ by 256. Check every byte. */
    unsigned char value = within < sizeof(interval)
        ? (unsigned char)(interval >> (within * 8))
        : (unsigned char)((interval * 71u + within * 131u +
                           (within >> 8) * 17u + 0x5du) & 255u);
    if (p->patch && off == G + 17) value ^= 0xa5;
    return value;
}
static int read_filter(EXCEPTION_POINTERS *info, const void *base, SIZE_T size) {
    const EXCEPTION_RECORD *r = info->ExceptionRecord;
    uintptr_t at;
    if (r->ExceptionCode != EXCEPTION_ACCESS_VIOLATION || r->NumberParameters < 2 ||
        r->ExceptionInformation[0] != 0) return EXCEPTION_CONTINUE_SEARCH;
    at = (uintptr_t)r->ExceptionInformation[1];
    if (at < (uintptr_t)base || at - (uintptr_t)base >= size) return EXCEPTION_CONTINUE_SEARCH;
    return EXCEPTION_EXECUTE_HANDLER;
}
static int verify_bytes(Probe *p, size_t leaf) {
    volatile const unsigned char *src = address(p, leaf);
    SIZE_T j;
    int ok = 1;
    __try {
        for (j = 0; j < G; ++j) {
            unsigned char expected = pattern(p, p->offsets[leaf] + j);
            if (src[j] != expected || p->backing[p->offsets[leaf] + j] != expected) {
                printf("MARKER=FAIL leaf=%zu byte=0x%zx offset=0x%zx\n", leaf, j, p->offsets[leaf]);
                ok = 0;
                break;
            }
        }
    } __except(read_filter(GetExceptionInformation(), (const void *)src, G)) {
        printf("UNEXPECTED_SIBLING_OR_MAPPED_READ_AV leaf=%zu\n", leaf);
        ok = 0;
    }
    return ok;
}
/* VirtualQuery does not have a 'placeholder' bit. Validate exact allocation
 * boundaries plus state/type, tied to successful operations in our ledger. */
static int verify_extent(Probe *p, size_t ei, int verbose) {
    Extent e = p->extents[ei];
    SIZE_T step;
    for (step = 0; step < e.count * G; step += sysinfo.dwPageSize) {
        MEMORY_BASIC_INFORMATION m;
        unsigned char *base = address(p, e.first);
        SIZE_T got = VirtualQuery(base + step, &m, sizeof(m));
        int good;
        if (!got) {
            printf("QUERY=FAIL base=%p error=%lu\n", (void *)(base + step), GetLastError());
            return 0;
        }
        /* VirtualQuery reports forward from the queried page, not from the
         * beginning of the allocation when queried at an interior page. */
        good = m.AllocationBase == base && m.BaseAddress == base + step &&
               m.RegionSize == e.count * G - step;
        if (e.state == MAPPED)
            good = good && e.count == 1 && m.State == MEM_COMMIT && m.Type == MEM_MAPPED &&
                   m.Protect == PAGE_READWRITE && m.AllocationProtect == PAGE_READWRITE;
        else
            good = good && m.State == MEM_RESERVE && m.Type == MEM_PRIVATE &&
                   m.AllocationProtect == PAGE_NOACCESS && (m.Protect == 0 || m.Protect == PAGE_NOACCESS);
        if (!good || (verbose && step == 0))
            printf("QUERY=%s extent=%zu first=%zu count=%zu expected=%s base=%p "
                   "allocation_base=%p region_base=%p region_size=0x%zx state=0x%lx "
                   "type=0x%lx protect=0x%lx allocation_protect=0x%lx\n",
                   good ? "PASS" : "FAIL", ei, e.first, e.count,
                   e.state == MAPPED ? "MAPPED_VIEW" : "PLACEHOLDER_BY_LEDGER",
                   (void *)(base + step), m.AllocationBase, m.BaseAddress, (SIZE_T)m.RegionSize,
                   m.State, m.Type, m.Protect, m.AllocationProtect);
        if (!good) return 0;
    }
    return 1;
}
static int verify_leaf(Probe *p, size_t leaf, int verbose) {
    size_t ei = extent_at(p, leaf);
    return verify_extent(p, ei, verbose) &&
           (p->extents[ei].state != MAPPED || verify_bytes(p, leaf));
}
static int snapshot(Probe *p, const char *label, int verbose) {
    size_t i, next = 0;
    int ok = 1;
    if (verbose) printf("SNAPSHOT label=%s extent_count=%zu leaf_count=%zu\n", label, p->extent_count, p->count);
    for (i = 0; i < p->extent_count; ++i) {
        Extent e = p->extents[i];
        if (e.first != next || !e.count || e.count > p->count - next) fail_stop("invalid extent partition");
        next += e.count;
        if (verbose) printf("LEDGER extent=%zu first=%zu count=%zu state=%s base=%p size=0x%zx "
                            "section_offset_if_mapped=0x%zx\n", i, e.first, e.count,
                            e.state == MAPPED ? "MAPPED" : "PLACEHOLDER",
                            (void *)address(p, e.first), e.count * G, p->offsets[e.first]);
        if (!verify_extent(p, i, verbose)) ok = 0;
        if (e.state == MAPPED && !verify_bytes(p, e.first)) ok = 0;
    }
    if (next != p->count) fail_stop("incomplete extent partition");
    return ok;
}
static int reserve_root(Probe *p, size_t count, int quiet) {
    memset(p, 0, sizeof(*p));
    p->count = count;
    p->target_end = count;
    p->quiet = quiet;
    if (count < 4 || count > MAX_LEAVES || (count * G) % sysinfo.dwAllocationGranularity)
        return check(0, "bounded root aligned to allocation granularity");
    printf("RESERVE_REQUEST size=0x%zx flags=0x%lx protection=PAGE_NOACCESS\n",
           count * G, (unsigned long)(MEM_RESERVE | MEM_RESERVE_PLACEHOLDER));
    p->root = alloc2(NULL, NULL, count * G, MEM_RESERVE | MEM_RESERVE_PLACEHOLDER,
                     PAGE_NOACCESS, NULL, 0);
    if (!p->root) {
        printf("REAL_OS_FAILURE api=VirtualAlloc2 error=%lu real_os_invoked=YES\n", GetLastError());
        return 0;
    }
    if ((uintptr_t)p->root % sysinfo.dwAllocationGranularity)
        fail_stop("reserved root does not meet allocation granularity");
    p->extent_count = 1;
    p->extents[0] = (Extent){0, count, PLACEHOLDER};
    printf("RESERVED base=%p size=0x%zx\n", (void *)p->root, count * G);
    if (!verify_extent(p, 0, !quiet)) fail_stop("post-reserve state disagrees with ledger");
    return 1;
}
static int split_root(Probe *p) {
    size_t i;
    for (i = 0; i + 1 < p->count; ++i) {
        Extent tail = p->extents[i];
        if (tail.first != i || tail.state != PLACEHOLDER || tail.count != p->count - i)
            fail_stop("split ledger mismatch");
        if (!before(p, "split", i, i, 1, 0, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER, "initial")) return 0;
        if (!VirtualFree(address(p, i), G, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER))
            return actual_failure(p, "VirtualFree(split)", GetLastError());
        p->extents[i].count = 1;
        p->extents[i + 1] = (Extent){i + 1, tail.count - 1, PLACEHOLDER};
        ++p->extent_count;
        if (!verify_extent(p, i, !p->quiet) || !verify_extent(p, i + 1, !p->quiet))
            fail_stop("post-split state disagrees with ledger");
        if (!p->quiet) printf("OS_OK stage=split index=%zu real_os_invoked=YES\n", i);
    }
    return 1;
}
static int backing_section(Probe *p) {
    ULONGLONG size = (ULONGLONG)(p->count + 1) * G;
    SIZE_T i;
    printf("SECTION api=CreateFileMappingW size=0x%llx flags=PAGE_READWRITE|SEC_COMMIT\n",
           (unsigned long long)size);
    p->section = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_COMMIT,
                                   (DWORD)(size >> 32), (DWORD)size, NULL);
    if (!p->section) {
        printf("REAL_OS_FAILURE api=CreateFileMappingW error=%lu\n", GetLastError());
        return 0;
    }
    p->backing = MapViewOfFile(p->section, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, (SIZE_T)size);
    if (!p->backing) {
        printf("REAL_OS_FAILURE api=MapViewOfFile(backing) error=%lu\n", GetLastError());
        return 0;
    }
    for (i = 0; i < (SIZE_T)size; ++i) p->backing[i] = pattern(p, i);
    return 1;
}
static int map_leaf(Probe *p, size_t leaf, SIZE_T off, const char *stage, size_t index, const char *phase) {
    size_t ei = extent_at(p, leaf), other;
    void *mapped;
    if (p->extents[ei].state != PLACEHOLDER || p->extents[ei].count != 1 ||
        off % sysinfo.dwPageSize || off > p->count * G)
        fail_stop("map arguments do not match exact placeholder or section extent");
    for (other = 0; other < p->extent_count; ++other) {
        Extent e = p->extents[other];
        if (e.state == MAPPED && off < p->offsets[e.first] + G && p->offsets[e.first] < off + G)
            fail_stop("accidental backing interval overlap with another leaf");
    }
    if (!before(p, stage, index, leaf, 1, off, MEM_REPLACE_PLACEHOLDER, phase)) return 0;
    printf("CALL api=MapViewOfFile3 section=%p process=%p base=%p offset=0x%zx "
           "size=0x%zx flags=0x%lx protection=PAGE_READWRITE extended_parameters=NULL count=0\n",
           p->section, GetCurrentProcess(), (void *)address(p, leaf), off, G,
           (unsigned long)MEM_REPLACE_PLACEHOLDER);
    mapped = map3(p->section, GetCurrentProcess(), address(p, leaf), off, G,
                  MEM_REPLACE_PLACEHOLDER, PAGE_READWRITE, NULL, 0);
    if (!mapped) return actual_failure(p, "MapViewOfFile3", GetLastError());
    if (mapped != address(p, leaf)) fail_stop("MapViewOfFile3 returned wrong address");
    p->extents[ei].state = MAPPED;
    p->offsets[leaf] = off;
    if (!verify_leaf(p, leaf, !p->quiet)) fail_stop("post-map layout or backing mismatch");
    if (!p->quiet) printf("OS_OK stage=%s index=%zu real_os_invoked=YES returned=%p size=0x%zx\n",
                          stage, index, mapped, G);
    if ((uintptr_t)mapped % 0x10000 && off % 0x10000) {
        p->non64_verified = 1;
        if (!p->quiet) printf("ALIGNMENT=PASS leaf=%zu base=%p section_offset=0x%zx "
                              "both_non_64KiB=YES host_page_aligned=YES size=0x%zx\n", leaf, mapped, off, G);
    }
    return 1;
}
static int initial_maps(Probe *p) {
    size_t i;
    for (i = 0; i < p->count; ++i)
        if (!map_leaf(p, i, i * G, "map", i, "initial")) return 0;
    return check(p->non64_verified, "non-64KiB base AND offset were actually mapped and checked");
}
static int unmap_leaf(Probe *p, size_t leaf, size_t index, const char *phase) {
    size_t ei = extent_at(p, leaf);
    const char *stage = !strcmp(phase, "cleanup") ? "cleanup-unmap" : "unmap";
    if (p->extents[ei].state != MAPPED || p->extents[ei].count != 1)
        fail_stop("unmap must name one known exact mapped view");
    if (!before(p, stage, index, leaf, 1, p->offsets[leaf], MEM_PRESERVE_PLACEHOLDER, phase)) return 0;
    if (!unmap2(GetCurrentProcess(), address(p, leaf), MEM_PRESERVE_PLACEHOLDER))
        return actual_failure(p, "UnmapViewOfFile2", GetLastError());
    p->extents[ei].state = PLACEHOLDER;
    if (!verify_extent(p, ei, !p->quiet)) fail_stop("post-unmap state disagrees with ledger");
    if (!p->quiet) printf("OS_OK stage=%s index=%zu real_os_invoked=YES removed_view_size=0x%zx\n", stage, index, G);
    return 1;
}
static int intentional_invalidation(Probe *p, size_t leaf) {
    volatile unsigned char value = 0;
    int caught = 0;
    SIZE_T j;
    unsigned char *at = address(p, leaf);
    if (p->extents[extent_at(p, leaf)].state != PLACEHOLDER || !verify_leaf(p, leaf, 1)) return 0;
    for (j = 0; j < G; ++j)
        if (!check(p->backing[p->offsets[leaf] + j] == pattern(p, p->offsets[leaf] + j),
                   "backing interval survives intentional target hole")) return 0;
    printf("TARGET_BACKING=PASS section=%p former_offset=0x%zx alias=%p bytes_verified=0x%zx "
           "target_section_view_present=NO\n", p->section, p->offsets[leaf],
           (void *)(p->backing + p->offsets[leaf]), G);
    printf("TARGET_AV_ARMED address=%p state=MEM_RESERVE not_committed=YES not_section_view=YES\n", (void *)at);
    __try { value = *(volatile unsigned char *)at; }
    __except(read_filter(GetExceptionInformation(), at, 1)) { caught = 1; }
    (void)value;
    printf("TARGET_INVALIDATION=%s expected_read_av=0xC0000005 caught=%d "
           "filter=read_AV_at_exact_target_only\n", caught ? "PASS" : "FAIL", caught);
    return caught;
}
static DWORD WINAPI reader_main(void *arg) {
    Reader *r = arg;
    if (!SetEvent(r->ready)) fail_stop("reader ready event failed");
    for (;;) {
        unsigned i;
        if (WaitForSingleObject(r->request, WAIT_MS) != WAIT_OBJECT_0) fail_stop("reader request timeout");
        if (InterlockedCompareExchange(&r->stop, 0, 0)) break;
        r->ok = 1;
        for (i = 0; i < READER_PASSES; ++i)
            if (!verify_leaf(r->probe, r->leaf, 0)) { r->ok = 0; break; }
        ++r->rounds;
        printf("READER=%s leaf=%zu round=%u passes=%u base=%p offset=0x%zx protection=PAGE_READWRITE\n",
               r->ok ? "PASS" : "FAIL", r->leaf, r->rounds, READER_PASSES,
               (void *)address(r->probe, r->leaf), r->probe->offsets[r->leaf]);
        if (!SetEvent(r->done)) fail_stop("reader completion event failed");
    }
    return 0;
}
static void wait_event(HANDLE event, const char *why) {
    if (WaitForSingleObject(event, WAIT_MS) != WAIT_OBJECT_0) fail_stop(why);
}
static void stop_readers(Reader readers[2]) {
    size_t i;
    for (i = 0; i < 2; ++i) {
        Reader *r = &readers[i];
        if (r->thread) {
            InterlockedExchange(&r->stop, 1);
            if (!SetEvent(r->request)) fail_stop("reader stop event failed");
            wait_event(r->thread, "reader join timeout; do not free live-reader memory");
            CloseHandle(r->thread);
        }
        if (r->request) CloseHandle(r->request);
        if (r->done) CloseHandle(r->done);
        if (r->ready) CloseHandle(r->ready);
        memset(r, 0, sizeof(*r));
    }
}
static int start_readers(Probe *p, Reader readers[2]) {
    size_t i;
    memset(readers, 0, sizeof(Reader) * 2);
    for (i = 0; i < 2; ++i) {
        Reader *r = &readers[i];
        r->probe = p;
        r->leaf = i ? p->count - 1 : 0;
        r->request = CreateEventW(NULL, FALSE, FALSE, NULL);
        r->done = CreateEventW(NULL, FALSE, FALSE, NULL);
        r->ready = CreateEventW(NULL, TRUE, FALSE, NULL);
        if (!r->request || !r->done || !r->ready) {
            printf("REAL_OS_FAILURE api=CreateEventW error=%lu\n", GetLastError());
            stop_readers(readers);
            return 0;
        }
        r->thread = CreateThread(NULL, 0, reader_main, r, 0, NULL);
        if (!r->thread) {
            printf("REAL_OS_FAILURE api=CreateThread error=%lu\n", GetLastError());
            stop_readers(readers);
            return 0;
        }
        wait_event(r->ready, "reader startup timeout");
    }
    return 1;
}
static int reader_round(Reader readers[2], const char *label) {
    size_t i;
    int ok = 1;
    printf("PAUSE label=%s next_mapping_operation_blocked_until_both_readers_finish=YES\n", label);
    for (i = 0; i < 2; ++i)
        if (!SetEvent(readers[i].request)) fail_stop("reader round request failed");
    for (i = 0; i < 2; ++i) {
        wait_event(readers[i].done, "reader round timeout; no unsafe unmap on timeout");
        if (!readers[i].ok) ok = 0;
    }
    return ok;
}
static int unaffected(Probe *p, int verbose) {
    size_t i;
    int ok = 1;
    for (i = 0; i < p->count; ++i) {
        if (i >= p->target_first && i < p->target_end) continue;
        if (p->extents[extent_at(p, i)].state != MAPPED || p->offsets[i] != i * G ||
            !verify_leaf(p, i, verbose)) ok = 0;
    }
    return ok;
}
static int cleanup(Probe *p) {
    size_t i, coalesce_index = 0;
    MEMORY_BASIC_INFORMATION after;
    void *old_root = p->root;
    if (p->uncertain) fail_stop("real mutator failure: cleanup against uncertain layout prohibited");
    if (!p->root) goto ancillary;
    printf("CLEANUP_BEGIN readers_joined=YES intended_target=ENTIRE_OWNED_ROOT\n");
    for (i = 0; i < p->count; ++i)
        if (p->extents[extent_at(p, i)].state == MAPPED && !unmap_leaf(p, i, i, "cleanup")) return 0;
    while (p->extent_count > 1) {
        Extent a = p->extents[0], b = p->extents[1];
        if (a.state != PLACEHOLDER || b.state != PLACEHOLDER || a.first + a.count != b.first)
            fail_stop("coalesce requires known adjacent owned placeholders");
        if (!before(p, "coalesce", coalesce_index, a.first, a.count + b.count, 0,
                    MEM_RELEASE | MEM_COALESCE_PLACEHOLDERS, "cleanup")) return 0;
        if (!VirtualFree(address(p, a.first), (a.count + b.count) * G,
                         MEM_RELEASE | MEM_COALESCE_PLACEHOLDERS))
            return actual_failure(p, "VirtualFree(coalesce)", GetLastError());
        p->extents[0].count += b.count;
        memmove(p->extents + 1, p->extents + 2, (p->extent_count - 2) * sizeof(Extent));
        --p->extent_count;
        if (!verify_extent(p, 0, !p->quiet)) fail_stop("post-coalesce layout mismatch");
        if (!p->quiet) printf("OS_OK stage=coalesce index=%zu real_os_invoked=YES\n", coalesce_index);
        ++coalesce_index;
    }
    if (!snapshot(p, "before-root-release", !p->quiet)) fail_stop("cleanup ledger mismatch");
    if (!before(p, "release", 0, 0, p->count, 0, MEM_RELEASE, "cleanup")) return 0;
    printf("RELEASE api=VirtualFree base=%p size_argument=0 known_extent_size=0x%zx\n", old_root, p->count * G);
    if (!VirtualFree(p->root, 0, MEM_RELEASE)) return actual_failure(p, "VirtualFree(release)", GetLastError());
    p->root = NULL;
    p->extent_count = 0;
    if (!VirtualQuery(old_root, &after, sizeof(after))) return check(0, "query released root");
    /* Other allocators could theoretically reclaim released VA. No such allocation
     * is made by this probe; a non-free observation is reported, not ignored. */
    if (!check(after.State == MEM_FREE && after.BaseAddress == old_root &&
               after.RegionSize >= p->count * G, "entire root reports MEM_FREE after release")) return 0;
ancillary:
    if (p->backing) {
        printf("RELEASE_BACKING base=%p size=0x%zx\n", (void *)p->backing, (p->count + 1) * G);
        if (!UnmapViewOfFile(p->backing)) return check(0, "backing alias cleanup");
        p->backing = NULL;
    }
    if (p->section) {
        if (!CloseHandle(p->section)) return check(0, "section handle cleanup");
        p->section = NULL;
    }
    printf("CLEANUP=PASS root_released=YES backing_unmapped=YES section_closed=YES\n");
    return 1;
}
static void simulation_snapshot(Probe *p, const char *when) {
    int good;
    if (p->uncertain) fail_stop("real OS failure instead of simulation");
    good = snapshot(p, when, 1);
    if (!strcmp(injection_stage, "unmap") || !strcmp(injection_stage, "remap"))
        good = unaffected(p, 1) && good;
    printf("SIMULATION_STATE stage=%s index=%zu when=%s all_known_leaves=%s "
           "carve_siblings_checked=%s rollback_attempted=NO cleanup_pending=YES\n",
           injection_stage, injection_index, when, good ? "PASS" : "FAIL",
           (!strcmp(injection_stage, "unmap") || !strcmp(injection_stage, "remap")) ? "YES" : "NOT_CARVE_PHASE");
    if (!good) fail_stop("simulation state validation failed");
}
static int run_case(int multi) {
    Probe p;
    Reader readers[2];
    size_t i;
    int ok, readers_live = 0, cleanup_ok;
    memset(readers, 0, sizeof(readers));
    printf("CASE mode=%s G=0x%zx ROOT_SIZE=0x%zx injection=%s index=%zu\n",
           multi ? "multi-interior" : "single-interior", G, 4 * G,
           injection_stage ? injection_stage : "DISABLED", injection_index);
    ok = reserve_root(&p, 4, 0);
    if (!ok) goto finished;
    if (!(ok = split_root(&p))) goto finished;
    if (!(ok = backing_section(&p))) goto finished;
    if (!(ok = initial_maps(&p))) goto finished;
    p.backing[G + 17] ^= 0xa5;
    p.patch = 1;
    if (!(ok = snapshot(&p, "backing-alias-write", 1))) goto finished;
    printf("BACKING_ALIAS_WRITE=PASS section_offset=0x%zx all_leaf_patterns_verified=YES\n", G + 17);
    p.target_first = multi ? 1 : 2;
    p.target_end = 3;
    if (!(ok = start_readers(&p, readers))) goto finished;
    readers_live = 1;
    if (!(ok = reader_round(readers, "before-first-unmap"))) goto finished;
    for (i = p.target_first; i < p.target_end; ++i) {
        if (!(ok = unmap_leaf(&p, i, i - p.target_first, "carve"))) goto finished;
        if (!(ok = reader_round(readers, "after-each-target-unmap"))) goto finished;
        if (!(ok = snapshot(&p, "after-each-target-unmap", 1))) goto finished;
        if (!(ok = unaffected(&p, 1))) goto finished;
        if (!(ok = intentional_invalidation(&p, i))) goto finished;
    }
    for (i = p.target_first; i < p.target_end; ++i) {
        if (!(ok = map_leaf(&p, i, i * G, "remap", i - p.target_first, "remap"))) goto finished;
        if (!(ok = reader_round(readers, "same-offset-remap"))) goto finished;
    }
    if (!(ok = snapshot(&p, "same-offset-restored", 1))) goto finished;
    printf("BACKING_SAME_OFFSET_REMAP=PASS\n");
    /* Rebind only the first target to the fifth, distinct backing interval. */
    if (!(ok = unmap_leaf(&p, p.target_first, p.target_end - p.target_first, "carve"))) goto finished;
    if (!(ok = reader_round(readers, "alternate-offset-target-unmapped"))) goto finished;
    if (!(ok = intentional_invalidation(&p, p.target_first))) goto finished;
    if (!(ok = map_leaf(&p, p.target_first, p.count * G, "remap", p.target_end - p.target_first, "remap"))) goto finished;
    if (!(ok = reader_round(readers, "alternate-offset-remapped"))) goto finished;
    if (!(ok = snapshot(&p, "alternate-offset-final", 1))) goto finished;
    if (!(ok = unaffected(&p, 1))) goto finished;
    printf("BACKING_DIFFERENT_OFFSET_REMAP=PASS target=%zu old_offset=0x%zx new_offset=0x%zx "
           "neighbor_offsets_unchanged=YES\n", p.target_first, p.target_first * G, p.count * G);
finished:
    if (readers_live) stop_readers(readers);
    if (p.uncertain) fail_stop("actual OS mutation failed; see preceding arguments and error");
    if (p.injected) simulation_snapshot(&p, "before-recovery-cleanup");
    cleanup_ok = cleanup(&p);
    if (!cleanup_ok && p.uncertain) fail_stop("real OS cleanup failure");
    if (!cleanup_ok && p.injected && injection_stage && !strcmp(injection_stage, "coalesce")) {
        simulation_snapshot(&p, "coalesce-failed-before-call");
        printf("RECOVERY action=retry-known-ledger-cleanup injection=already-consumed\n");
        cleanup_ok = cleanup(&p);
        printf("RECOVERY cleanup_retry=%s rollback_of_mapping_transaction=NOT_ATTEMPTED\n", cleanup_ok ? "PASS" : "FAIL");
    }
    if (!cleanup_ok) fail_stop("cleanup did not complete");
    if (p.injected && injection_stage && !strcmp(injection_stage, "coalesce") && !ok) {
        printf("RESULT=FAIL reason=lifecycle_failed_before_cleanup_injection\n");
        return 1;
    }
    if (p.injected) {
        printf("SIMULATION_CHECK=PASS stage=%s index=%zu real_os_invoked_for_failed_call=NO "
               "cleanup=PASS outcome=FAILURE_RETURN exit=%d\n", injection_stage, injection_index, SIMULATED_EXIT);
        printf("RESULT=EXPECTED_SIMULATED_FAILURE\n");
        return SIMULATED_EXIT;
    }
    if (injection_stage) return check(0, "requested injection point reached") ? 0 : 1;
    printf("CASE_RESULT=%s mode=%s structural_invariant=%s\n", ok ? "PASS" : "FAIL",
           multi ? "multi-interior" : "single-interior", ok ? "PASS" : "NOT_QUALIFIED");
    return ok ? 0 : 1;
}
static double clock_ms(void) {
    LARGE_INTEGER t;
    if (!QueryPerformanceCounter(&t)) fail_stop("QPC measurement failed");
    return (double)t.QuadPart * 1000.0 / (double)frequency.QuadPart;
}
static int benchmark(unsigned repetitions) {
    static const size_t sizes[] = {4, 64, 256, 1024};
    size_t k;
    unsigned rep;
    for (k = 0; k < sizeof(sizes) / sizeof(sizes[0]); ++k) {
        for (rep = 0; rep < repetitions; ++rep) {
            Probe p;
            double t0, t1, t2, t3, t4, t5, t6, section_ms, validation_ms;
            double before_section, after_section, before_validation;
            size_t i;
            t0 = clock_ms();
            if (!reserve_root(&p, sizes[k], 1)) { cleanup(&p); return 1; }
            t1 = clock_ms();
            if (!split_root(&p)) fail_stop("benchmark split failed");
            t2 = clock_ms();
            before_section = clock_ms();
            if (!backing_section(&p)) { cleanup(&p); return 1; }
            after_section = clock_ms();
            section_ms = after_section - before_section;
            if (!initial_maps(&p)) fail_stop("benchmark map failed");
            t3 = clock_ms();
            p.target_first = p.count / 2;
            p.target_end = p.target_first + 1;
            if (!unmap_leaf(&p, p.target_first, 0, "carve")) fail_stop("benchmark unmap failed");
            t4 = clock_ms();
            if (!map_leaf(&p, p.target_first, p.target_first * G, "remap", 0, "remap")) fail_stop("benchmark remap failed");
            t5 = clock_ms();
            before_validation = clock_ms();
            for (i = 0; i < p.count; ++i) if (!verify_leaf(&p, i, 0)) fail_stop("benchmark final validation failed");
            validation_ms = clock_ms() - before_validation;
            if (!cleanup(&p)) fail_stop("benchmark cleanup failed");
            t6 = clock_ms();
            printf("BENCH leaves=%zu views=%zu peak_extents=%zu section_handles=1 backing_views=1 "
                   "metadata_capacity=%u metadata_bytes=%zu failure_count=0 "
                   "repetition=%u reserve_ms=%.6f split_ms=%.6f section_seed_ms=%.6f "
                   "map_ms=%.6f target_unmap_ms=%.6f target_remap_ms=%.6f "
                   "final_validation_ms=%.6f cleanup_ms=%.6f "
                   "timing_includes_wrapper_checks=YES\n",
                   sizes[k], sizes[k], sizes[k], MAX_LEAVES, sizeof(p), rep, t1-t0, t2-t1, section_ms,
                   t3-after_section, t4-t3, t5-t4, validation_ms, t6-t5-validation_ms);
        }
    }
    printf("RESULT=PASS mode=benchmark repetitions=%u production_projection=NONE\n", repetitions);
    return 0;
}
static int parse_number(const char *text, size_t *out) {
    char *end;
    unsigned long long value;
    if (!text[0] || text[0] == '-') return 0;
    errno = 0;
    value = strtoull(text, &end, 10);
    if (errno || *end || value > MAX_LEAVES) return 0;
    *out = (size_t)value;
    return 1;
}
int main(int argc, char **argv) {
    char stage[32];
    if (!environment()) { printf("RESULT=UNSUPPORTED exit=2\n"); return 2; }
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        printf("Usage: placeholder_leaf_probe [--inject split:1|map:2|unmap:1|remap:0|coalesce:0] "
               "[OR --benchmark [repetitions=1..10]]\n");
        return 0;
    }
    if (argc >= 2 && !strcmp(argv[1], "--benchmark")) {
        size_t reps = 3;
        if (argc > 3 || (argc == 3 && !parse_number(argv[2], &reps)) || !reps || reps > 10) goto usage;
        return benchmark((unsigned)reps);
    }
    if (argc == 3 && !strcmp(argv[1], "--inject")) {
        const char *colon = strchr(argv[2], ':');
        size_t len = colon ? (size_t)(colon - argv[2]) : 0;
        if (!colon || !len || len >= sizeof(stage) || !parse_number(colon + 1, &injection_index)) goto usage;
        memcpy(stage, argv[2], len);
        stage[len] = 0;
        if (strcmp(stage, "split") && strcmp(stage, "map") && strcmp(stage, "unmap") &&
            strcmp(stage, "remap") && strcmp(stage, "coalesce")) goto usage;
        injection_stage = stage;
        return run_case(!strcmp(stage, "unmap"));
    }
    if (argc != 1) goto usage;
    if (run_case(0) || run_case(1)) { printf("RESULT=FAIL mode=basic\n"); return 1; }
    printf("RESULT=PASS mode=basic native_lifecycle=YES alignment=YES sibling_continuity=YES "
           "target_av=YES backing_offsets=YES structural_operand_audit=YES "
           "production_safety_claim=NONE\n");
    return 0;
usage:
    printf("RESULT=INVALID_ARGUMENT exit=2 (use --help)\n");
    return 2;
}
