"""Actual feedback and download-only card metrics, without network/database writes."""
import unittest
from collections import defaultdict
from unittest.mock import patch

from website import app as web
from website.test_feedback import Collection


class CardMetricsTests(unittest.TestCase):
    def setUp(self):
        self.template = web.create_app().jinja_env.get_template("_card_metrics.html")

    def render_metrics(self, card):
        return str(self.template.module.card_metrics(card))

    def test_positive_counts_use_plain_arrow_with_actual_rating(self):
        html = self.render_metrics({
            "download_count": 23,
            "rating": {"count": 2, "avg": 4.5},
        })
        self.assertIn('class="meta-stars"', html)
        self.assertIn('class="meta-val">4.5', html)
        self.assertIn('class="meta-icon" aria-hidden="true">↓', html)
        self.assertIn('class="meta-val">23', html)
        self.assertNotIn("📥", html)
        self.assertNotIn("تقديري", html)

    def test_zero_and_missing_counts_do_not_show_arrow_or_zero(self):
        for value in (0, None):
            with self.subTest(value=value):
                html = self.render_metrics({
                    "download_count": value, "click_count": 999,
                    "rating": {"count": 0, "avg": 0},
                })
                self.assertNotIn("↓", html)
                self.assertNotIn('class="meta-val"', html)
                self.assertNotIn("999", html)

    def test_real_item_and_shared_button_ratings_keep_their_identity(self):
        db = defaultdict(Collection)
        db["item_ratings"].docs = [
            {"item_id": 11, "rating": 4}, {"item_id": 11, "rating": 5},
            {"item_id": 12, "rating": 2},
        ]
        db["button_ratings"].docs = [{"button_id": 7, "rating": 3}]
        db["item_twins"].docs = [{"a": 11, "b": 21}]
        with patch.object(web, "_col", side_effect=lambda name: db[name]):
            self.assertEqual(
                web._attachment_card_rating({"id": 7}, {"id": 11}),
                {"count": 2, "avg": 4.5},
            )
            self.assertEqual(
                web._attachment_card_rating({"id": 7}, {"id": 21}),
                {"count": 2, "avg": 4.5},
            )
            self.assertEqual(
                web._attachment_card_rating({"id": 7}, {"id": 12}),
                {"count": 1, "avg": 2.0},
            )
            self.assertEqual(
                web._attachment_card_rating({"id": 7, "unified_rating": 1}, {"id": 12}),
                {"count": 1, "avg": 3.0},
            )
            self.assertEqual(
                web._attachment_card_rating({"id": 7}, {"id": 99}),
                {"count": 0, "avg": 0.0},
            )


if __name__ == "__main__":
    unittest.main()
