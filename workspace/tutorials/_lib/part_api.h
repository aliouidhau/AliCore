/* ============================================================
   part_api.h —— 零件模块的「契约」（头文件）
   ============================================================
   这里只有声明，没有函数体。
   谁要用零件模块，就 #include 这个文件。
   ============================================================ */

#ifndef PART_API_H
#define PART_API_H

/* 单独编译时也要有 NULL：头文件自己带齐依赖，别指望调用方 */
#include <stddef.h>

#define PART_NAME_MAX 24

/* 一个零件：名字 + 长宽高（毫米） */
typedef struct {
    char   name[PART_NAME_MAX];
    double length;
    double width;
    double height;
} Part;

/* 体积（立方毫米） */
double part_volume(const Part *p);

/* 质量 = 体积 × 密度 */
double part_mass(const Part *p, double density);

/* 尺寸是否合法：三个方向都 > 0 */
int part_is_valid(const Part *p);

/* 粗略分级：大件 / 中件 / 小件（按体积） */
const char *part_grade(const Part *p);

/* 实现文件内部记录的函数调用次数（演示模块自省） */
int part_internal_calls(void);

#endif /* PART_API_H */
