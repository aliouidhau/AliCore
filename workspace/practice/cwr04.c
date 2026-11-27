#include <stdio.h>

/* TODO：写 clamp。 */
int clamp(int value, int lo, int hi) {
    return value;
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%d\n", clamp(15, 0, 10));
    return 0;
}
