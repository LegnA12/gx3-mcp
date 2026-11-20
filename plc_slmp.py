"""Lectura de dispositivos de un PLC FX5 por SLMP (trama 3E binaria, TCP). Solo lectura.

Requiere en GX Works3: Ethernet Port > External Device Configuration > "SLMP Connection Module",
TCP, puerto propio (ej. 5000; no usar 5549-5569) y Communication Data Code = Binary.
Formato: FX5 User's Manual (Communication), trama 3E p576-586, comando 0401 p627-630.
"""
import re
import socket
import struct

# Codigos de dispositivo binarios (manual de comunicacion p623-624)
_CODIGO = {"X": 0x9C, "Y": 0x9D, "M": 0x90, "L": 0x92, "F": 0x93, "B": 0xA0, "SM": 0x91,
           "D": 0xA8, "W": 0xB4, "R": 0xAF, "SD": 0xA9, "TN": 0xC2, "TS": 0xC1, "CN": 0xC5, "CS": 0xC4}
_BITS = {"X", "Y", "M", "L", "F", "B", "SM", "TS", "CS"}
_BASE = {"X": 8, "Y": 8, "B": 16, "W": 16}  # FX5: X/Y octal; B/W hexadecimal; resto decimal


def _parsear(texto: str) -> tuple[str, int, int]:
    """'D10' -> ('D', 10, 1); 'M0:8' -> ('M', 0, 8); 'X10:4' -> ('X', 8, 4) (X10 octal = 8)."""
    m = re.fullmatch(r"([A-Z]+)([0-9A-F]+)(?::(\d+))?", texto.strip().upper())
    if not m or m.group(1) not in _CODIGO:
        raise ValueError(f"Dispositivo invalido '{texto}'. Validos: {sorted(_CODIGO)}, ej. D10, M0:8, X0:16")
    tipo, num, n = m.group(1), m.group(2), int(m.group(3) or 1)
    try:
        numero = int(num, _BASE.get(tipo, 10))
    except ValueError:
        raise ValueError(f"'{texto}': numero invalido para {tipo} (X/Y octal, B/W hex, resto decimal)")
    limite = 3584 if tipo in _BITS else 960  # puntos maximos por mensaje binario (p628)
    if not 1 <= n <= limite:
        raise ValueError(f"'{texto}': entre 1 y {limite} puntos")
    return tipo, numero, n


def trama_lectura(tipo: str, numero: int, puntos: int) -> bytes:
    """Peticion 3E binaria de lectura por lotes (0401): bits subcomando 0001, palabras 0000."""
    sub = 0x0001 if tipo in _BITS else 0x0000
    datos = struct.pack("<HHH", 0x0000, 0x0401, sub) + numero.to_bytes(3, "little") + bytes([_CODIGO[tipo]]) \
        + struct.pack("<H", puntos)  # 0x0000 = temporizador de monitoreo (el socket tiene su propio timeout)
    return bytes([0x50, 0x00, 0x00, 0xFF, 0xFF, 0x03, 0x00]) + struct.pack("<H", len(datos)) + datos


def decodificar(tipo: str, puntos: int, respuesta: bytes) -> list[int]:
    if len(respuesta) < 11 or respuesta[:2] != b"\xD0\x00":
        raise ConnectionError(f"Respuesta SLMP invalida: {respuesta[:16].hex(' ')}")
    fin = struct.unpack_from("<H", respuesta, 9)[0]
    if fin:
        raise ConnectionError(f"El PLC respondio con error {fin:04X}H (ver manual de comunicacion, codigos C0xxH)")
    datos = respuesta[11:]
    if tipo in _BITS:  # 2 puntos por byte, nibble alto = primer punto (p629)
        return [(datos[i // 2] >> (4 if i % 2 == 0 else 0)) & 1 for i in range(puntos)]
    return list(struct.unpack_from(f"<{puntos}h", datos))


def leer(ip: str, puerto: int, dispositivos: list[str], timeout: float = 3.0) -> list[list[int]]:
    """Lee varios dispositivos por UNA conexion TCP (el modulo SLMP del PLC atiende un cliente por conexion)."""
    pedidos = [_parsear(d) for d in dispositivos]  # valida todo antes de conectar
    resultados = []
    with socket.create_connection((ip, puerto), timeout=timeout) as s:
        for tipo, numero, puntos in pedidos:  # semi-duplex: una peticion, su respuesta, la siguiente (p572)
            s.sendall(trama_lectura(tipo, numero, puntos))
            cab = _recibir(s, 9)
            resto = _recibir(s, struct.unpack_from("<H", cab, 7)[0])
            resultados.append(decodificar(tipo, puntos, cab + resto))
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
    # Autoprueba contra los ejemplos del manual (p629) y un PLC simulado local
    assert trama_lectura(*_parsear("M100:8"))[11:] == bytes.fromhex("01 04 01 00 64 00 00 90 08 00")
    assert trama_lectura(*_parsear("D100:3")).hex(" ") == "50 00 00 ff ff 03 00 0c 00 00 00 01 04 00 00 64 00 00 a8 03 00"
    assert _parsear("X10:4") == ("X", 8, 4) and _parsear("Y17") == ("Y", 15, 1)
    for malo in ("X8", "D0:961", "Q5"):
        try:
            _parsear(malo)
            raise AssertionError(malo)
        except ValueError:
            pass
    assert decodificar("M", 3, bytes.fromhex("d0 00 00 ff ff 03 00 04 00 00 00 10 10")) == [1, 0, 1]
    assert decodificar("D", 2, bytes.fromhex("d0 00 00 ff ff 03 00 06 00 00 00 34 12 ff ff")) == [0x1234, -1]
    try:
        decodificar("D", 1, bytes.fromhex("d0 00 00 ff ff 03 00 02 00 56 c0"))
        raise AssertionError("debio fallar")
    except ConnectionError as e:
        assert "C056H" in str(e)

    import threading
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    conexiones = []
    def plc_falso():  # acepta UNA conexion y responde dos peticiones seguidas
        c, _ = srv.accept()
        conexiones.append(c)
        c.recv(64)
        c.sendall(bytes.fromhex("d0 00 00 ff ff 03 00 06 00 00 00 05 00 0a 00"))
        c.recv(64)
        c.sendall(bytes.fromhex("d0 00 00 ff ff 03 00 03 00 00 00 10"))
        c.close()
    threading.Thread(target=plc_falso, daemon=True).start()
    assert leer("127.0.0.1", srv.getsockname()[1], ["D10:2", "Y0:2"]) == [[5, 10], [1, 0]]
    assert len(conexiones) == 1
    print("autoprueba SLMP OK")
