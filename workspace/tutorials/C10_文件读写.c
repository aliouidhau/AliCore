/* ============================================================
   C10：文件读写——把数据从内存搬到磁盘
   ============================================================
   【本课目标】
   1. 会用 fopen / fclose，理解文件指针。
   2. 会用 fprintf / fscanf / fgets 读写文本。
   3. 知道检查 fopen 是否成功。
   4. 知道文本文件和二进制文件的区别。

   本课使用 tmpfile()，不依赖固定路径，也不会留下垃圾文件。
   ============================================================ */

#include <stdio.h>
#include <string.h>

typedef struct {
    char name[16];
    float length;
    float width;
    float height;
    int quantity;
} Part;

int main(void) {
    setbuf(stdout, NULL);

    /* --------------------------------------------------------
       一、打开文件
       --------------------------------------------------------
       fopen 返回 FILE*，失败返回 NULL。
       模式 "w" 写、 "r" 读、 "a" 追加、 "w+" 读写。
       用完必须 fclose。 */
    FILE *file = tmpfile();
    if (file == NULL) {
        printf("无法创建临时文件。\n");
        return 1;
    }

    Part parts[2] = {
        {"底座", 80, 50, 10, 2},
        {"支架", 120, 60, 8, 1}
    };

    /* --------------------------------------------------------
       二、写文件
       --------------------------------------------------------
       fprintf 的用法和 printf 类似，只是多了一个文件指针。
       这里每行写一个零件，用逗号分隔。 */
    int i;
    for (i = 0; i < 2; i++) {
        fprintf(
            file,
            "%s,%.0f,%.0f,%.0f,%d\n",
            parts[i].name,
            parts[i].length,
            parts[i].width,
            parts[i].height,
            parts[i].quantity
        );
    }
    printf("已写入 2 行。\n");

    /* --------------------------------------------------------
       三、回到文件开头再读
       --------------------------------------------------------
       写完以后文件指针在末尾，rewind 把它拨回开头。 */
    rewind(file);

    char line[128];
    printf("\n读回内容：\n");
    while (fgets(line, sizeof(line), file) != NULL) {
        /* fgets 会把换行符也读进来，有时需要去掉。 */
        size_t length = strlen(line);
        if (length > 0 && line[length - 1] == '\n') {
            line[length - 1] = '\0';
        }
        printf("  %s\n", line);
    }

    /* --------------------------------------------------------
       四、解析一行
       --------------------------------------------------------
       sscanf 可以从字符串里按格式提取字段。
       返回值是成功匹配的字段个数，必须检查。 */
    rewind(file);
    printf("\n解析第一行：\n");
    if (fgets(line, sizeof(line), file) != NULL) {
        Part parsed;
        int matched = sscanf(
            line,
            "%15[^,],%f,%f,%f,%d",
            parsed.name,
            &parsed.length,
            &parsed.width,
            &parsed.height,
            &parsed.quantity
        );
        if (matched == 5) {
            printf(
                "  %s：%.0f×%.0f×%.0f，数量 %d\n",
                parsed.name,
                parsed.length,
                parsed.width,
                parsed.height,
                parsed.quantity
            );
        } else {
            printf("  格式不匹配，只读到 %d 个字段。\n", matched);
        }
    }

    /* --------------------------------------------------------
       五、关闭文件
       --------------------------------------------------------
       fclose 会把缓冲区里的数据刷到磁盘并释放资源。
       不关闭文件，数据可能丢，句柄也可能耗尽。 */
    fclose(file);
    printf("\n文件已关闭。\n");

    /* --------------------------------------------------------
       六、常见错误
       --------------------------------------------------------
       1. fopen 返回 NULL 却直接使用。
       2. 用 "w" 打开已有文件，内容会被清空。
       3. 写完不 rewind 就读，读到的是文件末尾。
       4. fgets 忘记去掉换行符。
       5. sscanf 的字段宽度不设上限，可能写越界。
       6. 忘记 fclose。

       七、动手练习
       1. 把零件写入 "parts.csv"，再用文本编辑器打开看。
       2. 读一个 CSV 文件，统计总数量。
       3. 故意用 "w" 打开一个已有文件，观察内容被清空。
       -------------------------------------------------------- */

    printf("\nC10 结束：文件操作三件事——检查、读写、关闭。\n");
    return 0;
}
