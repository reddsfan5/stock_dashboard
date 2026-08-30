import unittest

from backtest.validation import (
    TimeSplit,
    assert_no_temporal_leakage,
    chronological_holdout,
    walk_forward_splits,
)


class TimeValidationTest(unittest.TestCase):
    def test_holdout_with_embargo(self):
        split = chronological_holdout(
            ["d1", "d2", "d3", "d4", "d5", "d6"],
            validation_size=2,
            embargo_size=1,
        )
        self.assertEqual(split.train, ("d1", "d2", "d3"))
        self.assertEqual(split.embargo, ("d4",))
        self.assertEqual(split.validation, ("d5", "d6"))

    def test_rolling_walk_forward(self):
        splits = walk_forward_splits(
            list(range(10)), train_size=4, validation_size=2,
            step_size=2, embargo_size=1,
        )
        self.assertEqual(len(splits), 2)
        self.assertEqual(splits[0].train, (0, 1, 2, 3))
        self.assertEqual(splits[0].embargo, (4,))
        self.assertEqual(splits[0].validation, (5, 6))
        self.assertEqual(splits[1].train, (2, 3, 4, 5))
        self.assertEqual(splits[1].embargo, (6,))
        self.assertEqual(splits[1].validation, (7, 8))

    def test_anchored_walk_forward_expands_training(self):
        splits = walk_forward_splits(
            list(range(8)), train_size=3, validation_size=2,
            step_size=1, anchored=True,
        )
        self.assertEqual(splits[0].train, (0, 1, 2))
        self.assertEqual(splits[1].train, (0, 1, 2, 3))

    def test_rejects_unsorted_and_duplicate_dates(self):
        with self.assertRaisesRegex(ValueError, "升序"):
            chronological_holdout(["d2", "d1", "d3"], 1)
        with self.assertRaisesRegex(ValueError, "重复"):
            chronological_holdout(["d1", "d1", "d2"], 1)

    def test_detects_overlap(self):
        with self.assertRaisesRegex(ValueError, "不能重叠"):
            assert_no_temporal_leakage(TimeSplit(
                fold=0, train=(1, 2), embargo=(), validation=(2, 3),
            ))

    def test_rejects_insufficient_sample(self):
        with self.assertRaisesRegex(ValueError, "训练样本不足"):
            chronological_holdout([1, 2, 3], 2, embargo_size=1)
        with self.assertRaisesRegex(ValueError, "不足"):
            walk_forward_splits([1, 2, 3], train_size=2, validation_size=2)


if __name__ == "__main__":
    unittest.main()
