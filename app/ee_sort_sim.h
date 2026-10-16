// ee_sort_sim.h - the executable's std::sort by angle, run on indices
// (app/ee_sort.cpp says why).
//
// SortSimulation replays MSVC's introsort as the executable's instantiation
// at 0x63a355 runs it, comparison for comparison, over the elements' angles
// computed beforehand, and stops where the original would read outside the
// array. Nothing here touches guest memory, so tests/sort/ checks it alone.
//
//   sort      0x63a355  n <= 16: insertion sort; else the introsort, an
//                       insertion sort of the first 16, then an unguarded
//                       insertion of each later element
//   introsort 0x63ab78  partitions while more than 16 remain, the left part
//                       by recursion; `ideal` (n at the start) halves at
//                       every partition and a heap sort takes over at 0
//   partition 0x63b8d2  median of three in place, then the unguarded scans
//   insertion 0x63b79d  guarded by the first element
//   insert    0x63ba38  unguarded
//
// The comparator, as compiled: less(x, y) = angle(y) > (double)(float)angle(x).
// The first angle is spilled to a float, the second compared in ST(0) at the
// thread's 53-bit precision.
#pragma once

#include <algorithm>
#include <cstddef>
#include <utility>
#include <vector>

namespace ee_sort {

constexpr long kSortMax = 16; // _SORT_MAX: insertion sort at or below

class SortSimulation {
  public:
    enum Result {
        kInBounds, // every read stayed inside the array: order() is the original's result
        kEscapes,  // the original reads outside the array
        kHeap,     // the introsort fell back to its heap sort, which is not simulated
    };

    explicit SortSimulation(std::vector<double> angle) : angle_(std::move(angle)), a_(angle_.size()) {
        for (size_t i = 0; i < a_.size(); ++i)
            a_[i] = int(i);
    }

    Result run() {
        const long n = long(a_.size());
        if (n <= kSortMax) {
            insertion(0, n);
        } else {
            introsort(0, n, n);
            if (state_ == kInBounds)
                insertion(0, kSortMax);
            for (long i = kSortMax; i < n && state_ == kInBounds; ++i)
                insert(i, a_[i]);
        }
        return state_;
    }

    // order()[i] is the index of the element the original leaves at position i.
    const std::vector<int> &order() const { return a_; }

    // The comparator as the executable evaluates it.
    static bool less(double x, double y) { return y > double(float(x)); }

  private:
    bool less_at(int x, int y) const { return less(angle_[size_t(x)], angle_[size_t(y)]); }

    // Whether position i is inside the array; past either end the original
    // reads whatever lies beyond it as an element, so the simulation ends.
    bool inside(long i) {
        if (i >= 0 && i < long(a_.size()))
            return true;
        if (state_ == kInBounds)
            state_ = kEscapes;
        return false;
    }

    void insertion(long first, long last) {
        if (first == last)
            return;
        for (long i = first + 1; i != last && state_ == kInBounds; ++i) {
            const int v = a_[size_t(i)];
            if (less_at(v, a_[size_t(first)])) {
                std::move_backward(a_.begin() + first, a_.begin() + i, a_.begin() + i + 1);
                a_[size_t(first)] = v;
            } else {
                insert(i, v);
            }
        }
    }

    void insert(long last, int v) {
        for (long m = last - 1;; --m) {
            if (!inside(m))
                return;
            if (!less_at(v, a_[size_t(m)]))
                break;
            a_[size_t(last)] = a_[size_t(m)];
            last = m;
        }
        a_[size_t(last)] = v;
    }

    long partition(long first, long last) {
        const long mid = first + (last - first) / 2;
        if (less_at(a_[size_t(mid)], a_[size_t(first)]))
            std::swap(a_[size_t(first)], a_[size_t(mid)]);
        if (less_at(a_[size_t(last - 1)], a_[size_t(mid)]))
            std::swap(a_[size_t(mid)], a_[size_t(last - 1)]);
        if (less_at(a_[size_t(mid)], a_[size_t(first)]))
            std::swap(a_[size_t(first)], a_[size_t(mid)]);
        const int pivot = a_[size_t(mid)];
        for (;;) {
            for (;; ++first) {
                if (!inside(first))
                    return first;
                if (!less_at(a_[size_t(first)], pivot))
                    break;
            }
            do {
                if (!inside(--last))
                    return first;
            } while (less_at(pivot, a_[size_t(last)]));
            if (!(first < last))
                return first;
            std::swap(a_[size_t(first)], a_[size_t(last)]);
            ++first;
        }
    }

    void introsort(long first, long last, long ideal) {
        while (last - first > kSortMax && state_ == kInBounds) {
            if (ideal == 0) {
                state_ = kHeap;
                return;
            }
            const long mid = partition(first, last);
            if (state_ != kInBounds)
                return;
            ideal /= 2;
            introsort(first, mid, ideal);
            first = mid;
        }
    }

    const std::vector<double> angle_;
    std::vector<int> a_;
    Result state_ = kInBounds;
};

// The elements by their float angle, ties in their original order: the order
// written when the original would read outside the array. The comparator
// agrees with it wherever it is consistent, and it is a strict weak order
// (a NaN, which the angle never is, sorts last).
inline std::vector<int> bounded_order(const std::vector<double> &angle) {
    std::vector<int> order(angle.size());
    for (size_t i = 0; i < order.size(); ++i)
        order[i] = int(i);
    auto key = [&](int i) {
        const float f = float(angle[size_t(i)]);
        return f == f ? f : __builtin_inff();
    };
    std::stable_sort(order.begin(), order.end(), [&](int x, int y) { return key(x) < key(y); });
    return order;
}

} // namespace ee_sort
