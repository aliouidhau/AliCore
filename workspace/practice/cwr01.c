#include <stdio.h>
#include <assert.h>

/* TODO：写 safe_div。
   b 为 0 时返回 fallback；其它情况返回 a / b。
   先用 assert 守住你认为一定成立的假设。 */
int safe_div(int a, int b, int fallback) {
    return fallback;
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%d\n", safe_div(10, 2, 0));
    printf("%d\n", safe_div(10, 0, -1));
    return 0;
}
