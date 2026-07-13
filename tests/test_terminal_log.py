import io
import unittest

from roguetrader.graph.trading_graph import _TeeStream


class TerminalLogTests(unittest.TestCase):
    def test_tee_stream_writes_to_terminal_and_log_streams(self):
        terminal = io.StringIO()
        log = io.StringIO()
        tee = _TeeStream(terminal, log)

        written = tee.write("hello\n")
        tee.flush()

        self.assertEqual(written, 6)
        self.assertEqual(terminal.getvalue(), "hello\n")
        self.assertEqual(log.getvalue(), "hello\n")


if __name__ == "__main__":
    unittest.main()
