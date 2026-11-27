/* ============================================================
   C11：综合——零件库存链表
   ============================================================
   【本课目标】
   1. 把结构体、指针、动态内存合起来用。
   2. 会写单链表的插入、遍历、汇总、释放。
   3. 理解“头指针”和“节点指针”的区别。
   4. 知道链表适合频繁插入、但不适合随机访问。

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

typedef struct Node {
    Part part;
    struct Node *next;
} Node;

void append(Node **head, const Part *part);
void print_list(const Node *head);
float total_volume(const Node *head);
int count_low_stock(const Node *head);
void free_list(Node *head);

int main(void) {
    setbuf(stdout, NULL);

    Node *head = NULL;

    /* --------------------------------------------------------
       一、为什么用 Node**？
       --------------------------------------------------------
       要在函数里修改 head 本身（让它指向新节点），
       必须把 head 的地址传进去，所以参数是 Node**。 */
    Part base = {"底座", 80, 50, 10, 2};
    Part bracket = {"支架", 120, 60, 8, 1};
    Part shim = {"垫块", 30, 30, 5, 6};
    Part bearing = {"轴承", 20, 20, 15, 3};

    append(&head, &base);
    append(&head, &bracket);
    append(&head, &shim);
    append(&head, &bearing);

    printf("链表内容：\n");
    print_list(head);

    printf("\n库存总体积：%.0f\n", total_volume(head));
    printf("低库存节点数：%d\n", count_low_stock(head));

    /* --------------------------------------------------------
       二、链表的代价
       --------------------------------------------------------
       每个节点单独 malloc，节点之间用 next 连接。
       插入很方便，但查找第 n 个节点要从头走 n 步。
       数组随机访问快，链表插入快，选择看场景。 */
    printf("\n头节点：%s\n", head->part.name);
    printf("第二个节点：%s\n", head->next->part.name);

    /* --------------------------------------------------------
       三、释放整条链表
       --------------------------------------------------------
       不能只 free(head)，否则后面的节点全部丢失。
       要先用 next 记住下一个，再释放当前。 */
    free_list(head);
    head = NULL;
    printf("\n链表已释放。\n");

    /* --------------------------------------------------------
       四、常见错误
       --------------------------------------------------------
       1. append 里不检查 malloc 失败。
       2. 插入时没有把新节点的 next 接上。
       3. 遍历时提前 free 当前节点，导致 next 丢失。
       4. 头指针没有初始化，指向随机地址。
       5. 释放后继续使用 head。

       五、动手练习
       1. 写一个 find_by_name，按名称查找节点。
       2. 写一个 remove_by_name，删除节点并释放。
       3. 把链表改成按体积从大到小插入的有序链表。
       -------------------------------------------------------- */

    printf("\nC11 结束：链表是“节点 + 指针”的组合拳。\n");
    return 0;
}

void append(Node **head, const Part *part) {
    Node *node = (Node *)malloc(sizeof(Node));
    if (node == NULL) {
        return;
    }
    node->part = *part;
    node->next = NULL;

    if (*head == NULL) {
        *head = node;
        return;
    }

    Node *cursor = *head;
    while (cursor->next != NULL) {
        cursor = cursor->next;
    }
    cursor->next = node;
}

void print_list(const Node *head) {
    const Node *cursor = head;
    while (cursor != NULL) {
        printf(
            "  %-4s %.0f×%.0f×%.0f 数量 %d\n",
            cursor->part.name,
            cursor->part.length,
            cursor->part.width,
            cursor->part.height,
            cursor->part.quantity
        );
        cursor = cursor->next;
    }
}

float total_volume(const Node *head) {
    float total = 0;
    const Node *cursor = head;
    while (cursor != NULL) {
        total += cursor->part.length * cursor->part.width * cursor->part.height * cursor->part.quantity;
        cursor = cursor->next;
    }
    return total;
}

int count_low_stock(const Node *head) {
    int count = 0;
    const Node *cursor = head;
    while (cursor != NULL) {
        if (cursor->part.quantity < 2) {
            count++;
        }
        cursor = cursor->next;
    }
    return count;
}

void free_list(Node *head) {
    Node *cursor = head;
    while (cursor != NULL) {
        Node *next = cursor->next;
        free(cursor);
        cursor = next;
    }
}
