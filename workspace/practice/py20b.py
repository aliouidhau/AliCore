from typing import Protocol


class Calculator(Protocol):
    def calculate(self, volume: float) -> float:
        ...


class Plain:
    def calculate(self, volume: float) -> float:
        # TODO: 原样返回
        return 0


class Weighted:
    def calculate(self, volume: float) -> float:
        # TODO: 乘 1.2
        return 0


def report(calculator, volumes):
    # TODO: 逐个调用 calculate 并求和
    return 0


print(report(Plain(), [10, 20]))
