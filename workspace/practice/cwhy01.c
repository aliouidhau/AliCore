#include <stdio.h>

int sum_all(int a[], int n) {
    int total = 0;
    int i;
    printf("函数里 sizeof(a) = %d\n", (int)sizeof(a));
    for (i = 0; i < n; i++) {
        total += a[i];
    }
    return total;
}

int main(void) {
    int data[4] = {1, 2, 3, 4};
    setbuf(stdout, NULL);
    printf("main 里 sizeof(data) = %d\n", (int)sizeof(data));
    printf("sum = %d\n", sum_all(data, 4));
    return 0;
}
