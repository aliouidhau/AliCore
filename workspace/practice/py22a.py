import threading
import time


def run_all():
    results = []

    def work(n):
        time.sleep(0.02)
        results.append(n)

    threads = [threading.Thread(target=work, args=(n,)) for n in (1, 2, 3)]
    for t in threads:
        t.start()
    return results


print(run_all())
