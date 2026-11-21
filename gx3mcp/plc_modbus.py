"""Cliente Modbus/TCP para un PLC FX5 (esclavo): lectura FC01, FC02, FC03 y escritura FC0F, FC10.

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


def _verificar(fc: int, respuesta: bytes):
    if len(respuesta) < 9:
        raise ConnectionError(f"Respuesta Modbus corta: {respuesta.hex(' ')}")
    if respuesta[7] == fc | 0x80:
        raise ConnectionError(f"El PLC respondio con excepcion Modbus {respuesta[8]:02X}H "
                              "(01 funcion no soportada, 02 direccion no asignada, 03 valor invalido)")
    if respuesta[7] != fc:
        raise ConnectionError(f"Codigo de funcion inesperado {respuesta[7]:02X}H")


def trama_escritura(transaccion: int, dispositivo: str, valores: list[int]) -> bytes:
    """FC0F (bobinas: Y, M, SM...) o FC10 (registros: D, SD, TN...), valores seguidos desde 'dispositivo'."""
    fc, direccion, _ = peticion(dispositivo)
    n = len(valores)
    if fc == 2:
        raise ValueError(f"{dispositivo}: X es entrada, no se escribe por Modbus")
    if fc == 1:
        if not 1 <= n <= 1968 or any(v not in (0, 1) for v in valores):
            raise ValueError(f"{dispositivo}: de 1 a 1968 bits, cada uno 0 o 1")
        datos = bytes(sum(v << j for j, v in enumerate(valores[i:i + 8])) for i in range(0, n, 8))  # LSB primero
        pdu = struct.pack(">BHHB", 0x0F, direccion, n, len(datos)) + datos
    else:
        if not 1 <= n <= 123 or any(not -32768 <= v <= 65535 for v in valores):
            raise ValueError(f"{dispositivo}: de 1 a 123 registros entre -32768 y 65535")
        pdu = struct.pack(">BHHB", 0x10, direccion, n, 2 * n) + struct.pack(f">{n}H", *(v & 0xFFFF for v in valores))
    return struct.pack(">HHHB", transaccion, 0, len(pdu) + 1, 0xFF) + pdu


class ClienteModbus:
    """Conexion Modbus/TCP persistente (ej. software de vision). El FX5 rechaza a veces una conexion
    abierta justo despues de cerrar otra (medido: sin pausa 2/4 OK, con 50 ms 4/4): conecta una vez y reutiliza.

        with ClienteModbus("192.168.3.250") as plc:
            d200, d201 = plc.leer(["D200:2"])[0]
            plc.escribir("D202", [150])

    Si la conexion cae se lanza ConnectionError/OSError: cerrar, esperar y crear otro cliente.
    """

    def __init__(self, ip: str, puerto: int = 502, timeout: float = 3.0):
        self.s = socket.create_connection((ip, puerto), timeout=timeout)
        self.n = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.s.close()

    def _pedir(self, armar, fc: int, cantidad: int) -> list[int]:
        self.n = self.n % 0xFFFF + 1
        self.s.sendall(armar(self.n))
        cab = _recibir(self.s, 6)
        respuesta = cab + _recibir(self.s, struct.unpack(">H", cab[4:6])[0])
        if struct.unpack(">H", cab[:2])[0] != self.n:
            raise ConnectionError("Respuesta de otra transaccion")
        if fc in (0x0F, 0x10):
            _verificar(fc, respuesta)
            return []
        return decodificar(fc, cantidad, respuesta)

    def leer(self, dispositivos: list[str]) -> list[list[int]]:
        """['D10', 'Y0:8'] -> [[v], [8 bits]]."""
        return [self._pedir(lambda n: trama(n, fc, d, c), fc, c) for fc, d, c in map(peticion, dispositivos)]

    def escribir(self, dispositivo: str, valores: list[int]) -> None:
        """Valores seguidos desde 'dispositivo': FC0F si es de bit, FC10 si es registro."""
        trama_escritura(0, dispositivo, valores)  # valida antes de enviar
        fc = 0x0F if peticion(dispositivo)[0] == 1 else 0x10
        self._pedir(lambda n: trama_escritura(n, dispositivo, valores), fc, len(valores))


def escribir(ip: str, puerto: int, dispositivo: str, valores: list[int], timeout: float = 3.0) -> list[int]:
    """Escribe y relee por la misma conexion; devuelve lo releido."""
    trama_escritura(0, dispositivo, valores)  # valida antes de conectar
    with ClienteModbus(ip, puerto, timeout) as plc:
        plc.escribir(dispositivo, valores)
        return plc.leer([f"{dispositivo}:{len(valores)}"])[0]


def decodificar(fc: int, cantidad: int, respuesta: bytes) -> list[int]:
    _verificar(fc, respuesta)
    datos = respuesta[9:9 + respuesta[8]]
    if fc in (1, 2):  # bits empaquetados, el primero en el bit menos significativo
        return [(datos[i // 8] >> (i % 8)) & 1 for i in range(cantidad)]
    return list(struct.unpack(f">{cantidad}h", datos))


def leer(ip: str, puerto: int, dispositivos: list[str], timeout: float = 3.0) -> list[list[int]]:
    """Lee varios dispositivos por una sola conexion TCP."""
    [peticion(d) for d in dispositivos]  # valida todo antes de conectar
    with ClienteModbus(ip, puerto, timeout) as plc:
        return plc.leer(dispositivos)


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

    # Escritura: FC0F bits LSB primero, FC10 registros big-endian (estandar Modbus)
    assert trama_escritura(1, "M300", [1, 0, 1]).hex(" ") == "00 01 00 00 00 08 ff 0f 21 2c 00 03 01 05"
    assert trama_escritura(1, "D202", [150, -1]).hex(" ") == "00 01 00 00 00 0b ff 10 00 ca 00 02 04 00 96 ff ff"
    for disp, vals in (("X0", [1]), ("M0", [2]), ("D0", [70000]), ("D0", [0] * 124)):
        try:
            trama_escritura(1, disp, vals)
            raise AssertionError(disp)
        except ValueError:
            pass
    srv2 = socket.socket(); srv2.bind(("127.0.0.1", 0)); srv2.listen(1)
    def esclavo_escritura():
        c, _ = srv2.accept()
        c.recv(64); c.sendall(bytes.fromhex("00 01 00 00 00 06 ff 10 00 ca 00 01"))  # eco de FC10
        c.recv(64); c.sendall(bytes.fromhex("00 02 00 00 00 05 ff 03 02 00 96"))     # relectura D202 = 150
        c.close()
    threading.Thread(target=esclavo_escritura, daemon=True).start()
    assert escribir("127.0.0.1", srv2.getsockname()[1], "D202", [150]) == [150]
    print("autoprueba Modbus OK")
