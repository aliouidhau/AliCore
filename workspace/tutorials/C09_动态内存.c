/* ============================================================
   C09：动态内存——运行时才决定要多少空间
   ============================================================
   【本课目标】
   1. 理解栈内存和堆内存的区别。
   2. 会用 malloc / calloc / realloc / free。
   3. 知道 malloc 可能失败，必须检查 NULL。
   4. 知道 malloc 和 free 必须成对出现。

   本课不需要输入。
   ============================================================ */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char name[16];
    float length;
    float width;
    float height;
    int quantity;
} Part;

void set_part(Part *part, const char *name, float l, float w, float h, int q);
void print_parts(const Part *parts, int count);
void free_parts(Part *parts);

int main(void) {
    setbuf(stdout, NULL);

    /* --------------------------------------------------------
       一、为什么需要动态内存？
       --------------------------------------------------------
       数组 int data[10] 的大小在编译时就固定了。
       如果数量要运行时才知道（例如读文件、用户输入），
       就要向堆申请空间。malloc 返回首地址，失败返回 NULL。 */
    int count = 3;
    Part *parts = (Part *)malloc((size_t)count * sizeof(Part));
    if (parts == NULL) {
        printf("内存分配失败。\n");
        return 1;
    }

    /* --------------------------------------------------------
       二、使用申请到的空间
       --------------------------------------------------------
       malloc 不会初始化内容；calloc 会把内容清零。
       这里逐个填入零件数据。 */
    set_part(&parts[0], "底座", 80, 50, 10, 2);
    set_part(&parts[1], "支架", 120, 60, 8, 1);
    set_part(&parts[2], "垫块", 30, 30, 5, 4);

    printf("初始数组：\n");
    print_parts(parts, count);

    /* --------------------------------------------------------
       三、realloc：扩大或缩小
       --------------------------------------------------------
       realloc 可能把旧数据搬到新地址。
       因此必须用临时指针接收返回值，避免原指针丢失。 */
    int new_count = 5;
    Part *bigger = (Part *)realloc(parts, (size_t)new_count * sizeof(Part));
    if (bigger == NULL) {
        printf("扩容失败，原数组仍然有效。\n");
        free(parts);
        return 1;
    }
    parts = bigger;

    set_part(&parts[3], "轴承", 20, 20, 15, 3);
    set_part(&parts[4], "端盖", 50, 50, 6, 2);

    printf("\n扩容后：\n");
    print_parts(parts, new_count);

    /* --------------------------------------------------------
       四、calloc：申请并清零
       --------------------------------------------------------
       calloc(count, size) 适合“初始值必须是 0”的场景。 */
    int *zeros = (int *)calloc(5, sizeof(int));
    if (zeros == NULL) {
        printf("calloc 失败。\n");
        free_parts(parts);
        return 1;
    }
    printf("\ncalloc 初始值：");
    int i;
    for (i = 0; i < 5; i++) {
        printf("%d ", zeros[i]);
    }
    printf("\n");
    free(zeros);

    /* --------------------------------------------------------
       五、释放
       --------------------------------------------------------
       谁申请，谁释放。free 后不要再访问那块内存。
       释放后把指针设为 NULL，能减少误用。 */
    free_parts(parts);
    parts = NULL;
    printf("\n已释放动态数组。\n");

    /* --------------------------------------------------------
       六、常见错误
       --------------------------------------------------------
       1. malloc 后不检查 NULL 就直接写。
       2. 申请多少字节算错，例如忘了 sizeof(Part)。
       3. free 后继续使用（悬空指针）。
       4. 同一块内存 free 两次。
       5. realloc 直接用原指针接收，失败后原地址丢失。
       6. 只释放数组首地址，却忘了释放数组元素里再申请的内存。

       七、动手练习
       1. 让用户输入数量，动态创建 Part 数组。
       2. 写一个函数，把数组扩容并返回新指针。
       3. 用 calloc 创建 10 个 int，全部打印为 0。
       -------------------------------------------------------- */

    printf("\nC09 结束：动态内存给你自由，也把收尾责任交给你。\n");
    return 0;
}

void set_part(Part *part, const char *name, float l, float w, float h, int q) {
    /* 这个函数只填写调用者已经拥有的结构体，不额外申请内存。
       这样不会产生“申请了却没人释放”的隐藏泄漏。 */
    snprintf(part->name, sizeof(part->name), "%s", name);
    part->length = l;
    part->width = w;
    part->height = h;
    part->quantity = q;
}

void print_parts(const Part *parts, int count) {
    int i;
    for (i = 0; i < count; i++) {
        printf(
            "  %-4s %.0f×%.0f×%.0f 数量 %d 合计 %.0f\n",
            parts[i].name,
            parts[i].length,
            parts[i].width,
            parts[i].height,
            parts[i].quantity,
            parts[i].length * parts[i].width * parts[i].height * parts[i].quantity
        );
    }
}

void free_parts(Part *parts) {
    free(parts);
}
