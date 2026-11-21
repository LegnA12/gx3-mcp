"""Cliente SLMP (trama 3E binaria, TCP) para PLC FX5: lectura (0401) y escritura (1401). Solo biblioteca estandar.

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


def _trama(comando: int, tipo: str, numero: int, puntos: int, extra: bytes = b"") -> bytes:
    sub = 0x0001 if tipo in _BITS else 0x0000  # bits subcomando 0001, palabras 0000
    datos = struct.pack("<HHH", 0x0000, comando, sub) + numero.to_bytes(3, "little") + bytes([_CODIGO[tipo]]) \
        + struct.pack("<H", puntos) + extra  # 0x0000 = temporizador de monitoreo (el socket tiene su propio timeout)
    return bytes([0x50, 0x00, 0x00, 0xFF, 0xFF, 0x03, 0x00]) + struct.pack("<H", len(datos)) + datos


def trama_lectura(tipo: str, numero: int, puntos: int) -> bytes:
    """Peticion 3E binaria de lectura por lotes (0401)."""
    return _trama(0x0401, tipo, numero, puntos)


def trama_escritura(tipo: str, numero: int, valores: list[int]) -> bytes:
    """Escritura por lotes (1401, p630-632): bits 2 por byte (nibble alto primero), palabras 16 bits little-endian."""
    if tipo in _BITS:
        v = list(valores) + [0] * (len(valores) % 2)
        datos = bytes((v[i] << 4) | v[i + 1] for i in range(0, len(v), 2))
    else:
        datos = struct.pack(f"<{len(valores)}H", *(x & 0xFFFF for x in valores))
    return _trama(0x1401, tipo, numero, len(valores), datos)


def rangos_permitidos(texto: str) -> list[tuple[str, int, int]]:
    """'M100-M199, D100-D110, Y20' -> [('M', 100, 199), ('D', 100, 110), ('Y', 16, 16)]."""
    rangos = []
    for parte in filter(None, (p.strip() for p in texto.split(","))):
        ini, _, fin = parte.partition("-")
        (t1, n1, _), (t2, n2, _) = _parsear(ini), _parsear(fin or ini)
        if t1 != t2 or n2 < n1:
            raise ValueError(f"Rango permitido invalido '{parte}' (ej. M100-M199)")
        rangos.append((t1, n1, n2))
    return rangos


class ClienteSLMP:
    """Conexion SLMP persistente, para programas que leen/escriben seguido (ej. un software de vision).

        with ClienteSLMP("192.168.3.250", 5000) as plc:
            disparo, = plc.leer(["M100"])[0]
            plc.escribir("D100", [1, 250, -40])

    Si el PLC corta la conexion se lanza ConnectionError/OSError: cerrar y crear otro cliente.
    escribir() no filtra rangos; la politica de que se puede escribir es de la aplicacion.
    """

    def __init__(self, ip: str, puerto: int, timeout: float = 3.0):
        self.s = socket.create_connection((ip, puerto), timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.s.close()

    def _pedir(self, trama: bytes, tipo: str, puntos: int) -> list[int]:
        self.s.sendall(trama)  # semi-duplex: una peticion, su respuesta, la siguiente (p572)
        cab = _recibir(self.s, 9)
        return decodificar(tipo, puntos, cab + _recibir(self.s, struct.unpack_from("<H", cab, 7)[0]))

    def leer(self, dispositivos: list[str]) -> list[list[int]]:
        """['D10', 'M0:8'] -> [[v], [8 bits]]."""
        return [self._pedir(trama_lectura(t, n, p), t, p) for t, n, p in map(_parsear, dispositivos)]

    def escribir(self, dispositivo: str, valores: list[int]) -> None:
        """Valores seguidos desde 'dispositivo' (bits 0/1, palabras -32768..65535)."""
        tipo, numero, _ = _validar_escritura(dispositivo, valores)
        self._pedir(trama_escritura(tipo, numero, valores), tipo, 0)


def _validar_escritura(dispositivo: str, valores: list[int]) -> tuple[str, int, int]:
    tipo, numero, _ = _parsear(dispositivo)
    if not 1 <= len(valores) <= 960:
        raise ValueError("Entre 1 y 960 valores por escritura")
    if tipo in _BITS and any(v not in (0, 1) for v in valores):
        raise ValueError(f"{dispositivo} es de bit: solo 0 o 1")
    if any(not -32768 <= v <= 65535 for v in valores):
        raise ValueError("Valores de palabra entre -32768 y 65535")
    return tipo, numero, len(valores)


def escribir(ip: str, puerto: int, dispositivo: str, valores: list[int], permitidos: str,
             timeout: float = 3.0) -> list[int]:
    """Escribe valores seguidos desde 'dispositivo' SOLO si todo el tramo cae en 'permitidos'; devuelve la relectura."""
    tipo, numero, n = _validar_escritura(dispositivo, valores)
    if not any(t == tipo and a <= numero and numero + n - 1 <= b for t, a, b in rangos_permitidos(permitidos)):
        raise PermissionError(f"{dispositivo} ({n} puntos) esta fuera de los permitidos: {permitidos}")
    with ClienteSLMP(ip, puerto, timeout) as plc:
        plc.escribir(dispositivo, valores)
        return plc.leer([f"{dispositivo}:{n}"])[0]


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
    with ClienteSLMP(ip, puerto, timeout) as plc:
        return [plc._pedir(trama_lectura(t, n, p), t, p) for t, n, p in pedidos]


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

    # Escritura: ejemplos binarios del manual (p631-632)
    assert trama_escritura("M", 100, [1, 1, 0, 0, 1, 1, 0, 0])[11:] == bytes.fromhex("01 14 01 00 64 00 00 90 08 00 11 00 11 00")
    assert trama_escritura("D", 100, [0x1995, 0x1202, 0x1130])[11:] == \
        bytes.fromhex("01 14 00 00 64 00 00 a8 03 00 95 19 02 12 30 11")
    assert trama_escritura("M", 0, [1, 0, 1])[-2:] == bytes.fromhex("10 10")  # impar: ultimo nibble en 0
    assert rangos_permitidos("M100-M199, Y10-Y17,D5") == [("M", 100, 199), ("Y", 8, 15), ("D", 5, 5)]
    for disp, vals, error in [("M200", [1], PermissionError), ("M199", [1, 1], PermissionError),
                              ("X0", [1], PermissionError), ("M100", [2], ValueError), ("D5", [70000], ValueError)]:
        try:
            escribir("127.0.0.1", 1, disp, vals, "M100-M199, D5")
            raise AssertionError(disp)
        except error:
            pass
    srv2 = socket.socket(); srv2.bind(("127.0.0.1", 0)); srv2.listen(1)
    recibido = []
    def plc_falso_escritura():
        c, _ = srv2.accept()
        recibido.append(c.recv(64))
        c.sendall(bytes.fromhex("d0 00 00 ff ff 03 00 02 00 00 00"))  # escritura OK
        c.recv(64)
        c.sendall(bytes.fromhex("d0 00 00 ff ff 03 00 04 00 00 00 07 00"))  # relectura D5 = 7
        c.close()
    threading.Thread(target=plc_falso_escritura, daemon=True).start()
    assert escribir("127.0.0.1", srv2.getsockname()[1], "D5", [7], "D5") == [7]
    assert recibido[0][11:13] == b"\x01\x14"
    print("autoprueba SLMP OK")
