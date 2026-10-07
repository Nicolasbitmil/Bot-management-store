"""Telegram storefront bot."""

from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from store import Store, StoreError

load_dotenv()
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SELLER_ID = int(os.getenv("TELEGRAM_SELLER_ID", "0"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "store.sqlite3")
CATALOG_PATH = os.getenv("CATALOG_PATH", "catalog.json")
CURRENCY = os.getenv("CURRENCY", "ARS")
SHOP_NAME = os.getenv("SHOP_NAME", "Tienda").strip() or "Tienda"
ORDER_RESERVATION_HOURS = int(os.getenv("ORDER_RESERVATION_HOURS", "24"))
PERSISTENCE_PATH = os.getenv("PERSISTENCE_PATH", "bot_state.pickle")
STORE = Store(DATABASE_PATH, CATALOG_PATH)
RESERVATION_TASK: asyncio.Task | None = None

STATUS_LABELS = {
    "pending": "Pendiente de confirmación",
    "confirmed": "Confirmado",
    "rejected": "Rechazado",
    "preparing": "En preparación",
    "shipped": "Enviado",
    "ready_for_pickup": "Listo para retirar",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}
MENU_CATALOG = "🛍️ Catálogo"
MENU_CART = "🛒 Mi carrito"
MENU_ORDERS = "📦 Mis pedidos"
MENU_HELP = "ℹ️ Ayuda"
MENU_ADMIN = "🛠️ Administración"
MENU_SEARCH = "🔎 Buscar"
MENU_RESTART = "↻ Empezar de nuevo"
ADMIN_FIELDS = {
    "name": "nombre",
    "description": "descripción",
}


def brand_heading(section: str) -> str:
    return f"✦ {SHOP_NAME} · {section.upper()} ✦"


def main_menu(show_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [[MENU_CATALOG, MENU_CART], [MENU_ORDERS, MENU_HELP]]
    if show_admin:
        keyboard.append([MENU_ADMIN])
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Elegí una opción",
    )


def cancel_checkout_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancelar compra", callback_data="cancel_checkout")]]
    )


def money(amount_minor: int, currency: str = CURRENCY) -> str:
    whole, cents = divmod(amount_minor, 100)
    grouped_whole = f"{whole:,}".replace(",", ".")
    return f"{currency} {grouped_whole},{cents:02d}"


def cart_text(items: list, currency: str = CURRENCY) -> str:
    if not items:
        return (
            f"{brand_heading('Tu selección')}\n\n"
            "Todavía no agregaste prendas.\n"
            "Explorá la colección y encontrá algo para vos."
        )
    lines = [brand_heading("Tu selección"), ""]
    total = 0
    for item in items:
        lines.append(
            f"▪ {item['product_name']}\n"
            f"  {item['color']} · talle {item['size']} · "
            f"{item['quantity']} × {money(item['price_minor'], currency)}\n"
            f"  Subtotal: {money(item['subtotal_minor'], currency)}"
        )
        total += item["subtotal_minor"]
    lines.extend(["", f"TOTAL  ·  {money(total, currency)}"])
    return "\n".join(lines)


def order_text(order_id: int, order, items: list, include_customer: bool = True) -> str:
    lines = [brand_heading(f"Pedido #{order_id}"), ""]
    if include_customer:
        lines.extend(
            [
                f"CLIENTE  ·  {order['customer_name']}",
                f"CONTACTO  ·  {order['contact']}",
                f"ENTREGA  ·  {order['delivery']}",
                "",
            ]
        )
    total = 0
    for item in items:
        lines.append(
            f"▪ {item['product_name']} · {item['color']} · talle {item['size']}\n"
            f"  {item['quantity']} × {money(item['unit_price_minor'], order['currency'])}"
            f"  ·  {money(item['subtotal_minor'], order['currency'])}"
        )
        total += item["subtotal_minor"]
    lines.extend(
        [
            "",
            f"TOTAL  ·  {money(total, order['currency'])}",
            f"ESTADO  ·  {STATUS_LABELS.get(order['status'], order['status'])}",
        ]
    )
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        first_name = update.effective_user.first_name if update.effective_user else ""
        greeting = f"Hola, {first_name}." if first_name else "Hola."
        await update.effective_message.reply_text(
            f"{brand_heading('Moda para elegir')}\n\n"
            f"{greeting} Bienvenido/a a {SHOP_NAME}.\n"
            "Descubrí la colección, elegí tus favoritos y armá tu pedido "
            "con atención personalizada.",
            reply_markup=main_menu(is_admin(update)),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        help_text = (
            f"{brand_heading('Cómo comprar')}\n\n"
            "01  Explorar la colección  ·  /catalogo\n"
            "02  Revisar tu selección  ·  /carrito\n"
            "03  Enviar un pedido  ·  /pedido\n"
            "04  Consultar su estado  ·  /mis_pedidos\n\n"
            "Buscar una prenda  ·  /buscar\n"
            "Vaciar selección y empezar de nuevo  ·  /reiniciar\n\n"
            "¿Necesitás una mano? Escribí /ayuda."
        )
        if is_admin(update):
            help_text += "\n\nADMINISTRACIÓN  ·  /admin"
        await update.effective_message.reply_text(help_text)


def is_admin(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == SELLER_ID


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Nuevo producto", callback_data="admin:add"),
                InlineKeyboardButton("📦 Productos", callback_data="admin:list"),
            ],
            [InlineKeyboardButton("🏷️ Categorías", callback_data="admin:categories")],
            [InlineKeyboardButton("🧾 Pedidos pendientes", callback_data="admin:orders")],
        ]
    )


def category_selection_keyboard(categories, callback_prefix: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                f"{category['name']} · {category['product_count']} productos",
                callback_data=f"{callback_prefix}:{category['id']}",
            )
        ]
        for category in categories
    ]
    return InlineKeyboardMarkup(buttons)


async def prompt_product_category(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    categories = STORE.categories()
    flow = context.user_data.get("admin_flow")
    if flow is None:
        return
    flow["step"] = "category_choice"
    keyboard = category_selection_keyboard(categories, "admin:setnewcat")
    buttons = list(keyboard.inline_keyboard)
    buttons.append(
        [
            InlineKeyboardButton(
                "➕ Crear categoría",
                callback_data="admin:addcategoryfromproduct",
            )
        ]
    )
    buttons.append(
        [InlineKeyboardButton("Dejar sin categoría por ahora", callback_data="admin:setnewcat:0")]
    )
    await message.reply_text(
        "Elegí la categoría del producto. Si todavía no existe, podés crearla ahora:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def product_category_keyboard(product_id: int) -> InlineKeyboardMarkup:
    keyboard = category_selection_keyboard(
        STORE.categories(), f"admin:setcat:{product_id}"
    )
    buttons = list(keyboard.inline_keyboard)
    buttons.extend(
        [
            [
                InlineKeyboardButton(
                    "Sin categoría",
                    callback_data=f"admin:setcat:{product_id}:0",
                )
            ],
            [
                InlineKeyboardButton(
                    "➕ Crear categoría",
                    callback_data=f"admin:addcategoryfor:{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "Volver al producto",
                    callback_data=f"admin:product:{product_id}",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(buttons)


def order_actions_keyboard(order) -> InlineKeyboardMarkup | None:
    actions = {
        "pending": [("Confirmar", "confirmed"), ("Rechazar", "rejected")],
        "confirmed": [("Empezar preparación", "preparing"), ("Cancelar pedido", "cancel")],
        "preparing": [
            ("Marcar enviado", "shipped"),
            ("Listo para retirar", "ready_for_pickup"),
            ("Cancelar pedido", "cancel"),
        ],
        "shipped": [("Marcar entregado", "delivered")],
        "ready_for_pickup": [("Marcar entregado", "delivered")],
    }.get(order["status"], [])
    if not actions:
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"ordercancel:{order['id']}"
                        if status == "cancel"
                        else f"orderstatus:{order['id']}:{status}"
                    ),
                )
                for label, status in actions[index : index + 2]
            ]
            for index in range(0, len(actions), 2)
        ]
    )


def product_list_keyboard(products, back_callback: str = "category:home") -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                f"{product['name']}  ·  {product['category'] or 'Colección'}",
                callback_data=f"product:{product['id']}",
            )
        ]
        for product in products
    ]
    buttons.extend(
        [
            [InlineKeyboardButton("Buscar prendas", callback_data="search:start")],
            [InlineKeyboardButton("Volver a categorías", callback_data=back_callback)],
        ]
    )
    return InlineKeyboardMarkup(buttons)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if not is_admin(update):
        await message.reply_text("Este acceso es exclusivo del administrador.")
        return
    context.user_data.pop("admin_flow", None)
    await message.reply_text(
        f"{brand_heading('Gestión de tienda')}\n\n"
        "Gestioná el catálogo, las variantes, los precios y el stock.",
        reply_markup=admin_keyboard(),
    )


async def admin_text_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, value: str
) -> None:
    message = update.effective_message
    flow = context.user_data.get("admin_flow")
    if message is None or flow is None:
        return
    step = flow["step"]
    value = value.strip()
    if not value:
        await message.reply_text("El valor no puede estar vacío. Intentá nuevamente.")
        return
    if value.casefold() == "cancelar":
        if flow["mode"] == "new_category" and flow.get("return_to_product"):
            product_flow = context.user_data.pop("product_flow", None)
            context.user_data["admin_flow"] = product_flow
            await message.reply_text("Creación de categoría cancelada.")
            await prompt_product_category(message, context)
            return
        if flow["mode"] == "new_category" and flow.get("return_to_assignment"):
            product_id = flow["product_id"]
            context.user_data.pop("admin_flow", None)
            await message.reply_text("Creación de categoría cancelada.")
            await message.reply_text(
                "Podés elegir una categoría existente:",
                reply_markup=product_category_keyboard(product_id),
            )
            return
        context.user_data.pop("admin_flow", None)
        await message.reply_text(
            "Operación cancelada.",
            reply_markup=admin_keyboard(),
        )
        return

    mode = flow["mode"]
    if mode == "rename_category":
        category_id = flow["category_id"]
        try:
            STORE.rename_category(category_id, value)
        except StoreError as error:
            await message.reply_text(str(error))
            return
        context.user_data.pop("admin_flow", None)
        await message.reply_text("Categoría renombrada correctamente.")
        await admin_category_detail(message, category_id)
    elif mode == "new_category":
        try:
            STORE.create_category(value)
        except StoreError as error:
            await message.reply_text(str(error))
            return
        if flow.get("return_to_product"):
            product_flow = context.user_data.pop("product_flow", None)
            context.user_data["admin_flow"] = product_flow
            await message.reply_text(f"Categoría «{value}» creada.")
            await prompt_product_category(message, context)
        elif flow.get("return_to_assignment"):
            product_id = flow["product_id"]
            context.user_data.pop("admin_flow", None)
            await message.reply_text(
                f"Categoría «{value}» creada. Ahora elegila para asignarla:"
            )
            await message.reply_text(
                "Categorías disponibles:",
                reply_markup=product_category_keyboard(product_id),
            )
        else:
            context.user_data.pop("admin_flow", None)
            await message.reply_text(
                f"Categoría «{value}» creada correctamente.",
                reply_markup=admin_keyboard(),
            )
    elif mode == "new_product":
        if step == "name":
            flow["name"] = value
            flow["step"] = "description"
            await message.reply_text("Escribí la descripción de la prenda:")
        elif step == "description":
            flow["description"] = value
            await prompt_product_category(message, context)
        elif step == "size":
            flow.setdefault("variant", {})["size"] = value
            flow["step"] = "color"
            await message.reply_text("¿Qué color tiene esta variante?")
        elif step == "color":
            flow.setdefault("variant", {})["color"] = value
            flow["step"] = "price"
            await message.reply_text(
                f"Ingresá el precio en {CURRENCY}, por ejemplo 12500 o 12500,50:"
            )
        elif step == "price":
            try:
                flow.setdefault("variant", {})["price_minor"] = parse_price(value)
            except ValueError as error:
                await message.reply_text(str(error))
                return
            flow["step"] = "stock"
            await message.reply_text("¿Cuántas unidades hay disponibles? (número entero)")
        elif step == "stock":
            try:
                stock = int(value)
                if stock < 0:
                    raise ValueError
            except ValueError:
                await message.reply_text("El stock debe ser un entero igual o mayor a cero.")
                return
            variant = flow.pop("variant")
            variant["stock"] = stock
            flow.setdefault("variants", []).append(variant)
            flow["step"] = "variant_choice"
            await message.reply_text(
                f"Variante agregada: {variant['color']} · talle {variant['size']} · "
                f"{money(variant['price_minor'])} · stock {stock}.",
                reply_markup=variant_choice_keyboard(),
            )
        else:
            await message.reply_text("Ese paso del alta ya no es válido. Escribí «cancelar».")
    elif mode == "edit_product":
        if step != "edit_product":
            await message.reply_text("Enviá una foto o escribí «cancelar».")
            return
        product_id = flow["product_id"]
        field = flow["field"]
        try:
            STORE.update_product_field(product_id, field, value)
        except StoreError as error:
            await message.reply_text(str(error))
            return
        context.user_data.pop("admin_flow", None)
        await message.reply_text("Producto actualizado correctamente.")
        await show_admin_product(message, product_id)
    elif mode == "edit_variant":
        variant_id = flow["variant_id"]
        field = flow["field"]
        try:
            parsed = parse_price(value) if field == "price_minor" else int(value)
            if parsed < 0:
                raise ValueError
            STORE.update_variant_field(variant_id, field, parsed)
        except ValueError:
            await message.reply_text(
                "Ingresá un precio válido." if field == "price_minor"
                else "El stock debe ser un entero igual o mayor a cero."
            )
            return
        except StoreError as error:
            await message.reply_text(str(error))
            return
        product_id = flow["product_id"]
        context.user_data.pop("admin_flow", None)
        await message.reply_text("Variante actualizada correctamente.")
        await show_admin_product(message, product_id)
    elif mode == "new_variant":
        if step == "new_variant_size":
            flow["size"] = value
            flow["step"] = "new_variant_color"
            await message.reply_text("¿Qué color tiene la variante?")
        elif step == "new_variant_color":
            flow["color"] = value
            flow["step"] = "new_variant_price"
            await message.reply_text(f"Ingresá el precio en {CURRENCY}:")
        elif step == "new_variant_price":
            try:
                flow["price_minor"] = parse_price(value)
            except ValueError as error:
                await message.reply_text(str(error))
                return
            flow["step"] = "new_variant_stock"
            await message.reply_text("¿Cuántas unidades hay disponibles? (número entero)")
        elif step == "new_variant_stock":
            try:
                stock = int(value)
                if stock < 0:
                    raise ValueError
            except ValueError:
                await message.reply_text("El stock debe ser un entero igual o mayor a cero.")
                return
            product_id = flow["product_id"]
            try:
                STORE.add_variant(
                    product_id,
                    flow["size"],
                    flow["color"],
                    flow["price_minor"],
                    stock,
                )
            except StoreError as error:
                await message.reply_text(str(error))
                return
            context.user_data.pop("admin_flow", None)
            await message.reply_text("Nueva variante agregada.")
            await show_admin_product(message, product_id)


def parse_price(value: str) -> int:
    try:
        amount = Decimal(value.strip().replace(",", "."))
    except InvalidOperation as error:
        raise ValueError("Ingresá un precio válido, por ejemplo 12500 o 12500,50.") from error
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise ValueError("El precio debe ser positivo y tener como máximo dos decimales.")
    return int(amount * 100)


def variant_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("➕ Agregar otro talle/color", callback_data="admin:add_variant"),
            InlineKeyboardButton("✅ Guardar producto", callback_data="admin:save"),
        ]]
    )


async def admin_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    products = STORE.admin_products()
    buttons = [
        [InlineKeyboardButton(
            f"{'✅' if product['active'] else '⏸️'} {product['name']}",
            callback_data=f"admin:product:{product['id']}",
        )]
        for product in products
    ]
    buttons.append([InlineKeyboardButton("↩️ Panel", callback_data="admin:home")])
    await query.answer()
    await query.edit_message_text(
        f"{brand_heading('Inventario')}\n\n"
        "Seleccioná una prenda para revisar sus datos, variantes y disponibilidad:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_categories(update: Update) -> None:
    query = update.callback_query
    if query is None:
        return
    categories = STORE.categories()
    lines = [brand_heading("Categorías"), ""]
    if categories:
        lines.extend(
            f"▪ {category['name']}  ·  {category['product_count']} productos"
            for category in categories
        )
    else:
        lines.append("Todavía no hay categorías. Creá una para organizar el catálogo.")
    buttons = [
        [
            InlineKeyboardButton(
                f"{category['name']} · {category['product_count']} productos",
                callback_data=f"admin:category:{category['id']}",
            )
        ]
        for category in categories
    ]
    buttons.extend(
        [
            [InlineKeyboardButton("➕ Crear categoría", callback_data="admin:addcategory")],
            [InlineKeyboardButton("Volver al panel", callback_data="admin:home")],
        ]
    )
    await query.answer()
    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons)
    )


async def confirm_category_deletion(update: Update, category_id: int) -> None:
    query = update.callback_query
    if query is None:
        return
    category = STORE.get_category(category_id)
    if category is None:
        await query.answer("No se encontró esa categoría.", show_alert=True)
        return
    summary = next(
        (item for item in STORE.categories() if item["id"] == category_id), None
    )
    if summary is None:
        await query.answer("No se encontró esa categoría.", show_alert=True)
        return
    buttons = []
    if summary["associated_product_count"]:
        buttons.extend(
            [
                [
                    InlineKeyboardButton(
                        "Eliminar categoría y productos",
                        callback_data=f"admin:confirmdeletecategory:{category_id}:1",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Eliminar categoría, conservar productos",
                        callback_data=f"admin:confirmdeletecategory:{category_id}:0",
                    )
                ],
            ]
        )
        explanation = (
            f"«{category['name']}» tiene {summary['associated_product_count']} "
            "producto(s) asociado(s). ¿Qué querés hacer?\n\n"
            "Si también los eliminás, saldrán del catálogo y del carrito; "
            "los que ya figuren en pedidos se archivarán para conservar el "
            "historial. Si los conservás, quedarán sin categoría."
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Sí, eliminar categoría",
                    callback_data=f"admin:confirmdeletecategory:{category_id}:0",
                )
            ]
        )
        explanation = f"¿Seguro que querés eliminar la categoría «{category['name']}»?"
    buttons.append(
        [InlineKeyboardButton("Cancelar", callback_data="admin:categories")]
    )
    await query.answer()
    await query.edit_message_text(
        explanation, reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_category_detail(message, category_id: int) -> None:
    category = STORE.get_category(category_id)
    if category is None:
        await message.reply_text("No se encontró esa categoría.")
        return
    details = next(
        (item for item in STORE.categories() if item["id"] == category_id), None
    )
    product_count = details["product_count"] if details else 0
    await message.reply_text(
        f"{brand_heading(category['name'])}\n\n"
        f"Productos activos  ·  {product_count}",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✏️ Renombrar",
                        callback_data=f"admin:renamecategory:{category_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗑️ Eliminar categoría",
                        callback_data=f"admin:deletecategory:{category_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Volver a categorías", callback_data="admin:categories"
                    )
                ],
            ]
        ),
    )


async def admin_assign_product_category(update: Update, product_id: int) -> None:
    query = update.callback_query
    if query is None:
        return
    product = STORE.admin_get_product(product_id)
    if product is None:
        await query.answer("No se encontró ese producto.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        f"Elegí una categoría para «{product['name']}»:",
        reply_markup=product_category_keyboard(product_id),
    )


async def admin_pending_orders(update: Update) -> None:
    query = update.callback_query
    if query is None:
        return
    orders = STORE.pending_orders()
    buttons = [
        [
            InlineKeyboardButton(
                f"#{order['id']} · {order['customer_name']} · "
                f"{money(order['total_minor'], order['currency'])}",
                callback_data=f"admin:order:{order['id']}",
            )
        ]
        for order in orders
    ]
    buttons.append([InlineKeyboardButton("Volver al panel", callback_data="admin:home")])
    await query.answer()
    text = (
        f"{brand_heading('Pedidos pendientes')}\n\n"
        "Seleccioná un pedido para revisarlo y actualizar su estado."
        if orders
        else f"{brand_heading('Pedidos pendientes')}\n\nNo hay pedidos pendientes."
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_admin_product(message, product_id: int) -> None:
    product = STORE.admin_get_product(product_id)
    if product is None:
        await message.reply_text("No se encontró ese producto.")
        return
    variants = STORE.list_variants(product_id)
    state = "Activo" if product["active"] else "Inactivo"
    lines = [
        brand_heading(product["name"]),
        "",
        f"ESTADO  ·  {state}",
        f"CATEGORÍA  ·  {product['category'] or 'Sin categoría'}",
        f"{product['description'] or 'Sin descripción'}",
        "",
        "VARIANTES",
    ]
    buttons = []
    for variant in variants:
        lines.append(
            f"▪ {variant['color']} · talle {variant['size']}  "
            f"·  {money(variant['price_minor'])}\n"
            f"  Disponibles: {variant['available']} de {variant['stock']}"
        )
        buttons.append(
            [InlineKeyboardButton(
                f"Editar {variant['color']} · {variant['size']}",
                callback_data=f"admin:variant:{variant['id']}",
            )]
        )
    if product["active"]:
        buttons.extend(
            [
                [
                    InlineKeyboardButton("✏️ Renombrar producto", callback_data=f"admin:field:{product_id}:name"),
                    InlineKeyboardButton("✏️ Descripción", callback_data=f"admin:field:{product_id}:description"),
                ],
                [
                    InlineKeyboardButton("🏷️ Asignar categoría", callback_data=f"admin:assigncategory:{product_id}"),
                    InlineKeyboardButton("🖼️ Foto", callback_data=f"admin:photo:{product_id}"),
                ],
                [InlineKeyboardButton("➕ Agregar variante", callback_data=f"admin:newvar:{product_id}")],
                [InlineKeyboardButton("🗑️ Eliminar producto", callback_data=f"admin:deactivate:{product_id}")],
            ]
        )
    else:
        buttons.append(
            [InlineKeyboardButton("▶️ Reactivar producto", callback_data=f"admin:activate:{product_id}")]
        )
    buttons.append([InlineKeyboardButton("↩️ Volver a productos", callback_data="admin:list")])
    await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    query = update.callback_query
    if query is None:
        return
    if not is_admin(update):
        await query.answer("Acceso exclusivo del administrador.", show_alert=True)
        return
    if data == "admin:home":
        await query.answer()
        await query.edit_message_text(
            brand_heading("Gestión de tienda"), reply_markup=admin_keyboard()
        )
    elif data == "admin:list":
        await admin_list_products(update, context)
    elif data == "admin:categories":
        await admin_categories(update)
    elif data.startswith("admin:category:"):
        category_id = int(data.rsplit(":", 1)[1])
        category = STORE.get_category(category_id)
        if category is None:
            await query.answer("No se encontró esa categoría.", show_alert=True)
            return
        summary = next(
            (item for item in STORE.categories() if item["id"] == category_id), None
        )
        product_count = summary["product_count"] if summary else 0
        await query.answer()
        await query.edit_message_text(
            f"{brand_heading(category['name'])}\n\n"
            f"Productos activos  ·  {product_count}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✏️ Renombrar",
                            callback_data=f"admin:renamecategory:{category_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🗑️ Eliminar categoría",
                            callback_data=f"admin:deletecategory:{category_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Volver a categorías",
                            callback_data="admin:categories",
                        )
                    ],
                ]
            ),
        )
    elif data.startswith("admin:renamecategory:"):
        category_id = int(data.rsplit(":", 1)[1])
        category = STORE.get_category(category_id)
        if category is None:
            await query.answer("No se encontró esa categoría.", show_alert=True)
            return
        context.user_data["admin_flow"] = {
            "mode": "rename_category",
            "step": "category_name",
            "category_id": category_id,
        }
        await query.answer()
        await query.message.reply_text(
            f"Escribí el nuevo nombre para «{category['name']}». "
            "Los productos asociados se actualizarán automáticamente. "
            "Escribí «cancelar» para salir."
        )
    elif data.startswith("admin:deletecategory:"):
        await confirm_category_deletion(update, int(data.rsplit(":", 1)[1]))
    elif data.startswith("admin:confirmdeletecategory:"):
        _, _, category_id_text, delete_products_text = data.split(":")
        category_id = int(category_id_text)
        try:
            deactivated_count = STORE.delete_category(
                category_id, delete_products=delete_products_text == "1"
            )
        except StoreError as error:
            await query.answer(str(error), show_alert=True)
            return
        await query.answer("Categoría eliminada.")
        if delete_products_text == "1":
            result = (
                f"Categoría eliminada. Se retiraron {deactivated_count} "
                "producto(s) del catálogo; sus pedidos históricos se conservaron."
            )
        else:
            result = (
                "Categoría eliminada. Los productos asociados quedaron "
                "sin categoría."
            )
        await query.edit_message_text(
            result,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Volver a categorías", callback_data="admin:categories")]]
            ),
        )
    elif data in {"admin:addcategory", "admin:addcategoryfromproduct"}:
        current_flow = context.user_data.get("admin_flow")
        if data == "admin:addcategoryfromproduct":
            if (
                current_flow is None
                or current_flow.get("mode") != "new_product"
                or current_flow.get("step") != "category_choice"
            ):
                await query.answer("El alta del producto ya no está activa.", show_alert=True)
                return
            context.user_data["product_flow"] = current_flow
            context.user_data["admin_flow"] = {
                "mode": "new_category",
                "step": "category_name",
                "return_to_product": True,
            }
        else:
            context.user_data["admin_flow"] = {
                "mode": "new_category",
                "step": "category_name",
            }
        await query.answer()
        await query.message.reply_text(
            "Escribí el nombre de la nueva categoría. "
            "Podés escribir «cancelar» para volver."
        )
    elif data.startswith("admin:addcategoryfor:"):
        product_id = int(data.rsplit(":", 1)[1])
        if STORE.admin_get_product(product_id) is None:
            await query.answer("No se encontró ese producto.", show_alert=True)
            return
        context.user_data["admin_flow"] = {
            "mode": "new_category",
            "step": "category_name",
            "return_to_assignment": True,
            "product_id": product_id,
        }
        await query.answer()
        await query.message.reply_text(
            "Escribí el nombre de la nueva categoría. "
            "Podés escribir «cancelar» para volver."
        )
    elif data.startswith("admin:assigncategory:"):
        product_id = int(data.rsplit(":", 1)[1])
        await admin_assign_product_category(update, product_id)
    elif data.startswith("admin:setnewcat:"):
        flow = context.user_data.get("admin_flow")
        category_id = int(data.rsplit(":", 1)[1])
        category = STORE.get_category(category_id) if category_id else None
        if (
            flow is None
            or flow.get("mode") != "new_product"
            or flow.get("step") != "category_choice"
            or (category_id and category is None)
        ):
            await query.answer("La categoría o el alta del producto ya no están disponibles.", show_alert=True)
            return
        flow["category"] = category["name"] if category is not None else ""
        flow["step"] = "photo"
        category_label = category["name"] if category is not None else "Sin categoría"
        await query.answer(category_label)
        await query.message.reply_text(
            f"Categoría seleccionada: {category_label}. "
            "Enviá una foto del producto o tocá Omitir foto.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Omitir foto", callback_data="admin:skip_photo")]]
            ),
        )
    elif data.startswith("admin:setcat:"):
        _, _, product_id_text, category_id_text = data.split(":")
        product_id = int(product_id_text)
        category_id = int(category_id_text)
        try:
            STORE.assign_product_category(
                product_id, category_id if category_id > 0 else None
            )
        except StoreError as error:
            await query.answer(str(error), show_alert=True)
            return
        context.user_data.pop("admin_flow", None)
        await query.answer("Categoría actualizada.")
        await query.edit_message_text("Categoría actualizada correctamente.")
        await show_admin_product(query.message, product_id)
    elif data == "admin:orders":
        await admin_pending_orders(update)
    elif data.startswith("admin:order:"):
        order_id = int(data.rsplit(":", 1)[1])
        order = STORE.get_order(order_id)
        if order is None:
            await query.answer("No se encontró ese pedido.", show_alert=True)
            return
        await query.answer()
        actions = order_actions_keyboard(order)
        buttons = list(actions.inline_keyboard) if actions else []
        buttons.append(
            [InlineKeyboardButton("Volver a pendientes", callback_data="admin:orders")]
        )
        await query.edit_message_text(
            order_text(order_id, order, STORE.order_items(order_id)),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    elif data == "admin:add":
        context.user_data["admin_flow"] = {
            "mode": "new_product",
            "step": "name",
            "variants": [],
            "photo_url": "",
        }
        await query.answer()
        await query.message.reply_text(
            "🆕 Nuevo producto\n\nEscribí el nombre de la prenda. "
            "En cualquier momento podés escribir «cancelar»."
        )
    elif data == "admin:skip_photo":
        flow = context.user_data.get("admin_flow")
        if not flow or flow.get("mode") != "new_product" or flow.get("step") != "photo":
            await query.answer("La operación venció.", show_alert=True)
            return
        flow["step"] = "size"
        await query.answer()
        await query.message.reply_text("Indicá el talle de la primera variante:")
    elif data == "admin:add_variant":
        flow = context.user_data.get("admin_flow")
        if not flow or flow.get("step") != "variant_choice":
            await query.answer("La operación venció.", show_alert=True)
            return
        flow["step"] = "size"
        await query.answer()
        await query.message.reply_text("Indicá el talle de la siguiente variante:")
    elif data == "admin:save":
        flow = context.user_data.get("admin_flow")
        if not flow or flow.get("mode") != "new_product" or not flow.get("variants"):
            await query.answer("Agregá al menos una variante.", show_alert=True)
            return
        try:
            product_id = STORE.create_product(
                flow["name"],
                flow["description"],
                flow["category"],
                flow.get("photo_url", ""),
                flow["variants"],
            )
        except StoreError as error:
            await query.answer(str(error), show_alert=True)
            return
        context.user_data.pop("admin_flow", None)
        await query.answer("Producto guardado.")
        await query.edit_message_text(f"✅ Producto #{product_id} guardado en el catálogo.")
        await show_admin_product(query.message, product_id)
    elif data.startswith("admin:product:"):
        product_id = int(data.rsplit(":", 1)[1])
        await query.answer()
        await show_admin_product(query.message, product_id)
    elif data.startswith("admin:field:"):
        _, _, product_id_text, field = data.split(":")
        if field not in ADMIN_FIELDS:
            await query.answer("Campo no válido.", show_alert=True)
            return
        context.user_data["admin_flow"] = {
            "mode": "edit_product",
            "step": "edit_product",
            "product_id": int(product_id_text),
            "field": field,
        }
        await query.answer()
        await query.message.reply_text(f"Escribí el nuevo valor para {ADMIN_FIELDS[field]}:")
    elif data.startswith("admin:photo:"):
        product_id = int(data.rsplit(":", 1)[1])
        context.user_data["admin_flow"] = {
            "mode": "edit_product",
            "step": "edit_photo",
            "product_id": product_id,
        }
        await query.answer()
        await query.message.reply_text("Enviá la nueva foto del producto o escribí «cancelar».")
    elif data.startswith("admin:variant:"):
        variant_id = int(data.rsplit(":", 1)[1])
        variant = STORE.admin_variant(variant_id)
        if variant is None:
            await query.answer("No se encontró la variante.", show_alert=True)
            return
        context.user_data["admin_variant_product"] = variant["product_id"]
        await query.answer()
        await query.edit_message_text(
            f"Variante: {variant['color']} · talle {variant['size']}\n"
            f"Precio: {money(variant['price_minor'])}\n"
            f"Stock disponible: {variant['available']} (reservado: {variant['reserved']})",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("💲 Cambiar precio", callback_data=f"admin:vfield:{variant_id}:price_minor"),
                        InlineKeyboardButton("📊 Cambiar stock", callback_data=f"admin:vfield:{variant_id}:stock"),
                    ],
                    [InlineKeyboardButton("↩️ Volver al producto", callback_data=f"admin:product:{variant['product_id']}")],
                ]
            ),
        )
    elif data.startswith("admin:vfield:"):
        _, _, variant_id_text, field = data.split(":")
        variant_id = int(variant_id_text)
        variant = STORE.admin_variant(variant_id)
        if variant is None or field not in {"price_minor", "stock"}:
            await query.answer("Variante o campo no válido.", show_alert=True)
            return
        context.user_data["admin_flow"] = {
            "mode": "edit_variant",
            "step": "edit_variant",
            "variant_id": variant_id,
            "product_id": variant["product_id"],
            "field": field,
        }
        await query.answer()
        prompt = f"Ingresá el nuevo precio en {CURRENCY}:" if field == "price_minor" else "Ingresá el nuevo stock total:"
        await query.message.reply_text(prompt)
    elif data.startswith("admin:newvar:"):
        product_id = int(data.rsplit(":", 1)[1])
        context.user_data["admin_flow"] = {
            "mode": "new_variant",
            "step": "new_variant_size",
            "product_id": product_id,
        }
        await query.answer()
        await query.message.reply_text("Indicá el talle de la nueva variante:")
    elif data.startswith("admin:deactivate:"):
        product_id = int(data.rsplit(":", 1)[1])
        await query.answer()
        await query.edit_message_text(
            "¿Seguro que querés eliminar este producto? Se quitará del catálogo "
            "y de los carritos. Si ya figura en pedidos, se archivará para "
            "conservar el historial.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("Sí, eliminar producto", callback_data=f"admin:confirmdeactivate:{product_id}"),
                    InlineKeyboardButton("Cancelar", callback_data=f"admin:product:{product_id}"),
                ]]
            ),
        )
    elif data.startswith("admin:confirmdeactivate:"):
        product_id = int(data.rsplit(":", 1)[1])
        try:
            archived = STORE.remove_product(product_id)
        except StoreError as error:
            await query.answer(str(error), show_alert=True)
            return
        await query.answer("Producto archivado." if archived else "Producto eliminado.")
        result = (
            "Producto archivado: permanece desactivado para conservar los pedidos "
            "históricos."
            if archived
            else "Producto eliminado del catálogo y del inventario."
        )
        await query.edit_message_text(
            result,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Volver a productos", callback_data="admin:list")]]
            ),
        )
    elif data.startswith("admin:activate:"):
        product_id = int(data.rsplit(":", 1)[1])
        try:
            STORE.activate_product(product_id)
        except StoreError as error:
            await query.answer(str(error), show_alert=True)
            return
        await query.answer("Producto reactivado.")
        await query.edit_message_text(
            "Producto reactivado.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Volver al producto", callback_data=f"admin:product:{product_id}")]]
            ),
        )


async def admin_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    message = update.effective_message
    flow = context.user_data.get("admin_flow")
    if message is None or not message.photo or flow is None:
        return
    file_id = message.photo[-1].file_id
    if flow.get("mode") == "new_product" and flow.get("step") == "photo":
        flow["photo_url"] = file_id
        flow["step"] = "size"
        await message.reply_text("Foto recibida. Indicá el talle de la primera variante:")
    elif flow.get("mode") == "edit_product" and flow.get("step") == "edit_photo":
        try:
            STORE.update_product_field(flow["product_id"], "photo_url", file_id)
        except StoreError as error:
            await message.reply_text(str(error))
            return
        product_id = flow["product_id"]
        context.user_data.pop("admin_flow", None)
        await message.reply_text("Foto actualizada correctamente.")
        await show_admin_product(message, product_id)


async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    products = STORE.list_products()
    if not products:
        if update.effective_message:
            await update.effective_message.reply_text(
                f"{brand_heading('Colección')}\n\n"
                "Estamos preparando las próximas prendas. Volvé pronto."
            )
        return
    categories = STORE.categories(populated_only=True)
    buttons = [
        [InlineKeyboardButton("Ver toda la colección", callback_data="category:all")]
    ]
    buttons.extend(
        [
            InlineKeyboardButton(
                f"{category['name']} · {category['product_count']}",
                callback_data=f"category:{category['id']}",
            )
        ]
        for category in categories
    )
    unassigned_count = STORE.unassigned_product_count()
    if unassigned_count:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Sin categoría · {unassigned_count}",
                    callback_data="category:0",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(MENU_SEARCH, callback_data="search:start")])
    if update.effective_message:
        await update.effective_message.reply_text(
            f"{brand_heading('Colección')}\n\n"
            f"{len(products)} prendas para descubrir.\n"
            "Explorá por categoría o buscá una prenda por nombre.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def show_category(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
) -> None:
    query = update.callback_query
    if query is None:
        return
    if data == "category:all":
        title = "Toda la colección"
        products = STORE.list_products()
    else:
        try:
            category_id = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer("Esa categoría ya no está disponible.", show_alert=True)
            return
        if category_id == 0:
            title = "Sin categoría"
        else:
            category = STORE.get_category(category_id)
            if category is None:
                await query.answer("Esa categoría ya no está disponible.", show_alert=True)
                return
            title = category["name"]
        products = STORE.products_in_category(category_id)
    await query.answer()
    if not products:
        await query.edit_message_text(
            f"{brand_heading(title)}\n\n"
            "No encontramos prendas en esta categoría.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Volver a categorías", callback_data="category:home")]]
            ),
        )
        return
    await query.edit_message_text(
        f"{brand_heading(title)}\n\n"
        f"{len(products)} prendas disponibles. Elegí una para ver sus detalles.",
        reply_markup=product_list_keyboard(products),
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    query = " ".join(context.args).strip()
    if not query:
        context.user_data["catalog_search"] = True
        await message.reply_text(
            f"{brand_heading('Buscar prendas')}\n\n"
            "Escribí el nombre, categoría o una palabra de la descripción."
        )
        return
    await show_search_results(message, query)


async def show_search_results(message, search_term: str) -> None:
    products = STORE.search_products(search_term)
    if not products:
        await message.reply_text(
            f"{brand_heading('Resultados')}\n\n"
            f"No encontramos prendas para «{search_term}». Probá con otra palabra.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Volver a categorías", callback_data="category:home")]]
            ),
        )
        return
    await message.reply_text(
        f"{brand_heading('Resultados')}\n\n"
        f"{len(products)} prendas para «{search_term}».",
        reply_markup=product_list_keyboard(products),
    )


async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.effective_message is None:
        return
    items = STORE.cart_items(user.id)
    buttons = []
    for item in items:
        buttons.append(
            [
                InlineKeyboardButton("−", callback_data=f"qty:{item['variant_id']}:{item['quantity'] - 1}"),
                InlineKeyboardButton("+", callback_data=f"qty:{item['variant_id']}:{item['quantity'] + 1}"),
                InlineKeyboardButton("Quitar", callback_data=f"qty:{item['variant_id']}:0"),
            ]
        )
    if items:
        buttons.append([InlineKeyboardButton("Continuar con mi pedido", callback_data="checkout")])
    else:
        buttons.append([InlineKeyboardButton("Explorar colección", callback_data="show_catalog")])
    await update.effective_message.reply_text(
        cart_text(items),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    query = update.callback_query
    if user is None or query is None:
        return
    items = STORE.cart_items(user.id)
    if not items:
        await query.answer("El carrito está vacío.", show_alert=True)
        return
    context.user_data["checkout"] = {"step": "contact"}
    await query.answer()
    await query.message.reply_text(
        f"{brand_heading('Tu pedido · 1 de 2')}\n\n"
        "¿Cuál es el mejor medio para contactarte? Podés indicar tu teléfono "
        "o usuario de Telegram.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await query.message.reply_text(
        "Podés cancelar este proceso cuando quieras.",
        reply_markup=cancel_checkout_button(),
    )


async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or not message.text:
        return
    if message.text.strip() == MENU_RESTART:
        await request_restart(update, context)
        return
    admin_flow = context.user_data.get("admin_flow")
    if admin_flow:
        if not is_admin(update):
            context.user_data.pop("admin_flow", None)
            await message.reply_text("Este acceso es exclusivo del administrador.")
            return
        await admin_text_input(update, context, message.text)
        return
    if context.user_data.pop("catalog_search", False):
        await show_search_results(message, message.text.strip())
        return
    checkout_state = context.user_data.get("checkout")
    if not checkout_state:
        menu_actions = {
            MENU_CATALOG: catalog,
            MENU_CART: show_cart,
            MENU_ORDERS: order_history,
            MENU_HELP: help_command,
            MENU_ADMIN: admin_command,
            MENU_RESTART: request_restart,
        }
        action = menu_actions.get(message.text.strip())
        if action:
            await action(update, context)
        else:
            await message.reply_text(
                f"{brand_heading('A tu ritmo')}\n\n"
                "Elegí una opción del menú o escribí /ayuda.",
                reply_markup=main_menu(is_admin(update)),
            )
        return
    value = message.text.strip()
    if not value:
        await message.reply_text("Por favor, enviá un valor válido.")
        return
    if value in {MENU_CATALOG, MENU_CART, MENU_ORDERS, MENU_HELP, MENU_ADMIN, MENU_RESTART}:
        await message.reply_text(
            "Terminá este paso o cancelá el pedido para volver al menú.",
            reply_markup=cancel_checkout_button(),
        )
        return
    if checkout_state["step"] == "contact":
        checkout_state["contact"] = value
        checkout_state["step"] = "delivery"
        await message.reply_text(
            f"{brand_heading('Tu pedido · 2 de 2')}\n\n"
            "¿Cómo preferís recibirlo? Indicá envío y dirección, o retiro en el local.",
            reply_markup=cancel_checkout_button(),
        )
        return
    checkout_state["delivery"] = value
    checkout_state["step"] = "review"
    items = STORE.cart_items(user.id)
    if not items:
        context.user_data.pop("checkout", None)
        await message.reply_text("El carrito quedó vacío. Volvé a elegir productos con /catalogo.")
        return
    preview = cart_text(items)
    await message.reply_text(
        f"{preview}\n\n"
        "RESUMEN DE ENTREGA\n"
        f"Contacto  ·  {checkout_state['contact']}\n"
        f"Modalidad  ·  {value}\n\n"
        "¿Está todo correcto? Al confirmar, enviaremos tu pedido a la tienda.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Confirmar pedido", callback_data="place_order"),
                InlineKeyboardButton("Cancelar", callback_data="cancel_checkout"),
            ]]
        ),
    )


async def place_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    state = context.user_data.get("checkout")
    if query is None or user is None or not state or state.get("step") != "review":
        if query:
            await query.answer("La sesión de compra venció. Iniciá /pedido nuevamente.", show_alert=True)
        return
    await query.answer()
    try:
        order_id = STORE.create_order(
            user.id,
            user.full_name,
            state["contact"],
            state["delivery"],
            CURRENCY,
        )
    except StoreError as error:
        context.user_data.pop("checkout", None)
        await query.message.reply_text(str(error), reply_markup=main_menu())
        return
    context.user_data.pop("checkout", None)
    order = STORE.get_order(order_id)
    items = STORE.order_items(order_id)
    await query.edit_message_text(
        f"{brand_heading('Pedido recibido')}\n\n"
        "Gracias por elegirnos. La tienda revisará tu pedido y te avisará "
        "cuando haya una actualización.\n\n"
        f"{order_text(order_id, order, items, include_customer=False)}",
    )
    await query.message.reply_text(
        "Mientras tanto, podés seguir descubriendo la colección.",
        reply_markup=main_menu(),
    )
    seller_message = order_text(order_id, order, items)
    keyboard = order_actions_keyboard(order)
    if keyboard is None:
        raise RuntimeError(f"Pending order {order_id} has no available seller actions.")
    try:
        await context.bot.send_message(
            chat_id=SELLER_ID, text=seller_message, reply_markup=keyboard
        )
    except TelegramError:
        logger.exception("Order %s created but seller notification failed", order_id)
        await query.message.reply_text(
            "El pedido quedó guardado, pero no se pudo notificar al vendedor. "
            "Por favor, contactá a la tienda e indicá el número de pedido."
        )


async def cancel_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        context.user_data.pop("checkout", None)
        await query.answer("Compra cancelada.")
        await query.edit_message_text("No envié el pedido. Tu carrito sigue guardado.")
        await query.message.reply_text(
            "Tu selección sigue guardada. Podés continuar cuando quieras.",
            reply_markup=main_menu(),
        )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    data = query.data
    if data.startswith("admin:"):
        await admin_callbacks(update, context, data)
    elif data == "restart:ask":
        await request_restart(update, context)
    elif data == "restart:confirm":
        await restart_customer_session(update, context)
    elif data == "restart:cancel":
        await cancel_restart(update)
    elif data.startswith("category:"):
        if data == "category:home":
            await query.answer()
            await catalog(update, context)
        else:
            await show_category(update, context, data)
    elif data == "search:start":
        await query.answer()
        context.user_data["catalog_search"] = True
        await query.message.reply_text(
            f"{brand_heading('Buscar prendas')}\n\n"
            "Escribí el nombre, categoría o una palabra de la descripción."
        )
    elif data.startswith("product:"):
        await product_details(update, context, int(data.split(":")[1]))
    elif data.startswith("variant:"):
        await add_variant(update, context, int(data.split(":")[1]))
    elif data.startswith("qty:"):
        await update_quantity(update, context, data)
    elif data == "checkout":
        await checkout(update, context)
    elif data == "place_order":
        await place_order(update, context)
    elif data == "cancel_checkout":
        await cancel_checkout(update, context)
    elif data.startswith(("orderstatus:", "resolve:")):
        await resolve_order(update, context, data)
    elif data.startswith("ordercancel:"):
        await confirm_order_cancellation(update, data)
    elif data.startswith("customerorder:"):
        await customer_order_detail(update, int(data.split(":", 1)[1]))
    elif data == "show_catalog":
        await query.answer()
        await catalog(update, context)
    elif data == "show_cart":
        await query.answer()
        await show_cart(update, context)
    elif data == "show_orders":
        await query.answer()
        await order_history(update, context)


async def product_details(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int
) -> None:
    query = update.callback_query
    product = STORE.get_product(product_id)
    if query is None:
        return
    if product is None:
        await query.answer("Producto no disponible.", show_alert=True)
        return
    variants = STORE.list_variants(product_id)
    lines = [brand_heading(product["name"]), ""]
    if product["category"]:
        lines.append(product["category"].upper())
    if product["description"]:
        lines.extend(["", product["description"]])
    lines.extend(["", "DETALLES"])
    buttons = []
    for variant in variants:
        availability = variant["available"]
        lines.append(
            f"▪ {variant['color']} · talle {variant['size']}\n"
            f"  {money(variant['price_minor'])}  ·  "
            f"{'Disponible' if availability > 0 else 'Agotado'}"
        )
        if availability > 0:
            buttons.append(
                [InlineKeyboardButton(
                    f"➕ {variant['color']} · {variant['size']} — {money(variant['price_minor'])}",
                    callback_data=f"variant:{variant['id']}",
                )]
            )
    buttons.append([InlineKeyboardButton("Volver a la colección", callback_data="show_catalog")])
    await query.answer()
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    photo_url = product["photo_url"]
    if photo_url:
        await query.message.reply_photo(photo=photo_url, caption="\n".join(lines), reply_markup=markup)
    else:
        await query.message.reply_text("\n".join(lines), reply_markup=markup)


async def add_variant(update: Update, context: ContextTypes.DEFAULT_TYPE, variant_id: int) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    try:
        STORE.add_to_cart(user.id, variant_id)
    except StoreError as error:
        await query.answer(str(error), show_alert=True)
        return
    await query.answer("Agregado al carrito.")
    await query.message.reply_text(
        f"{brand_heading('Buena elección')}\n\n"
        "La prenda ya está en tu selección. ¿Cómo querés continuar?",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Seguir explorando", callback_data="show_catalog"),
                InlineKeyboardButton("Ver mi selección", callback_data="show_cart"),
            ]]
        ),
    )


async def update_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    _, variant_id, quantity = data.split(":")
    try:
        STORE.set_cart_quantity(user.id, int(variant_id), int(quantity))
    except StoreError as error:
        await query.answer(str(error), show_alert=True)
        return
    await query.answer("Carrito actualizado.")
    items = STORE.cart_items(user.id)
    keyboard = []
    for item in items:
        keyboard.append(
            [
                InlineKeyboardButton("−", callback_data=f"qty:{item['variant_id']}:{item['quantity'] - 1}"),
                InlineKeyboardButton("+", callback_data=f"qty:{item['variant_id']}:{item['quantity'] + 1}"),
                InlineKeyboardButton("Quitar", callback_data=f"qty:{item['variant_id']}:0"),
            ]
        )
    if items:
        keyboard.append([InlineKeyboardButton("Continuar con mi pedido", callback_data="checkout")])
    else:
        keyboard.append([InlineKeyboardButton("Explorar colección", callback_data="show_catalog")])
    await query.edit_message_text(
        cart_text(items),
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def order_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    orders = STORE.customer_orders(user.id)
    if not orders:
        await message.reply_text(
            f"{brand_heading('Mis pedidos')}\n\n"
            "Todavía no hay pedidos para mostrar. Cuando hagas tu primera compra, "
            "vas a poder seguirla desde acá."
        )
        return
    lines = [brand_heading("Mis pedidos"), "", "Tus pedidos más recientes:", ""]
    buttons = []
    for order in orders:
        status = STATUS_LABELS.get(order["status"], order["status"])
        lines.append(
            f"▪ Pedido #{order['id']}  ·  {status}\n"
            f"  {money(order['total_minor'], order['currency'])}  ·  {order['created_at']}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Ver pedido #{order['id']}",
                    callback_data=f"customerorder:{order['id']}",
                )
            ]
        )
    await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def customer_order_detail(update: Update, order_id: int) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    order = STORE.customer_order(order_id, user.id)
    if order is None:
        await query.answer("No se encontró ese pedido.", show_alert=True)
        return
    await query.answer()
    events = STORE.order_events(order_id)
    history_lines = [
        f"▪ {STATUS_LABELS.get(event['new_status'], event['new_status'])}"
        f"  ·  {event['created_at']}"
        for event in events
    ]
    await query.edit_message_text(
        f"{order_text(order_id, order, STORE.order_items(order_id))}\n\n"
        "HISTORIAL\n" + "\n".join(history_lines),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Volver a mis pedidos", callback_data="show_orders")]]
        ),
    )


async def confirm_order_cancellation(update: Update, data: str) -> None:
    query = update.callback_query
    if query is None:
        return
    if not is_admin(update):
        await query.answer("Esta acción solo está disponible para el vendedor.", show_alert=True)
        return
    order_id = int(data.split(":", 1)[1])
    order = STORE.get_order(order_id)
    if order is None or order["status"] not in {"confirmed", "preparing"}:
        await query.answer("Este pedido ya no se puede cancelar.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        f"¿Confirmás cancelar el pedido #{order_id}?\n\n"
        "Las unidades volverán al stock disponible.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "Sí, cancelar pedido",
                    callback_data=f"orderstatus:{order_id}:cancelled",
                ),
                InlineKeyboardButton(
                    "Volver al pedido",
                    callback_data=f"admin:order:{order_id}",
                ),
            ]]
        ),
    )


async def resolve_order(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    _, raw_order_id, new_status = data.split(":", 2)
    if not is_admin(update):
        await query.answer("Esta acción solo está disponible para el vendedor.", show_alert=True)
        return
    order_id = int(raw_order_id)
    try:
        status = STORE.update_order_status(order_id, new_status, user.id)
    except StoreError as error:
        await query.answer(str(error), show_alert=True)
        return
    order = STORE.get_order(order_id)
    if order is None:
        await query.answer("Pedido no encontrado.", show_alert=True)
        return
    await query.answer("Pedido actualizado.")
    await query.edit_message_text(
        f"{order_text(order_id, order, STORE.order_items(order_id))}\n\n"
        f"ACTUALIZACIÓN  ·  {STATUS_LABELS.get(status, status)}",
        reply_markup=order_actions_keyboard(order),
    )
    try:
        await context.bot.send_message(
            chat_id=order["telegram_id"],
            text=(
                f"{brand_heading('Novedades de tu pedido')}\n\n"
                f"El estado del pedido #{order_id} cambió a "
                f"{STATUS_LABELS.get(status, status)}."
            ),
        )
    except TelegramError:
        logger.exception("Could not notify customer about order %s", order_id)


async def begin_checkout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if not STORE.cart_items(user.id):
        await message.reply_text(
            f"{brand_heading('Tu pedido')}\n\n"
            "Tu selección está vacía. Explorá la colección para empezar.",
            reply_markup=main_menu(),
        )
        return
    context.user_data["checkout"] = {"step": "contact"}
    await message.reply_text(
        f"{brand_heading('Tu pedido · 1 de 2')}\n\n"
        "¿Cuál es el mejor medio para contactarte? Podés indicar tu teléfono "
        "o usuario de Telegram.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.reply_text(
        "Podés cancelar este proceso cuando quieras.",
        reply_markup=cancel_checkout_button(),
    )


async def request_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        f"{brand_heading('Empezar de nuevo')}\n\n"
        "Esto vaciará tu carrito y cancelará el proceso de compra actual. "
        "Los pedidos que ya enviaste no se modificarán.\n\n"
        "Telegram no permite al bot borrar el historial anterior del chat. "
        "¿Querés reiniciar tu selección?",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Sí, vaciar y reiniciar", callback_data="restart:confirm"),
                InlineKeyboardButton("No, volver", callback_data="restart:cancel"),
            ]]
        ),
    )


async def restart_customer_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    STORE.clear_cart(user.id)
    for key in (
        "checkout",
        "admin_flow",
        "product_flow",
        "admin_variant_product",
        "catalog_search",
        "catalog_categories",
    ):
        context.user_data.pop(key, None)
    await query.answer("Tu selección se reinició.")
    await query.edit_message_text(
        f"{brand_heading('Todo listo')}\n\n"
        "Tu carrito está vacío y podés empezar de nuevo cuando quieras."
    )
    if query.message is not None:
        await query.message.reply_text(
            "Elegí por dónde querés empezar.",
            reply_markup=main_menu(is_admin(update)),
        )


async def cancel_restart(update: Update) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer("No se modificó tu selección.")
    await query.edit_message_text("De acuerdo. Tu carrito y el proceso actual siguen igual.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled bot error", exc_info=context.error)


async def notify_expired_orders(application: Application, expired_orders) -> None:
    for order in expired_orders:
        try:
            await application.bot.send_message(
                chat_id=order["telegram_id"],
                text=(
                    f"{brand_heading('Reserva vencida')}\n\n"
                    f"El pedido #{order['id']} venció porque no pudo confirmarse "
                    "a tiempo. La reserva se liberó; podés iniciar otro pedido "
                    "desde el catálogo."
                ),
            )
        except TelegramError:
            logger.exception(
                "Could not notify customer about expired order %s", order["id"]
            )


async def reservation_expiry_loop(application: Application) -> None:
    while True:
        await asyncio.sleep(60)
        expired_orders = STORE.expire_pending_orders(ORDER_RESERVATION_HOURS)
        await notify_expired_orders(application, expired_orders)


async def start_reservation_expiry(application: Application) -> None:
    global RESERVATION_TASK
    expired_orders = STORE.expire_pending_orders(ORDER_RESERVATION_HOURS)
    await notify_expired_orders(application, expired_orders)
    RESERVATION_TASK = asyncio.create_task(
        reservation_expiry_loop(application), name="reservation-expiry"
    )


async def stop_reservation_expiry(application: Application) -> None:
    global RESERVATION_TASK
    if RESERVATION_TASK is None:
        return
    RESERVATION_TASK.cancel()
    try:
        await RESERVATION_TASK
    except asyncio.CancelledError:
        pass
    RESERVATION_TASK = None


def build_application() -> Application:
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the environment or .env file.")
    if SELLER_ID <= 0:
        raise RuntimeError("Set TELEGRAM_SELLER_ID to the seller's Telegram user ID.")
    if ORDER_RESERVATION_HOURS < 1:
        raise RuntimeError("ORDER_RESERVATION_HOURS must be at least 1.")
    from telegram.ext import PicklePersistence

    persistence = PicklePersistence(filepath=Path(PERSISTENCE_PATH))
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .persistence(persistence)
        .post_init(start_reservation_expiry)
        .post_shutdown(stop_reservation_expiry)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", help_command))
    application.add_handler(CommandHandler("catalogo", catalog))
    application.add_handler(CommandHandler("carrito", show_cart))
    application.add_handler(CommandHandler("pedido", begin_checkout_command))
    application.add_handler(CommandHandler("mis_pedidos", order_history))
    application.add_handler(CommandHandler("reiniciar", request_restart))
    application.add_handler(CommandHandler("buscar", search_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(callbacks))
    application.add_handler(MessageHandler(filters.PHOTO, admin_photo_input))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    application = build_application()
    logger.info("Starting Telegram storefront polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
