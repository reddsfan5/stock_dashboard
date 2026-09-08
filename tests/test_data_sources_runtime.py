import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from data import sources


class SinaRuntimeTest(unittest.TestCase):
    def test_runtime_is_initialized_once_before_concurrent_sina_fallbacks(self):
        original = sources._SINA_RUNTIME
        sources._SINA_RUNTIME = None
        runtime = Mock()
        try:
            with patch("py_mini_racer.MiniRacer", return_value=runtime) as factory:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    list(executor.map(lambda _: sources._ensure_sina_runtime(), range(40)))
            factory.assert_called_once_with()
            runtime.eval.assert_called_once_with("1+1")
        finally:
            sources._SINA_RUNTIME = original


if __name__ == "__main__":
    unittest.main()
