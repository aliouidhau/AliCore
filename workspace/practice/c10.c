#include <stdio.h>

int *max_ptr(int *a, int n) {
    int *best = &a[0];
    for (int i = 1; i < n; i++) {
        if (a[i] > *best) {
            best = &a[0];
        }
    }
    return best;
}

int main(void) {
    int a[] = {3, 7, 2};
    printf("%d\n", *max_ptr(a, 3));
    return 0;
}
