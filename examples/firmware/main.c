#include <stdint.h>
#include <stdio.h>

volatile uint32_t g_phase = 0;
volatile uint32_t g_checksum = 0;
volatile uint32_t g_state = 0x12345678u;
volatile uint32_t g_history[16];
volatile uint32_t g_idle_counter = 0;

/* Puzzle observability state. */
volatile uint32_t g_expected_slot = 0;
volatile uint32_t g_observed_slot = 0;
volatile uint32_t g_mismatch_iter = 0xFFFFFFFFu;
volatile uint32_t g_guard = 0xCAFEBABEu;

static uint32_t rotl32(uint32_t x, uint32_t n)
{
    return (x << n) | (x >> (32u - n));
}

static uint32_t mix_word(uint32_t x, uint32_t salt)
{
    x ^= 0x9E3779B9u ^ salt;
    x = rotl32(x, 5u);
    x *= 0x85EBCA6Bu;
    x ^= x >> 13;
    return x;
}

static uint32_t phase_step(uint32_t phase, uint32_t value)
{
    uint32_t lane = phase & 3u;
    if (lane == 0u) {
        return value + 0x11111111u;
    }
    if (lane == 1u) {
        return value ^ 0xA5A5A5A5u;
    }
    if (lane == 2u) {
        return rotl32(value, 3u);
    }
    return value - 0x01020304u;
}

static uint32_t history_slot_for_iter(uint32_t iter)
{
    return iter & 15u;
}

/*
 * Intentionally subtle bug for debugging practice:
 * Around iter 24, slot selection becomes data-dependent and can shift by one.
 */
static uint32_t commit_history(uint32_t iter, uint32_t value)
{
    uint32_t slot = history_slot_for_iter(iter);
    if ((iter & 31u) == 24u) {
        slot ^= (value >> 31);
        slot &= 15u;
    }
    g_history[slot] = value;
    return slot;
}

int main(void)
{
    puts("DBG_BOOT");

    for (uint32_t i = 0; i < 16u; ++i) {
        uint32_t seed = (i + 1u) * 17u;
        g_history[i] = mix_word(seed, i * 3u);
    }

    for (uint32_t iter = 0; iter < 48u; ++iter) {
        g_phase = iter;

        uint32_t a = g_history[(iter + 1u) & 15u];
        uint32_t b = g_history[(iter + 7u) & 15u];
        uint32_t c = mix_word(a ^ b, iter * 97u);
        uint32_t next = phase_step(iter, c ^ g_state);

        g_state = next;
        g_expected_slot = history_slot_for_iter(iter);
        g_observed_slot = commit_history(iter, next);

        uint32_t observed_slot_snapshot = g_observed_slot;
        uint32_t expected_slot_snapshot = g_expected_slot;
        uint32_t mismatch_snapshot = g_mismatch_iter;

        if ((observed_slot_snapshot != expected_slot_snapshot) && (mismatch_snapshot == 0xFFFFFFFFu)) {
            g_mismatch_iter = iter;
            g_guard ^= 0xDEAD0000u | iter;
        }

        g_checksum ^= (next + (iter << 8));

        if ((iter % 12u) == 0u) {
            uint32_t state_snapshot = g_state;
            uint32_t checksum_snapshot = g_checksum;
            uint32_t mismatch_snapshot = g_mismatch_iter;
            printf(
                "DBG_PHASE iter=%lu state=0x%08lx checksum=0x%08lx mismatch=%ld\n",
                (unsigned long)iter,
                (unsigned long)state_snapshot,
                (unsigned long)checksum_snapshot,
                (long)mismatch_snapshot
            );
        }
    }

    puts("DBG_DONE");

    while (1) {
        g_idle_counter++;
        if ((g_idle_counter & 0x7FFFFu) == 0u) {
            uint32_t idle_snapshot = g_idle_counter;
            g_checksum ^= idle_snapshot;
        }
    }
}
