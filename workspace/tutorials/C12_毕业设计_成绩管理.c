/* ============================================================
   C12：毕业设计——成绩管理报表
   ============================================================
   【本课目标】
   把 C 语言的核心能力合成一个可交付的小项目：
   结构体、数组、函数、排序、计算、格式化输出。

   【项目需求】
   1. 保存若干学生成绩。
   2. 计算每个人的总分和平均分。
   3. 按总分从高到低排序。
   4. 输出排名、最高分、最低分、班级平均。
   5. 标记不及格科目。

   本课不需要输入，运行即可看到完整报表。
   ============================================================ */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char name[16];
    float chinese;
    float math;
    float english;
    float total;
    float average;
} Student;

void calculate(Student *student);
int compare_by_total(const void *left, const void *right);
void print_report(const Student *students, int count);
int failed_count(const Student *student);

int main(void) {
    setbuf(stdout, NULL);

 /* --------------------------------------------------------
       一、准备数据
       --------------------------------------------------------
       真实项目里这些数据会来自文件或输入。
       这里先写死，重点是后面的计算和报表结构。 */
    Student students[] = {
        {"学生甲", 86, 92, 78, 0, 0},
        {"学生乙", 74, 65, 88, 0, 0},
        {"学生丙", 58, 71, 63, 0, 0},
        {"学生丁", 91, 88, 95, 0, 0},
        {"学生戊", 66, 54, 72, 0, 0}
    };

    int count = (int)(sizeof(students) / sizeof(students[0]));
    int i;

    /* --------------------------------------------------------
       二、计算派生字段
       --------------------------------------------------------
       总分和平均分不手工填，统一由 calculate 算，
       避免数据不一致。 */
    for (i = 0; i < count; i++) {
        calculate(&students[i]);
    }

    /* --------------------------------------------------------
       三、排序
       --------------------------------------------------------
       qsort 需要比较函数；返回负数表示左边应排在前面。
       这里按总分从高到低，所以总分低的返回正数。 */
    qsort(students, (size_t)count, sizeof(Student), compare_by_total);

    /* --------------------------------------------------------
       四、输出报表
       --------------------------------------------------------
       报表只负责展示，不重新计算业务数据。 */
    print_report(students, count);

    /* --------------------------------------------------------
       五、常见错误
       --------------------------------------------------------
       1. 比较函数返回布尔值，排序结果不稳定。
       2. qsort 的元素个数或元素大小传错。
       3. 排序后下标和原始顺序对不上，却仍按原下标引用。
       4. 平均分除以 0 或科目数写错。
       5. 输出格式宽度不够，中文名字对不齐。

       六、继续扩展
       1. 从 CSV 读学生数据。
       2. 增加“按数学成绩排序”的第二个报表。
       3. 把 calculate / compare / print 拆成模块。
       4. 用文件保存报表，再用另一程序读取。
       -------------------------------------------------------- */

    printf("\nC12 结束：能定义数据、算对结果、稳定输出，就是毕业。\n");
    return 0;
}

void calculate(Student *student) {
    student->total = student->chinese + student->math + student->english;
    student->average = student->total / 3.0f;
}

int compare_by_total(const void *left, const void *right) {
    const Student *a = (const Student *)left;
    const Student *b = (const Student *)right;

    if (a->total < b->total) {
        return 1;   /* a 总分低，排到后面 */
    }
    if (a->total > b->total) {
        return -1;  /* a 总分高，排到前面 */
    }
    return 0;
}

int failed_count(const Student *student) {
    int count = 0;
    if (student->chinese < 60) count++;
    if (student->math < 60) count++;
    if (student->english < 60) count++;
    return count;
}

void print_report(const Student *students, int count) {
    printf("============================================================\n");
    printf("成绩管理报表\n");
    printf("============================================================\n");
    printf("排名  姓名       语文  数学  英语  总分   平均   不及格科目\n");

    int i;
    float class_total = 0;
    float highest = -1;
    float lowest = 1000;

    for (i = 0; i < count; i++) {
        printf(
            "%-4d  %-8s  %5.1f %5.1f %5.1f %6.1f %6.2f  %d\n",
            i + 1,
            students[i].name,
            students[i].chinese,
            students[i].math,
            students[i].english,
            students[i].total,
            students[i].average,
            failed_count(&students[i])
        );
        class_total += students[i].total;
        if (students[i].total > highest) highest = students[i].total;
        if (students[i].total < lowest) lowest = students[i].total;
    }

    printf("------------------------------------------------------------\n");
    printf("班级总分：%.1f\n", class_total);
    printf("班级平均：%.2f\n", class_total / count);
    printf("最高总分：%.1f\n", highest);
    printf("最低总分：%.1f\n", lowest);
    printf("============================================================\n");

    printf("\n毕业检查：\n");
    printf(" 1. 数据结构是否表达了业务对象？\n");
    printf(" 2. 计算逻辑是否只在一个地方？\n");
    printf(" 3. 排序比较函数是否正确？\n");
    printf(" 4. 输出是否对齐、可读？\n");
    printf(" 5. 是否检查了数组长度和边界？\n");
}
