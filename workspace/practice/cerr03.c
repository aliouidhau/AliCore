#include <stdio.h>

int read_qty(int *p) {
    return *p;
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%d\n", read_qty(0));
    return 0;
}
