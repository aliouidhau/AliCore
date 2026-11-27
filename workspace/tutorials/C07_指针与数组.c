/* ============================================================
   C07：指针与数组——把“连续内存”用起来
   ============================================================
   【本课目标】
   1. 理解数组名退化成首地址。
   2. 会用指针遍历数组和字符串。
   3. 会区分“指针数组”和“二维数组”。
   4. 知道 const 指针能限制写权限。

   本课不需要输入。
   ============================================================ */

#include <stdio.h>
#include <string.h>

void print_array(const int *data, int count);
void print_string_by_pointer(const char *text);

int main(void) {
    setbuf(stdout, NULL);

    /* --------------------------------------------------------
       一、数组名就是首元素地址
       --------------------------------------------------------
       data 和 &data[0] 的值相同，但类型语义略有差别。
       日常遍历时，把 data 当成 int* 使用就够了。 */
    int data[6] = {3, 6, 9, 12, 15, 18};
    int count = (int)(sizeof(data) / sizeof(data[0]));

    printf("data 地址：%p\n", (void *)data);
    printf("&data[0] 地址：%p\n", (void *)&data[0]);

    printf("\n用指针遍历：\n");
    print_array(data, count);

    /* --------------------------------------------------------
       二、指针遍历字符串
       --------------------------------------------------------
       字符串以 '\0' 结尾；指针每步移动到下一个字符，
       遇到 '\0' 就停。 */
    const char *name = "逻辑之塔";
    printf("\n逐字符读取：\n");
    print_string_by_pointer(name);

    /* --------------------------------------------------------
       三、指针数组 vs 二维数组
       --------------------------------------------------------
       const char *names[3]：3 个指针，每个指向一个字符串。
       char grid[3][8]：3 行，每行 8 个 char，内存连续。
       前者适合长度差别大的字符串；后者适合固定宽度表格。 */
    const char *names[3] = {"底座", "支架", "垫块"};
    printf("\n指针数组：\n");
    int i;
    for (i = 0; i < 3; i++) {
        printf("  names[%d] -> %s（长度 %d）\n", i, names[i], (int)strlen(names[i]));
    }

    char grid[3][8] = {"A1", "B2", "C3"};
    printf("\n二维字符数组：\n");
    for (i = 0; i < 3; i++) {
        printf("  grid[%d] = %s\n", i, grid[i]);
    }

    /* --------------------------------------------------------
       四、const 指针
       --------------------------------------------------------
       const int *p：不能通过 p 修改它指向的数据。
       但 p 本身可以指向别处。
       这在函数参数里很常见：我只读，不改你的数组。 */
    const int *read_only = data;
    printf("\n只读指针第一个值：%d\n", *read_only);
    /* read_only[0] = 100;  // 这行会被编译器拒绝 */
    read_only++;
    printf("只读指针后移一位：%d\n", *read_only);

    /* --------------------------------------------------------
       五、常见错误
       --------------------------------------------------------
       1. 以为 sizeof(data) 在函数参数里还是整个数组大小。
       2. 指针越界后继续解引用。
       3. 用 char* 指向字符串字面量后尝试修改，行为未定义。
       4. 混淆“指针数组”和“二维数组”的内存布局。
       5. const 只限制通过该指针写入，不保证别的指针不改。

       六、动手练习
       1. 写一个函数，用指针统计字符串里有多少个大写字母。
       2. 用指针把 int 数组倒序。
       3. 画图说明 names[3] 和 grid[3][8] 的内存区别。
       -------------------------------------------------------- */

    printf("\nC07 结束：连续内存 + 指针偏移 = C 的基本功。\n");
    return 0;
}

void print_array(const int *data, int count) {
    const int *cursor = data;
    int i;
    for (i = 0; i < count; i++) {
        printf("  [%d] 值=%d 地址=%p\n", i, *(cursor + i), (void *)(cursor + i));
    }
}

void print_string_by_pointer(const char *text) {
    const char *cursor = text;
    while (*cursor != '\0') {
        printf("  '%c' (ASCII %d)\n", *cursor, (int)*cursor);
        cursor++;
    }
}
