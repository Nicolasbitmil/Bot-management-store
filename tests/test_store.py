import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from store import Store, StoreError


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.database = str(root / "test.sqlite3")
        self.catalog = str(root / "catalog.json")
        Path(self.catalog).write_text(
            json.dumps(
                {
                    "products": [
                        {
                            "name": "Remera",
                            "description": "Algodón",
                            "category": "Remeras",
                            "variants": [
                                {
                                    "sku": "REM-NEG-M",
                                    "size": "M",
                                    "color": "Negro",
                                    "price_minor": 250000,
                                    "stock": 2,
                                }
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.store = Store(self.database, self.catalog)
        self.variant_id = self.store.list_variants(1)[0]["id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _add_one_and_order(self) -> int:
        self.store.add_to_cart(1001, self.variant_id)
        return self.store.create_order(1001, "Cliente", "12345", "Retiro", "ARS")

    def test_order_reserves_stock_until_seller_decides(self) -> None:
        order_id = self._add_one_and_order()
        variant = self.store.list_variants(1)[0]
        self.assertEqual(variant["stock"], 2)
        self.assertEqual(variant["reserved"], 1)
        self.assertEqual(self.store.get_order(order_id)["status"], "pending")

    def test_rejection_releases_reserved_stock(self) -> None:
        order_id = self._add_one_and_order()
        self.assertEqual(self.store.resolve_order(order_id, "rejected", 9001), "rejected")
        variant = self.store.list_variants(1)[0]
        self.assertEqual(variant["stock"], 2)
        self.assertEqual(variant["reserved"], 0)

    def test_confirmation_deducts_stock_once(self) -> None:
        order_id = self._add_one_and_order()
        self.assertEqual(self.store.resolve_order(order_id, "confirmed", 9001), "confirmed")
        self.assertEqual(self.store.resolve_order(order_id, "rejected", 9001), "confirmed")
        variant = self.store.list_variants(1)[0]
        self.assertEqual(variant["stock"], 1)
        self.assertEqual(variant["reserved"], 0)

    def test_order_can_progress_through_fulfillment_states(self) -> None:
        order_id = self._add_one_and_order()
        self.assertEqual(
            self.store.update_order_status(order_id, "confirmed", 9001), "confirmed"
        )
        self.assertEqual(
            self.store.update_order_status(order_id, "preparing", 9001), "preparing"
        )
        self.assertEqual(
            self.store.update_order_status(order_id, "shipped", 9001), "shipped"
        )
        self.assertEqual(
            self.store.update_order_status(order_id, "delivered", 9001), "delivered"
        )
        events = self.store.order_events(order_id)
        self.assertEqual(
            [event["new_status"] for event in events],
            ["pending", "confirmed", "preparing", "shipped", "delivered"],
        )
        with self.assertRaises(StoreError):
            self.store.update_order_status(order_id, "cancelled", 9001)

    def test_cancelling_confirmed_order_restores_inventory(self) -> None:
        order_id = self._add_one_and_order()
        self.store.update_order_status(order_id, "confirmed", 9001)
        self.store.update_order_status(order_id, "cancelled", 9001)
        variant = self.store.list_variants(1)[0]
        self.assertEqual(variant["stock"], 2)
        self.assertEqual(variant["reserved"], 0)

    def test_expiring_pending_order_releases_reservation_and_records_event(self) -> None:
        order_id = self._add_one_and_order()
        db = sqlite3.connect(self.database)
        try:
            db.execute(
                "UPDATE orders SET expires_at = datetime('now', '-1 hour') WHERE id = ?",
                (order_id,),
            )
            db.commit()
        finally:
            db.close()
        expired = self.store.expire_pending_orders()
        self.assertEqual([order["id"] for order in expired], [order_id])
        self.assertEqual(self.store.get_order(order_id)["status"], "cancelled")
        self.assertEqual(self.store.list_variants(1)[0]["reserved"], 0)
        event = self.store.order_events(order_id)[-1]
        self.assertEqual(event["new_status"], "cancelled")
        self.assertEqual(event["reason"], "Reserva vencida automáticamente.")

    def test_customer_order_details_are_scoped_to_owner(self) -> None:
        order_id = self._add_one_and_order()
        self.assertIsNotNone(self.store.customer_order(order_id, 1001))
        self.assertIsNone(self.store.customer_order(order_id, 1002))

    def test_product_categories_and_search(self) -> None:
        self.assertEqual(self.store.product_categories(), ["Remeras"])
        self.assertEqual(self.store.categories(populated_only=True)[0]["name"], "Remeras")
        self.assertEqual(
            [product["name"] for product in self.store.search_products("algodón")],
            ["Remera"],
        )
        self.assertEqual(
            [product["name"] for product in self.store.search_products("Remeras")],
            ["Remera"],
        )
        self.assertEqual(self.store.search_products("no existe"), [])

    def test_admin_can_create_category_and_assign_existing_products(self) -> None:
        category_id = self.store.create_category("Básicos")
        self.store.assign_product_category(1, category_id)
        category = self.store.get_category(category_id)
        self.assertEqual(category["name"], "Básicos")
        self.assertEqual(self.store.admin_get_product(1)["category"], "Básicos")
        products = self.store.products_in_category(category_id)
        self.assertEqual([product["name"] for product in products], ["Remera"])
        self.assertEqual(self.store.categories(populated_only=True)[0]["product_count"], 1)

    def test_renaming_category_updates_assigned_products(self) -> None:
        category_id = self.store.create_category("Básicos")
        self.store.assign_product_category(1, category_id)
        self.store.rename_category(category_id, "Esenciales")
        self.assertEqual(self.store.get_category(category_id)["name"], "Esenciales")
        self.assertEqual(self.store.admin_get_product(1)["category"], "Esenciales")
        self.assertEqual(
            [product["name"] for product in self.store.products_in_category(category_id)],
            ["Remera"],
        )

    def test_category_rename_rejects_existing_name(self) -> None:
        category_id = self.store.create_category("Básicos")
        with self.assertRaises(StoreError):
            self.store.rename_category(category_id, "remeras")
        self.assertEqual(self.store.get_category(category_id)["name"], "Básicos")

    def test_deleting_category_can_keep_products_unassigned(self) -> None:
        category_id = self.store.create_category("Básicos")
        self.store.assign_product_category(1, category_id)
        affected = self.store.delete_category(category_id, delete_products=False)
        self.assertEqual(affected, 1)
        self.assertIsNone(self.store.get_category(category_id))
        self.assertEqual(self.store.admin_get_product(1)["active"], 1)
        self.assertEqual(self.store.admin_get_product(1)["category"], "")
        self.assertEqual(self.store.unassigned_product_count(), 1)

    def test_deleting_category_can_deactivate_associated_products_safely(self) -> None:
        category_id = self.store.create_category("Básicos")
        self.store.assign_product_category(1, category_id)
        self.store.add_to_cart(1002, self.variant_id)
        order_id = self._add_one_and_order()
        affected = self.store.delete_category(category_id, delete_products=True)
        product = self.store.admin_get_product(1)
        self.assertEqual(affected, 1)
        self.assertEqual(product["active"], 0)
        self.assertEqual(product["category"], "")
        self.assertEqual(self.store.cart_items(1002), [])
        self.assertEqual(self.store.get_order(order_id)["status"], "pending")
        self.assertEqual(self.store.order_items(order_id)[0]["product_name"], "Remera")

    def test_deleting_products_in_category_removes_unordered_inventory(self) -> None:
        category_id = self.store.create_category("Básicos")
        self.store.assign_product_category(1, category_id)
        self.store.add_to_cart(1001, self.variant_id)
        removed = self.store.delete_category(category_id, delete_products=True)
        self.assertEqual(removed, 1)
        self.assertIsNone(self.store.admin_get_product(1))
        self.assertEqual(self.store.cart_items(1001), [])

    def test_remove_product_archives_products_with_order_history(self) -> None:
        order_id = self._add_one_and_order()
        archived = self.store.remove_product(1)
        self.assertTrue(archived)
        product = self.store.admin_get_product(1)
        self.assertEqual(product["active"], 0)
        self.assertIsNotNone(self.store.get_order(order_id))
        self.assertEqual(self.store.order_items(order_id)[0]["product_name"], "Remera")

    def test_remove_product_permanently_deletes_products_without_orders(self) -> None:
        self.store.add_to_cart(1001, self.variant_id)
        archived = self.store.remove_product(1)
        self.assertFalse(archived)
        self.assertIsNone(self.store.admin_get_product(1))
        self.assertEqual(self.store.cart_items(1001), [])

    def test_category_names_are_unique_case_insensitively(self) -> None:
        self.store.create_category("Abrigos")
        with self.assertRaises(StoreError):
            self.store.create_category("abrigos")

    def test_existing_catalog_categories_are_migrated(self) -> None:
        db = sqlite3.connect(self.database)
        try:
            db.execute("DROP TABLE categories")
            db.commit()
        finally:
            db.close()
        Store(self.database, self.catalog)
        category = self.store.categories()[0]
        self.assertEqual(category["name"], "Remeras")
        self.assertEqual(category["product_count"], 1)

    def test_store_migrates_legacy_orders_table(self) -> None:
        legacy_database = str(Path(self.temp_dir.name) / "legacy.sqlite3")
        db = sqlite3.connect(legacy_database)
        try:
            db.execute(
                """CREATE TABLE orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    customer_name TEXT NOT NULL,
                    contact TEXT NOT NULL,
                    delivery TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    total_minor INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            db.commit()
        finally:
            db.close()
        Store(legacy_database, self.catalog)
        db = sqlite3.connect(legacy_database)
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
        finally:
            db.close()
        self.assertIn("expires_at", columns)

    def test_cannot_add_more_than_available_stock(self) -> None:
        self.store.add_to_cart(1001, self.variant_id, 2)
        order_id = self.store.create_order(1001, "Cliente", "12345", "Retiro", "ARS")
        with self.assertRaises(StoreError):
            self.store.add_to_cart(1002, self.variant_id)
        self.store.resolve_order(order_id, "rejected", 9001)
        self.store.add_to_cart(1002, self.variant_id, 2)

    def test_adding_same_variant_increments_cart_quantity(self) -> None:
        self.store.add_to_cart(1001, self.variant_id)
        self.store.add_to_cart(1001, self.variant_id)
        self.assertEqual(self.store.cart_items(1001)[0]["quantity"], 2)

    def test_customer_can_clear_cart_without_deleting_orders(self) -> None:
        self.store.add_to_cart(1001, self.variant_id)
        order_id = self.store.create_order(1001, "Cliente", "12345", "Retiro", "ARS")
        self.store.add_to_cart(1001, self.variant_id)
        self.store.clear_cart(1001)
        self.assertEqual(self.store.cart_items(1001), [])
        self.assertIsNotNone(self.store.customer_order(order_id, 1001))

    def test_order_keeps_catalog_snapshot(self) -> None:
        order_id = self._add_one_and_order()
        item = self.store.order_items(order_id)[0]
        self.assertEqual(item["product_name"], "Remera")
        self.assertEqual(item["size"], "M")
        self.assertEqual(item["color"], "Negro")
        self.assertEqual(item["unit_price_minor"], 250000)

    def test_admin_can_create_product_with_variant_details(self) -> None:
        product_id = self.store.create_product(
            "Campera",
            "Abrigo liviano",
            "Abrigos",
            "telegram-photo-id",
            [{"size": "L", "color": "Verde", "price_minor": 870000, "stock": 4}],
        )
        product = self.store.get_product(product_id)
        variant = self.store.list_variants(product_id)[0]
        self.assertEqual(product["description"], "Abrigo liviano")
        self.assertEqual(product["photo_url"], "telegram-photo-id")
        self.assertEqual(variant["size"], "L")
        self.assertEqual(variant["color"], "Verde")
        self.assertEqual(variant["price_minor"], 870000)
        self.assertEqual(variant["stock"], 4)

    def test_admin_updates_variant_price_and_stock(self) -> None:
        self.store.update_variant_field(self.variant_id, "price_minor", 310000)
        self.store.update_variant_field(self.variant_id, "stock", 5)
        variant = self.store.admin_variant(self.variant_id)
        self.assertEqual(variant["price_minor"], 310000)
        self.assertEqual(variant["stock"], 5)

    def test_admin_cannot_reduce_stock_below_reserved_units(self) -> None:
        self._add_one_and_order()
        with self.assertRaises(StoreError):
            self.store.update_variant_field(self.variant_id, "stock", 0)

    def test_deactivated_product_disappears_and_is_removed_from_carts(self) -> None:
        self.store.add_to_cart(1001, self.variant_id)
        self.store.deactivate_product(1)
        self.assertEqual(self.store.list_products(), [])
        self.assertEqual(self.store.cart_items(1001), [])
        self.assertEqual(self.store.admin_get_product(1)["active"], 0)
        self.store.activate_product(1)
        self.assertEqual(len(self.store.list_products()), 1)

    def test_admin_rejects_duplicate_size_and_color_variant(self) -> None:
        with self.assertRaises(StoreError):
            self.store.add_variant(1, "m", "negro", 100000, 1)


if __name__ == "__main__":
    unittest.main()
