#include <stdio.h>

typedef struct {
    char name[16];
    int qty;
} Part;

void set_qty(Part *part, int qty) {
    part->qty == qty;
}

int main(void) {
    Part p = {"轴承", 5};
    set_qty(&p, 12);
    printf("%d\n", p.qty);
    return 0;
}
