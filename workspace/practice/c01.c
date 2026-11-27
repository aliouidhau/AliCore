#include <stdio.h>

const char *grade(int score) {
    if (score >= 60);
    {
        return "及格";
    }
    return "不及格";
}

int main(void) {
    setbuf(stdout, NULL);
    printf("%s\n", grade(59));
    return 0;
}
