class Guard:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True


def risky():
    with Guard():
        raise ValueError("库存不能为负")


if __name__ == "__main__":
    risky()
    print("继续执行")
