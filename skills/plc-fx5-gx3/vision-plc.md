# Conectar software Python (visión) a un PLC FX5

## Protocolo

| | SLMP (recomendado) | Modbus/TCP |
|---|---|---|
| Cliente | `plc_slmp.py` del repo gx3-mcp: un archivo, solo biblioteca estándar, probado con FX5U real | `plc_modbus.py` (solo lectura) o `pymodbus` |
| Direcciones | Nombres del PLC: `D100`, `M0`, `X10` | Tabla Modbus del PLC (D = holding register 0..) |
| Usar cuando | El otro lado es tu programa Python | Un equipo de terceros solo habla Modbus |

`plc_slmp.py` está en la carpeta `gx3mcp/` del repositorio gx3-mcp (en esta PC: la ruta que muestra `claude mcp get gx3`). **Cópialo** al proyecto de visión; no agregues dependencias.

## Configuración del PLC (GX Works3)

*Parámetro → FX5UCPU → Parámetro de módulo → Puerto Ethernet*: Communication Data Code = **Binary**; en *External Device Configuration* agrega un **SLMP Connection Module** TCP **exclusivo para la visión** (ej. puerto 5001). Cada módulo SLMP atiende **un cliente**: si la visión y el MCP comparten puerto, uno queda rechazado. Escribir parámetros y **reiniciar** el PLC. Máximo 8 conexiones Ethernet en el FX5.

## Intercambio por número de solicitud (sin bits de handshake)

| Registro | Escribe | Contenido |
|---|---|---|
| D100 | PLC | N.º de solicitud: +1 cada pieza en posición |
| D101 | Visión | Resultado: 1 OK, 2 NOK, 3 error de visión |
| D102, D103 | Visión | Desplazamiento X, Y en décimas de mm (con signo, ±3276.7 mm) |
| D104 | Visión | N.º de solicitud que responde (D101-D103 valen si D104 = D100) |
| D105 | Visión | Latido: +1 en cada vuelta del bucle |

Por qué así: la visión escribe D101-D104 en **una sola petición SLMP**, así que el PLC nunca ve un resultado a medias. Una respuesta vieja nunca coincide con una solicitud nueva. Si la visión se reinicia, ve D104 ≠ D100 y atiende la pieza pendiente; no hay bits que se queden pegados ni que resetear.

## Ladder (probado con `simular_ld` y `revisar_ld`)

`agregar_renglon_ld(columnas, salida)`:

| Columnas | Salida | Qué hace |
|---|---|---|
| `[["X10"]]` | `INCP D100` | Pieza en posición: nueva solicitud |
| `[["= D104 D100"], ["= D101 K1"]]` | `Y5` | Respuesta vigente y OK |
| `[["= D104 D100"], ["= D101 K2"]]` | `Y6` | Respuesta vigente y NOK |
| `[["X10"], ["<> D104 D100"]]` | `OUT T10 K10` | 1 s sin respuesta |
| `[["T10"]]` | `SET M50` | Falla de visión |
| `[["X11"]]` | `RST M50` | Reset de falla |
| `[["= D104 D100"], ["= D101 K3"]]` | `SET M50` | La visión reporta error propio |
| `[["= D105 D106"]]` | `OUT T11 K20` | 2 s sin cambio de latido |
| `[["<> D105 D106"]]` | `MOV D105 D106` | Copia del latido (**después** del timer) |
| `[["T11"]]` | `SET M50` | Falla: visión caída o colgada |

Ajusta T10 por encima de tu peor tiempo de inspección. D106 es del PLC (copia del latido).

Prueba antes de cargar: `simular_ld(..., pasos=[{"X10":1}, {"D101":1,"D102":15,"D103":-3,"D104":1}, {"X10":0}, {"X10":1}, {"segundos":1.2}], mostrar=["D100","D104","Y5","Y6","T10","M50"])`. Se espera: Y5=1 tras la respuesta, Y5=0 con la nueva solicitud, y M50=1 tras 1 s sin respuesta. Latido: sin cambios en D105 durante 2 s → M50=1, y `RST M50` no lo limpia mientras siga sin latido.

## Cliente Python

```python
import time
from plc_slmp import ClienteSLMP  # copiado de gx3mcp/plc_slmp.py

IP, PUERTO = "192.168.3.250", 5001

def inspeccionar() -> tuple[int, float, float]:
    """Tu visión: (1 OK / 2 NOK / 3 error, dx_mm, dy_mm)."""
    raise NotImplementedError

def a_decimas(mm: float) -> int:
    return max(-32768, min(32767, round(mm * 10)))

def bucle():
    latido = 0
    while True:
        try:
            with ClienteSLMP(IP, PUERTO, timeout=1.0) as plc:
                while True:
                    solicitud, _, _, _, respondida = plc.leer(["D100:5"])[0]
                    if solicitud != respondida:
                        resultado, dx, dy = inspeccionar()
                        plc.escribir("D101", [resultado, a_decimas(dx), a_decimas(dy), solicitud])  # una petición
                    latido = (latido + 1) % 32768
                    plc.escribir("D105", [latido])
                    time.sleep(0.005)
        except (OSError, ConnectionError) as e:  # PLC apagado, cable, timeout: reintentar sin tumbar la visión
            print("PLC sin conexión:", e)
            time.sleep(1)
```

Medido con un FX5U real: unos 2.5 ms por petición con conexión persistente; deja holgura de sobra para 20 inspecciones/s.

## Puesta en marcha

1. Ladder en una copia del `.gx3` → `revisar_ld` → `simular_ld` → GX Works3 *Convertir todo* → descargar.
2. Sin la visión: `escribir_plc` en D101-D104 imita respuestas. Necesita `GX3_ESCRITURA_PERMITIDA="D101-D105"`.
3. Con la visión corriendo: `monitorear_ld` o `leer_plc(["D100:6","Y5:2","M50"])` para ver solicitud, respuesta y latido.

## Errores comunes

| Error | Corrección |
|---|---|
| Bit "listo" + datos en peticiones separadas | Datos y n.º de solicitud en **una** escritura |
| Conexión nueva por cada imagen | `ClienteSLMP` persistente; reconectar solo en error |
| Mismo puerto SLMP para visión y diagnóstico | Un SLMP Connection Module por cliente |
| Escribir en Y o en bobinas del programa | Solo registros de intercambio (D101-D105) |
| Desplazamiento en mm con decimales | Entero en décimas de mm, limitado a ±32767 |
| `ConnectionError ... error 7167H` al escribir | El PLC tiene prohibida la escritura en RUN para esa conexión: habilitarla en la configuración del puerto Ethernet |
