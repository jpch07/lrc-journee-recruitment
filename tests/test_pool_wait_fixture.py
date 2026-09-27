"""Actual queue waiting under a controlled fictional connection hold, not production."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool


def test_one_connection_waits_and_two_can_overlap(tmp_path):
    evidence = {}
    for size in (1, 2):
        engine = create_engine(f"sqlite:///{tmp_path / ('pool'+str(size)+'.db')}",
                               poolclass=QueuePool, pool_size=size, max_overflow=0,
                               pool_timeout=2, pool_pre_ping=True,
                               connect_args={"check_same_thread": False})
        # Warm every physical connection, so acquisition excludes new connection establishment.
        warmed = [engine.connect() for _ in range(size)]
        for connection in warmed: connection.close()
        held, started, release = threading.Event(), threading.Event(), threading.Event()
        def first():
            with engine.connect() as connection:
                assert connection.exec_driver_sql('SELECT 1').scalar() == 1
                held.set()
                assert release.wait(2)
        def second():
            assert held.wait(2)
            begin = time.perf_counter(); started.set()
            with engine.connect() as connection:
                elapsed = (time.perf_counter()-begin)*1000
                assert connection.exec_driver_sql('SELECT 1').scalar() == 1
                return elapsed
        try:
            with ThreadPoolExecutor(max_workers=2) as workers:
                a = workers.submit(first); b = workers.submit(second)
                assert started.wait(2)
                # Explicit fixture hold exposes contention, not simulated Aiven CPU or production load.
                time.sleep(.15)
                release.set(); a.result(timeout=2)
                evidence[size] = round(b.result(timeout=2), 2)
            assert engine.pool.checkedout() == 0
        finally:
            release.set(); engine.dispose()
    assert evidence[1] >= 100
    assert evidence[2] < 100
    print('CONTROLLED_POOL_ACQUIRE_MS', evidence)
