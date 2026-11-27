# ============================================================
# 第 23 课：JSON 与接口数据——程序之间怎么交接
# ============================================================
# 【本课目标】
# 1. 理解 JSON 的几种基本类型。
# 2. 会用 json.loads / json.dumps 做序列化和反序列化。
# 3. 会把接口数据转成 dataclass，并处理缺失字段。
#
# 【心智模型】
# JSON 像一张标准交接单：
# 字段名、类型、嵌套关系都写清楚，不同程序才不用猜。
# Python 对象是内存里的零件，JSON 是装箱单。

import json
from dataclasses import dataclass, asdict
from urllib.parse import urlencode

# ------------------------------------------------------------
# 一、JSON 的基本类型
# ------------------------------------------------------------
# JSON 支持：对象 {}、数组 []、字符串 ""、数字、true/false、null。
# 它不支持 Python 的 tuple、set、datetime、自定义对象。
# 这些类型要先转换成 JSON 能表达的形式。

payload = {
    "workshop": "一号车间",
    "parts": [
        {"name": "底座", "size": [80, 50, 10], "quantity": 2},
        {"name": "支架", "size": [120, 60, 8], "quantity": 1},
    ],
    "active": True,
    "remark": None,
}

text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
print("序列化后的 JSON：")
print(text)

# ensure_ascii=False 让中文保持中文，而不是 \uXXXX。
# indent=2 方便人阅读；传输时通常不加缩进，减少体积。
# sort_keys=True 让字段顺序稳定，便于比较和测试。

# ------------------------------------------------------------
# 二、反序列化
# ------------------------------------------------------------
# json.loads 把字符串变回 Python 的 dict / list。
parsed = json.loads(text)
print("\n反序列化后的类型：", type(parsed).__name__)
print("车间：", parsed["workshop"])
print("第一个零件：", parsed["parts"][0])

# 注意：JSON 的 true/false/null 会变成 Python 的 True/False/None。
# JSON 的数字会变成 int 或 float；JSON 没有 tuple，只有 list。

# ------------------------------------------------------------
# 三、把接口数据转成 dataclass
# ------------------------------------------------------------
# 直接用 dict 也能工作，但字段一多就容易写错 key。
# dataclass 让字段有名字、有类型，也方便统一校验。

@dataclass
class Part:
    name: str
    length: float
    width: float
    height: float
    quantity: int = 1

    @classmethod
    def from_api(cls, row: dict) -> "Part":
        # 用 .get 处理缺失字段，给出明确默认值。
        size = row.get("size") or [0, 0, 0]
        if len(size) != 3:
            raise ValueError("size 必须是三个数字")
        return cls(
            name=row["name"],
            length=float(size[0]),
            width=float(size[1]),
            height=float(size[2]),
            quantity=int(row.get("quantity", 1)),
        )

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height * self.quantity


parts = [Part.from_api(row) for row in parsed["parts"]]
for part in parts:
    print(f"{part.name}: {part.volume} mm³")

# asdict 可以把 dataclass 转回 dict，再交给 json.dumps。
print("\n转回 JSON：")
print(json.dumps([asdict(part) for part in parts], ensure_ascii=False, indent=2))

# ------------------------------------------------------------
# 四、构造查询字符串
# ------------------------------------------------------------
# 真正发请求时，参数不能手写拼接，否则中文、空格、& 都会出问题。
# urlencode 会负责转义。

query = urlencode({"workshop": "一号车间", "keyword": "底座 A", "page": 2})
print("\n查询字符串：", query)
print("完整 URL：", "https://example.local/api/parts?" + query)

# 这里不真的联网：教程需要离线可运行。
# 真正请求时通常用 urllib.request 或第三方 requests，
# 但第一步永远是：先确认返回的 JSON 结构。

# ------------------------------------------------------------
# 五、错误处理：接口数据不可信
# ------------------------------------------------------------
# 真实接口可能缺字段、类型不对、返回 HTML 错误页。
# 不要假设 row["size"] 一定存在，也不要把异常吞掉。

def parse_rows(rows):
    results = []
    for index, row in enumerate(rows):
        try:
            results.append(Part.from_api(row))
        except (KeyError, TypeError, ValueError) as error:
            print(f"第 {index} 条数据跳过：{error}")
    return results


bad_rows = [
    {"name": "正常件", "size": [10, 20, 30]},
    {"name": "缺尺寸"},
    {"name": "尺寸不对", "size": [10, 20]},
    {"name": "数量非法", "size": [10, 20, 30], "quantity": "abc"},
]
valid = parse_rows(bad_rows)
print("\n可用数据条数：", len(valid))

# ------------------------------------------------------------
# 六、常见错误
# ------------------------------------------------------------
# 1. 把 json.loads 和 json.load 混用：前者接字符串，后者接文件对象。
# 2. 用 eval 解析 JSON。eval 会执行代码，绝对不要这么做。
# 3. 直接 row["field"]，接口一改字段就崩。
# 4. 中文乱码：读写文件时忘记 encoding="utf-8"。
# 5. 把 JSON 当数据库，每次全量读写；数据量大时要换存储。
#
# JSON 是交换格式，不是长期数据库。

# ------------------------------------------------------------
# 七、动手练习
# ------------------------------------------------------------
# 复制练习后完成：
# 1. 给 Part 增加 `to_api()`，输出和接口一致的 dict。
# 2. 让 from_api 接受字符串形式的数字，例如 "80"。
# 3. 写一个函数，统计所有零件的总体积。
#
# 提示：接口数据先校验，再进入业务逻辑。

print("\n第 23 课结束：先定义结构，再相信数据。")
