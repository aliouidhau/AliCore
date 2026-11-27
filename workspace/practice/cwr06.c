#include <stdio.h>

/* TODO：三个尺寸都 > 0 才算有效。 */
int is_valid_part(double length, double width, double height) {
    return 0;
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%d\n", is_valid_part(10.0, 20.0, 5.0));
    return 0;
}
