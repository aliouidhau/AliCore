#include <stdio.h>

int counter(void) {
    static int c = 0;
    return ++c;
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%d\n", counter());
    printf("%d\n", counter());
    printf("%d\n", counter());
    return 0;
}
