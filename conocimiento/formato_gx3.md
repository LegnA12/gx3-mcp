# Formato .gx3 (GX Works3): etiquetas, FB y elementos ladder

Estas notas se obtuvieron en un entorno controlado, en modo solo lectura, sobre proyectos FX5U de prueba propios (no incluidos). Todo se extrajo a directorios temporales; ningún `.gx3` se modificó.

- **[V] VERIFICADO**: lo respaldan varios ejemplos o todos los proyectos.
- **[H] HIPÓTESIS**: es coherente con los datos, pero no está probado.

Script reutilizable: `etiquetas.py`, en esta misma carpeta. Su función `nombres_etiquetas(ruta)` devuelve
`{"_lid/L/R": {nombre, tipo_dato, clase, dispositivo_asignado, comentario, pou}}`. Su autoprueba recorre los 24
proyectos y comprueba que cada `_lid` del ladder y del MIL resuelve a una fila de LabelData.

---

## 0. Archivos del ZIP que vimos además de los ya conocidos

| Archivo | Contenido |
|---|---|
| `LabelData.db` | Todas las tablas de etiquetas: globales, locales de cada programa y FB, estructuras y FUN. Su texto está en `UTF-16be` (`pragma encoding`). Python lo decodifica bien. [V] |
| `<hash16>_LDDB.db` / `_STDB.db` / `_MilDB.db` | Cuerpo LD, cuerpo ST y MIL de un POU. MIL existe tanto para POU LD como ST. [V] |
| `<dec>_StepInfo.db` | Una por POU; `<dec>` es el **ID decimal del POU**. `T_Block(BlockID '{GUID}', BodyID, StepSize)` y `T_Step(ElementIDMacroFB, MilID, StepSize)`. `BodyID` es el `hash16` del cuerpo como int64 con signo, o 0. `BlockID` es el GUID del bloque (`_guid/<guid en minúsculas>` en LDDB/MilDB). [V] |
| `ConvertData/<dec>/` | Datos compilados por "Convertir". Por POU: `PouPCode.pcode`, y `TempAggrigaton.bin` solo en POU ST [V]. Por archivo de programa: `Program.qpg`, `PouLinkOrder.info` (IDs decimales de POU, uno por línea), `ProgramFilePCode.pcode` y otros. `Progress.txt` guarda el log de la última conversión, en texto. |
| `_Project.txc` | Cifrado (árbol del proyecto). Fuera del alcance de este proyecto. |
| `*.w3pa`, `*.PRM`, `!ProjectExInfo.dat` | Parámetros binarios (CPU, módulos, ethernet). No se analizaron. |
| `<hash>_DM.db` | `MEMORY_DATA(DevCode, DevNo, MemSize, MemData BLOB)`: memoria de dispositivos o valores iniciales [H]. |

## 1. Etiquetas: resolución de `_lid/<LabelID>/<RowID>`

### 1.1 Cadena de resolución [V]
```
LabelTbl(LabelID, LabelTypeID)                  -> a quién pertenece la tabla (ver 1.2)
RowTbl(LabelID, RowID, RowNo, VarType, BitCount, StateFlag, CheckerState)  -> una fila = una etiqueta
ColumnDataTbl(LabelID, RowID, ColumnID, ColumnStrValue, ColumnIntValue)  -> los campos (ver 1.3)
```
- `LabelID` es un entero decimal de 64 bits guardado como TEXT. `RowID` es estable: no cambia al reordenar.
  `RowNo` es el orden visible en el editor y puede diferir de `RowID`. [V]
- Las filas con `RowID` negativo solo aparecen en FUN: `-1` es el valor de retorno (su nombre es el de la función),
  `-2` es `EN` y `-3` es `ENO`. [V]

### 1.2 `LabelTypeID` [V salvo lo indicado]
| ID | Recurso | Significado | Ejemplo |
|---|---|---|---|
| 5121 | 1000 | Etiquetas locales de un **programa** | `Valor_Float`, `PasoA` |
| 5122 | 1001 | Etiquetas **globales**. Puede haber varias tablas (una por "Global Label" del árbol) | `SensorA`, `Linea1` |
| 5123 | 1002 | **Estructura** (sus filas son miembros, sin clase) | `Tiempo{Dias,Horas,Minutos,Segundos}` |
| 5124 | 1003 | No apareció en ningún proyecto [H: otra categoría de POU] | - |
| 5125 | 1004 | **FUN**. En estos proyectos solo hay FUN estándar usadas desde ST | `INT_TO_REAL`, `ADD_TIME`, `ABS` |
| 5126 | 1005 | Etiquetas de un **FB**, de usuario o de biblioteca | `TemporizadorX`, `TON(IN,PT,Q,ET)` |

Todo proyecto trae 6 estructuras 5123 de sistema con miembros `S`, `C` y `N`: `S` y `C` son BOOL, y `N` es WORD en
3 de ellas y DWORD en las otras 3. [H: TIMER, COUNTER y RETENTIVETIMER, y sus versiones LONG.]

### 1.3 `ColumnID` (`ColumnDataTbl`)
| ColumnID | Campo | Detalle |
|---|---|---|
| 1 | **Clase** | Texto, más un código en `ColumnIntValue` (1.4). No existe en miembros de estructura. [V] |
| 2 | **Nombre** | Texto. [V] |
| 3 | **Tipo de dato** | Si `ColumnIntValue=99`, es un tipo de usuario (estructura o FB) y el nombre va en `ColumnStrValue`. Si no, es un código (1.5). [V] |
| 4 | Sin datos. `ColumnDefineMst.ItemType=7` (casilla) [H: "Constante"]. | |
| 5 | **Valor inicial o valor de la constante**. Ej.: `Constante VAR_CONSTANT REAL = '100.00'`. [V, 1 caso con valor] | |
| 6 | Siempre vacío (solo en miembros de estructuras de sistema). [H] | |
| 7 | **Dispositivo asignado manualmente** (`D700`, `M600`). Coincide con `DeviceAssignTbl.ManualSet=1`. [V] | |
| 8 | Vacío siempre que existe el 7 [H: dirección IEC (`%MW..`)]. | |
| 9..28 | **Comentario** (20 columnas multilínea de 1024 caracteres). Solo traen texto los FB de biblioteca, en la 9 o la 15 según el proyecto (`'Input'`, `'Delay Time Setting Value'`). [H: una columna por idioma o destino de visualización]. Ninguna etiqueta de usuario tiene comentario en estos proyectos. | |
| 29..37 | Definidas en `ColumnDefineMst` y sin datos. | |

`ColumnMappingMst(LabelTypeID, ColumnID, Hidden, Width…)` solo define qué columnas muestra el editor para cada tipo de
tabla. No contiene datos.

### 1.4 Clase (ColumnID 1, `ColumnIntValue`) [V salvo lo indicado]
`1 VAR_GLOBAL` · `3 VAR_GLOBAL_RETAIN` · `4 VAR` · `5 VAR_CONSTANT` · `6 VAR_RETAIN` · `7 VAR_INPUT` ·
`9 VAR_OUTPUT` · `10 VAR_OUTPUT_RETAIN`. Los códigos 2 y 8 no aparecieron [H: `VAR_GLOBAL_CONSTANT` y `VAR_IN_OUT`].
En FUN, el texto viene vacío y solo hay código: `-1` (retorno) y `ENO` llevan 9, y `EN` lleva 7.

### 1.5 Tipo de dato (ColumnID 3, `ColumnIntValue`)
`1 BOOL` · `2 WORD` · `3 DWORD` · `4 INT` · `5 DINT` · `6 REAL` · `8 TIME` · `99 usuario` · `210 ANY_NUM`
(en `ABS`). [V: deducidos de las FUN estándar `INT_TO_DINT(IN:4)->5`, `WORD_TO_DWORD(IN:2)->3`,
`INT_TO_REAL->6`, `ADD_TIME->8`.] Los códigos `11` (`_FBEND`) y `12` (`T_INS`) solo aparecen dentro de FB de biblioteca [H].
Los códigos 7, 9 y 10 no aparecieron [H: LREAL, STRING y WSTRING].

### 1.6 `RowTbl` [V salvo lo indicado]
- `VarType`: `1` simple, `2` estructura, `3` instancia de FB.
- `BitCount` es, en realidad, el **tamaño en palabras**: BOOL=1, INT=1, REAL=2, `INT[8]`=8, `REAL[16]`=32,
  `BOOL[8]`=1 (bits empaquetados), `Tiempo` (4 WORD)=4, constante=0. En instancias de FB vale `hi<<32 | lo`
  (`103079215184 = 24<<32 | 80`) [H: bits y palabras de la instancia].
- `StateFlag`: 1 en filas de usuario y 0 en estructuras y FB de sistema o biblioteca [H]. `CheckerState` siempre vale 0.

### 1.7 Arrays [V]
`ArrayInfoTbl(LabelID, RowID, ArrayNo, ArrayCount, StartNo)` tiene una fila por dimensión. La ColumnID 3 guarda el tipo base.
Ej.: `16552222161467189903/3 TablaA`, tipo 4 con `(1, 8, 0)` → `ARRAY[0..7] OF INT`.

### 1.8 Tipos de usuario [V]
`RowRelationTbl(LabelID, RowID, RelatedLabelID, …)` liga cada fila con tipo 99 a la tabla que define su tipo (5123 o 5126).
Así se obtiene **el nombre de una estructura o FB**, que no está guardado en su propia tabla: se toma la ColumnID 3 de
cualquier fila que lo use. Ej.: `6762031742376906287/8 Area2 : TipoArea` → `RelatedLabelID=13217166433150932760`.

### 1.9 Dispositivo asignado [V]
- Asignación manual: ColumnID 7, igual a `DeviceAssignTbl.MELSECDevice` con `ManualSet=1`.
- Asignación automática al convertir (`ManualSet=0`): `GV:8C01` (área de globales), `LV:5DC.0` (locales),
  `LLV:140.0` (locales retentivas), `LLP<n>` [H: punteros locales]. Los registros `BaseRegisterNo`
  227, 234 y 235 corresponden a GV, LV y LLV.
- Estructuras y FB: hay una fila por **miembro instanciado**. La clave es
  `(LabelID, RowID) = etiqueta raíz` y `(RelatedLabelID, RelatedRowID) = miembro en la tabla del tipo`. La jerarquía
  está en `InstanceLayerTbl(InstanceID, RootLabelID, RootRowID, LayerLevel, ParentInstanceID, LabelID, RowID)`.
  Ej.: la global `SensorA` está asignada miembro a miembro (`M610`, `D610`, `M614`…).

## 2. POU: programa, FB y FUN

### 2.1 El hash del cuerpo y el LabelID no coinciden [V]
Ningún `hash16` de un LDDB o STDB coincide con un `LabelID`, ni en hexadecimal ni en decimal. Tampoco coincide el ID decimal del POU.
Ej.: el FB TemporizadorX tiene etiquetas `5765284748379193221` y cuerpo `516379e132da4bd9` (= 5864665147894287321).
**La relación se deduce de las referencias.** La tabla 5121 o 5126 que un cuerpo (LDDB, o MilDB si es ST) usa como etiqueta
**base** es la suya. Se ignoran las apariciones como miembro (`pl{id}`, `m=l{id}`) y como pin (`cbag{l=l{id}}`, `vag{l=l{id}}`).
Con esta regla cada tabla 5121 tiene exactamente un dueño en todos los proyectos. [V]
Si un POU ST no se convirtió después de editarlo, su MilDB no trae `_lid`. En ese caso `etiquetas.py` asigna la tabla
que no tiene dueño al ST cuyo texto contiene más nombres de esa tabla (con un umbral del 50 % y un ganador único). Si después
quedan un solo nombre de programa y un solo cuerpo sin nombre, los empareja por eliminación. [H, aunque en
`Prueba_Control_editado_MCP` da los mismos pares tabla↔hash que el MIL del proyecto de referencia.]

### 2.2 Programa, FB o FUN [V]
| Cómo se reconoce | Programa | FB | FUN |
|---|---|---|---|
| Tabla propia | 5121 | 5126 | 5125 |
| Llamado desde ladder | no | bloque `blocktype=4 fbcs` (§3.9) | - |
| Llamado en MIL | no | `@FE/FB_START NAME=inst … FBCALL inst … @FE/FB_END` | `@FE/FUN_START … FUNCALL _pouid/<dec> … @FE/FUN_END` |
| En `ConvertData` | aparece en un `Program.qpg` normal | aparece en un `Program.qpg` cuya cabecera (offset 100) contiene `#FT_FB_#` | no tiene archivo propio |
| Cuerpo con END | sí (`blocktype 5 end{}`) | no: su LDDB termina en `blocktype 5 cb{fg=fg{dim=-1x1}}` | - |

### 2.3 Nombres de los POU
- **FB y estructuras**: su nombre se obtiene de la ColumnID 3 de las instancias (§1.8) y del `dn` del bloque `fbcs`. [V]
- **FUN**: el nombre está en la fila `-1` (ColumnID 2). [V]
- **Programas**: el nombre real está en `_Project.txc`, que está cifrado. Hay un camino alternativo que **solo funciona si el proyecto fue convertido**:
  1. En `ConvertData/<archivo>/`, la línea *i* de `PouLinkOrder.info` es el ID decimal del POU *i*.
  2. En `Program.qpg` (desde el offset 100), la *i*-ésima cadena UTF‑16LE con prefijo `u16` de longitud
     (en bytes, incluido el nulo) que es un identificador es el nombre del POU *i*. Siempre aparecen `N+1` cadenas para
     `N` IDs; se toman las `N` primeras.
  3. Para ir del ID decimal al hash se usa `<dec>_StepInfo.db`: `BodyID` si no es 0, o `T_Block.BlockID` comparado con los ids `_guid/…`
     de LDDB/MilDB.
  Comprobado en los 24 proyectos sin un solo conflicto. Ej.: el 14.º POU `10_Programa`
  ↔ `3496f9fe47600579`, cuyo texto ST empieza con `(* ===== 10_Programa …`. `000_Entradas` lee X y `100_Salidas` escribe Y. [V]
  Límite: en POU ST editados después de convertir, el BlockID ya no coincide y `BodyID` vale 0, así que quedan sin nombre (§5).

### 2.4 FB: entradas, salidas y tipo (macro o subrutina)
- Entradas y salidas: filas de la tabla 5126 con clase 7, 9 o 10. Ej.: `ArranqueMotorFB` tiene
  `VAR_INPUT Paro, FallaTermico, Arranque, Estado, NivelBajo` y `VAR_OUTPUT Motor, FallaArranque,
  FallaMarcha, SigPaso`. Usa internamente `VAR TempFalla1 : TON`. [V]
- FB de usuario vistos: `ArranqueMotorFB` (LD), `TemporizadorX` y `RestaFB` (ST), `control_de_motor`.
  FB de biblioteca vistos: `TON`, `RS` (tienen tabla 5126 pero no cuerpo en el ZIP). [V]
- **Macro o subrutina**: todos los FB observados se comportan como **subrutina**. En el MIL del llamador aparece
  `FBCALL`, el FB tiene su propio `PouPCode` dentro de un archivo FB (`#FT_FB_#`) y `T_Step.ElementIDMacroFB=0` en los
  58 126 pasos. [H: un FB macro marcaría `ElementIDMacroFB≠0` y se expandiría en línea. No hubo ningún ejemplo.]

### 2.5 FUN [V]
FUN estándar llamadas desde ST: tienen tabla 5125 con `IN`/`IN1`/`IN2`, retorno `-1` y `EN`/`ENO`. En MIL los argumentos
se pasan con `MOV` a `_lid/<FUN>/1`, después viene `FUNCALL _pouid/<dec>`, y el resultado se lee de `_lid/<FUN>/-1`.

## 3. Elementos ladder (`LadderBlocks.data`)

### 3.1 `blocktype` [V]
`0` renglón `cb{fg=…}` · `1` comentario de línea `st{st=c{t=#:st=i|s}:dim=0x1}` · `4` **llamada a FB** `fbcs{…}` ·
`5` fin: `end{type=end:dim=1x1}` en programas y `cb{fg=fg{dim=-1x1}}` en FB.
En `st=i` / `st=s`, ambos se compilan en `Program.qpg` [H: tipo de statement].

### 3.2 Celdas, líneas y tipos de argumento [V]
- `e{s=<ce>:pos=c,r}` es un elemento en la columna c y la fila r. `e{s=-:pos=c,r}` es un **tramo horizontal de línea**.
- `as{vt=…}` indica el tipo de cada argumento de la instrucción: `Abl` bit, `A16` 16 bits, `A16s` 16 bits con signo,
  `A32` 32 bits, `Ar32` REAL, `Aea` cualquier elemento o array, `Aba` array de bits, `fb`/`fun` en MIL.
- `d{s=#:a=N:vt=nn}` es un dispositivo. En MIL, el contacto se escribe `lct{op=A|B:lt=l|a|o}` (mayúsculas).

### 3.3 Constantes `c{…}` [V]
| `s` | Significado | Ejemplo |
|---|---|---|
| `K_1` | Decimal de 16 bits. `v` es el valor, `t` el texto tecleado (opcional), `si=s` | `c{s=K_1:v=10:t=10}` → `K10` |
| `K_2` | Decimal de 32 bits; también TIME | `c{s=K_2:v=3000:t=T#3s}` → `T#3s` (v en ms) |
| `H_1` | Hexadecimal de 16 bits, `si=u`. `v` está en **decimal** | `v=12290` → `H3002` |
| `E_2n` | REAL. `v` son **los bits IEEE‑754 en hexadecimal** | `v=41880000:t=17.0` → `E17.0`; `3F800000` = 1.0 |
| `Dots` | Número de bit de `D.b` (solo dentro de `M{}`) | `v=0` |

### 3.4 `I{b=…:m=…}`: acceso a miembro [V]
`b` es la base: una etiqueta `l{}` u otro `I{}` anidado. `m` es el miembro:
- `m=pl{id=_lid/<tabla 5123>/<fila>}`: **miembro de estructura** (23 756 casos, todos de tablas 5123).
- `m=l{id=_lid/<tabla 5126>/<fila>}`: **pin o variable de una instancia de FB** (3 699 casos, todos de tablas 5126).

Ejemplo (programa `000_Entradas`):
```
S: ['b','D','Dots','c','_lid/6762031742376906287/8','_lid/13217166433150932760/13','_lid/6296409023833962909/9']
T: cb{fg=fg{dim=2x1:es=[e{s=ce{op=ct{op=#:ct=a:..}:args=[M{b=d{s=#:a=5016:vt=nn}:m=c{s=#:v=0}}]}:pos=0,0}
   :e{s=ce{op=cl{op=#:ct=a:..}:args=[I{b=I{b=l{id=#}:m=pl{id=#}}:m=pl{id=#}}]}:pos=1,0}]}}
=> LDI D5016.0
   OUT Area2.Sensores.EstadoA     (global : TipoArea → miembro : TipoSensores → EstadoA)
```
Miembro de FB fuera de una llamada: `I{b=l{id=_lid/14796676062093164489/3}:m=l{id=_lid/5765284748379193221/5}}`
→ `AND TemporizadorX_1.Ocupado`.

### 3.5 `A{a=…:i=[…]}`: elemento de array [V]
`a` es la base (`l{}` o `I{}`). `i` es la lista de índices, uno por dimensión: constantes `c{}` o etiquetas `l{}`.
```
S: ['a','_lid/9269856180786072116/3','K_1','1','BMOV','_lid/9269856180786072116/2','_lid/16552222161467189903/6',
    '_lid/9269856180786072116/4', …]
T: …args=[A{a=l{id=#}:i=[c{s=#:v=1:t=#:si=s}]}]… :args=[l{id=#}:A{a=l{id=#}:i=[l{id=#}]}:…]
=> LD BloqueEstado[1]
   BMOV Bufer DatoA[IndiceB] TablaB[IndiceA]
```
Array dentro de una estructura: `A{a=I{b=l{SensorB}:m=pl{Boton}}:i=[c{v=1}]}` → `SensorB.Boton[1]`.

### 3.6 `M{b=d{…}:m=c{s=Dots:v=n}}`: bit de una palabra [V]
`M{b=d{s=D:a=5016}:m=c{s=Dots:v=3}}` → `D5016.3`. En los proyectos aparecen 88 casos, todos D5016.0–7 en
`000_Entradas`, dentro de contactos `b` (LDI). [H: para bits 10–15, `v` sería decimal y GX mostraría `.A`–`.F`.]

### 3.7 Flancos: `ct=p` / `ct=f` [V]
En un contacto `ct{op=a:ct=p}`, `ct=p` es pulso ascendente y `ct=f` pulso descendente. Según la posición se traduce como
`LDP`/`ANDP`/`ORP` o `LDF`/`ANDF`/`ORF`. El MIL lo confirma: `lct{op=A:lt=l:ct=p}` (LDP), `lt=o:ct=f` (ORF).
```
S: ['a','_lid/6762031742376906287/12','MOV','K_1','10','_lid/6762031742376906287/10','_lid/11288658208846375304/1']
T: …ct{op=#:ct=p:..}:args=[l{id=#}]…cl{op=#:ct=a:as=[A16,A16]}:args=[c{s=#:v=10:t=#:si=s}:I{b=l{id=#}:m=pl{id=#}}]…
=> LDP AvisoFalla
   MOV K10 AlarmasX.AlarmaA
S: ['a','_lid/3724218109329403988/3','SET','M']   T: ct{op=#:ct=f}… d{s=#:a=704}
=> LDF HabilitarZona
   SET M704
```
En bobinas, `cl{op=INCP:ct=p}` indica la variante de ejecución por pulso. El nombre de `op` ya trae la "P", así que
`ct=p` es redundante (46 de 46 casos).

### 3.8 Contactos de comparación [V]
Es un `ct` cuyo `op` es `=`, `<>`, `>`, `<`, `>=` o `<=`, con `ct=a`, dos argumentos y un ancho de 3 celdas. El tipo
lo da `as`: `A16s` es una comparación de 16 bits (`LD=`, `AND>`, …) y `Ar32` una comparación REAL (`LDE<`, `ANDE>`)
[H: `A32s` daría `LDD=`, no hubo casos].
```
S: ['>=','_lid/16552222161467189903/2','_lid/16552222161467189903/1','MOV','K_1','_lid/16552222161467189903/2']
T: cb{fg=fg{dim=6x1:es=[e{s=ce{op=ct{op=#:ct=a:as=[as{vt=A16s}:as{vt=A16s}]}:args=[l{id=#}:l{id=#}]}:pos=0,0}
   :e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}]}:args=[c{s=#:v=0:si=s}:l{id=#}]}:pos=3,0}]}}
=> LD>= IndiceA NumEsclavos
   MOV K0 IndiceA
S: ['a','T','<','_lid/4566376985116216867/6','E_2n','17.0','SET',…]  (as=[Ar32,Ar32], c{v=41880000})
=> LD T208
   ANDE< ValorB E17.0
   SET Paso[6] …
```
Frecuencias: `=` (c, A) 140 casos, `>` 92, `<` 35, `>=` 23.

### 3.9 Llamada a FB: `fbcs` (blocktype 4) [V]
```
fbcs{fb=fb{dn=<nombre del FB>:in=<_lid de la instancia en el llamador>:bd=1}:dim=WxH
     :ig=[ pines de entrada, en orden de declaración ]:og=[ pines de salida ]}
cbag{l=l{id=_lid/<tabla FB>/<pin>}:fg=<mini-ladder>}   pin BOOL conectado a un circuito
vag{l=l{id=_lid/<tabla FB>/<pin>}:v=<operando>}        pin de palabra o valor (sin v = pin libre)
```
- `dn` es el nombre del FB, igual a la ColumnID 3 de la instancia. `in` es la instancia, una fila 5121 o 5126 con `VarType=3`,
  y `RowRelationTbl` la liga a la tabla 5126. `l` de cada pin apunta **directamente a la tabla del FB**. Por eso
  instancia, definición y pines quedan enlazados sin pasar por el hash del cuerpo. `bd` vale siempre `1` (475 de 475) [H].
- Un `cbag` de entrada es un renglón que termina en la bobina `OUT <inst>.<pin>` (`I{b=inst:m=l{pin}}`).
  Un `cbag` de salida empieza con el contacto `<inst>.<pin>` y puede seguir con bobinas. Si solo tiene el contacto, la salida está libre.
- El MIL equivalente es `@FE/FB_START NAME=inst`, luego `OUT inst.IN`, luego `LD SM400`, `MOV T#3s inst.PT`, `FBCALL inst` y `@FE/FB_END`.

Ejemplo completo (cuerpo del FB `ArranqueMotorFB`, `c3671022a86b94a4`):
```
S: ['TON','_lid/8146367130163462779/14','1','_lid/15248735797103159039/1','c','_lid/8146367130163462779/14',
    '_lid/15248735797103159039/1','a','_lid/8146367130163462779/15','_lid/15248735797103159039/2','K_2','T#3s',
    '_lid/15248735797103159039/3','a','_lid/8146367130163462779/14','_lid/15248735797103159039/3',
    '_lid/15248735797103159039/4']
T: fbcs{fb=fb{dn=#:in=#:bd=#}:dim=13x3
   :ig=[cbag{l=l{id=#}:fg=fg{dim=6x1:es=[e{s=ce{op=cl{op=#:..}:args=[I{b=l{id=#}:m=l{id=#}}]}:pos=1,0}
                                         :e{s=ce{op=ct{op=#:..}:args=[l{id=#}]}:pos=0,0}]}}
        :vag{l=l{id=#}:v=c{s=#:v=3000:t=#:si=s}}]
   :og=[cbag{l=l{id=#}:fg=fg{dim=6x1:es=[e{s=ce{op=ct{op=#:..}:args=[I{b=l{id=#}:m=l{id=#}}]}:pos=0,0}]}}
        :vag{l=l{id=#}}]}
=> TempFalla2(IN := Aux2, PT := T#3s, Q => (libre), ET => (libre))   ; instancia de TON
```
Llamada a un FB de usuario desde un programa (`10_Programa`, 13x10):
`TemporizadorX_3(Habilitar := HabilitarZona AND (PasoB OR PasoD), Dias := Intervalo.Dias, …,
Reset := TemporizadorX_2.Hecho OR TemporizadorX_1.Hecho, Reloj := SM412,
Segs => Linea1.Zona.Espera.Segundos, …)`. Los pines de palabra son `vag` con `v=I{…}`.

### 3.10 `IstBlocks`, `McMcrTable` y otras tablas del LDDB
- `IstBlocks(id, pos, data)`: solo hubo 70 filas, con `data` vacío y GUIDs repetidos entre POU copiados. Ningún
  `LadderBlocks` las referencia. [H: almacén del ST en línea (inline ST) del ladder. No hay ejemplos con contenido.]
- `McMcrTable`, `FoldBlockTable`, `DeletedBlockTable` y `NopTemporaryTable` estaban vacías en los 24 proyectos.
  [H: anidamiento MC/MCR, renglones plegados, papelera de deshacer y NOP temporales.]

## 4. Resumen tabla/campo → significado

| Tabla.campo | Significado | Estado |
|---|---|---|
| `LabelTbl.LabelTypeID` | 5121 programa, 5122 global, 5123 estructura, 5125 FUN, 5126 FB | V |
| `RowTbl.RowID` / `RowNo` | Id estable (el que usa `_lid`) / orden visible | V |
| `RowTbl.VarType` | 1 simple, 2 estructura, 3 instancia FB | V |
| `RowTbl.BitCount` | Tamaño en palabras (en FB: hi=bits?, lo=palabras) | V / H(FB) |
| `ColumnDataTbl` col 1/2/3 | Clase / nombre / tipo (99 = nombre de tipo de usuario) | V |
| `ColumnDataTbl` col 5 / 7 | Valor inicial o constante / dispositivo manual | V |
| `ColumnDataTbl` col 9..28 | Comentarios (multi-idioma) | H |
| `ArrayInfoTbl` | Dimensiones: `ArrayCount`, `StartNo` | V |
| `RowRelationTbl` | Fila con tipo de usuario → tabla que lo define | V |
| `InstanceLayerTbl` | Árbol de miembros instanciados (raíz → miembro, nivel) | V |
| `DeviceAssignTbl` | Dispositivo por miembro instanciado; `ManualSet` 1=manual, 0=automático (GV/LV/LLV/LLP) | V |
| `StructureLayerTbl`, `ColumnPropertyTbl`, `CommentDataTbl`, `*Additional*` | Vacías en todos | - |
| `StepInfo.T_Block.BodyID` | hash16 del cuerpo como int64 con signo (0 si no está enlazado) | V |
| `StepInfo.T_Block.BlockID` | `{GUID}` = `_guid/guid` de LadderBlocks/MIL | V |
| `StepInfo.T_Step.ElementIDMacroFB` | Siempre 0 | H (macro FB) |
| `ConvertData/<f>/PouLinkOrder.info` + `Program.qpg` | ID de POU ↔ nombre de POU (mismo orden) | V |
| `LadderBlocks.blocktype` | 0 renglón, 1 comentario, 4 llamada FB, 5 END | V |
| Nodos `I` / `pl` / `A` / `M` | Miembro / miembro de estructura / array / bit de palabra | V |
| `ct=p` / `ct=f` | Flanco ascendente / descendente | V |
| `fbcs` / `cbag` / `vag` / `fb{dn,in,bd}` | Llamada FB / pin bit con circuito / pin valor / def+instancia | V (`bd` H) |

## 5. Límites y pendientes
- Los nombres de programa dependen de `ConvertData`. Si el proyecto no se convirtió después de editar, en algunos POU ST
  no se pudo enlazar el `StepInfo` (9 de 17 POU en `<proyecto>`). Para esos, `etiquetas.py`
  devuelve `PROGRAMA ? [hash]` o solo el hash. La fuente definitiva sería `_Project.txc`, que está cifrado y queda fuera del alcance.
- No vimos FUN de usuario, FB macro, `VAR_IN_OUT`, STRING, LREAL, ni comentarios en etiquetas de usuario.
- `etiquetas.py` no resuelve el dispositivo de un **miembro** (`Global.Miembro`). Para eso hay que consultar
  `DeviceAssignTbl` con raíz y miembro (§1.9).

---


## 6. Inventario de un proyecto real

Omitido en la versión pública (datos privados).
