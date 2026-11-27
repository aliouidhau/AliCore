#include <stdio.h>

/* TODO：在这里写两个函数原型（声明）。
   double part_volume(double length, double width, double height);
   double part_mass(double volume, double density); */

int main(void) {
    setbuf(stdout, NULL);
    printf("%.1f\n", part_volume(2.0, 3.0, 4.0));
    printf("%.1f\n", part_mass(part_volume(2.0, 3.0, 4.0), 1.5));
    return 0;
}

/* TODO：在这里写两个函数体（实现）。 */
