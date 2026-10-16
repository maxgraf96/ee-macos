// The replay of the executable's sort by angle (app/ee_sort_sim.h) on
// synthetic angles: consistent ones sort and stay inside the array, ties
// that round down to float are where the original runs off it.
//
//     .venv/bin/python -m pytest -q tests/test_sort.py
#include "ee_sort_sim.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

using ee_sort::SortSimulation;

static int failures = 0;
#define CHECK(cond, ...)                                                                           \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__);                                   \
            fprintf(stderr, __VA_ARGS__);                                                          \
            fprintf(stderr, "\n");                                                                 \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

static bool permutation(const std::vector<int> &order, size_t n) {
    std::vector<int> seen(n, 0);
    if (order.size() != n)
        return false;
    for (int i : order)
        if (i < 0 || size_t(i) >= n || seen[size_t(i)]++)
            return false;
    return true;
}

// Float-exact angles make the comparator a strict weak order: the original
// sorts them, and never leaves the array.
static void consistent_angles_sort_in_bounds() {
    std::mt19937 rng(7);
    std::uniform_real_distribution<float> any(0.0f, 6.2831855f);
    std::uniform_int_distribution<int> few(0, 5);
    for (int n = 0; n <= 300; n += (n < 40 ? 1 : 13)) {
        for (int ties = 0; ties < 2; ++ties) {
            std::vector<double> angle(static_cast<size_t>(n), 0.0);
            for (double &a : angle)
                a = ties ? double(float(few(rng)) * 0.7f) : double(any(rng));
            SortSimulation sim(angle);
            const SortSimulation::Result r = sim.run();
            if (r == SortSimulation::kHeap)
                continue;
            CHECK(r == SortSimulation::kInBounds, "n=%d ties=%d: result %d", n, ties, int(r));
            CHECK(permutation(sim.order(), size_t(n)), "n=%d: not a permutation", n);
            for (int i = 1; i < n; ++i)
                CHECK(angle[size_t(sim.order()[size_t(i - 1)])] <= angle[size_t(sim.order()[size_t(i)])],
                      "n=%d ties=%d: out of order at %d", n, ties, i);
        }
    }
}

// The crash: more than 16 elements at one angle that rounds down to float.
// Each compares less than every other, and the partition's scan runs past
// the end of the array.
static void ties_rounding_down_escape() {
    const double k = 1.0 + std::ldexp(1.0, -40); // float(k) = 1 < k
    CHECK(SortSimulation::less(k, k), "a tie rounding down must compare less than itself");
    for (int n : {17, 18, 40, 200}) {
        SortSimulation sim(std::vector<double>(size_t(n), k));
        CHECK(sim.run() == SortSimulation::kEscapes, "n=%d: the original runs off the array", n);
    }
    // At 16 or fewer the insertion sort is guarded by the first element.
    for (int n : {2, 5, 16}) {
        SortSimulation sim(std::vector<double>(size_t(n), k));
        CHECK(sim.run() == SortSimulation::kInBounds, "n=%d: the guarded insertion sort", n);
        CHECK(permutation(sim.order(), size_t(n)), "n=%d: not a permutation", n);
    }
    // A tie that rounds up is consistent: nothing is less than anything.
    const double up = 1.0 - std::ldexp(1.0, -40); // float(up) = 1 > up
    CHECK(!SortSimulation::less(up, up), "a tie rounding up is irreflexive");
    SortSimulation sim(std::vector<double>(40, up));
    CHECK(sim.run() == SortSimulation::kInBounds, "ties rounding up stay inside the array");
}

// A real-looking case: distinct angles with a cluster of ties that round
// down. Whatever the replay says, the bounded order is a sorted permutation.
static void bounded_order_sorts_by_float_angle() {
    std::mt19937 rng(11);
    std::uniform_real_distribution<double> any(0.0, 6.283185307179586);
    std::vector<double> angle;
    for (int i = 0; i < 30; ++i)
        angle.push_back(any(rng));
    for (int i = 0; i < 20; ++i)
        angle.push_back(3.0 + std::ldexp(1.0, -40));
    const std::vector<int> order = ee_sort::bounded_order(angle);
    CHECK(permutation(order, angle.size()), "bounded order: not a permutation");
    for (size_t i = 1; i < order.size(); ++i)
        CHECK(float(angle[size_t(order[i - 1])]) <= float(angle[size_t(order[i])]),
              "bounded order: out of order at %zu", i);
    // ties keep their original order
    for (size_t i = 1; i < order.size(); ++i)
        if (float(angle[size_t(order[i - 1])]) == float(angle[size_t(order[i])]))
            CHECK(order[i - 1] < order[i], "bounded order: a tie reordered at %zu", i);
}

int main() {
    consistent_angles_sort_in_bounds();
    ties_rounding_down_escape();
    bounded_order_sorts_by_float_angle();
    if (failures) {
        fprintf(stderr, "%d failures\n", failures);
        return 1;
    }
    printf("sort replay: all checks passed\n");
    return 0;
}
