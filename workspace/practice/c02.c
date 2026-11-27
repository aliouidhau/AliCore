#include <stdio.h>

int sum_first(int *values, int n) {
    int sum = 0;
    for (int i = 0; i <= n; i++) {
        sum += values[i];
    }
    return sum;
}

int main(void) {
    int data[] = {1, 2, 3, 4, 99};
    printf("%d\n", sum_first(data, 4));
    return 0;
}
