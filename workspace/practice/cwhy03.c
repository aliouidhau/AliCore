#include <stdio.h>

int step_bytes(void) {
    int data[2] = {1, 2};
    int *p = data;
    int *q = p + 1;
    return (int)((char *)q - (char *)p);
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%d\n", step_bytes());
    return 0;
}
