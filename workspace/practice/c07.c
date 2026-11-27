#include <stdio.h>

int text_length(const char *s) {
    int count = 0;
    while (*s != '\0') {
        count++;
        s++;
    }
    return count + 1;
}

int main(void) {
    printf("%d\n", text_length("hello"));
    return 0;
}
