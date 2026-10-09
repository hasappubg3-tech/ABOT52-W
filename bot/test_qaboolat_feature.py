import unittest

from bot.qaboolat_feature import format_results, search_results


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


class QaboolatFeatureTests(unittest.TestCase):
    def test_splits_a_large_university_without_dropping_colleges(self):
        results = [
            {
                "university": "جامعة بغداد",
                "college": f"كلية طويلة الاسم رقم {index} " + ("هندسة " * 5),
                "department": f"قسم {index}",
                "branch": "علمي",
                "min_grade": 95.0,
            }
            for index in range(140)
        ]

        messages = format_results("علمي", 95.0, results)

        self.assertGreater(len(messages), 1)
        self.assertTrue(all(utf16_length(message) <= 4096 for message in messages))
        combined = "\n".join(messages)
        for index in range(140):
            self.assertIn(f"كلية طويلة الاسم رقم {index}", combined)
        self.assertIn("جامعة بغداد", combined)
        self.assertIn("جامعة بغداد — تكملة", combined)

    def test_all_real_dataset_chunks_stay_within_telegram_limit(self):
        for branch in ("علمي", "أدبي"):
            for grade in (80, 85, 90, 95, 100, 105):
                results = search_results(branch, grade)
                messages = format_results(branch, grade, results)
                for message in messages:
                    self.assertLessEqual(
                        utf16_length(message),
                        4096,
                        msg=f"{branch} at {grade} produced an oversized message",
                    )


if __name__ == "__main__":
    unittest.main()
