/* Guest mutex/condition-variable handle lifetime and statistics.
 *
 * The guest sees these handles as opaque pointers it keeps in its own structures, so a handle
 * used after pthread_mutex_destroy / pthread_cond_destroy must be *reported*, never silently
 * replaced by a fresh host object: a silently re-created condition variable is never signalled
 * by the code that destroyed the old one, which turns a guest lifecycle bug into a hang.
 * The same file's statistics are updated from every guest thread and read by
 * runtime_mutex_report() from another one, so the counters must not race.
 *
 * No Vulkan, SDL or game data: this includes runtime_mutex.c the way tests/test_pad.c includes
 * runtime_pad.c. */
#define _GNU_SOURCE
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/runtime_mutex.c"

/* Guest error numbers as the runtime reports them (0x80020000 | errno). */
#define GUEST_EINVAL ((int32_t)UINT32_C(0x80020016))
#define GUEST_EPERM ((int32_t)UINT32_C(0x80020001))

static long double_checks;

static void check_pair(const char *what, int32_t got, int32_t want) {
    if (got != want) {
        fprintf(stderr, "FAIL: %s: got %#x, want %#x\n", what, (uint32_t)got, (uint32_t)want);
        exit(1);
    }
    ++double_checks;
}

/* ---- a real wait/signal round trip, so the working path stays covered ---- */

typedef struct {
    GuestCond **cond;
    GuestMutex **mutex;
    _Atomic int ready;
} Waiter;

static void *waiter_thread(void *argument) {
    Waiter *w = argument;
    check_pair("waiter: lock", mutex_lock(w->mutex), 0);
    /* Still holding the mutex, so the signaller cannot slip in before pthread_cond_wait. */
    atomic_store(&w->ready, 1);
    check_pair("waiter: wait", cond_wait(w->cond, w->mutex), 0);
    check_pair("waiter: unlock", mutex_unlock(w->mutex), 0);
    return NULL;
}

static void test_wait_signal_round_trip(void) {
    GuestMutex *mutex = NULL;
    GuestCond *cond = NULL;
    check_pair("init mutex", mutex_init(&mutex, NULL, NULL), 0);
    check_pair("init cond", cond_init(&cond, NULL, NULL), 0);
    Waiter w = {&cond, &mutex, 0};
    pthread_t thread;
    assert(pthread_create(&thread, NULL, waiter_thread, &w) == 0);
    while (!atomic_load(&w.ready)) {
    }
    /* Acquiring the mutex now blocks until the waiter is inside pthread_cond_wait. */
    check_pair("signaller: lock", mutex_lock(&mutex), 0);
    check_pair("signal", cond_signal(&cond), 0);
    check_pair("signaller: unlock", mutex_unlock(&mutex), 0);
    assert(pthread_join(thread, NULL) == 0);
    check_pair("destroy cond", cond_destroy(&cond), 0);
    check_pair("destroy mutex", mutex_destroy(&mutex), 0);
}

/* ---- a destroyed condition variable must stay destroyed ---- */

static void test_destroyed_cond_is_not_re_created(void) {
    GuestMutex *mutex = NULL;
    GuestCond *cond = NULL;
    if (mutex_init(&mutex, NULL, NULL) || cond_init(&cond, NULL, NULL)) {
        fputs("FAIL: mutex_init/cond_init\n", stderr);
        exit(1);
    }
    check_pair("destroy cond", cond_destroy(&cond), 0);
    /* The handle keeps the sentinel: nothing allocates a replacement behind the guest's back. */
    check_pair("handle is the destroyed sentinel", (int32_t)((uintptr_t)cond == 2), 1);
    check_pair("signal after destroy", cond_signal(&cond), GUEST_EINVAL);
    check_pair("broadcast after destroy", cond_broadcast(&cond), GUEST_EINVAL);
    check_pair("wait after destroy", cond_wait(&cond, &mutex), GUEST_EINVAL);
    check_pair("timedwait after destroy", cond_timedwait(&cond, &mutex, 1000), GUEST_EINVAL);
    check_pair("destroy twice", cond_destroy(&cond), GUEST_EINVAL);
    check_pair("destroyed handle still not re-created", (int32_t)((uintptr_t)cond == 2), 1);
    mutex_destroy(&mutex);
}

/* ---- the semaphore-backed static handles (0/1) still come up on first use ---- */

static void test_static_handles(void) {
    GuestMutex *mutex = NULL; /* PTHREAD_MUTEX_INITIALIZER: zeroed storage */
    GuestCond *cond = NULL;
    check_pair("static cond: signal creates it", cond_signal(&cond), 0);
    check_pair("static cond: created once", (int32_t)((uintptr_t)cond > 2), 1);
    check_pair("static cond: second signal", cond_signal(&cond), 0);
    check_pair("static mutex: lock", mutex_lock(&mutex), 0);
    check_pair("static mutex: unlock", mutex_unlock(&mutex), 0);
    check_pair("static mutex: destroy", mutex_destroy(&mutex), 0);
    check_pair("static mutex: lock after destroy", mutex_lock(&mutex), GUEST_EINVAL);
    check_pair("static mutex: destroy twice", mutex_destroy(&mutex), GUEST_EINVAL);
    check_pair("static cond: destroy", cond_destroy(&cond), 0);
    check_pair("static cond: signal after destroy", cond_signal(&cond), GUEST_EINVAL);
    check_pair("static cond: unlock of a statically initialized mutex", mutex_unlock(&(GuestMutex *){NULL}), GUEST_EPERM);
}

/* ---- statistics are shared by every guest thread ---- */

#define STAT_THREADS 8
#define STAT_ROUNDS 20000

static GuestMutex *stat_mutex;
static _Atomic size_t stat_failures;

static void *stat_thread(void *unused) {
    (void)unused;
    for (int i = 0; i < STAT_ROUNDS; ++i) {
        if (mutex_lock(&stat_mutex) || mutex_unlock(&stat_mutex)) {
            atomic_fetch_add(&stat_failures, 1);
            return NULL;
        }
    }
    return NULL;
}

static void test_counters_do_not_lose_updates(void) {
    const size_t lock_before = atomic_load_explicit(&locks, memory_order_relaxed);
    const size_t unlock_before = atomic_load_explicit(&unlocks, memory_order_relaxed);
    check_pair("counter mutex: init", mutex_init(&stat_mutex, NULL, NULL), 0);
    pthread_t threads[STAT_THREADS];
    for (int t = 0; t < STAT_THREADS; ++t) assert(pthread_create(&threads[t], NULL, stat_thread, NULL) == 0);
    for (int t = 0; t < STAT_THREADS; ++t) assert(pthread_join(threads[t], NULL) == 0);
    check_pair("counter mutex: no failing lock", (int32_t)atomic_load(&stat_failures), 0);
    check_pair("counter mutex: destroy", mutex_destroy(&stat_mutex), 0);
    const size_t expected = (size_t)STAT_THREADS * STAT_ROUNDS;
    if (atomic_load_explicit(&locks, memory_order_relaxed) - lock_before != expected) {
        fprintf(stderr, "FAIL: lost lock updates: %zu of %zu counted\n",
                atomic_load_explicit(&locks, memory_order_relaxed) - lock_before, expected);
        exit(1);
    }
    if (atomic_load_explicit(&unlocks, memory_order_relaxed) - unlock_before != expected) {
        fprintf(stderr, "FAIL: lost unlock updates: %zu of %zu counted\n",
                atomic_load_explicit(&unlocks, memory_order_relaxed) - unlock_before, expected);
        exit(1);
    }
}

int main(void) {
    test_wait_signal_round_trip();
    test_destroyed_cond_is_not_re_created();
    test_static_handles();
    test_counters_do_not_lose_updates();
    runtime_mutex_report();
    printf("PASS: mutex/cond handle lifetime, destroyed-sentinel reporting and shared statistics "
           "(%ld checks)\n", double_checks);
    return 0;
}
