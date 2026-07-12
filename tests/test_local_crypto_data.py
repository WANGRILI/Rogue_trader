import tempfile
import unittest
from pathlib import Path

import pandas as pd

from roguetrader.dataflows.config import set_config
from roguetrader.dataflows.local_crypto_data import get_local_ohlcv_report
from roguetrader.default_config import DEFAULT_CONFIG


class LocalCryptoDataTests(unittest.TestCase):
    def test_report_excludes_rows_after_reference_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_path = (
                root
                / "ohlcv"
                / "source=unit"
                / "symbol=BTC-USD"
                / "timeframe=1d"
                / "data.parquet"
            )
            data_path.parent.mkdir(parents=True)

            df = pd.DataFrame(
                {
                    "time": pd.to_datetime(
                        ["2023-01-02", "2024-01-01", "2024-01-02", "2024-12-31"]
                    ),
                    "open": [50, 100, 110, 1000],
                    "high": [55, 110, 120, 1100],
                    "low": [45, 90, 100, 900],
                    "close": [50, 100, 110, 1000],
                    "volume": [5, 10, 20, 999],
                    "raw_path": ["raw-a", "raw-a", "raw-a", "future-raw"],
                    "quality_flags": ["", "", "", "future"],
                }
            )
            df.to_parquet(data_path, index=False)

            try:
                set_config({"local_crypto_data": {"parquet_root": str(root)}})

                report = get_local_ohlcv_report(
                    ticker="BTC-USD",
                    curr_date="2024-01-02",
                    days=30,
                    timeframe="1d",
                    source="unit",
                )
            finally:
                set_config(DEFAULT_CONFIG.copy())

        self.assertIn("Reference date: 2024-01-02", report)
        self.assertIn("Full time range: 2023-01-02", report)
        self.assertIn("~ 2024-01-02", report)
        self.assertIn("Close: 110", report)
        self.assertIn("365d return: +120.00%", report)
        self.assertNotIn("1,000", report)
        self.assertNotIn("future-raw", report)


if __name__ == "__main__":
    unittest.main()
