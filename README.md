# Bot-management-store

Bot de Telegram para automatizar las ventas de una marca de ropa mediante un
catálogo interactivo, carrito y pedidos sujetos a confirmación del vendedor.

## Etapa 2: modelo de datos

Esta etapa define qué información debe guardar la aplicación, sin fijar todavía
una base de datos o tecnología. Los nombres son conceptuales y pueden adaptarse
al implementar el esquema.

### Entidades

| Entidad | Información principal |
| --- | --- |
| **Cliente** | ID interno, ID de Telegram (único), nombre, usuario de Telegram y teléfono. |
| **Producto** | ID, nombre, descripción, fotos, categoría y estado (activo/inactivo). |
| **Variante** | Producto, SKU, talle, color, precio, unidades en stock y unidades reservadas. Cada combinación vendible de talle y color es una variante. |
| **Carrito** | Cliente, estado (activo/convertido) y fecha de actualización. |
| **Ítem del carrito** | Carrito, variante y cantidad. Una variante aparece una sola vez por carrito. |
| **Pedido** | Cliente, estado, moneda, subtotal, total, datos de contacto y entrega, fechas de creación y confirmación. |
| **Ítem del pedido** | Pedido, cantidad, precio unitario, subtotal y copia del nombre del producto, talle, color y SKU al momento de la compra. |
| **Evento del pedido** | Pedido, estado anterior y nuevo, quién realizó el cambio, fecha y motivo opcional. |

Los ID de Telegram identifican al cliente sin exigir que cree una cuenta. El
vendedor se configura aparte mediante su ID de Telegram; no se debe codificar
ese dato en el catálogo ni en cada pedido.

### Relaciones

- Un cliente puede tener un carrito activo y muchos pedidos.
- Un producto tiene una o más variantes; el stock y el precio se gestionan por
  variante, no solo por producto.
- Un carrito contiene ítems asociados a variantes.
- Un pedido tiene uno o más ítems y varios eventos de seguimiento.
- Los ítems del pedido conservan una copia de los datos y precios mostrados al
  confirmar. Así, una modificación posterior del catálogo no altera pedidos
  anteriores.

### Estados y reglas

- El pedido se crea como **pendiente de confirmación** cuando el cliente revisa
  el resumen y lo envía.
- El vendedor recibe el pedido con nombre de producto, talle, color, cantidad,
  precio unitario, subtotal por ítem y total, además de los datos de contacto y
  entrega necesarios.
- El vendedor puede **confirmar** o **rechazar** el pedido. La respuesta se
  registra como evento y se notifica al cliente.
- Después de confirmarlo, el pedido puede pasar por **en preparación**,
  **enviado** o **listo para retirar**, y **entregado**. También puede quedar
  **cancelado**. Cada cambio se registra para permitir seguimiento.
- El stock disponible se calcula como `unidades en stock - unidades
  reservadas`; no puede ser negativo. Al enviar el pedido se reservan las
  unidades solicitadas. Si se rechaza o cancela, se liberan; al completar la
  venta, las unidades reservadas se descuentan del stock físico.
- La reserva debe liberarse al rechazar/cancelar y tener un vencimiento
  configurable si el vendedor no responde. El plazo de vencimiento queda por
  definir antes de implementarlo.
- Los importes se guardan en una moneda definida para la tienda y con precisión
  decimal fija. El total del pedido debe coincidir con la suma de sus ítems y
  cualquier costo de envío que se agregue.
- Un pedido debe conservar el precio vigente al momento de enviarlo, aunque el
  vendedor cambie el precio antes de confirmarlo.

### Fuera de este modelo inicial

La integración de pagos online y el panel administrativo pueden añadirse en
etapas posteriores. Para incorporar pagos, se agregará un registro de pago
asociado al pedido, con proveedor, importe, estado e identificador de
transacción; no se guardarán datos de tarjetas.

## Etapa 3: preparar el bot de Telegram

La tercera etapa define cómo se conecta la lógica de la tienda con Telegram y
qué funcionalidades básicas debe ofrecer el bot para interactuar con clientes y
vendedor.

### Objetivo

Crear la capa de conversación del bot, sin todavía definir el diseño visual del
panel administrativo ni integrar pagos. La prioridad es que el bot pueda:

- Recibir y validar la interacción del cliente.
- Mostrar el catálogo y variantes de productos.
- Guardar y actualizar el carrito.
- Pedir información de entrega, contacto y confirmación.
- Enviar el pedido al vendedor para aprobación.
- Informar al cliente del resultado final del pedido.

### Configuración inicial

- Crear un bot con BotFather y obtener el token del bot.
- Guardar la configuración en variables de entorno: token, ID del vendedor,
  modo de ejecución, URL del webhook si se usa, y clave de acceso a la base de
  datos o servicio de almacenamiento.
- Definir un canal o ID de chat del vendedor para recibir notificaciones.
- Configurar el bot en modo de prueba con un entorno de staging antes del
  despliegue final.

### Comandos y flujo principal

Los comandos iniciales del bot pueden incluir:

- `/start` — bienvenida y explicación del servicio.
- `/catalogo` — abre el catálogo de ropa.
- `/carrito` — muestra los productos agregados y el total actual.
- `/pedido` — revisa el resumen y confirma la compra.
- `/mis_pedidos` — consulta el estado de los pedidos del cliente.
- `/ayuda` — explica cómo usar el bot.

También se pueden usar botones inline para simplificar la experiencia y evitar
que el cliente tenga que escribir textos largos. Por ejemplo:

- `Ver catálogo`
- `Agregar al carrito`
- `Más detalles`
- `Aumentar cantidad`
- `Disminuir cantidad`
- `Revisar pedido`
- `Confirmar compra`
- `Cancelar`

### Estructura de conversación sugerida

El bot deberá seguir una secuencia clara para evitar caos en la compra:

1. El cliente inicia el bot con `/start`.
2. Explora el catálogo por categoría o producto.
3. Selecciona una variante: talle, color y cantidad disponible.
4. Agrega la variante al carrito.
5. Revisa el carrito y cambia cantidades o elimina ítems si hace falta.
6. Confirma la compra y completa nombre, teléfono y datos de entrega.
7. El bot crea el pedido con estado **pendiente de confirmación**.
8. La notificación se envía al vendedor con detalle del pedido.
9. El vendedor confirma o rechaza.
10. El cliente recibe la actualización del estado.

### Mensaje de confirmación al vendedor

Ejemplo de notificación que recibe el vendedor:

> Nuevo pedido #104
>
> Cliente: Martina López
> Teléfono: +54 9 11 5555-4444
> Entrega: Envío a domicilio
>
> - Remera básica / Negro / Talle M x 2 — $4.500
> - Pantalón cargo / Beige / Talle 40 x 1 — $8.400
>
> Total: $17.300
>
> [Confirmar pedido] [Rechazar pedido]

Este formato conviene para que el vendedor comprenda rápidamente qué está
comprando, en qué variante y a qué precio.

### Reglas de UX para la primera versión

- Usar mensajes cortos y claros, con un máximo razonable de texto por paso.
- Mostrar siempre el precio unitario y el total acumulado.
- No pedir datos innecesarios antes de confirmar la compra.
- Si un producto queda sin stock, mostrar el mensaje correspondiente y no dejar
  comprar esa variante.
- Si la variante no está disponible, no permitir agregarla al carrito.
- Cuando el cliente confirme el pedido, mostrar un resumen final antes de
  enviarlo.

### Persistencia y estado de sesión

El bot debe guardar la sesión y el estado actual del cliente por cada chat,
para saber si está navegando el catálogo, revisando el carrito o completando la
compra. Esto incluye:

- Producto actual visto.
- Carrito activo.
- Datos de entrega en progreso.
- Último pedido generado y su estado.
- Vendedor asociado al comercio.

Las sesiones deben poder recuperarse si ocurre un error o si el cliente vuelve a
escribir al bot.

## Bot funcional (MVP)

El repositorio incluye una primera versión ejecutable del bot:

- `/start`, `/ayuda`, `/catalogo`, `/buscar`, `/carrito`, `/pedido` y
  `/mis_pedidos`.
- Menú persistente con botones para catálogo, carrito, pedidos, ayuda y
  reiniciar la selección.
- Catálogo con productos, variantes, talles, colores, precios, stock y foto
  opcional, navegación por categoría y búsqueda en nombre, categoría y
  descripción.
- Categorías persistentes administrables desde `/admin`; el administrador
  puede crearlas, renombrarlas, eliminarlas, elegir una al dar de alta un
  producto y asignar o cambiar la categoría de productos existentes. Al borrar
  una categoría con productos asociados, el bot pregunta si también quiere
  eliminarlos del catálogo o conservarlos sin categoría.
- Eliminación de productos desde su ficha: se quitan del catálogo y los
  carritos; si aparecen en pedidos históricos, se archivan en vez de borrarse
  para preservar esos registros.
- El catálogo presenta primero las categorías con productos y después, al
  elegir una, sus prendas. Los productos todavía no asignados aparecen en una
  sección separada hasta que el administrador los organice.
- Panel administrativo protegido por el ID de Telegram configurado como
  vendedor; se abre con `/admin`.
- Alta guiada de productos con foto opcional, descripción, categoría y una o
  más combinaciones de talle/color, precio y stock.
- Edición de nombre, descripción, categoría, foto, precio y stock; los productos
  pueden retirarse del catálogo sin borrar pedidos anteriores.
- Botones inline para seleccionar variantes, volver al catálogo, editar el
  carrito, continuar comprando y cancelar la compra.
- Carrito persistente en SQLite con controles para aumentar, disminuir o quitar
  unidades.
- Checkout que solicita contacto y entrega, muestra el resumen y pide una
  confirmación antes de enviar el pedido.
- Comando `/reiniciar` para vaciar el carrito y cancelar el flujo de compra
  actual tras confirmar, sin afectar pedidos ya enviados.
- Panel de pedidos pendientes para el vendedor y notificación con productos,
  variantes, cantidades, importes, total y acciones de gestión.
- Seguimiento de estados: pendiente, confirmado, en preparación, enviado o
  listo para retirar, entregado, rechazado y cancelado. Cada actualización se
  registra y se notifica al cliente.
- Reserva de stock al crear el pedido; al confirmar se descuenta del stock y al
  rechazar o cancelar se libera la reserva. Los pedidos pendientes vencen
  automáticamente después del plazo configurado y liberan el stock.
- Persistencia de pedidos y estados en SQLite. Los pedidos guardan una copia de
  los datos del producto y del precio al momento de la compra. El cliente puede
  abrir un pedido para consultar sus artículos y el historial de cambios.

### Requisitos

- Python 3.10 o superior.
- Un bot creado con [BotFather](https://t.me/BotFather).
- El ID numérico de Telegram del vendedor. El vendedor debe iniciar el bot con
  `/start` antes de que el bot pueda enviarle mensajes.

### Instalación y ejecución

Desde la carpeta del proyecto, crear un entorno e instalar las dependencias:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Editar `.env` y establecer `TELEGRAM_BOT_TOKEN`, `TELEGRAM_SELLER_ID` y
`SHOP_NAME` con el nombre visible de la marca.
No compartir ni subir el archivo `.env`. Luego iniciar el bot:

```powershell
python bot.py
```

### Despliegue en Render

El archivo `render.yaml` configura el bot como un **Background Worker** con
polling y un disco persistente para SQLite y el estado de Telegram. Para
desplegarlo:

1. Subir el repositorio a GitHub y, en Render, elegir **New +** → **Blueprint**.
2. Conectar el repositorio y confirmar la creación del servicio definido en
   `render.yaml`.
3. En la configuración inicial, completar `TELEGRAM_BOT_TOKEN` (token de
   BotFather) y `TELEGRAM_SELLER_ID` (ID numérico del vendedor).
4. Antes del primer despliegue, reemplazar `catalog.json` por el catálogo real.
   El catálogo se importa a SQLite cuando la base de datos está vacía; después,
   los productos se administran desde Telegram con `/admin`.
5. Revisar `SHOP_NAME` y `CURRENCY` en las variables de entorno del servicio.

El servicio necesita un plan pago de worker para adjuntar el disco persistente.
No quites el disco ni cambies las rutas `DATABASE_PATH` y `PERSISTENCE_PATH`:
SQLite y el estado de conversación se perderían entre reinicios. Mantené una
sola instancia del worker, ya que el bot usa polling y el disco no debe
compartirse entre instancias.

El administrador autorizado debe abrir el bot en Telegram y enviar `/admin`.
Desde el panel, primero puede crear las categorías en **Categorías**. Luego
puede elegir una al crear cada producto o asignarla desde la ficha de un
producto existente. Las categorías ya presentes en productos existentes se
conservan automáticamente durante la migración. Al seleccionar una categoría
en **Categorías**, el administrador también puede renombrarla o eliminarla; si
tiene productos asociados, puede elegir entre conservarlos sin categoría o
eliminarlos junto con la categoría. Los productos sin pedidos asociados se
borran del inventario; los productos que ya aparecen en pedidos se archivan y
se conservan sus registros históricos. Desde el panel también puede crear
productos paso a paso; los importes se ingresan en la
moneda configurada (por ejemplo `12500` o `12500,50`). Puede enviar una foto
desde Telegram o continuar sin foto. Cada combinación de talle y color tiene
precio y stock propios. El menú administrativo no se muestra a otros usuarios y
las acciones administrativas verifican el ID del vendedor en el servidor.

La primera ejecución crea `store.sqlite3` y carga el catálogo inicial desde
`catalog.json`. El precio se expresa en centavos/unidades menores; por ejemplo,
`450000` se muestra como `ARS 4.500,00`. Los productos de ejemplo deben
reemplazarse por los de la tienda **antes de la primera ejecución**. Una vez
creada la base de datos, el catálogo vive en SQLite; modificar el JSON no
actualiza automáticamente una base ya existente. `DATABASE_PATH`,
`CATALOG_PATH`, `CURRENCY` y `PERSISTENCE_PATH` también pueden configurarse en
`.env`. `ORDER_RESERVATION_HOURS` define cuánto tiempo se mantiene una reserva
pendiente antes de liberarla automáticamente; el valor predeterminado es 24
horas. El bot revisa las reservas vencidas al iniciar y luego cada minuto.

### Pruebas

```powershell
python -m unittest discover -s tests -v
```

### Alcance y limitaciones actuales

Esta versión usa polling y está pensada para una tienda y un vendedor. No
incluye pagos online ni cálculo de envíos. La administración del catálogo está
disponible desde Telegram para un único vendedor. Los pedidos enviados o listos
para retirar se marcan como entregados desde el panel; todavía no se integra
con transportistas ni hay comprobantes de entrega. No se deben guardar
credenciales reales en el repositorio. El botón de reinicio limpia el carrito y
la sesión activa, pero Telegram no permite al bot borrar el historial completo
del chat.

### Próximas mejoras recomendadas

Las principales funciones de seguimiento, vencimiento de reservas, consulta de
pedidos y descubrimiento del catálogo ya están disponibles. Como siguientes
etapas se recomiendan:

1. **Pagos online:** integrar un proveedor elegido y guardar el estado e
   identificador de transacción sin almacenar datos de tarjetas.
2. **Envíos integrados:** definir zonas, tarifas y reglas de retiro antes de
   conectar un transportista o agregar costos al total.
3. **Comprobantes y devoluciones:** definir el flujo operativo para adjuntar
   comprobantes de entrega y resolver cancelaciones posteriores al despacho.

La identidad que aparece en los mensajes se configura con `SHOP_NAME` en `.env`.
