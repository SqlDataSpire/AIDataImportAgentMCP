"""Spike (design §8.10): pyodbc fast_executemany into NVARCHAR(MAX) on DEV.

Measures batch insert time and Python-side peak memory with and without
setinputsizes. Driver (C-side) memory is not visible to tracemalloc; watch the
python process in Task Manager during the 20,000-row runs.
"""

from __future__ import annotations

import sys
import time
import tracemalloc

import pyodbc

from importer.db import get_engine

COLS = 12


def _rows(n: int) -> list[tuple[str, ...]]:
    return [tuple(f"r{i}c{j}-" + "x" * 40 for j in range(COLS)) for i in range(n)]


def _run(engine, n: int, use_sizes: bool) -> tuple[float, int, int]:
    create = "CREATE TABLE #spike (" + ", ".join(f"c{j} NVARCHAR(MAX) NULL" for j in range(COLS)) + ")"
    insert = "INSERT INTO #spike VALUES (" + ", ".join(["?"] * COLS) + ")"
    data = _rows(n)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS #spike")
        conn.exec_driver_sql(create)
        cursor = conn.connection.cursor()
        try:
            cursor.fast_executemany = True
            if use_sizes:
                cursor.setinputsizes([(pyodbc.SQL_WVARCHAR, 0, 0)] * COLS)
            tracemalloc.start()
            started = time.perf_counter()
            cursor.executemany(insert, data)
            elapsed = time.perf_counter() - started
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        finally:
            cursor.close()
        landed = conn.exec_driver_sql("SELECT COUNT(*) FROM #spike").scalar()
        conn.exec_driver_sql("DROP TABLE #spike")
    return elapsed, peak, landed


def main() -> int:
    engine = get_engine()
    for n in (1_000, 5_000, 20_000):
        for use_sizes in (False, True):
            try:
                elapsed, peak, landed = _run(engine, n, use_sizes)
                print(f"rows={n:>6} setinputsizes={use_sizes!s:<5} secs={elapsed:7.2f} "
                      f"py_peak_MB={peak / 1e6:7.1f} landed={landed}")
            except Exception as exc:  # report every variant, keep going
                print(f"rows={n:>6} setinputsizes={use_sizes!s:<5} FAILED: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
