# Cómo arreglar el error de Move to iOS al transferir WhatsApp de Android a iPhone

Esta guía investiga un caso reportado: **Move to iOS falla siempre en el mismo porcentaje** al transferir WhatsApp, y muestra *"Ocurrió un error inesperado. Inténtalo más tarde"* (en inglés, *"An unknown error occurred. Please try again later"*).

En el caso que dio origen a esta guía, el problema eran referencias colgadas en la base de WhatsApp. Un porcentaje fijo por sí solo no demuestra esa causa: también hay que considerar la red, el almacenamiento, el software y otros problemas de la base.

Las herramientas revisan una copia descifrada, eliminan ciertos registros colgados cuando las comprobaciones lo permiten y preparan otro respaldo cifrado. No reparan corrupción física de SQLite ni garantizan que WhatsApp acepte una base modificada.

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

Un porcentaje fijo es una pista, no prueba de un registro concreto. Diagnostica una copia antes de decidir si corresponde limpiarla. Un porcentaje variable tampoco identifica la causa por sí solo.

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
- Python 3.10 o posterior y Git. Instala la revisión probada de [`wa-crypt-tools`](https://github.com/ElDavoo/wa-crypt-tools) con los comandos siguientes.
- `sqlite3`. Viene en macOS y en la mayoría de distribuciones.
- Depuración USB activada en el Android.
- Espacio para el respaldo original, la base descifrada, una copia temporal, el nuevo respaldo y el multimedia. Puede hacer falta varias veces el tamaño del respaldo cifrado. El cifrado también necesita memoria para la base y los datos comprimidos.
- Un sistema de archivos local que permita enlaces duros; así se publica la salida sin sobrescribir archivos existentes.

---

## Instala las herramientas

Clona el repositorio completo: los scripts necesitan los archivos Python que los acompañan.

```bash
git clone https://github.com/Yaeger42/whatsappDBCorruptionFixTool.git
cd whatsappDBCorruptionFixTool
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
umask 077
```

Mantén activo ese entorno durante los pasos siguientes. La dependencia está fijada a una revisión porque el recifrado usa su API de crypt15 para preservar el encabezado. El diagnóstico y la limpieza usan SQLite de la biblioteca estándar de Python.

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

```bash
bash wa-diagnose.sh decrypted.db
```

Abre el archivo existente en modo de solo lectura e imprime un informe JSON. El código de salida `0` significa que pasaron las comprobaciones implementadas; `1` indica hallazgos o un error. Lee el informe antes de continuar.

- `integrity` debe contener únicamente `ok`. La corrupción física se trata por separado.
- `bad_messages` cuenta mensajes sin chat o con tipo NULL, salvo el centinela documentado. El centinela se reconoce por su forma (`chat_row_id=-1`, `key_id='-1'`, tipo NULL), no por su rowid.
- `orphan_children` cuenta referencias positivas `message_row_id` y `parent_message_row_id` sin mensaje correspondiente.
- `dangling_chat_pointers` muestra punteros positivos del chat terminados en `_message_row_id` cuyo mensaje no existe.
- `foreign_key_errors` cuenta violaciones de claves foráneas declaradas. Muchas relaciones de WhatsApp son implícitas, por lo que esto no cubre todas sus relaciones.

El esquema admitido requiere `message`, `chat` y `message_media`, con claves primarias enteras en `message._id` y `chat._id`. Se rechazan bases que no tengan las tablas o columnas requeridas. Hay pruebas automatizadas con datos sintéticos; no se garantiza compatibilidad con todas las versiones de WhatsApp ni una migración real completada.

Rutas multimedia ausentes, MIME vacío o adjuntos sin descargar no justifican por sí solos borrar mensajes. Estas herramientas no eliminan archivos multimedia.

### Números que parecen catástrofe y no lo son

La contabilidad del multimedia genera cifras grandes y benignas. El informe no las marca, pero las vas a encontrar si consultas la base por tu cuenta:

| Señal | Por qué no es corrupción |
|---|---|
| Un mensaje con `message_type` NULL, `chat_row_id` igual a -1 y `key_id` igual a `-1` | Es la fila centinela que WhatsApp escribe en toda base. Las herramientas la conservan. |
| Filas de multimedia sin `file_path`, o con `transferred = 0` | Multimedia que nunca descargaste, o que borraste del disco. WhatsApp conserva la fila. |
| Filas con `file_size` en 0, o con `file_size` distinto de `file_length` | En los esquemas actuales `file_size` casi no se usa. El valor real vive en `file_length`. |
| Filas con `mime_type` vacío | Muchas imágenes enviadas guardan el `mime_type` vacío. |

En la base que dio origen a esta guía, esas cuatro señales sumaban más de 100.000 filas. Ninguna tenía parte en el fallo.

### Archivos rotos en el teléfono

Este caso las herramientas no lo ven, porque vive en el sistema de archivos del teléfono y no en la base. El multimedia de 0 bytes cuelga la transferencia:

```bash
adb shell 'find /sdcard/Android/media/com.whatsapp/WhatsApp/Media -type f -size 0'
```

Ignora los `.nomedia`: son marcadores vacíos por diseño. Borra cualquier otro archivo de 0 bytes.

---

## Paso 5: limpia la base de datos

Primero prueba la limpieza sobre una copia temporal:

```bash
bash wa-clean.sh decrypted.db --dry-run
```

Después genera un archivo nuevo:

```bash
bash wa-clean.sh decrypted.db work.db
bash wa-diagnose.sh work.db
```

Usa un nombre de salida nuevo en cada intento. Se rechazan archivos y enlaces simbólicos existentes. La API de respaldo de SQLite incluye cambios confirmados del WAL; no se modifica el original. La limpieza se hace en una transacción sobre una copia temporal privada, verifica el resultado y publica un archivo que solo su propietario puede leer. Un fallo no publica ninguna salida.

**Si un puntero del chat todavía referencia un mensaje seleccionado, la limpieza se detiene.** La herramienta no adivina un reemplazo, no borra el chat ni deja un puntero roto en silencio. Investiga esa relación antes de continuar.

### Hallazgos que la limpieza nunca repara

Dos hallazgos no tienen reparación segura: los punteros de chat colgados y las violaciones de claves foráneas declaradas. Si tu base ya los traía, la limpieza termina su trabajo y luego se detiene, porque una base con daño inexplicado no es una entrada segura. El error los nombra. Léelos y luego acéptalos de forma consciente:

```bash
bash wa-clean.sh decrypted.db work.db --allow-preexisting
```

La bandera acepta solo las cifras que la entrada ya tenía. Si esta ejecución aumenta alguna, la salida se rechaza igual. Los fallos de integridad, los mensajes malos que queden y las filas hijas huérfanas nunca se relajan. Pasa la misma bandera a `wa-repack.sh`, para que acepte la base que produjo la limpieza.

Estas comprobaciones cubren las relaciones descritas, no todas las reglas internas de WhatsApp. Conserva el respaldo cifrado original y la copia del multimedia.

---

## Paso 6: vuelve a cifrar y verifica

```bash
bash wa-repack.sh encrypted_backup.key work.db msgstore.ORIGINAL.crypt15 msgstore.NEW.crypt15
```

La herramienta autentica el respaldo crypt15 original, toma una copia consistente de SQLite y la cifra con un **IV aleatorio nuevo**. Conserva los metadatos del encabezado, incluidos campos protobuf desconocidos, y comprueba que solo cambió el IV. Nunca reutilices un IV de AES-GCM con la misma clave para contenidos distintos.

La verificación autentica y descomprime el resultado, y compara su tamaño y SHA-256 con los de la copia consistente. La API de respaldo puede normalizar contadores del encabezado SQLite: los bytes pueden cambiar aunque los datos sean iguales. No deja un `roundtrip.db` descifrado; elimina los temporales privados al terminar normalmente o ante un fallo controlado. Nunca sobrescribe una salida existente.

**Es una verificación local, no prueba de que WhatsApp pueda restaurar el respaldo.** Conserva las copias de recuperación hasta revisar chats y multimedia en el teléfono de destino. El tamaño cifrado puede variar por la limpieza y la compresión; por sí solo no demuestra que el archivo esté bien o mal.

---

## Paso 7: devuelve la base al teléfono

Move to iOS **no** lee el archivo `.crypt15`. Lee la base viva, en `/data/data/com.whatsapp/databases/msgstore.db`, que es inaccesible sin root. Por eso hay que hacer que WhatsApp restaure tu archivo.

### 7a. Conserva una vía de recuperación

Una comprobación local de cifrado y descifrado no es una prueba de restauración. Copiar los archivos y comparar sus tamaños demuestra que tienes copias. No demuestra que alguna copia restaure en un teléfono.

1. Desactiva las copias automáticas de WhatsApp, para que un intento fallido no reemplace una copia útil.
2. Conserva el archivo cifrado original y su clave fuera del teléfono. Comprueba que la copia multimedia terminó, revisa sus archivos y compara cantidades y tamaños con el teléfono. Guarda otra copia independiente si puedes.
3. Omite la búsqueda de respaldos de Google durante la configuración cuando aparezca esa opción. WhatsApp no puede leer una copia de Drive que no tiene permiso para buscar.

No ejecutes `pm clear` hasta verificar las copias fuera del dispositivo, y hasta aceptar que la restauración local puede fallar de todos modos.

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
1. Abre WhatsApp y verifica tu número. Usa el número que hizo la copia.
2. Cuando pida permiso para buscar copias en tu cuenta de Google, pulsa **Omitir**. Concederlo volvería a vincular Drive.
3. Si aparece la pantalla de transferir desde el teléfono anterior, recházala con la opción secundaria.
4. Cuando ofrezca la copia local, pulsa restaurar.
5. Introduce la clave de 64 dígitos.

No necesitas restaurar nada a mano. WhatsApp lee el archivo que colocaste.

### 7e. Si WhatsApp no ofrece ninguna copia

No completes nada en la pantalla de nombre y foto. Revisa primero estos cinco puntos, porque todos son gratis y reversibles:

1. Revisa el dueño del archivo del paso 7c. WhatsApp no puede leer un archivo cuyo dueño es `shell`.
2. Revisa que `Databases` tenga exactamente un archivo, tu `.crypt15`. Las copias con fecha y `msgstore-increment.db.crypt15` compiten con él.
3. Revisa que WhatsApp creara `Android/media/com.whatsapp` por sí mismo después del `pm clear`, antes de que empujaras el archivo. Una carpeta creada por `adb` puede quedar con permisos que WhatsApp no puede usar.
4. Revisa que el número que verificaste sea el número que hizo la copia.
5. Revisa que WhatsApp tenga el permiso de archivos y multimedia.

Si los cinco están bien y WhatsApp sigue sin ofrecer nada, hay una cosa más que se sabe que cambia el resultado, y es la única irreversible de esta lista. En el único caso que dio origen a esta guía, la restauración local apareció después de borrar la copia de Drive y desconectar WhatsApp de Drive. Ese es un solo reporte. No está establecido como la causa general.

> **PRECAUCIÓN:** Una copia de Drive no se puede descargar ni inspeccionar, y su borrado no se puede deshacer. Si la restauración local también falla, esa copia ya no vuelve. Decide esto con tus copias de la computadora verificadas delante.

- En la app de Google Drive: menú > **Copias de seguridad** > los tres puntos junto a WhatsApp > **Eliminar copia de seguridad**.
- Desde `drive.google.com`: engranaje > Configuración > **Administrar aplicaciones** > WhatsApp Messenger > Opciones > **Desconectar de Drive**.
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

**En el mismo porcentaje exacto.** La causa sigue sin resolverse. Vuelve al informe de diagnóstico e investiga otras causas antes de borrar más datos.

**En un porcentaje distinto.** El fallo cambió, pero eso no demuestra que haya otro registro que borrar. Investiga antes de repetir la limpieza.

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

Esta guía modifica la base de datos de WhatsApp. WhatsApp no soporta nada de esto. Los scripts trabajan siempre sobre copias y verifican el resultado antes de devolver nada al teléfono, pero la responsabilidad es tuya. Respalda la carpeta multimedia antes de empezar, guarda la clave de 64 dígitos y conserva la copia de Drive hasta revisar los chats y el multimedia del destino; la comprobación local por sí sola no demuestra que se pueda restaurar.
