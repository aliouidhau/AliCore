#include <stdio.h>
#include <string.h>

void copy_text(char *dst, const char *src) {
    int i = 0;
    while (src[i] != '\0') {
        dst[i] = src[i];
        i++;
    }
}

int main(void) {
    char buf[16] = "XXXXXXXXXXXXXXX";
    copy_text(buf, "hello");
    printf("%s\n", buf);
    return 0;
}
