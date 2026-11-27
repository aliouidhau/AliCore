/* ============================================================
   C05：数组与字符串——一批同类数据怎么放
   ============================================================
   【本课目标】
   1. 会定义、初始化、遍历数组。
   2. 理解下标从 0 开始，以及越界的危险。
   3. 会用 char 数组保存字符串，认识 strlen / strcpy。
   4. 知道数组作为函数参数时会退化成指针。

   本课不需要输入。
   ============================================================ */

#include <stdio.h>
#include <string.h>

/* 数组参数必须另外传长度，因为函数内部拿不到原始数组大小。 */
int sum_array(const int *data, int count);
double average_array(const int *data, int count);

int main(void) {
    setbuf(stdout, NULL);

    /* --------------------------------------------------------
       一、一维数组
       --------------------------------------------------------
       下标从 0 开始：5 个元素是 data[0] 到 data[4]。
       C 不会自动检查越界，data[5] 可能读到别的内存。 */
    int data[5] = {12, 25, 8, 30, 15};
    int i;

    printf("数组元素：");
    for (i = 0; i < 5; i++) {
        printf("%d ", data[i]);
    }
    printf("\n");

    printf("总和：%d\n", sum_array(data, 5));
    printf("平均值：%.2f\n", average_array(data, 5));

    /* 修改元素 */
    data[2] = 18;
    printf("修改 data[2] 后：%d\n", data[2]);

    /* --------------------------------------------------------
       二、sizeof 和元素个数
       --------------------------------------------------------
       在定义数组的同一个作用域里，可以用 sizeof 算元素个数。
       到了函数参数里，sizeof(data) 得到的只是指针大小。 */
    printf("\n数组总字节：%d\n", (int)sizeof(data));
    printf("一个 int 字节：%d\n", (int)sizeof(data[0]));
    printf("元素个数：%d\n", (int)(sizeof(data) / sizeof(data[0])));

    /* --------------------------------------------------------
       三、字符串就是 char 数组
       --------------------------------------------------------
       C 字符串以 '\0' 结尾。strlen 数的是 '\0' 前面的字符。
       中文在 UTF-8 里占多个字节，strlen 数的是字节，不是汉字数。 */
    char name[] = "底座";
    char copy[32];

    printf("\nname = %s\n", name);
    printf("name 的 strlen = %d\n", (int)strlen(name));
    printf("name 的 sizeof = %d\n", (int)sizeof(name));

    /* strcpy 把源字符串复制到目标；目标必须有足够空间。 */
    strcpy(copy, name);
    printf("copy = %s\n", copy);

    /* strcat 追加；依然要保证目标缓冲区够大。 */
    strcat(copy, "-A");
    printf("追加后 = %s，长度 = %d\n", copy, (int)strlen(copy));

    /* --------------------------------------------------------
       四、字符串数组
       --------------------------------------------------------
       char *names[] 是“指针数组”：每个元素指向一个字符串。
       它和 char names[][...] 的存储方式不同，但都能按下标访问。 */
    const char *names[] = {"底座", "支架", "垫块"};
    int count = (int)(sizeof(names) / sizeof(names[0]));

    printf("\n零件名：\n");
    for (i = 0; i < count; i++) {
        printf("  [%d] %s\n", i, names[i]);
    }

    /* --------------------------------------------------------
       五、常见错误
       --------------------------------------------------------
       1. 访问 data[5]，越界；C 不保证报错。
       2. 用 strlen 数中文汉字个数；UTF-8 下它会偏大。
       3. strcpy 目标缓冲区太小，造成溢出。
       4. 把数组整体赋值：data2 = data 不合法。
       5. 函数里用 sizeof 参数，误以为能拿到数组长度。

       六、动手练习
       1. 找出数组中的最大值和最小值。
       2. 把 names 倒序打印。
       3. 写一个函数，把字符串里的 ASCII 小写字母转成大写。
       -------------------------------------------------------- */

    printf("\nC05 结束：数组快，但边界要自己守。\n");
    return 0;
}

int sum_array(const int *data, int count) {
    int total = 0;
    int i;
    for (i = 0; i < count; i++) {
        total += data[i];
    }
    return total;
}

double average_array(const int *data, int count) {
    if (count <= 0) {
        return 0.0;
    }
    return (double)sum_array(data, count) / count;
}
