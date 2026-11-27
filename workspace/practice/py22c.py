import threading
import time


def run(threads=4, times=200):
    counter = 0

    def add():
        nonlocal counter
        for _ in range(times):
            current = counter
            time.sleep(0.0005)
            current += 1
            counter = current

    pool = [threading.Thread(target=add) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    return counter


print(run())
