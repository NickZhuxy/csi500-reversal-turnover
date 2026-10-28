"""Offline checks of the BaoStock result-set handling (no network)."""

import pytest

from revturn.download import PartialResult, drain


class FakeResult:
    """Mimics baostock ResultData: next() only advances after get_row_data()."""

    fields = ["date", "close"]

    def __init__(self, rows, fail_after=None):
        self.rows, self.fail_after = rows, fail_after
        self.cur, self.read = 0, 0
        self.error_code, self.error_msg = "0", "success"

    def next(self):
        if self.fail_after is not None and self.read >= self.fail_after:
            self.error_code, self.error_msg = "10002007", "network error"
            return False
        return self.cur < len(self.rows)

    def get_row_data(self):
        row = self.rows[self.cur]
        self.cur += 1
        self.read += 1
        return row


def test_drain_reads_every_row():
    rows = [["2024-01-02", "10.0"], ["2024-01-03", "10.1"], ["2024-01-04", "10.2"]]
    df = drain(FakeResult(rows))
    assert df.values.tolist() == rows
    assert list(df.columns) == ["date", "close"]


def test_drain_raises_instead_of_returning_a_short_table():
    rows = [[f"2024-01-{d:02d}", "1"] for d in range(1, 11)]
    with pytest.raises(PartialResult, match="after 4 rows"):
        drain(FakeResult(rows, fail_after=4))
