"""Prueba de Modbus/TCP contra un FX5 con el programa de ejemplo (renglones "Modbus - ..." del README).

    python ejemplos/prueba_modbus_tcp.py 192.168.3.250            solo lee, cada segundo
    python ejemplos/prueba_modbus_tcp.py 192.168.3.250 --escribir   ademas pone M300=1 y D202=150

Direcciones con la asignacion inicial "FX5 dedicado": D200 = holding register 200, M300 = bobina 8492 (2000H+300).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gx3mcp"))
from plc_modbus import ClienteModbus  # noqa: E402  (un archivo, solo biblioteca estandar: copialo a tu proyecto)

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.3.250"

with ClienteModbus(ip, 502) as plc:  # una conexion para todo: el FX5 rechaza reconexiones muy seguidas
    if "--escribir" in sys.argv:
        plc.escribir("M300", [1])    # FC0F -> Y5 debe encender
        plc.escribir("D202", [150])  # FC10 -> Y6 debe encender
    for _ in range(5):
        (d200, d201, d202), y, x = plc.leer(["D200:3", "Y0:8", "X0:8"])
        print(f"D200 segundos={d200}  D201={d201} (esperado 1234)  D202={d202}  Y0-Y7={y}  X0-X7={x}")
        time.sleep(1)
