#include <stdio.h>

/* TODO：按体积分级。小件 0 / 中件 1 / 大件 2。 */
int grade_code(double volume) {
    return 0;
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%d\n", grade_code(5000.0));
    return 0;
}
