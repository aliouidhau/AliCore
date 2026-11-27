#include <stdio.h>

/* TODO：返回 0 成功 / -1 空指针 / -2 长度非法 / -3 越界。 */
int safe_set(int *data, int size, int index, int value) {
    return 0;
}

int main(void) {
    int a[3] = {0, 0, 0};
    setbuf(stdout, NULL);
    printf("%d\n", safe_set(a, 3, 2, 42));
    printf("%d\n", safe_set(a, 3, 3, 42));
    return 0;
}
