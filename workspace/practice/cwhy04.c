#include <stdio.h>

void add_one(int x) {
    x = x + 1;
}

int main(void) {
    int x = 5;
    setbuf(stdout, NULL);
    add_one(x);
    printf("%d\n", x);
    return 0;
}
