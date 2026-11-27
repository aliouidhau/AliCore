/* ============================================================
   part_impl.c —— 零件模块的「实现」（函数体）
   ============================================================
   真实项目里它是单独的编译单元：
       tcc -c part_impl.c -o part_impl.o
   本课的教程为了能直接运行，用 #include 把它并进来。
   ============================================================ */

#include "part_api.h"

/* static：计数只在本编译单元可见，外面通过 part_internal_calls() 读 */
static int s_volume_calls = 0;

/* 内部辅助函数：static 意味着别的 .c 调不到它 */
static double part_volume_raw(double l, double w, double h) {
    if (l <= 0 || w <= 0 || h <= 0) {
        return 0.0;
    }
    return l * w * h;
}

double part_volume(const Part *p) {
    if (p == NULL) {
        return 0.0;
    }
    s_volume_calls++;
    return part_volume_raw(p->length, p->width, p->height);
}

double part_mass(const Part *p, double density) {
    if (density <= 0) {
        return 0.0;
    }
    return part_volume(p) * density;
}

int part_is_valid(const Part *p) {
    if (p == NULL) {
        return 0;
    }
    return p->length > 0 && p->width > 0 && p->height > 0;
}

const char *part_grade(const Part *p) {
    double v = part_volume(p);
    if (v >= 100000.0) {
        return "大件";
    }
    if (v >= 1000.0) {
        return "中件";
    }
    return "小件";
}

int part_internal_calls(void) {
    return s_volume_calls;
}
