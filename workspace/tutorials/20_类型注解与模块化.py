# ============================================================
# 第 20 课：类型注解与模块化——让代码更像工程
# ============================================================
# 【本课目标】
# 1. 会给变量、函数、容器写类型注解。
# 2. 知道类型注解不会在运行时强制检查，但能帮人和工具发现错误。
# 3. 理解模块、入口函数和项目目录的基本分层。
#
# 【心智模型】
# 类型注解像零件图纸上的尺寸标注：
# 不改变零件本身，但让加工、检验和交接更可靠。

from dataclasses import dataclass
from typing import Callable, Protocol, TypeAlias

# ------------------------------------------------------------
# 一、变量和函数的类型注解
# ------------------------------------------------------------
# Python 是动态类型语言，注解默认只是“说明”，不会阻止运行。
# 但编辑器、类型检查器和未来的同事会因此少踩很多坑。

count: int = 3
ratio: float = 0.75
name: str = "底座"
flags: list[bool] = [True, False, True]
part_map: dict[str, float] = {"底座": 80.0, "支架": 120.0}


def volume(length: float, width: float, height: float) -> float:
    return length * width * height


print("体积：", volume(80, 50, 10))

# 类型写错，运行不一定会报错，这是 Python 的特点，也是需要类型检查器的原因。
wrong: int = "这不是整数"  # 这行本身能运行
print("错误注解仍然能运行：", wrong)

# ------------------------------------------------------------
# 二、给容器和可选值写类型
# ------------------------------------------------------------
# list[int] / dict[str, int] / tuple[str, float] 都是常见写法。
# 如果值可能为 None，用 Optional 或 X | None。

from typing import Optional


def find_part(parts: dict[str, float], key: str) -> Optional[float]:
    return parts.get(key)


print("找到底座：", find_part(part_map, "底座"))
print("找不到轴承：", find_part(part_map, "轴承"))

# ------------------------------------------------------------
# 三、dataclass + 类型注解
# ------------------------------------------------------------
# dataclass 的字段本身就是类型注解，读代码时一眼能看出数据结构。

@dataclass
class PartRecord:
    name: str
    length: float
    width: float
    height: float
    quantity: int = 1

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height


records: list[PartRecord] = [
    PartRecord("底座", 80, 50, 10, 2),
    PartRecord("支架", 120, 60, 8, 1),
]
total = sum(record.volume * record.quantity for record in records)
print("\n总体积：", total)

# ------------------------------------------------------------
# 四、Protocol：描述“像什么”，而不是“是什么类”
# ------------------------------------------------------------
# 结构化类型：只要对象有指定的方法，就能被当成这个协议。
# 适合给回调、插件、适配器写接口说明。

class VolumeCalculator(Protocol):
    def calculate(self, part: PartRecord) -> float:
        ...


class BoxCalculator:
    def calculate(self, part: PartRecord) -> float:
        return part.volume


class WeightedCalculator:
    def calculate(self, part: PartRecord) -> float:
        return part.volume * 1.2


def report(calculator: VolumeCalculator, parts: list[PartRecord]) -> float:
    return sum(calculator.calculate(part) for part in parts)


print("\nBox 计算：", report(BoxCalculator(), records))
print("加权计算：", report(WeightedCalculator(), records))

# ------------------------------------------------------------
# 五、Callable：函数也是值
# ------------------------------------------------------------
# Callable[[参数类型], 返回类型] 用来描述一个函数参数。

Formatter: TypeAlias = Callable[[PartRecord], str]


def format_part(part: PartRecord) -> str:
    return f"{part.name}: {part.volume} mm³"


def render(parts: list[PartRecord], formatter: Formatter) -> list[str]:
    return [formatter(part) for part in parts]


print("\n格式化：")
for line in render(records, format_part):
    print(" ", line)

# ------------------------------------------------------------
# 六、模块化：把“入口”留在最后
# ------------------------------------------------------------
# 一个文件也可以有清晰的层次：
# 1. 常量
# 2. 数据类
# 3. 纯函数
# 4. 主流程
# 5. if __name__ == "__main__": 入口
#
# 真正拆成多个文件时，推荐：
# project/
#   models.py     数据结构
#   services.py   业务逻辑
#   cli.py        命令行入口
#   tests/        测试
#
# 不要把所有代码都塞进一个 1000 行的 main.py。
# 但也不要为了“模块化”把三行代码拆成三个文件。

def build_demo_report() -> str:
    lines = [format_part(part) for part in records]
    return "\n".join(lines)


def main() -> None:
    print("\n主流程开始")
    print(build_demo_report())
    print("主流程结束")


if __name__ == "__main__":
    main()

# ------------------------------------------------------------
# 七、常见错误
# ------------------------------------------------------------
# 1. 以为类型注解会在运行时强制检查。它不会。
# 2. 用 list 而不是 list[int]，表达不够具体。
# 3. 给所有东西都加注解，连显而易见的局部变量也不放过。
# 4. 模块化只看文件数量，不看职责边界。
#
# 实用原则：公共函数、数据结构、跨文件接口必须写清楚；
# 局部临时变量可以省略。

# ------------------------------------------------------------
# 八、动手练习
# ----------------------------------------------------
# 复制练习后完成：
# 1. 给 find_part 增加默认返回值，类型改成 float | None。
# 2. 写一个 Protocol，要求对象有 name 属性和 describe() 方法。
# 3. 把 PartRecord 和 report 拆成两个模块的目录草图（先写在注释里）。
#
# 提示：类型注解是给人和工具看的，不是给运行时看的。

print("\n第 20 课结束：先让接口清楚，再让代码变多。")
