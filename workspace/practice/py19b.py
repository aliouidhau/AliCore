from dataclasses import dataclass


@dataclass
class Part:
    name: str
    length: float
    width: float
    height: float
    quantity: int = 1

    # TODO: 尺寸必须大于 0，数量至少为 1
    # TODO: volume 单件体积，total_volume 总体积


p = Part("底座", 80, 50, 10, 2)
print(p.volume, p.total_volume)
