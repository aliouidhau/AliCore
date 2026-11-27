#include <stdio.h>
#include <stdlib.h>

/* TODO：分配 n 个 int 并填 0；n <= 0 或失败返回 NULL。 */
int *make_ints(int n) {
    return NULL;
}

/* TODO：释放 make_ints 返回的指针；对 NULL 要安全。 */
void free_ints(int *p) {
}

int main(void) {
    int *a = make_ints(3);
    setbuf(stdout, NULL);
    printf("%s\n", a ? "分配成功" : "分配失败");
    free_ints(a);
    return 0;
}
