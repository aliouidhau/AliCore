import unittest


def box_volume(a, b, c):
    if min(a, b, c) <= 0:
        raise ValueError("尺寸必须大于 0")
    return a * b * c


class TestBox(unittest.TestCase):
    def check_normal(self):
        self.assertEqual(box_volume(2, 3, 4), 24)

    def check_zero(self):
        with self.assertRaises(ValueError):
            box_volume(0, 3, 4)


if __name__ == "__main__":
    unittest.main()
