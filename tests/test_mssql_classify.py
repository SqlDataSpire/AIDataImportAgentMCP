"""Which pyodbc errors are row-data errors (bisect) vs infrastructure (fail the file)."""

from __future__ import annotations

import pyodbc
import pytest

from importer.mssql_store import is_data_error


@pytest.mark.parametrize("exc", [
    pyodbc.DataError("22001", "[22001] [Microsoft][ODBC Driver 18 for SQL Server]String data, right truncation"),
    pyodbc.IntegrityError("23000", "[23000] Violation of PRIMARY KEY constraint"),
    pyodbc.ProgrammingError("String data, right truncation: length 602 buffer 512", "HY000"),  # client-side, fast_executemany
])
def test_data_errors(exc):
    assert is_data_error(exc)


@pytest.mark.parametrize("exc", [
    pyodbc.OperationalError("08S01", "[08S01] Communication link failure"),
    pyodbc.ProgrammingError("42000", "[42000] The INSERT permission was denied"),
    pyodbc.InterfaceError("28000", "[28000] Login failed"),
    pyodbc.Error("HY000", "something else entirely"),
])
def test_infrastructure_errors(exc):
    assert not is_data_error(exc)
