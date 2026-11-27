/* ============================================================
   C16：内存错误排查——C 最容易出事的地方
   ============================================================
   【本课目标】
   1. 认识四类内存错误：越界写、野指针、双重释放、内存泄漏。
   2. 知道哪些错误会崩、哪些不会崩但更危险。
   3. 学会三种自检手段：边界检查、配对计数、哨兵值。
   4. 知道真实项目的工具（valgrind / AddressSanitizer）是干什么的。

   【本机环境说明】
   本机只有 TinyCC，没有 valgrind / ASan。
   所以本课教「自己写检查」，这也是理解工具原理的前提。
   ============================================================ */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* --------------------------------------------------------
   一、四类内存错误
   --------------------------------------------------------
   1. 越界写  int a[3]; a[3] = 1;
      —— 数组只有 0,1,2 三个位置，a[3] 写到数组外面。
      —— 不报错、不崩，悄悄覆盖了别的变量。这种最难查。

   2. 野指针  int *p; *p = 5;
      —— p 没初始化，指向随机地址。
      —— 大概率立刻崩（访问违例）。

   3. 双重释放  free(p); free(p);
      —— 同一块内存释放两次，堆结构被破坏，之后分配随机出错。

   4. 内存泄漏  p = malloc(...); p = malloc(...);
      —— 第一块再也找不到，但程序没崩。跑得越久占得越多，最后分配失败。

   会崩的错误反而好查；不崩的（越界写、泄漏）才是真麻烦。
   -------------------------------------------------------- */

/* --------------------------------------------------------
   二、手段 1：边界检查（自己拦住，而不是等它崩）
   --------------------------------------------------------
   所有接受"下标/长度"的函数，进来先检查。 */

int array_set(int *data, int size, int index, int value) {
    if (data == NULL) {
        return -1;                      /* 空指针 */
    }
    if (size <= 0) {
        return -2;                      /* 长度不合法 */
    }
    if (index < 0 || index >= size) {
        return -3;                      /* 越界 */
    }
    data[index] = value;
    return 0;
}

/* --------------------------------------------------------
   三、手段 2：配对计数（检查 malloc 和 free 是否配平）
   --------------------------------------------------------
   分配一次 +1，释放一次 -1。程序结束时应该回到 0。
   真实项目里这是最便宜的泄漏检查。 */

static int g_alloc_count = 0;

void *part_alloc(size_t size) {
    void *p = malloc(size);
    if (p) {
        g_alloc_count++;
    }
    return p;
}

void part_free(void *p) {
    if (p) {
        g_alloc_count--;
        free(p);
    }
}

int part_alloc_balance(void) {
    return g_alloc_count;
}

/* --------------------------------------------------------
   四、手段 3：哨兵值（看有没有被越界写坏）
   --------------------------------------------------------
   在数组两头各放一个已知值，检查时看它们还在不在。
   边界被写坏，哨兵就会变。 */

#define SENTINEL 0x5A5A
#define CAPACITY 4

typedef struct {
    int guard_head;
    int slot[CAPACITY];
    int guard_tail;
} GuardedInts;

void guarded_init(GuardedInts *g) {
    int i;
    g->guard_head = SENTINEL;
    g->guard_tail = SENTINEL;
    for (i = 0; i < CAPACITY; i++) {
        g->slot[i] = 0;
    }
}

int guarded_set(GuardedInts *g, int index, int value) {
    if (index < 0 || index >= CAPACITY) {
        return -1;
    }
    g->slot[index] = value;
    return 0;
}

int guarded_ok(const GuardedInts *g) {
    return g->guard_head == SENTINEL && g->guard_tail == SENTINEL;
}

/* --------------------------------------------------------
   五、真实项目用什么工具
   --------------------------------------------------------
   · AddressSanitizer（gcc/clang 的 -fsanitize=address）
     越界、野指针、双重释放都能当场报出来，还告诉你哪一行。
   · Valgrind（Linux）
     跑得慢，但泄漏和越界都能查出来。
   · Windows 上的替代：Visual Studio 的调试堆、Application Verifier。

   工具会告诉你问题，但"为什么会越界"还是要你自己想。
   所以先用本课这三招把思路建立起来。

   六、常见错误
   --------------------------------------------------------
   1. 只检查"能不能分配"，不检查"有没有释放"。
   2. 越界后再用指针运算继续走，读到垃圾值。
   3. 释放后继续用（use-after-free），比双重释放更常见。
   4. 用 malloc 分配的数组，拿 sizeof 测大小——那是指针大小。

   七、动手练习
   1. 在 main 里给 guarded_set 故意传 index = 4，看返回 -1 而不是崩。
   2. 故意漏掉一次 part_free，观察 part_alloc_balance() 变成 1。
   3. 写一个函数 int safe_sum(const int *a, int n, int *out)：
      检查 a 和 out 非空、n > 0，返回 0 表示成功。
   -------------------------------------------------------- */

int main(void) {
    setbuf(stdout, NULL);

    printf("=== 一、边界检查 ===\n");
    int data[3] = {0, 0, 0};
    printf("写 index=2：返回 %d\n", array_set(data, 3, 2, 42));
    printf("写 index=3：返回 %d（-3 表示越界被拦住）\n", array_set(data, 3, 3, 42));
    printf("空指针：返回 %d\n", array_set(NULL, 3, 0, 1));
    printf("data[2] = %d\n", data[2]);

    printf("\n=== 二、分配与释放配对 ===\n");
    printf("开始时未释放块数：%d\n", part_alloc_balance());
    int *p = (int *)part_alloc(4 * sizeof(int));
    int *q = (int *)part_alloc(4 * sizeof(int));
    printf("分配 2 块后：%d\n", part_alloc_balance());
    part_free(p);
    printf("释放 1 块后：%d\n", part_alloc_balance());
    part_free(q);
    printf("全部释放后：%d（不等于 0 就是有泄漏）\n", part_alloc_balance());

    printf("\n=== 三、哨兵值 ===\n");
    GuardedInts g;
    guarded_init(&g);
    printf("初始化后哨兵完好：%s\n", guarded_ok(&g) ? "是" : "否");
    guarded_set(&g, 2, 99);
    printf("正常写 slot[2] 后哨兵完好：%s\n", guarded_ok(&g) ? "是" : "否");
    g.guard_tail = 0;   /* 假装这里被越界写坏了 */
    printf("模拟越界写后哨兵完好：%s\n", guarded_ok(&g) ? "是" : "否（说明被写穿了）");

    printf("\nC16 结束：会崩的不可怕，不崩的最可怕。\n");
    return 0;
}
