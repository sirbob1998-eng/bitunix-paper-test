import unittest

from collector import normalize_depth, walk_book


class CollectorTests(unittest.TestCase):
    def test_normalize_depth_accepts_array_and_object_levels(self):
        data = {"asks": [[101, 2], {"price": "102", "volume": "3"}], "bids": [[99, 1]]}
        bids, asks = normalize_depth(data)
        self.assertEqual(bids, [(99.0, 1.0)])
        self.assertEqual(asks, [(101.0, 2.0), (102.0, 3.0)])

    def test_normalize_depth_sorts_sides(self):
        bids, asks = normalize_depth({"bids": [[98, 1], [99, 1]], "asks": [[102, 1], [101, 1]]})
        self.assertEqual(bids[0][0], 99)
        self.assertEqual(asks[0][0], 101)

    def test_walk_book_calculates_vwap(self):
        price, notional = walk_book([(100.0, 1.0), (101.0, 2.0)], 2.0)
        self.assertEqual(price, 100.5)
        self.assertEqual(notional, 201.0)

    def test_walk_book_rejects_insufficient_depth(self):
        with self.assertRaisesRegex(ValueError, "insufficient depth"):
            walk_book([(100.0, 1.0)], 2.0)


if __name__ == "__main__":
    unittest.main()
