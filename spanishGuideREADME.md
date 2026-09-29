# Cómo arreglar el error de Move to iOS al transferir WhatsApp de Android a iPhone

Esta guía resuelve un caso concreto: **Move to iOS falla siempre en el mismo porcentaje** al transferir WhatsApp, y muestra *"Ocurrió un error inesperado. Inténtalo más tarde"* (en inglés, *"An unknown error occurred. Please try again later"*).

La causa no es la red, ni el cable, ni la versión de la app. Es corrupción en la base de datos de WhatsApp en el teléfono Android: filas que apuntan a registros que ya no existen. El exportador de Move to iOS no maneja esas referencias y se detiene.

La solución es abrir la base de datos, eliminar las filas colgadas y devolverla al teléfono.

---

## ⚠️ LEE ESTO PRIMERO: respalda tu multimedia

Más adelante esta guía usa `adb shell pm clear com.whatsapp`.

**`pm clear` borra también `/sdcard/Android/media/com.whatsapp`.** Ahí viven todas tus fotos, vídeos, notas de voz y documentos de WhatsApp. Se borran de forma irrecuperable. El almacenamiento de Android moderno está cifrado por archivo, así que ninguna herramienta de recuperación los devuelve.

Antes de cualquier otra cosa, copia esa carpeta completa a tu computadora:

```bash
adb pull /sdcard/Android/media/com.whatsapp ./whatsapp-media-backup
du -sh ./whatsapp-media-backup
```

Puede tardar y pesar varios gigabytes. Hazlo igual.

Y activa la copia de carpetas del dispositivo en Google Fotos para `WhatsApp Images`, `WhatsApp Video` y `WhatsApp Documents`. Eso te protege el multimedia con independencia de WhatsApp.

Yo aprendí esto por el camino difícil. No repitas mi error.

---

## ¿Es este tu problema?

Esta guía sirve si cumples las dos condiciones:

- La transferencia falla en la pantalla de preparación o de envío de WhatsApp.
- Falla **siempre en el mismo porcentaje**.

Un porcentaje fijo significa un fallo determinista, y un fallo determinista significa un registro concreto. Si el porcentaje cambia en cada intento, tu problema es de red o de energía, y esta guía no aplica.

### Lo que no funciona (ya lo probamos)

| Consejo habitual | Resultado |
|---|---|
| Entrar en la beta de WhatsApp | A algunas personas les sirve. A muchas no. |
| Quitar la SIM del Android | No cambia nada por sí solo. |
| Cable en lugar de Wi-Fi, o al revés | No cambia nada si el fallo es determinista. |
| Reinstalar WhatsApp | No limpia la base de datos. |
| Reducir el tamaño de la copia | Mueve el porcentaje, pero sigue fallando. |
| Move to iOS 3.5.0 en lugar de la versión nueva | Resuelve otros fallos, no este. |

La versión antigua de Move to iOS sí vale la pena si tu problema es distinto. Está en [APKMirror](https://www.apkmirror.com/apk/apple/move-to-ios/), versión 3.5.0, build 3028.

---

## Requisitos

- Una computadora con macOS o Linux.
- `adb` instalado. En macOS: `brew install android-platform-tools`.
- Python con [`wa-crypt-tools`](https://github.com/ElDavoo/wa-crypt-tools): `python -m pip install wa-crypt-tools`.
- `sqlite3`. Viene en macOS y en la mayoría de distribuciones.
- Depuración USB activada en el Android.
- Espacio libre en disco: unas tres veces el tamaño de tu copia de WhatsApp.

---

## Paso 1: activa las copias con cifrado de extremo a extremo

Sin esto no puedes descifrar la base de datos.

1. En WhatsApp, ve a **Ajustes > Chats > Copia de seguridad > Copia con cifrado de extremo a extremo**.
2. Actívala y elige la opción de **clave de 64 dígitos**, no la de contraseña.
3. Guarda la clave: captura de pantalla, foto y gestor de contraseñas.

> **ADVERTENCIA:** Si pierdes la clave de 64 dígitos, pierdes el acceso a todas tus copias. No existe recuperación.

4. Haz una copia manual completa, con los chats al día.

La contraseña no sirve para descifrar. Solo protege la clave en los servidores de WhatsApp. Necesitas los 64 dígitos.

---

## Paso 2: respalda todo

```bash
# El multimedia (lee la advertencia del principio)
adb pull /sdcard/Android/media/com.whatsapp ./whatsapp-media-backup

# La base de datos cifrada
adb pull /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15 msgstore.ORIGINAL.crypt15

# Mira qué más hay en la carpeta
adb shell 'ls -l /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/'
```

Si aparece un archivo `.crypt14`, bájalo también. Esas copias son anteriores al cifrado de extremo a extremo y se restauran **sin** la clave de 64 dígitos. Son tu red de seguridad más barata.

---

## Paso 3: descifra la base de datos

```bash
wacreatekey --hex <tu clave de 64 dígitos, en minúsculas y sin espacios>
wadecrypt encrypted_backup.key msgstore.ORIGINAL.crypt15 decrypted.db
ls -lh decrypted.db
```

Si `wadecrypt` falla, borra `encrypted_backup.key` y repite `wacreatekey`. `wa-crypt-tools` también acepta directamente la captura de pantalla de la clave, si tienes Tesseract instalado.

---

## Paso 4: diagnostica

Guarda esto como `wa-diagnose.sh`:

```bash
#!/usr/bin/env bash
# wa-diagnose.sh — busca corrupción en una base msgstore de WhatsApp ya descifrada.
# Uso: ./wa-diagnose.sh decrypted.db
set -euo pipefail

DB="${1:?Uso: $0 <msgstore descifrado>}"

echo "=== Integridad SQLite ==="
sqlite3 "$DB" "PRAGMA integrity_check;"

echo
echo "=== Totales ==="
sqlite3 -header -column "$DB" "
SELECT (SELECT COUNT(*) FROM message) AS mensajes,
       (SELECT COUNT(*) FROM chat)    AS chats,
       (SELECT COUNT(*) FROM message_media) AS filas_media;"

echo
echo "=== Tipos de mensaje (un NULL aquí suele ser la fila centinela) ==="
sqlite3 -header -column "$DB" "
SELECT message_type, COUNT(*) AS n FROM message GROUP BY message_type ORDER BY n DESC LIMIT 15;"

echo
echo "=== Referencias colgadas (ESTO es lo que rompe Move to iOS) ==="
sqlite3 -header -column "$DB" "
SELECT 'mensajes sin chat' AS problema,
       COUNT(*) AS n
FROM message
WHERE chat_row_id NOT IN (SELECT _id FROM chat) AND key_id <> '-1'
UNION ALL
SELECT 'mensajes con tipo NULL',
       COUNT(*)
FROM message
WHERE message_type IS NULL AND key_id <> '-1';"

echo
echo "=== Filas hijas colgadas, tabla por tabla ==="
REPORT="$(mktemp)"
sqlite3 -noheader "$DB" "
SELECT 'SELECT ''' || m.name || ''' AS tabla, COUNT(*) AS n FROM \"' || m.name || '\" WHERE ' || p.name || ' > 0 AND ' || p.name || ' NOT IN (SELECT _id FROM message) UNION ALL'
FROM sqlite_master m
JOIN pragma_table_info(m.name) p
WHERE m.type = 'table'
  AND p.name IN ('message_row_id', 'parent_message_row_id')
  AND m.name NOT IN ('chat', 'message');" > "$REPORT"
echo "SELECT 'fin' AS tabla, 0 AS n;" >> "$REPORT"
sqlite3 -noheader -separator '|' "$DB" < "$REPORT" | awk -F'|' '$2 > 0'
rm -f "$REPORT"

echo
echo "=== Multimedia: en su mayoría RUIDO, lee la guía antes de alarmarte ==="
sqlite3 -header -column "$DB" "
SELECT 'sin ruta' AS campo, COUNT(*) AS n FROM message_media WHERE file_path IS NULL OR TRIM(file_path) = ''
UNION ALL SELECT 'tamaño 0 o nulo', COUNT(*) FROM message_media WHERE file_size IS NULL OR file_size <= 0
UNION ALL SELECT 'sin mime', COUNT(*) FROM message_media WHERE mime_type IS NULL OR TRIM(mime_type) = ''
UNION ALL SELECT 'file_size <> file_length', COUNT(*) FROM message_media WHERE file_size <> file_length
UNION ALL SELECT 'sin transferir', COUNT(*) FROM message_media WHERE transferred = 0;"
```

Ejecútalo:

```bash
chmod +x wa-diagnose.sh
./wa-diagnose.sh decrypted.db
```

### Cómo leer el resultado

**Lo que importa.** El bloque de referencias colgadas y el de filas hijas. Cualquier número distinto de cero ahí es corrupción real. En mi caso salieron 113 mensajes de sistema apuntando a chats inexistentes, más 847 filas repartidas en siete tablas:

```
message_text|11
message_thumbnail|140
message_media_interactive_annotation|13
message_secret|276
template_messages_metadata|75
message_media|331
message_inline_video_metadata|1
```

**Lo que es ruido.** Casi todo el bloque de multimedia:

| Señal | Por qué no es corrupción |
|---|---|
| `message_type` NULL con 1 fila | Es la fila centinela que WhatsApp crea en toda base. Se reconoce por `_id = 1`, `chat_row_id = -1`, `key_id = '-1'`. Normal. |
| `sin ruta` y `sin transferir` | Multimedia que nunca descargaste o que borraste. WhatsApp conserva la fila. Normal. |
| `tamaño 0 o nulo` y `file_size <> file_length` | En los esquemas actuales `file_size` casi no se usa. El valor real vive en `file_length`. Normal. |
| `sin mime` | Muchas imágenes enviadas guardan el `mime_type` vacío. Normal. |

En mi base, esas cuatro señales sumaban más de 100.000 filas y ninguna tenía nada que ver con el fallo.

**Archivos rotos en el disco.** Busca también multimedia de 0 bytes, que sí cuelga la transferencia:

```bash
adb shell 'find /sdcard/Android/media/com.whatsapp/WhatsApp/Media -type f -size 0'
```

Ignora los `.nomedia`: son marcadores vacíos por diseño. Cualquier otro archivo de 0 bytes, bórralo.

---

## Paso 5: limpia la base de datos

Guarda esto como `wa-clean.sh`:

```bash
#!/usr/bin/env bash
# wa-clean.sh — elimina referencias colgadas de una base msgstore descifrada.
# Uso: ./wa-clean.sh decrypted.db work.db
set -euo pipefail

SRC="${1:?Uso: $0 <msgstore descifrado> <salida>}"
DST="${2:?Uso: $0 <msgstore descifrado> <salida>}"

cp "$SRC" "$DST"

ANTES="$(sqlite3 "$DST" 'SELECT COUNT(*) FROM message;')"
echo "Mensajes antes: $ANTES"

# 1. Marca los mensajes cuyo chat ya no existe.
sqlite3 "$DST" "
DROP TABLE IF EXISTS bad_msgs;
CREATE TABLE bad_msgs AS
SELECT _id FROM message
WHERE (chat_row_id NOT IN (SELECT _id FROM chat) OR message_type IS NULL)
  AND key_id <> '-1';"

MALOS="$(sqlite3 "$DST" 'SELECT COUNT(*) FROM bad_msgs;')"
echo "Mensajes a eliminar: $MALOS"

if [ "$MALOS" = "0" ]; then
  echo "Nada que limpiar por este patrón."
fi

CLEAN="cleanup.sql"
: > "$CLEAN"

# 2. Borra las filas hijas de esos mensajes.
sqlite3 -noheader "$DST" "
SELECT 'DELETE FROM \"' || m.name || '\" WHERE ' || p.name || ' IN (SELECT _id FROM bad_msgs);'
FROM sqlite_master m
JOIN pragma_table_info(m.name) p
WHERE m.type = 'table'
  AND p.name IN ('message_row_id', 'parent_message_row_id')
  AND m.name NOT IN ('chat', 'message', 'bad_msgs');" >> "$CLEAN"

# 3. Borra los mensajes.
echo 'DELETE FROM message WHERE _id IN (SELECT _id FROM bad_msgs);' >> "$CLEAN"

# 4. Barrido general: cualquier fila hija que apunte a un mensaje inexistente.
sqlite3 -noheader "$DST" "
SELECT 'DELETE FROM \"' || m.name || '\" WHERE ' || p.name || ' > 0 AND ' || p.name || ' NOT IN (SELECT _id FROM message);'
FROM sqlite_master m
JOIN pragma_table_info(m.name) p
WHERE m.type = 'table'
  AND p.name IN ('message_row_id', 'parent_message_row_id')
  AND m.name NOT IN ('chat', 'message', 'bad_msgs');" >> "$CLEAN"

echo 'DROP TABLE bad_msgs;' >> "$CLEAN"

# 5. Control de seguridad: nunca tocar la tabla chat.
if grep -q 'DELETE FROM "chat"' "$CLEAN"; then
  echo "ABORTADO: el script generó un DELETE contra la tabla chat." >&2
  exit 1
fi

echo "Sentencias generadas: $(grep -c DELETE "$CLEAN")"

# 6. Ejecuta.
sqlite3 "$DST" < "$CLEAN"

# 7. Verifica.
echo
echo "=== Verificación ==="
sqlite3 -header -column "$DST" "
SELECT (SELECT COUNT(*) FROM message WHERE chat_row_id NOT IN (SELECT _id FROM chat) AND key_id <> '-1') AS msgs_huerfanos,
       (SELECT COUNT(*) FROM message_media WHERE message_row_id NOT IN (SELECT _id FROM message)) AS media_huerfano,
       (SELECT COUNT(*) FROM message) AS mensajes_ahora;"
sqlite3 "$DST" "PRAGMA integrity_check;"

DESPUES="$(sqlite3 "$DST" 'SELECT COUNT(*) FROM message;')"
echo "Mensajes: $ANTES -> $DESPUES (diferencia: $((ANTES - DESPUES)))"

sqlite3 "$DST" "VACUUM;"
echo "Listo: $DST"
```

Ejecútalo:

```bash
chmod +x wa-clean.sh
./wa-clean.sh decrypted.db work.db
```

Esperas ver `0`, `0`, `ok`, y una diferencia de mensajes igual al número que anunció como "a eliminar". Vuelve a correr `wa-diagnose.sh` sobre `work.db`: el bloque de filas hijas colgadas debe salir vacío.

El script nunca toca la tabla `chat`. Esa tabla contiene columnas como `last_message_row_id`, así que aparece en las búsquedas por nombre de columna, pero borrar de ella destruiría tus conversaciones.

---

## Paso 6: vuelve a cifrar y verifica

Guarda esto como `wa-repack.sh`:

```bash
#!/usr/bin/env bash
# wa-repack.sh — cifra una base limpia y comprueba el resultado.
# Uso: ./wa-repack.sh encrypted_backup.key work.db msgstore.ORIGINAL.crypt15 msgstore.NEW.crypt15
set -euo pipefail

KEY="${1:?}"
PLAIN="${2:?}"
REF="${3:?}"
OUT="${4:?}"

rm -f "$OUT" roundtrip.db

# --reference copia el IV, el encabezado y el nivel de compresión del respaldo real.
waencrypt --reference "$REF" "$KEY" "$PLAIN" "$OUT"

echo
echo "=== Encabezados: deben ser idénticos ==="
wainfo "$REF"
wainfo "$OUT"

echo
echo "=== Ida y vuelta ==="
wadecrypt "$KEY" "$OUT" roundtrip.db

if cmp -s "$PLAIN" roundtrip.db; then
  echo "OK: el archivo cifrado descifra exactamente a $PLAIN"
else
  echo "FALLO: el descifrado no coincide. No uses $OUT." >&2
  exit 1
fi

ls -l "$REF" "$OUT"
```

Ejecútalo:

```bash
chmod +x wa-repack.sh
./wa-repack.sh encrypted_backup.key work.db msgstore.ORIGINAL.crypt15 msgstore.NEW.crypt15
```

Dos notas sobre el resultado:

`wainfo` solo imprime campos del encabezado y no incluye el tamaño. Que las dos salidas sean idénticas, con el mismo IV, es exactamente lo que buscas: `--reference` reprodujo el encabezado del teléfono byte por byte.

**El archivo nuevo puede salir más grande que el original.** En mi caso pasó de 182 MB a 190 MB, aunque quité filas. `VACUUM` recoloca las páginas de SQLite y eso cambia cuánto comprime zlib. No es un síntoma de nada. Lo que decide es la comprobación de ida y vuelta.

---

## Paso 7: devuelve la base al teléfono

Move to iOS **no** lee el archivo `.crypt15`. Lee la base viva, en `/data/data/com.whatsapp/databases/msgstore.db`, que es inaccesible sin root. Por eso hay que hacer que WhatsApp restaure tu archivo.

### 7a. Quita la copia de Google Drive

WhatsApp prefiere la nube. Mientras exista una copia en Drive, nunca te ofrecerá la local.

1. En WhatsApp: **Ajustes > Chats > Copia de seguridad > Copias automáticas > No**.
2. En la app de Google Drive: menú > **Copias de seguridad** > los tres puntos junto a WhatsApp > **Eliminar copia de seguridad**.
3. Opcional y recomendado, desde `drive.google.com`: engranaje > Configuración > **Administrar aplicaciones** > WhatsApp Messenger > Opciones > **Desconectar de Drive** y **Borrar datos ocultos**.

### 7b. Aparta las otras copias del teléfono

Si la carpeta `Databases` contiene varios archivos, WhatsApp puede elegir el que no quieres. Un `.crypt14` es especialmente peligroso, porque se restaura sin pedir clave y pasaría desapercibido.

Muévelos **fuera** de `Android/media`, a un sitio que `pm clear` no alcance:

```bash
adb shell 'mkdir -p /sdcard/wa-db-aside'
adb shell 'mv /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/* /sdcard/wa-db-aside/'
```

### 7c. El orden correcto

Este orden importa. Si empujas el archivo antes de `pm clear`, lo pierdes.

```bash
# 1. Deja la app como recién instalada.
#    Esto BORRA /sdcard/Android/media/com.whatsapp. Tu respaldo del Paso 2 es obligatorio.
adb shell pm clear com.whatsapp
```

Ahora, **en el teléfono**: abre WhatsApp, acepta los términos, y detente en la pantalla que pide el número. No lo escribas. Esto hace que WhatsApp recree su carpeta con los permisos correctos.

```bash
# 2. Detén la app y coloca tu base limpia.
adb shell am force-stop com.whatsapp
adb shell 'mkdir -p /sdcard/Android/media/com.whatsapp/WhatsApp/Databases'
adb push msgstore.NEW.crypt15 /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/msgstore.db.crypt15

# 3. Comprueba el resultado.
adb shell 'ls -l /sdcard/Android/media/com.whatsapp/WhatsApp/Databases/'
```

Debe mostrar un solo archivo, con el tamaño de tu `msgstore.NEW.crypt15` y dueño `u0_aNNN media_rw`. Si el dueño es `shell`, WhatsApp no podrá leerlo.

### 7d. Restaura

En el teléfono:

1. Abre WhatsApp y verifica tu número.
2. Cuando pida permiso para buscar copias en tu cuenta de Google, pulsa **Omitir**. Concederlo volvería a vincular Drive.
3. Si aparece la pantalla de transferir desde el teléfono anterior, recházala con la opción secundaria.
4. Cuando ofrezca la copia local, pulsa restaurar.
5. Introduce la clave de 64 dígitos.

No necesitas restaurar nada a mano. WhatsApp lee el archivo que colocaste.

Si te lleva directo a poner nombre y foto sin ofrecer ninguna copia, no completes nada. Revisa el dueño del archivo y que la carpeta `Databases` tenga un solo `.crypt15`.

---

## Paso 8: recupera multimedia antes de transferir

Este paso va después del 7, no antes. El paso 7 vacía la carpeta multimedia, y este la vuelve a llenar. Un `adb push` antes del `pm clear` se pierde, y las flechas de descarga solo aparecen sobre los archivos que faltan en el disco.

Después de la restauración, WhatsApp muestra una flecha de descarga sobre el multimedia que no está en el disco. Los mensajes suficientemente recientes se vuelven a bajar del servidor; los antiguos dirán que ya no está disponible.

Recorre los chats que te importan y descarga lo que quieras conservar. Todo lo que descargues ahora viaja al iPhone. Lo que no, se queda ausente para siempre.

Si respaldaste la carpeta multimedia en el Paso 2, puedes devolverla:

```bash
adb push ./whatsapp-media-backup/WhatsApp/Media /sdcard/Android/media/com.whatsapp/WhatsApp/
```

---

## Paso 9: la transferencia

1. Borra el iPhone: **Ajustes > General > Transferir o restablecer iPhone > Borrar contenidos y ajustes**. La transferencia de WhatsApp solo es posible durante la configuración inicial.
2. Conecta los dos teléfonos a la corriente.
3. Desactiva la conexión automática a redes Wi-Fi en el Android.
4. Empieza la configuración del iPhone y empareja con Move to iOS.
5. Marca WhatsApp en la pantalla de datos.
6. **Mantén las dos pantallas encendidas todo el proceso.** Toca la del iPhone cada minuto. Cuando una pantalla se apaga, Android limita la Wi-Fi y suspende la conexión entre los dos teléfonos.
7. No canceles. La barra avanza muy despacio y el tiempo estimado no es fiable.

Señal de que va bien: el tiempo estimado del iPhone y el del Android coinciden. Si van desincronizados, el intento ya está perdido.

---

## Paso 10: activa WhatsApp en el iPhone

1. Termina la configuración del iPhone **antes** de abrir nada de WhatsApp.
2. Instala WhatsApp de la App Store.
3. Inicia sesión con el mismo número que usabas en Android. Debe coincidir carácter por carácter, código de país incluido.
4. Cuando lo pida, pulsa **Empezar** y espera a que termine la importación.

Si WhatsApp pide un código de verificación y lo envía al teléfono viejo, del que ya te cerró la sesión, vuelve a iniciar sesión en el Android para recibirlo. Los datos ya transferidos al iPhone no se pierden.

No borres WhatsApp del Android hasta que veas los chats completos en el iPhone.

---

## Si vuelve a fallar

**En el mismo porcentaje exacto.** La corrupción no era esta. Vuelve a `wa-diagnose.sh` y busca otros patrones en la base: ya está descifrada y puedes consultar lo que quieras.

**En un porcentaje distinto.** Buena señal. El exportador pasó de donde se atoraba y hay otro registro que limpiar. Repite el ciclo.

**Con el error de siempre pero en 0%.** Ese es otro fallo. Ahí sí prueba la beta de WhatsApp y Move to iOS 3.5.0.

### Consultas útiles para seguir cavando

```sql
-- Mensajes de un chat concreto, alrededor de un mensaje sospechoso
WITH bad AS (SELECT _id, chat_row_id, timestamp FROM message WHERE _id = <ID>)
SELECT m._id,
       datetime(m.timestamp/1000, 'unixepoch', 'localtime') AS fecha,
       m.from_me, m.message_type,
       substr(m.text_data, 1, 80) AS texto
FROM message m, bad
WHERE m.chat_row_id = bad.chat_row_id
  AND m.timestamp BETWEEN bad.timestamp - 86400000 AND bad.timestamp + 86400000
ORDER BY m.timestamp;

-- A qué conversación pertenece un mensaje
SELECT c._id, j.raw_string, c.subject
FROM chat c JOIN jid j ON j._id = c.jid_row_id
WHERE c._id = (SELECT chat_row_id FROM message WHERE _id = <ID>);

-- Inventario de multimedia por chat, útil para pedir archivos de vuelta
SELECT j.raw_string, COUNT(*) AS n, mm.mime_type
FROM message_media mm
JOIN message m ON m._id = mm.message_row_id
JOIN chat c ON c._id = m.chat_row_id
JOIN jid j ON j._id = c.jid_row_id
GROUP BY j.raw_string, mm.mime_type
ORDER BY n DESC;
```

---

## Respaldo después de migrar

Configura las dos capas el mismo día, no lo dejes pendiente:

- **iPhone:** Ajustes de WhatsApp > Chats > Copia de seguridad en iCloud.
- **Multimedia, en cualquier plataforma:** deja que Fotos de iCloud o Google Fotos guarde los archivos. Una copia del multimedia que no dependa de WhatsApp es lo único que sobrevive a un borrado accidental.

---

## Créditos

- La idea de que un porcentaje fijo indica un registro corrupto, y el método de descifrar la base para encontrarlo, viene de [esta guía de rkharsan](https://gist.github.com/rkharsan/2aa3c56b9aa1107439dd8881f680abe3). En su caso el registro era un mensaje de texto con `text_data` nulo.
- [`wa-crypt-tools`](https://github.com/ElDavoo/wa-crypt-tools) de ElDavoo hace posible todo esto.
- El hallazgo de que Move to iOS 3.5.0 resuelve otros fallos de transferencia viene de [r/ios](https://www.reddit.com/r/ios/comments/1cxu9zc/if_you_are_moving_from_android_to_ios_and_having/).
- Mantener las pantallas encendidas, de [r/iphone](https://www.reddit.com/r/iphone/comments/1uexn0h/the_move_to_ios_app_keeps_failing_on_large/).
- La restauración local de un `crypt15` está documentada en [el blog de Nicola Inchingolo](https://www.inginc.eu/2025/07/02/restore-whatsapp-local-backup-dbcrypt15-and-over-on-a-new-device/).

## Aviso

Esta guía modifica la base de datos de WhatsApp. WhatsApp no soporta nada de esto. Los scripts trabajan siempre sobre copias y verifican el resultado antes de devolver nada al teléfono, pero la responsabilidad es tuya. Respalda la carpeta multimedia antes de empezar, guarda la clave de 64 dígitos y no borres tu copia de Drive hasta que la comprobación de ida y vuelta del Paso 6 haya pasado.
