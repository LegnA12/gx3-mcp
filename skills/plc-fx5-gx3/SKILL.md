---
name: plc-fx5-gx3
description: Use when working with Mitsubishi FX5 / iQ-F / MELSEC PLCs or GX Works3 projects (.gx3) - reading, editing, reviewing or simulating ladder (escalera), ST, labels or FB; reading or writing PLC devices live over SLMP (MC protocol) or Modbus/TCP; diagnosing MODBUS RTU channels; or connecting Python software (machine vision, inspection, HMI, data logging) to an FX5 PLC over Ethernet.
---

# PLC FX5 y GX Works3 con el MCP `gx3`

## Panorama

El servidor MCP `gx3` lee y edita proyectos `.gx3` y habla con el PLC en vivo. Es un ZIP de bases SQLite; el MCP escribe renglones idénticos a los de GX Works3, pero **nunca compila ni descarga**: eso siempre lo hace la persona en GX Works3.

## Qué herramienta para qué

| Necesidad | Herramienta |
|---|---|
| Ver qué hay | `listar_programas` → `leer_ld` / `leer_st` / `leer_etiquetas` |
| Dónde se usa algo | `referencia_cruzada` |
| Agregar / borrar renglón | `agregar_renglon_ld` / `borrar_renglon_ld` |
| Cambiar ST | `editar_st` (el texto viejo debe aparecer 1 sola vez) |
| Revisar lógica | `revisar_ld` (dobles bobinas, SET sin RST, timers sin OUT...) |
| Probar sin PLC | `simular_ld` (pasos `{"X0":1}`, `{"segundos":3}`) |
| PLC en vivo (lectura) | `leer_plc`, `monitorear_ld`, `estado_plc`, `diagnosticar_modbus_rtu` |
| Escribir en el PLC | `escribir_plc` (solo si la persona lo pide) |

## Flujo para modificar un programa

1. La persona **guarda y cierra** el proyecto en GX Works3 (si está abierto, sus cambios pisan los tuyos).
2. `leer_ld` del programa; anota los números `R<n>`.
3. Edita con `ruta_salida` = el mismo archivo (se crea `.bak`) o una copia.
4. `revisar_ld` → debe decir "Sin problemas" o explica por qué el aviso es intencional.
5. `simular_ld` con la secuencia de la máquina; muestra a la persona la tabla de pasos.
6. La persona abre en GX Works3 → **Convertir todo** → descarga con la máquina en condición segura.
7. Verifica en vivo con `monitorear_ld` (el `.gx3` debe ser el mismo cargado en el PLC).

## Reglas que evitan errores

- **X/Y son octales**: X0-X7, X10-X17 (no existen X8, X9). D, M, T, C son decimales.
- `T0` como contacto = TS0 (terminó); como valor = TN0. Timers T cuentan en 100 ms: `OUT T0 K30` = 3 s.
- Renglones nuevos: solo **dispositivos directos** y **una salida** por renglón. Escritura de etiquetas, contadores, `D>=`, `DMOV`, `OUTH` aún no soportados: dilo, no inventes el formato.
- `escribir_plc` solo funciona dentro de `GX3_ESCRITURA_PERMITIDA` (variable de entorno del servidor). Nunca escribas en una bobina que el programa maneja: el siguiente scan la sobrescribe.
- Si un guardado responde "esta bloqueado", el proyecto sigue abierto en GX Works3: pide cerrarlo (no se cambio nada).
- Modbus: con el FX5 de **esclavo** (la PC pregunta) basta configurar el *MODBUS/TCP Connection Module*, sin instrucciones en el programa. Con el FX5 de **maestro** sí se programa: `ADPRW` en RTU, protocolo predefinido en TCP.
- Contactos: `X0` NA, `/X0` NC, `↑X0`/`↓X0` flancos, `>= D10 K5`, `E< D10 E1.5`. Salidas: `Y0`, `SET M0`, `RST M0`, `OUT T0 K30`, `MOV K0 D10`, `INCP D10`, `EMOV`, `ZRST`, `BMOV`.

## Conectar software Python (visión, HMI)

**REQUIRED:** lee `vision-plc.md` en esta carpeta antes de diseñar el intercambio o escribir el cliente. Tiene el mapa de registros, el ladder probado en simulador y el cliente Python con reconexión.

## Errores comunes

| Error | Corrección |
|---|---|
| Usar `X8` o leer X10 como decimal | Octal: X10 es la entrada 8 |
| Diagnosticar el monitor con otro `.gx3` | El archivo debe ser el que está en el PLC |
| Abrir una conexión TCP por lectura | Una conexión por ciclo de lecturas (`ClienteSLMP`) |
| Dar por bueno un cambio sin convertir | Pedir **Convertir todo** en GX Works3 |
