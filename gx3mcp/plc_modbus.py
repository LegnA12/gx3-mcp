"""Lectura de un PLC FX5 por Modbus/TCP (el FX5 actua como esclavo). Solo lectura: FC01, FC02, FC03.

Requiere en GX Works3: Ethernet Port > External Device Configuration > "MODBUS/TCP Connection Module" (puerto 502).
Direcciones segun el patron inicial "FX5 dedicado" (FX5 User's Manual (Communication), Appendix 8, p921).
shortcut: asume ese patron; si se cambio "MODBUS Device Assignment" en GX Works3, las direcciones no coinciden.
"""
import re
import socket
import struct

# dispositivo -> (codigo de funcion, direccion Modbus inicial, base del numero en GX Works3)
_MAPA = {
    "Y": (1, 0x0000, 8), "X": (2, 0x0000, 8),           # coils / inputs; X/Y octales en FX5
    "M": (1, 0x2000, 10), "SM": (1, 0x5000, 10), "L": (1, 0x5800, 10), "B": (1, 0x7800, 16),
    "F": (1, 0x9800, 10), "TC": (1, 0xD000, 10), "TS": (1, 0xD800, 10), "CC": (1, 0xF000, 10), "CS": (1, 0xF800, 10),
    "D": (3, 0x0000, 10), "SD": (3, 0x5000, 10), "W": (3, 0x7800, 16), "TN": (3, 0xD000, 10), "CN": (3, 0xF000, 10),
}
_LIMITE = {1: 2000, 2: 2000, 3: 125}  # puntos maximos por peticion (estandar Modbus)


def peticion(dispositivo: str) -> tuple[int, int, int]:
    """'D10' -> (3, 10, 1); 'Y0:5' -> (1, 0, 5); 'M0' -> (1, 0x2000, 1). Devuelve (fc, direccion, cantidad)."""
    m = re.fullmatch(r"([A-Z]+)([0-9A-F]+)(?::(\d+))?", dispositivo.strip().upper())
    if not m or m.group(1) not in _MAPA:
        raise ValueError(f"Dispositivo invalido '{dispositivo}'. Validos: {sorted(_MAPA)}, ej. D10, Y0:5, X0:8")
    fc, base, radix = _MAPA[m.group(1)]
    try:
        numero = int(m.group(2), radix)
    except ValueError:
        raise ValueError(f"'{dispositivo}': numero invalido (X/Y octal, B/W hex, resto decimal)")
    cantidad = int(m.group(3) or 1)
    if not 1 <= cantidad <= _LIMITE[fc]:
        raise ValueError(f"'{dispositivo}': entre 1 y {_LIMITE[fc]} puntos")
    return fc, base + numero, cantidad


def trama(transaccion: int, fc: int, direccion: int, cantidad: int) -> bytes:
    # MBAP: transaccion, protocolo 0, longitud 6, id de unidad (el FX5 lo ignora, manual p514) + PDU
    return struct.pack(">HHHBBHH", transaccion, 0, 6, 0xFF, fc, direccion, cantidad)


def decodificar(fc: int, cantidad: int, respuesta: bytes) -> list[int]:
    if len(respuesta) < 9:
        raise ConnectionError(f"Respuesta Modbus corta: {respuesta.hex(' ')}")
    fc_resp = respuesta[7]
    if fc_resp == fc | 0x80:
        raise ConnectionError(f"El PLC respondio con excepcion Modbus {respuesta[8]:02X}H "
                              "(01 funcion no soportada, 02 direccion no asignada, 03 valor invalido)")
    if fc_resp != fc:
        raise ConnectionError(f"Codigo de funcion inesperado {fc_resp:02X}H")
    datos = respuesta[9:9 + respuesta[8]]
    if fc in (1, 2):  # bits empaquetados, el primero en el bit menos significativo
        return [(datos[i // 8] >> (i % 8)) & 1 for i in range(cantidad)]
    return list(struct.unpack(f">{cantidad}h", datos))


def leer(ip: str, puerto: int, dispositivos: list[str], timeout: float = 3.0) -> list[list[int]]:
    """Lee varios dispositivos por una sola conexion TCP."""
    pedidos = [peticion(d) for d in dispositivos]
    resultados = []
    with socket.create_connection((ip, puerto), timeout=timeout) as s:
        for n, (fc, direccion, cantidad) in enumerate(pedidos, 1):
            s.sendall(trama(n, fc, direccion, cantidad))
            cab = _recibir(s, 6)
            cuerpo = _recibir(s, struct.unpack(">H", cab[4:6])[0])
            if struct.unpack(">H", cab[:2])[0] != n:
                raise ConnectionError("Respuesta de otra transaccion")
            resultados.append(decodificar(fc, cantidad, cab + cuerpo))
    return resultados


def _recibir(s: socket.socket, n: int) -> bytes:
    datos = b""
    while len(datos) < n:
        trozo = s.recv(n - len(datos))
        if not trozo:
            raise ConnectionError("El PLC cerro la conexion")
        datos += trozo
    return datos


if __name__ == "__main__":
    assert peticion("Y0:5") == (1, 0, 5) and peticion("X10:8") == (2, 8, 8)  # X10 octal = 8
    assert peticion("M0") == (1, 0x2000, 1) and peticion("TS0") == (1, 0xD800, 1)
    assert peticion("D10") == (3, 10, 1) and peticion("TN0") == (3, 0xD000, 1) and peticion("SD203") == (3, 0x5000 + 203, 1)
    assert trama(1, 3, 10, 2).hex(" ") == "00 01 00 00 00 06 ff 03 00 0a 00 02"
    for malo in ("X8", "D0:126", "Q1"):
        try:
            peticion(malo)
            raise AssertionError(malo)
        except ValueError:
            pass
    assert decodificar(1, 5, bytes.fromhex("00 01 00 00 00 04 ff 01 01 09")) == [1, 0, 0, 1, 0]
    assert decodificar(3, 2, bytes.fromhex("00 01 00 00 00 07 ff 03 04 00 1e ff ff")) == [30, -1]
    try:
        decodificar(3, 1, bytes.fromhex("00 01 00 00 00 03 ff 83 02"))
        raise AssertionError("debio fallar")
    except ConnectionError as e:
        assert "02H" in str(e)

    import threading
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    def esclavo_falso():
        c, _ = srv.accept()
        c.recv(64); c.sendall(bytes.fromhex("00 01 00 00 00 04 ff 01 01 03"))       # Y0:2 -> 1,1
        c.recv(64); c.sendall(bytes.fromhex("00 02 00 00 00 05 ff 03 02 00 05"))    # D10 -> 5
        c.close()
    threading.Thread(target=esclavo_falso, daemon=True).start()
    assert leer("127.0.0.1", srv.getsockname()[1], ["Y0:2", "D10"]) == [[1, 1], [5]]
    print("autoprueba Modbus OK")
