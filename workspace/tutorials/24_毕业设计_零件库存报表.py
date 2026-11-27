# ============================================================
# 第 24 课：毕业设计——零件库存报表
# ============================================================
# 【本课目标】
# 把前面学的东西合成一个能运行的小项目：
# 数据结构、校验、计算、CSV 读写、异常处理、入口函数。
#
# 【项目需求】
# 1. 读入一批零件记录。
# 2. 丢掉非法记录，并说明原因。
# 3. 计算每个零件的体积、库存总体积。
# 4. 生成 CSV 报表，并读回来验证。
# 5. 输出低库存提醒。
#
# 【项目结构】
# 这是一个单文件教学版，但按真实项目的层次写：
#   常量 -> 数据模型 -> 业务函数 -> 报表函数 -> main
# 真正拆文件时，可以按这些边界拆成 models.py / services.py / cli.py。

from __future__ import annotations

import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

# ------------------------------------------------------------
# 一、常量与数据模型
# ------------------------------------------------------------
LOW_STOCK_THRESHOLD = 2


@dataclass
class Part:
    name: str
    length: float
    width: float
    height: float
    quantity: int

    def __post_init__(self):
        if min(self.length, self.width, self.height) <= 0:
            raise ValueError("尺寸必须大于 0")
        if self.quantity < 0:
            raise ValueError("库存不能为负数")

    @property
    def unit_volume(self) -> float:
        return self.length * self.width * self.height

    @property
    def total_volume(self) -> float:
        return self.unit_volume * self.quantity

    @property
    def low_stock(self) -> bool:
        return self.quantity < LOW_STOCK_THRESHOLD


# ------------------------------------------------------------
# 二、输入校验
# ------------------------------------------------------------
# 原始数据通常来自 CSV、接口或人工录入，不能直接相信。
# 这里用 dict 模拟“刚从表格读出来”的原始行。

RAW_ROWS = [
    {"name": "底座", "length": "80", "width": "50", "height": "10", "quantity": "2"},
    {"name": "支架", "length": "120", "width": "60", "height": "8", "quantity": "1"},
    {"name": "垫块", "length": "30", "width": "30", "height": "5", "quantity": "4"},
    {"name": "坏件", "length": "0", "width": "30", "height": "5", "quantity": "1"},
    {"name": "缺字段", "length": "10", "width": "10"},
    {"name": "负库存", "length": "10", "width": "10", "height": "10", "quantity": "-2"},
    {"name": "非数字", "length": "十", "width": "10", "height": "10", "quantity": "1"},
]


def parse_part(row: dict) -> Part:
    """把一行原始数据转成 Part；非法数据抛 ValueError。"""
    required = ("name", "length", "width", "height", "quantity")
    missing = [key for key in required if key not in row]
    if missing:
        raise ValueError(f"缺少字段：{', '.join(missing)}")

    name = str(row["name"]).strip()
    if not name:
        raise ValueError("名称不能为空")

    try:
        length = float(row["length"])
        width = float(row["width"])
        height = float(row["height"])
        quantity = int(row["quantity"])
    except (TypeError, ValueError) as error:
        raise ValueError(f"数字格式错误：{error}") from error

    return Part(name, length, width, height, quantity)


def load_parts(rows: list[dict]) -> tuple[list[Part], list[str]]:
    """返回 (合法零件, 错误说明)。错误不吞掉，交给调用方展示。"""
    parts: list[Part] = []
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        try:
            parts.append(parse_part(row))
        except ValueError as error:
            errors.append(f"第 {index} 行：{error}")
    return parts, errors


# ------------------------------------------------------------
# 三、业务计算
# ------------------------------------------------------------
def total_inventory_volume(parts: list[Part]) -> float:
    return sum(part.total_volume for part in parts)


def low_stock_parts(parts: list[Part]) -> list[Part]:
    return [part for part in parts if part.low_stock]


def by_volume(parts: list[Part]) -> list[Part]:
    return sorted(parts, key=lambda part: part.total_volume, reverse=True)


# ------------------------------------------------------------
# 四、CSV 报表
# ------------------------------------------------------------
# 写入时用 utf-8-sig，Excel 打开中文 CSV 更不容易乱码。
# 读取时 newline="" 是 csv 模块的推荐写法。

def write_report(path: Path, parts: list[Part]) -> None:
    fields = ["name", "length", "width", "height", "quantity", "unit_volume", "total_volume"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for part in parts:
            writer.writerow(
                {
                    "name": part.name,
                    "length": part.length,
                    "width": part.width,
                    "height": part.height,
                    "quantity": part.quantity,
                    "unit_volume": part.unit_volume,
                    "total_volume": part.total_volume,
                }
            )


def read_report(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


# ------------------------------------------------------------
# 五、主流程
# ------------------------------------------------------------
def main() -> None:
    print("=" * 58)
    print("零件库存报表 · 毕业设计")
    print("=" * 58)

    parts, errors = load_parts(RAW_ROWS)

    print(f"\n读入 {len(RAW_ROWS)} 行，合法 {len(parts)} 行。")
    if errors:
        print("被跳过的数据：")
        for error in errors:
            print(" -", error)

    print("\n库存明细：")
    for part in by_volume(parts):
        flag = "  [低库存]" if part.low_stock else ""
        print(
            f" - {part.name:<4} 数量 {part.quantity:>2}  "
            f"单件 {part.unit_volume:>8.0f}  合计 {part.total_volume:>9.0f}{flag}"
        )

    print(f"\n库存总体积：{total_inventory_volume(parts):.0f} mm³")
    low = low_stock_parts(parts)
    print("低库存零件：", "、".join(part.name for part in low) if low else "无")

    with tempfile.TemporaryDirectory(prefix="inventory_report_") as temp_dir:
        report_path = Path(temp_dir) / "inventory_report.csv"
        write_report(report_path, parts)
        rows = read_report(report_path)

        print(f"\n报表已写入临时文件：{report_path.name}")
        print(f"读回 {len(rows)} 行，字段：{list(rows[0].keys())}")
        print("第一行：", rows[0])

    print("\n毕业检查：")
    print(" 1. 数据结构清楚吗？")
    print(" 2. 非法输入有没有被单独处理？")
    print(" 3. 计算逻辑能不能独立测试？")
    print(" 4. 文件读写有没有用 with？")
    print(" 5. 入口和业务逻辑有没有分开？")


if __name__ == "__main__":
    main()

# ------------------------------------------------------------
# 六、继续扩展
# ------------------------------------------------------------
# 想继续做大的话，下一步可以：
# 1. 用 argparse 接收 CSV 路径，而不是写死 RAW_ROWS。
# 2. 把 Part、load_parts、write_report 拆成模块。
# 3. 用 unittest 测 parse_part 的边界。
# 4. 增加“按车间筛选”“按月统计出入库”。
# 5. 数据量大时换 sqlite3，而不是每次全量 CSV。
#
# 毕业不是“学完了”，是“能自己定义一个完整的小项目并交付”。
print("\n第 24 课结束：把复杂问题拆成能验收的小步骤。")
