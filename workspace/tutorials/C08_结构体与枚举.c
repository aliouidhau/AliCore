/* ============================================================
   C08：结构体与枚举——把相关字段绑在一起
   ============================================================
   【本课目标】
   1. 会定义 struct，保存一组相关字段。
   2. 会用 typedef 简化类型名。
   3. 会定义结构体数组并遍历。
   4. 会用 enum 表示有限状态。

   本课不需要输入。
   ============================================================ */

#include <stdio.h>

/* ------------------------------------------------------------
   一、结构体
   ------------------------------------------------------------
   struct Part 把名称、尺寸、数量放在同一个对象里。
   没有结构体时，你只能开四个平行数组，很容易下标错位。 */
struct Part {
    char name[16];
    float length;
    float width;
    float height;
    int quantity;
};

/* typedef 给类型起别名，后面可以写 Part，不必写 struct Part。 */
typedef struct Part Part;

typedef enum {
    STOCK_OK = 0,
    STOCK_LOW = 1,
    STOCK_BAD = 2
} StockStatus;

float part_volume(const Part *part);
StockStatus stock_status(const Part *part);
const char *status_text(StockStatus status);
void print_part(const Part *part);

int main(void) {
    setbuf(stdout, NULL);

    /* --------------------------------------------------------
       二、创建结构体变量
       --------------------------------------------------------
       可以逐个赋值，也可以用花括号按字段顺序初始化。 */
    Part base;
    base.length = 80;
    base.width = 50;
    base.height = 10;
    base.quantity = 2;
    /* 字符串数组不能直接赋值，要用 strcpy 或初始化时写入。 */
    snprintf(base.name, sizeof(base.name), "%s", "底座");

    Part bracket = {"支架", 120, 60, 8, 1};
    Part shim = {"垫块", 30, 30, 5, 6};

    Part parts[3] = {base, bracket, shim};

    printf("零件清单：\n");
    int i;
    for (i = 0; i < 3; i++) {
        print_part(&parts[i]);
    }

    /* --------------------------------------------------------
       三、结构体指针
       --------------------------------------------------------
       传结构体本身会复制整个结构；传指针只复制地址，更高效。
       用 p->field 访问指针指向结构体的字段。 */
    printf("\n用结构体指针访问：\n");
    Part *cursor = &parts[1];
    printf("名称：%s，长度：%.1f\n", cursor->name, cursor->length);

    /* --------------------------------------------------------
       四、枚举：给有限状态起名字
       --------------------------------------------------------
       枚举值本质是整数，但名字让代码可读。
       switch 适合处理有限分支。 */
    printf("\n库存状态：\n");
    for (i = 0; i < 3; i++) {
        StockStatus status = stock_status(&parts[i]);
        printf("  %s：%s\n", parts[i].name, status_text(status));
    }

    /* --------------------------------------------------------
       五、常见错误
       --------------------------------------------------------
       1. 字符串字段直接用 = 赋值。
       2. 结构体字段顺序写错，初始化值全部错位。
       3. 传结构体指针却用 . 访问字段。
       4. 枚举值当成字符串打印。
       5. 结构体太大时频繁按值传参，造成复制开销。

       六、动手练习
       1. 给 Part 增加一个 `unit_price` 字段，计算库存金额。
       2. 写一个函数，返回库存最少的零件。
       3. 用 enum 表示“合格 / 待检 / 报废”三种状态。
       -------------------------------------------------------- */

    printf("\nC08 结束：结构体让字段有归属，枚举让状态有名字。\n");
    return 0;
}

float part_volume(const Part *part) {
    return part->length * part->width * part->height;
}

StockStatus stock_status(const Part *part) {
    if (part->quantity <= 0) {
        return STOCK_BAD;
    }
    if (part->quantity < 2) {
        return STOCK_LOW;
    }
    return STOCK_OK;
}

const char *status_text(StockStatus status) {
    switch (status) {
        case STOCK_OK:
            return "正常";
        case STOCK_LOW:
            return "低库存";
        case STOCK_BAD:
            return "无库存";
        default:
            return "未知";
    }
}

void print_part(const Part *part) {
    printf(
        "  %-4s %.0f×%.0f×%.0f 数量 %d 体积 %.0f\n",
        part->name,
        part->length,
        part->width,
        part->height,
        part->quantity,
        part_volume(part) * part->quantity
    );
}
