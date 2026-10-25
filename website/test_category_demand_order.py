import unittest
from unittest.mock import Mock, patch

from website import app as web


class CategoryDemandOrderTests(unittest.TestCase):
    def test_category_page_renders_menu_children_in_demand_order(self):
        parent = {
            "id": 10, "type": "menu", "label": "الكيمياء", "sort_demand": 1,
        }
        children = [
            {"id": 21, "type": "menu", "label": "ملخص الكيمياء", "demand_clicks": 1},
            {"id": 22, "type": "menu", "label": "الملازم", "demand_clicks": 7},
        ]
        app = web.create_app()

        with patch.object(web, "_btn", return_value=parent), \
                patch.object(web, "_children", return_value=children), \
                patch.object(web, "_breadcrumb", return_value=[]), \
                patch.object(web, "_seo_labels", return_value=[]), \
                patch.object(web.seo, "category_metadata", return_value=("عنوان", "وصف")):
            response = app.test_client().get("/cat/10")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertLess(page.index('href="/cat/22"'), page.index('href="/cat/21"'))

    def test_enabled_toggle_orders_children_by_repeated_student_presses(self):
        parent = {"id": 10, "type": "menu", "sort_demand": 1}
        children = [
            {"id": 1, "demand_clicks": 4},
            {"id": 2, "demand_clicks": 15},
            {"id": 3, "demand_clicks": 4},
        ]

        ordered = web._sort_category_children_by_demand(parent, children)

        self.assertEqual([button["id"] for button in ordered], [2, 1, 3])

    def test_disabled_toggle_keeps_existing_manual_order(self):
        parent = {"id": 10, "type": "menu", "sort_demand": 0}
        children = [
            {"id": 1, "demand_clicks": 4},
            {"id": 2, "demand_clicks": 15},
        ]

        ordered = web._sort_category_children_by_demand(parent, children)

        self.assertIs(ordered, children)
        self.assertEqual([button["id"] for button in ordered], [1, 2])

    def test_legacy_material_menu_uses_bot_default(self):
        parent = {"id": 10, "type": "menu", "parent_id": 9}
        buttons = Mock()
        buttons.find_one.return_value = {"label": "📚 الملازم"}

        with patch.object(web, "_col", return_value=buttons):
            ordered = web._sort_category_children_by_demand(
                parent, [{"id": 1, "demand_clicks": 1}, {"id": 2, "demand_clicks": 5}]
            )

        self.assertEqual([button["id"] for button in ordered], [2, 1])

    def test_non_menu_categories_do_not_apply_demand_sort(self):
        parent = {"id": 10, "type": "compound", "sort_demand": 1}
        children = [{"id": 1, "demand_clicks": 1}, {"id": 2, "demand_clicks": 5}]

        self.assertIs(web._sort_category_children_by_demand(parent, children), children)


if __name__ == "__main__":
    unittest.main()
