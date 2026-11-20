"""MCP para leer y editar programas dentro de proyectos GX Works3 (.gx3).

Un .gx3 es un ZIP; cada programa es una base SQLite dentro:
  <id>_STDB.db -> tabla StBlocks(id, pos, data)       ST en texto plano (leer / editar)
  <id>_LDDB.db -> tabla LadderBlocks(id, pos, data)   ladder codificado (agregar renglones)
Despues de editar, abrir en GX Works3 y hacer "Convertir todo" (Rebuild All):
el codigo compilado dentro del .gx3 no se regenera aqui.
"""
import os
import re
import shutil
import sqlite3
import struct
import tempfile
import time
import uuid
import zipfile

import plc_modbus  # lectura en vivo del PLC por Modbus/TCP
import plc_slmp  # lectura en vivo del PLC por SLMP
from etiquetas import nombres_etiquetas  # _lid/<LabelID>/<RowID> -> nombre, tipo, clase, dispositivo
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # su mensaje si llega a Claude

mcp = MCPServer("gx3")


def _abrir(ruta_gx3: str) -> zipfile.ZipFile:
    if not ruta_gx3.lower().endswith(".gx3"):
        raise ToolError(f"No es un .gx3: {ruta_gx3}")
    if not os.path.isfile(ruta_gx3):
        raise ToolError(f"No existe el archivo: {ruta_gx3}")
    try:
        return zipfile.ZipFile(ruta_gx3)
    except zipfile.BadZipFile:
        raise ToolError(f"El .gx3 esta danado o no es un proyecto GX Works3: {ruta_gx3}")


def _db_programa(z: zipfile.ZipFile, programa: str) -> str:
    nombre = f"{programa}_STDB.db"
    if nombre not in z.namelist():
        raise ToolError(f"No hay programa ST '{programa}'. Usa listar_programas.")
    return nombre


def _bloques_st(z: zipfile.ZipFile, nombre_db: str) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as d:
        c = sqlite3.connect(z.extract(nombre_db, d))
        try:
            return c.execute("select id, data from StBlocks order by pos").fetchall()
        finally:
            c.close()


def _resumen(texto: str) -> str:
    for linea in texto.splitlines():
        if linea.strip():
            return linea.strip()[:100]
    return ""


@mcp.tool()
def listar_programas(ruta_gx3: str) -> str:
    """Lista los programas del .gx3: id, lenguaje (ST: leer/editar, LD: agregar renglones) y primera linea ST."""
    salida = []
    with _abrir(ruta_gx3) as z:
        for n in z.namelist():
            if n.endswith("_STDB.db"):
                bloques = _bloques_st(z, n)
                primera = _resumen(bloques[0][1]) if bloques else ""
                salida.append(f"{n[:-8]}  ST  {len(bloques)} bloque(s)  | {primera}")
            elif n.endswith("_LDDB.db"):
                salida.append(f"{n[:-8]}  LD  (agregar_renglon_ld)")
    return "\n".join(salida) or "El proyecto no tiene programas ST ni LD."


@mcp.tool()
def leer_st(ruta_gx3: str, programa: str) -> str:
    """Devuelve el codigo ST completo de un programa (id de listar_programas), con lineas numeradas."""
    with _abrir(ruta_gx3) as z:
        bloques = _bloques_st(z, _db_programa(z, programa))
    partes = []
    for i, (_, data) in enumerate(bloques, 1):
        lineas = data.replace("\r\n", "\n").split("\n")
        numeradas = "\n".join(f"{n:4}| {l}" for n, l in enumerate(lineas, 1))
        partes.append(f"=== bloque {i} ===\n{numeradas}")
    return "\n".join(partes)


def _guardar_cambio(ruta_gx3: str, programa: str, sufijo: str, cambiar, ruta_salida: str, extra: str = "") -> str:
    """Extrae <programa><sufijo>, aplica cambiar(conexion), revisa y reescribe el .gx3 con respaldo.

    extra: otra base del mismo programa (ej. '_MilDB.db') que cambiar() ve adjunta como esquema 'mil'.
    """
    if not ruta_salida.lower().endswith(".gx3"):
        raise ToolError("ruta_salida debe terminar en .gx3")
    with _abrir(ruta_gx3) as z, tempfile.TemporaryDirectory() as d:
        nombre_db = f"{programa}{sufijo}"
        if nombre_db not in z.namelist():
            raise ToolError(f"No hay programa '{programa}' de ese lenguaje. Usa listar_programas.")
        cambiadas = {nombre_db: z.extract(nombre_db, d)}
        if extra and f"{programa}{extra}" in z.namelist():
            cambiadas[f"{programa}{extra}"] = z.extract(f"{programa}{extra}", d)
        c = sqlite3.connect(cambiadas[nombre_db])
        try:
            for ruta in list(cambiadas.values())[1:]:
                c.execute("attach database ? as mil", (ruta,))
            cambiar(c)
            c.commit()
            for esquema in ["main"] + (["mil"] if len(cambiadas) > 1 else []):
                if c.execute(f"pragma {esquema}.integrity_check").fetchone()[0] != "ok":
                    raise ToolError("La base SQLite quedo danada; no se guardo nada.")
        finally:
            c.close()

        tmp = ruta_salida + ".tmp"
        with zipfile.ZipFile(tmp, "w") as out:
            for info in z.infolist():  # mismo orden, nombres, fechas y compresion
                datos = open(cambiadas[info.filename], "rb").read() if info.filename in cambiadas else z.read(info)
                out.writestr(info, datos)

    with zipfile.ZipFile(tmp) as check:
        if check.testzip() is not None:
            os.remove(tmp)
            raise ToolError("El ZIP generado esta danado; no se guardo nada.")

    respaldo = ""
    if os.path.exists(ruta_salida):
        respaldo = f"{ruta_salida[:-4]}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(ruta_salida, respaldo)
    os.replace(tmp, ruta_salida)
    msg = f"Guardado en {ruta_salida}."
    if respaldo:
        msg += f" Respaldo del archivo anterior: {respaldo}."
    return msg + " Abre el proyecto en GX Works3 y haz Convertir todo (Rebuild All) antes de descargar al PLC."


@mcp.tool()
def editar_st(ruta_gx3: str, programa: str, texto_viejo: str, texto_nuevo: str, ruta_salida: str) -> str:
    """Reemplaza texto_viejo (debe aparecer UNA sola vez en el programa) por texto_nuevo y guarda en ruta_salida.

    ruta_salida puede ser el mismo archivo: si ya existe se respalda antes como <nombre>.<fecha>.bak.
    Despues hay que abrir el proyecto en GX Works3 y hacer Convertir todo (Rebuild All).
    """
    viejo = texto_viejo.replace("\r\n", "\n")
    nuevo = texto_nuevo.replace("\r\n", "\n")
    if not viejo:
        raise ToolError("texto_viejo vacio")

    def cambiar(c):
        bloques = c.execute("select id, data from StBlocks").fetchall()
        hallados = [(i, data.replace("\r\n", "\n")) for i, data in bloques if viejo in data.replace("\r\n", "\n")]
        total = sum(t.count(viejo) for _, t in hallados)
        if total != 1:
            raise ToolError(f"texto_viejo aparece {total} veces; debe ser exactamente 1. Agrega mas contexto.")
        id_bloque, texto = hallados[0]
        # GX Works3 guarda saltos de linea CRLF
        c.execute("update StBlocks set data=? where id=?", (texto.replace(viejo, nuevo).replace("\n", "\r\n"), id_bloque))

    return _guardar_cambio(ruta_gx3, programa, "_STDB.db", cambiar, ruta_salida)


# ---------------- Ladder ----------------
# Bloque: V1:<n cadenas>:<longitudes>:<cadenas>:<arbol>; cada '#' del arbol toma la siguiente cadena en orden.
_CONTACTOS = {"X", "Y", "M", "L", "B", "F", "SM", "T", "C"}
_BOBINAS = {"Y", "M", "L", "B", "F"}


def _dispositivo(texto: str, validos: set) -> tuple[str, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)", texto.strip().upper())
    if not m or m.group(1) not in validos:
        raise ToolError(f"Dispositivo invalido '{texto}'. Validos: {sorted(validos)} + numero, ej. X0, M10.")
    tipo, num = m.groups()
    if tipo in ("X", "Y"):
        if re.search(r"[89]", num):
            raise ToolError(f"{texto}: X/Y son octales en FX5/iQ-F (sin 8 ni 9).")
        return tipo, int(num, 16)  # GX Works3 guarda X/Y octal leyendo sus digitos como hex (X10 -> 16)
    return tipo, int(num)


def _bloque(cadenas: list[str], arbol: str) -> str:
    return f"V1:{len(cadenas)}:" + "".join(f"{len(s)}:" for s in cadenas) + "".join(f"{s}:" for s in cadenas) + arbol


def _separar(data: str) -> tuple[list[str], str]:
    """Inverso de _bloque: devuelve (cadenas, arbol)."""
    _, n, resto = data.split(":", 2)
    n = int(n)
    partes = resto.split(":", n)
    largos, texto = list(map(int, partes[:n])), partes[n]
    cadenas, i = [], 0
    for largo in largos:
        cadenas.append(texto[i:i + largo])
        i += largo + 1
    return cadenas, texto[i:]


def _arbol(texto: str, cadenas: list[str]):
    """Convierte 'nodo{k=v:k=[n{..}:n{..}]}' en dicts {'_': nodo, k: v}; cada '#' toma la siguiente cadena."""
    pos, pila = 0, iter(cadenas)

    def valor():
        nonlocal pos
        if texto[pos] == "[":
            pos += 1
            lista = []
            while texto[pos] != "]":
                lista.append(valor())
                if texto[pos] == ":":
                    pos += 1
            pos += 1
            return lista
        ini = pos
        while pos < len(texto) and texto[pos] not in ":{}[]":
            pos += 1
        atomo = texto[ini:pos]
        if pos < len(texto) and texto[pos] == "{":
            pos += 1
            nodo = {"_": atomo}
            while texto[pos] != "}":
                ini = texto.index("=", pos)
                clave, pos = texto[pos:ini], ini + 1
                nodo[clave] = valor()
                if texto[pos] == ":":
                    pos += 1
            pos += 1
            return nodo
        return next(pila) if atomo == "#" else atomo

    return valor()


def _operando(n, etiquetas: dict) -> str:
    """Texto legible de un operando: dispositivo, constante, etiqueta, miembro, arreglo o bit de palabra."""
    if not isinstance(n, dict):
        return str(n)
    t = n["_"]
    if t == "d":
        a = int(n["a"])
        return n["s"] + (format(a, "X") if n["s"] in ("X", "Y") else str(a))  # X/Y: digitos octales guardados como hex
    if t == "c":
        prefijo = n["s"].split("_")[0]
        if prefijo == "E":  # REAL: v son los bits IEEE-754 en hex; t el texto tecleado
            return "E" + (n.get("t") or format(struct.unpack(">f", bytes.fromhex(str(n["v"]).zfill(8)))[0], "g"))
        if prefijo == "H":  # hexadecimal: v viene en decimal
            return "H" + format(int(n["v"]), "X")
        return prefijo + str(n["v"])  # 'K_1' + v=30 -> K30
    if t in ("l", "pl"):
        return etiquetas.get(n["id"], n["id"])
    if t == "I":
        return f"{_operando(n['b'], etiquetas)}.{_operando(n['m'], etiquetas)}"
    if t == "M":
        return f"{_operando(n['b'], etiquetas)}.{n['m'].get('v', '?') if isinstance(n['m'], dict) else n['m']}"
    if t == "A":
        return f"{_operando(n['a'], etiquetas)}[{','.join(_operando(i, etiquetas) for i in n['i'])}]"
    return t + "{?}"


_FLANCO = {"a": "", "p": "↑", "f": "↓"}  # ct=a normal, p subida, f bajada (segun manual cap. 5)


_COMPARAR = {"=": lambda a, b: a == b, "<>": lambda a, b: a != b, ">": lambda a, b: a > b,
             "<": lambda a, b: a < b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}


def _marca(conduce) -> str:
    return "" if conduce is None else ("■" if conduce else "□")


def _celda(e: dict, etiquetas: dict, valores: dict | None = None) -> str:
    """valores (opcional, monitor en vivo): (operando, es_palabra) -> valor leido del PLC."""
    s = e["s"]
    if s == "-":  # linea horizontal explicita (ramas que llegan mas lejos que sus contactos)
        return "-"
    if not isinstance(s, dict) or s.get("_") != "ce":
        return f"[{s.get('_')}]"
    op = s["op"]
    args = [_operando(a, etiquetas) for a in s.get("args", [])]
    nombre, flanco = op["op"], _FLANCO.get(op.get("ct", "a"), "?")
    val = (lambda a, palabra=False: valores.get((a, palabra))) if valores is not None else (lambda a, palabra=False: None)
    if op["_"] == "ct":
        if nombre in ("a", "b"):
            v = val(args[0])
            conduce = None if v is None or flanco else (v == 1) == (nombre == "a")  # flanco: solo dura un scan
            return f"[{'/' if nombre == 'b' else ''}{flanco}{args[0]}]{_marca(conduce)}"
        etiqueta = ("E" if any(a.get("vt") == "Ar32" for a in op.get("as", [])) else "") + nombre  # REAL: LDE<
        # shortcut: el monitor lee palabras de 16 bits; un REAL (2 palabras) se muestra sin valor hasta leerlo como REAL
        if valores is None or etiqueta.startswith("E"):
            return f"[{etiqueta} {' '.join(args)}]"  # contacto de comparacion: [> D0 K10]
        nums = [int(a[1:]) if re.fullmatch(r"K-?\d+", a) else val(a, True) for a in args]
        conduce = _COMPARAR[nombre](*nums) if None not in nums and nombre in _COMPARAR and len(nums) == 2 else None
        texto = " ".join(a if re.fullmatch(r"K-?\d+", a) or val(a, True) is None else f"{a}={val(a, True)}" for a in args)
        return f"[{etiqueta} {texto}]{_marca(conduce)}"
    if nombre == "c":
        v = val(args[0])
        return f"({args[0]}){_marca(None if v is None else v == 1)}"
    nombre = nombre.replace("OUT__16", "OUT")
    if flanco == "↑" and not nombre.endswith("P"):  # INCP ya trae la P en el nombre
        nombre += "P"
    vistos = [f"{a}={val(a, nombre not in ('SET', 'RST') or _es_palabra(a))}" for a in args
              if val(a, nombre not in ("SET", "RST") or _es_palabra(a)) is not None]
    return f"[{nombre} {' '.join(args)}]" + (f"{{{', '.join(vistos)}}}" if vistos else "")


def _dibujar(fg: dict, etiquetas: dict, valores: dict | None = None) -> list[str]:
    """Dibuja una rejilla fg{dim, es, vs} como lineas de texto. Las lineas horizontales son implicitas."""
    cols, filas = (int(x) for x in fg["dim"].split("x")) if "x" in fg.get("dim", "") else (1, 1)
    cols, filas = max(cols, 1), max(filas, 1)
    celdas = {}
    for e in fg.get("es", []):
        c, r = (int(x) for x in e["pos"].split(","))
        celdas[(c, r)] = _celda(e, etiquetas, valores)
    vert = {tuple(int(x) for x in v["pos"].split(",")) for v in fg.get("vs", [])}
    ancho = max([len(t) for t in celdas.values()] + [6]) + 2
    lineas = []
    for r in range(filas):
        # puntos conectados de la fila: celdas con elemento y bordes con linea vertical (arriba o abajo)
        usados = [c for (c, rr) in celdas if rr == r] + [c - 0.5 for (c, rr) in vert if rr in (r, r + 1)]
        ini, fin = min(usados, default=0), max(usados, default=-1)
        linea = "|" if ini <= 0 else " "
        for c in range(cols):
            borde = "+" if ((c, r) in vert or (c, r + 1) in vert) else ("-" if ini <= c - 0.5 <= fin else " ")
            linea += borde if c else ""
            t = celdas.get((c, r))
            relleno = "-" if ini <= c <= fin else " "
            linea += (t.center(ancho, relleno) if t else relleno * ancho)
        lineas.append(linea.rstrip())
    return lineas


def _leer_bloque(data: str, etiquetas: dict, clases: dict | None = None, valores: dict | None = None) -> list[str]:
    """etiquetas: _lid -> nombre; clases: _lid -> clase (VAR_INPUT, VAR_OUTPUT...) para la direccion de pines de FB.
    valores: lecturas en vivo para el monitor (ver monitorear_ld)."""
    cadenas, texto = _separar(data)
    arbol = _arbol(texto, cadenas)
    t = arbol.get("_")
    if t == "st":
        return [f"// {arbol['st']['t']}"]
    if t == "end":
        return ["[END]"]
    if t == "cb":
        return _dibujar(arbol["fg"], etiquetas, valores)
    if t == "fbcs":
        # llamada a FB: cbag = pin de bit alimentado por un circuito; vag = pin de valor con su argumento
        fb = arbol["fb"]
        lineas = [f"[FB {fb.get('dn')}  instancia {_operando({'_': 'l', 'id': fb.get('in')}, etiquetas)}]"]
        for pin in arbol.get("ig", []):
            nombre = _operando(pin["l"], etiquetas)
            flecha = "=>" if (clases or {}).get(pin["l"].get("id"), "").startswith("VAR_OUTPUT") else ":="
            if pin["_"] == "vag":
                arg = _operando(pin["v"], etiquetas)
                v = (valores or {}).get((arg, True))
                lineas.append(f"    {nombre} {flecha} {arg}" + ("" if v is None else f" = {v}"))
            else:
                circuito = _dibujar(pin["fg"], etiquetas, valores)
                lineas.append(f"    {nombre} {flecha} " + circuito[0].lstrip("|"))
                lineas += ["    " + " " * (len(nombre) + 4) + l.lstrip("|") for l in circuito[1:]]
        return lineas
    return [f"(bloque '{t}' sin decodificar)"]


@mcp.tool()
def leer_ld(ruta_gx3: str, programa: str) -> str:
    """Muestra el programa ladder (id de listar_programas) como texto: comentarios, renglones dibujados y END.

    [X0] contacto NA, [/X0] NC, [↑X0] flanco subida, [↓X0] bajada, (Y0) bobina, [SET M0] instruccion,
    [> D0 K10] contacto de comparacion, '+' union de ramas. X/Y en octal como en GX Works3.
    """
    with _abrir(ruta_gx3) as z, tempfile.TemporaryDirectory() as d:
        nombre = f"{programa}_LDDB.db"
        if nombre not in z.namelist():
            raise ToolError(f"No hay programa LD '{programa}'. Usa listar_programas.")
        c = sqlite3.connect(z.extract(nombre, d))
        try:
            bloques = c.execute("select blocktype, data from LadderBlocks order by pos").fetchall()
        finally:
            c.close()
    info = nombres_etiquetas(ruta_gx3)
    nombres = {k: v["nombre"] or k for k, v in info.items()}
    clases = {k: v["clase"] for k, v in info.items()}
    salida, n = [], 0
    for tipo, data in bloques:
        try:
            lineas = _leer_bloque(data, nombres, clases)
        except Exception as e:  # un bloque raro no debe tapar el resto del programa
            lineas = [f"(no se pudo leer este bloque: {e})"]
        if tipo in (0, 4):  # renglon normal o FB: misma numeracion que agregar/borrar
            n += 1
            lineas[0] = f"R{n:<3}" + lineas[0]
            lineas[1:] = ["    " + l for l in lineas[1:]]
        salida += lineas
    return "\n".join(salida)


def _dispositivos_de_miembros(ruta_gx3: str, nombres: dict) -> list[str]:
    """Dispositivos asignados a mano a miembros de estructuras (ej. Linea1.Motor1.Arranque=M0)."""
    with _abrir(ruta_gx3) as z, tempfile.TemporaryDirectory() as d:
        if "LabelData.db" not in z.namelist():
            return []
        c = sqlite3.connect(z.extract("LabelData.db", d))
        try:
            capa = {i: (p, f"_lid/{l}/{r}") for i, p, l, r in
                    c.execute("select InstanceID, ParentInstanceID, LabelID, RowID from InstanceLayerTbl")}
            filas = c.execute("select InstanceID, MELSECDevice from DeviceAssignTbl where ManualSet = 1 "
                              "and not (LabelID = RelatedLabelID and RowID = RelatedRowID) order by MELSECDevice").fetchall()
        finally:
            c.close()
    salida = []
    for inst, dev in filas:
        ruta, i = [], inst
        while i in capa and len(ruta) < 20:  # sube por la jerarquia hasta la etiqueta raiz
            padre, lid = capa[i]
            ruta.append(nombres.get(lid, lid))
            i = padre
        salida.append(f"{'.'.join(reversed(ruta))} = {dev}")
    return sorted(salida, key=lambda s: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.split(" = ")[1])])


@mcp.tool()
def leer_etiquetas(ruta_gx3: str) -> str:
    """Lista etiquetas globales, estructuras (con miembros), etiquetas locales por programa/FB y los
    dispositivos asignados a miembros de estructuras. Omite estructuras de sistema y FUN estandar."""
    info = nombres_etiquetas(ruta_gx3)
    grupos: dict[str, list[str]] = {}
    for v in info.values():
        if v["pou"].startswith(("FUN", "ESTRUCTURA ?")) or not v["nombre"]:
            continue
        extra = "".join(f"  {k}={v[x]}" for k, x in (("dispositivo", "dispositivo_asignado"), ("comentario", "comentario"))
                        if v[x] and not v[x].startswith(("GV:", "LV:", "LLV:", "LLP")))
        clase = "" if v["clase"] == "MIEMBRO" else f"{v['clase']:<18}"
        grupos.setdefault(v["pou"], []).append(f"  {clase}{v['nombre']} : {v['tipo_dato']}{extra}")
    orden = sorted(grupos, key=lambda p: ({"GLOBAL": 0, "ESTRUCTURA": 1, "PROGRAMA": 2, "FB": 3}.get(p.split()[0], 4), p))
    salida = []
    for p in orden:
        salida += [f"== {p}"] + grupos[p]
    miembros = _dispositivos_de_miembros(ruta_gx3, {k: v["nombre"] for k, v in info.items()})
    if miembros:
        salida += ["== DISPOSITIVOS DE MIEMBROS"] + [f"  {m}" for m in miembros]
    return "\n".join(salida) or "El proyecto no tiene etiquetas de usuario."


_PALABRAS = {"D", "R", "W", "SD"}  # dispositivos de palabra validos como operando
_COMPARA = ("<>", ">=", "<=", "=", ">", "<")  # contactos de comparacion (LD=, LD>...); con prefijo E: REAL (LDE>=)
# instruccion -> (ct, tipos 'as', clase de cada operando). Ancho en celdas = 1 + operandos. Formatos copiados de
# proyectos reales: p = palabra o K, r = REAL (D/R o constante E), d = cualquier dispositivo (bit o palabra)
_INSTRUCCIONES = {
    "MOV": ("a", ["A16", "A16"], "pp"),
    "INCP": ("p", ["A16s"], "p"),
    "EMOV": ("a", ["Ar32", "Ar32"], "rr"),
    "ZRST": ("a", ["Aea", "Aea"], "dd"),
    "BMOV": ("a", ["A16", "A16", "A16"], "ppp"),  # BMOV origen destino cantidad
}
_EJEMPLOS = "'MOV K0 D10', 'INCP D10', 'EMOV E1.5 D20', 'ZRST M0 M15', 'BMOV D0 D100 K8'"


def _operando_w(texto: str, clase: str) -> tuple[list[str], str, bool]:
    """Operando de instruccion o comparacion -> (cadenas, nodo, es_constante)."""
    t = texto.strip().upper()
    if clase in "pr" and re.fullmatch(r"K\d+", t):
        if clase == "r":
            raise ToolError(f"{texto}: para un valor REAL usa una constante E, ej. E1.5")
        # shortcut: solo K positivas (0..32767); negativas y H sin ejemplo real verificado
        if int(t[1:]) > 32767:
            raise ToolError(f"{texto}: la constante K debe estar entre 0 y 32767.")
        return ["K_1"], f"c{{s=#:v={int(t[1:])}:si=s}}", True
    if clase == "r" and re.fullmatch(r"E-?\d+(\.\d+)?", t):
        bits = struct.pack(">f", float(t[1:])).hex().upper()  # GX Works3 guarda los bits IEEE-754 en hexadecimal
        return ["E_2n", t[1:]], f"c{{s=#:v={bits}:t=#}}", True
    validos = _PALABRAS | _CONTACTOS if clase == "d" else {"D", "R"} if clase == "r" else _PALABRAS
    tipo, num = _dispositivo(t, validos)
    return [tipo], f"d{{s=#:a={num}:vt=nn}}", False


def _contacto(texto: str) -> tuple[list[str], str, int]:
    """'X0' NA, '/X0' NC, '↑X0' flanco subida, '↓X0' bajada, '>= D10 K100' comparacion,
    'E>= D10 E17.5' comparacion REAL -> (cadenas, s, celdas)."""
    t = texto.strip()
    partes = t.split()
    if len(partes) == 3 and partes[0].upper().lstrip("E") in _COMPARA:
        real = partes[0].upper().startswith("E")
        clase, vt = ("r", "Ar32") if real else ("p", "A16s")
        (c1, n1, _), (c2, n2, _) = _operando_w(partes[1], clase), _operando_w(partes[2], clase)
        s = f"ce{{op=ct{{op=#:ct=a:as=[as{{vt={vt}}}:as{{vt={vt}}}]}}:args=[{n1}:{n2}]}}"
        return [partes[0].upper().lstrip("E")] + c1 + c2, s, 3
    flanco = {"↑": "p", "↓": "f"}.get(t[:1], "a")
    if flanco != "a":
        t = t[1:]
    nc = t.startswith("/")
    if nc and flanco != "a":
        raise ToolError(f"'{texto}': flanco con contacto NC (LDPI/LDFI) no esta soportado.")
    tipo, num = _dispositivo(t.lstrip("/"), _CONTACTOS)
    s = f"ce{{op=ct{{op=#:ct={flanco}:as=[as{{vt=Abl}}]}}:args=[d{{s=#:a={num}:vt=nn}}]}}"
    return ["b" if nc else "a", tipo], s, 1


def _salida(texto: str) -> tuple[list[str], str, int]:
    """Bobina/instruccion de salida -> (cadenas, s, celdas)."""
    partes = texto.upper().split()
    if len(partes) == 3 and partes[0] == "OUT" and re.fullmatch(r"K\d+", partes[2]):
        # timer: OUT T0 K30 (FX5: T cuenta en 100 ms -> K30 = 3 s). shortcut: solo T y preset K; contadores sin verificar
        tipo, num = _dispositivo(partes[1], {"T"})
        k = int(partes[2][1:])
        if not 0 < k <= 32767:
            raise ToolError("La constante K del timer debe estar entre 1 y 32767.")
        s = (f"ce{{op=cl{{op=#:ct=a:as=[as{{vt=Abl}}:as{{vt=A16}}]}}:"
             f"args=[d{{s=#:a={num}:vt=nn}}:c{{s=#:v={k}:si=s}}]}}")
        return ["OUT__16", tipo, "K_1"], s, 3
    if partes and partes[0] in _INSTRUCCIONES:
        ct, tipos, clases = _INSTRUCCIONES[partes[0]]
        if len(partes) - 1 != len(tipos):
            raise ToolError(f"{partes[0]} lleva {len(tipos)} operando(s). Ejemplos: {_EJEMPLOS}.")
        ops = [_operando_w(p, c) for p, c in zip(partes[1:], clases)]
        destino = 1 if partes[0] == "BMOV" else -1
        if ops[destino][2] or (partes[0] == "ZRST" and any(o[2] for o in ops)):
            raise ToolError(f"{texto}: el destino no puede ser una constante.")
        if partes[0] == "ZRST" and ops[0][0][0] != ops[1][0][0]:
            raise ToolError(f"{texto}: ZRST necesita los dos extremos del mismo tipo de dispositivo.")
        s = (f"ce{{op=cl{{op=#:ct={ct}:as=[{':'.join(f'as{{vt={v}}}' for v in tipos)}]}}:"
             f"args=[{':'.join(n for _, n, _ in ops)}]}}")
        return [partes[0]] + [c for cad, _, _ in ops for c in cad], s, 1 + len(tipos)
    if len(partes) == 1:
        op, celdas = "c", 1  # bobina OUT
    elif len(partes) == 2 and partes[0] in ("SET", "RST"):
        op, celdas = partes[0], 2
    else:
        raise ToolError(f"salida: 'Y0', 'SET M0', 'RST M0', 'OUT T0 K30', {_EJEMPLOS}.")
    tipo, num = _dispositivo(partes[-1], _BOBINAS)
    s = f"ce{{op=cl{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[d{{s=#:a={num}:vt=nn}}]}}"
    return [op, tipo], s, celdas


def _renglon(columnas: list[list[str]], salida: str) -> tuple[str, int]:
    """Devuelve (data, filas). columnas en serie (AND); cada una con contactos en paralelo (OR), en cualquier columna.

    Una rama OR que empieza en la columna x y mide w celdas lleva lineas verticales en x (abre, si x > 0)
    y en x + w (cierra), como en los renglones reales: |-[Y1]-+-[/M505]-+-(Y2)  ->  v{pos=1,1}, v{pos=2,1}.
    """
    if not columnas or any(not col for col in columnas):
        raise ToolError("columnas necesita al menos una columna y ninguna vacia, ej. [['X0','Y0'],['X1']]")
    cadenas, elementos, verticales, x = [], [], set(), 0
    for col in columnas:
        hechos = [_contacto(t) for t in col]
        ancho_col = max(h[2] for h in hechos)
        for y, (cad, s, celdas) in enumerate(hechos):
            cadenas += cad
            elementos.append(f"e{{s={s}:pos={x},{y}}}")
            # rama mas corta que la columna: lineas horizontales explicitas hasta la union
            elementos += [f"e{{s=-:pos={x + k},{y}}}" for k in range(celdas, ancho_col)]
        for y in range(1, len(col)):
            if x > 0:
                verticales.add((x, y))
            verticales.add((x + ancho_col, y))
        x += ancho_col
    cad, s, celdas = _salida(salida)
    cadenas += cad
    elementos.append(f"e{{s={s}:pos={x},0}}")
    filas = max(len(col) for col in columnas)
    vs = (":vs=[" + ":".join(f"v{{pos={c},{y}}}" for c, y in sorted(verticales)) + "]") if verticales else ""
    arbol = f"cb{{fg=fg{{dim={x + celdas}x{filas}:es=[{':'.join(elementos)}]{vs}}}}}"
    return _bloque(cadenas, arbol), filas


def _renglones(c) -> list[tuple]:
    """Renglones (blocktype 0 normal, 4 FB) en orden, igual que la numeracion R1, R2... de leer_ld."""
    return c.execute("select id, pos from LadderBlocks where blocktype in (0, 4) order by pos").fetchall()


def _anterior(c, pos: float):
    return c.execute("select id, pos, blocktype from LadderBlocks where pos < ? order by pos desc limit 1",
                     (pos,)).fetchone()


@mcp.tool()
def agregar_renglon_ld(ruta_gx3: str, programa: str, columnas: list[list[str]], salida: str,
                       comentario: str, ruta_salida: str, antes_de_renglon: int = 0) -> str:
    """Agrega un renglon ladder con un comentario encima. Por defecto al final (antes de END).

    columnas: grupos en serie (AND); cada grupo lista contactos en paralelo (OR, solo en el primer grupo).
      Contactos: 'X0' NA, '/X0' NC, '↑X0' flanco de subida, '↓X0' flanco de bajada,
      '>= D10 K100' comparacion (=, <>, >, <, >=, <=; operandos D/R/W/SD o K).
      Solo dispositivos directos (X0, M10, D5...), no etiquetas.
      Ej. arranque/paro con enclavamiento: columnas=[["X0","Y0"],["X1"]], salida="Y0"
    salida: "Y0" (bobina), "SET M0", "RST M0", "OUT T0 K30" (timer, 100 ms -> K30 = 3 s),
      "MOV K0 D10", "INCP D10" (+1 en flanco de subida).
    antes_de_renglon: numero R de leer_ld; el nuevo queda antes de ese renglon y de su comentario. 0 = al final.
    El renglon queda sin convertir: abrir en GX Works3 y hacer Convertir todo.
    """
    datos, filas = _renglon(columnas, salida)

    def cambiar(c):
        if antes_de_renglon:
            rs = _renglones(c)
            if not 1 <= antes_de_renglon <= len(rs):
                raise ToolError(f"No existe el renglon R{antes_de_renglon}; el programa tiene {len(rs)}.")
            fin = rs[antes_de_renglon - 1][1]
            previo = _anterior(c, fin)
            if previo and previo[2] == 1:  # su comentario se queda pegado a el
                fin = previo[1]
        else:
            fin = c.execute("select min(pos) from LadderBlocks where data like '%end{type=end%'").fetchone()[0]
            if fin is None:
                raise ToolError("El programa no tiene END; no se toco.")
        ultimo = c.execute("select coalesce(max(pos), 0) from LadderBlocks where pos < ?", (fin,)).fetchone()[0]
        paso = min(1024, (fin - ultimo) / 3)  # dos bloques nuevos entre el anterior y el destino
        nuevos = []
        if comentario.strip():
            nuevos.append((1, 1, 1, _bloque([comentario.strip()], "st{st=c{t=#:st=s}:dim=0x1}")))
        nuevos.append((0, filas, 0, datos))  # translated=0: pendiente de convertir
        for i, (tipo, alto, traducido, data) in enumerate(nuevos, 1):
            c.execute("insert into LadderBlocks (id, pos, blocktype, data, rowsize, translated, ConvTarget) "
                      "values (?, ?, ?, ?, ?, ?, 0)",
                      (f"_guid/{uuid.uuid4()}", ultimo + paso * i, tipo, data, alto, traducido))

    return _guardar_cambio(ruta_gx3, programa, "_LDDB.db", cambiar, ruta_salida)


@mcp.tool()
def borrar_renglon_ld(ruta_gx3: str, programa: str, renglon: int, ruta_salida: str) -> str:
    """Borra el renglon R<renglon> (numeracion de leer_ld) y el comentario que tiene justo encima.

    Leer antes con leer_ld para confirmar el numero. Despues: Convertir todo en GX Works3.
    """
    def cambiar(c):
        rs = _renglones(c)
        if not 1 <= renglon <= len(rs):
            raise ToolError(f"No existe el renglon R{renglon}; el programa tiene {len(rs)}.")
        ids = [rs[renglon - 1][0]]
        previo = _anterior(c, rs[renglon - 1][1])
        if previo and previo[2] == 1:
            ids.append(previo[0])
        c.executemany("delete from LadderBlocks where id = ?", [(i,) for i in ids])
        # su lista de instrucciones (IL) ya convertida; GX Works3 la regenera al convertir
        if "mil" in [r[1] for r in c.execute("pragma database_list")]:
            c.executemany("delete from mil.MIL where id = ?", [(i,) for i in ids])

    return _guardar_cambio(ruta_gx3, programa, "_LDDB.db", cambiar, ruta_salida, extra="_MilDB.db")


# ---------------- Analisis: recorrido comun de usos (referencia cruzada, revision, monitor) ----------------
_PAL = {"D", "R", "SD", "TN", "CN"}
# shortcut: sin B/W (numeracion hex): se omiten en el monitor y en el ST hasta verificar su formato en ladder
_DISP = re.compile(r"(SM|SD|TS|TN|CS|CN|X|Y|M|L|F|D|R|T|C)(\d+)")
_CONSTANTE = re.compile(r"[KHE]-?[\d.]+")
# instruccion -> indices de operandos que ESCRIBE (el resto se leen). shortcut: lista corta; el resto se marca "operando"
_DESTINOS = {"MOV": [-1], "MOVP": [-1], "DMOV": [-1], "DMOVP": [-1], "EMOV": [-1], "EMOVP": [-1],
             "BMOV": [1], "BMOVP": [1], "FMOV": [1], "FMOVP": [1], "ZRST": [0, 1], "ZRSTP": [0, 1],
             "INC": [0], "INCP": [0], "DEC": [0], "DECP": [0], "DINC": [0], "DINCP": [0]}
_ESCRIBE = {"bobina", "SET", "RST", "OUT timer", "destino", "escritura ST"}


def _es_palabra(texto: str) -> bool:
    m = _DISP.fullmatch(texto)
    return bool(m) and m.group(1) in _PAL


def _clave_plc(texto: str, palabra: bool, alias: dict) -> str | None:
    """Operando -> dispositivo legible por SLMP/Modbus. T/C: contacto (TS/CS) o valor actual (TN/CN)."""
    m = _DISP.fullmatch(alias.get(texto, texto))
    if not m:
        return None
    tipo, num = m.groups()
    if tipo in ("T", "C"):
        tipo = tipo + ("N" if palabra else "S")
    return f"{tipo}{num}"


def _usos_elemento(s: dict, etiquetas: dict):
    """(operando, rol) de un elemento ladder ce{...}. Omite constantes."""
    op = s["op"]
    nombre = op["op"]
    args = [_operando(a, etiquetas) for a in s.get("args", [])]
    if op["_"] == "ct":
        rol = {"a": "contacto NA", "b": "contacto NC"}.get(nombre, "comparacion")
        if nombre in ("a", "b") and op.get("ct") in ("p", "f"):
            rol = "flanco " + ("subida" if op["ct"] == "p" else "bajada")
        roles = [rol] * len(args)
    elif nombre == "c":
        roles = ["bobina"]
    elif nombre in ("SET", "RST"):
        roles = [nombre]
    elif nombre == "OUT__16":
        roles = ["OUT timer", "lectura"]
    else:
        destinos = {i % len(args) for i in _DESTINOS.get(nombre, [])} if args else set()
        roles = ["destino" if i in destinos else ("fuente" if nombre in _DESTINOS else "operando")
                 for i in range(len(args))]
    for a, rol in zip(args, roles):
        if not _CONSTANTE.fullmatch(a):
            yield a, rol


def _usos_bloque(arbol: dict, etiquetas: dict):
    if arbol.get("_") == "cb":
        circuitos = [arbol["fg"]]
    elif arbol.get("_") == "fbcs":
        circuitos = [p["fg"] for p in arbol.get("ig", []) if p["_"] == "cbag"]
        for p in arbol.get("ig", []):
            if p["_"] == "vag":
                yield _operando(p["v"], etiquetas), "argumento FB"
    else:
        return
    for fg in circuitos:
        for e in fg.get("es", []):
            if isinstance(e["s"], dict) and e["s"].get("_") == "ce":
                yield from _usos_elemento(e["s"], etiquetas)


def _usos(ruta_gx3: str, programa: str = "") -> tuple[list[tuple], dict, dict, int]:
    """Todos los usos del proyecto: [(operando, programa, lugar, rol)], info de etiquetas, ST por programa, renglones."""
    info = nombres_etiquetas(ruta_gx3)
    nombres = {k: v["nombre"] or k for k, v in info.items()}
    usos, st, n_renglones = [], {}, 0
    with _abrir(ruta_gx3) as z, tempfile.TemporaryDirectory() as d:
        for n in z.namelist():
            m = re.fullmatch(r"([0-9a-f]{16})_(LDDB|STDB)\.db", n)
            if not m or (programa and m.group(1) != programa):
                continue
            c = sqlite3.connect(z.extract(n, d))
            try:
                if m.group(2) == "LDDB":
                    filas = c.execute("select data from LadderBlocks where blocktype in (0, 4) order by pos").fetchall()
                else:
                    st[m.group(1)] = "\n".join(t for (t,) in c.execute("select data from StBlocks order by pos"))
                    continue
            finally:
                c.close()
            for i, (data,) in enumerate(filas, 1):
                n_renglones += 1
                try:
                    cad, texto = _separar(data)
                    usos += [(a, m.group(1), f"R{i}", rol) for a, rol in _usos_bloque(_arbol(texto, cad), nombres)]
                except Exception:  # un bloque raro no debe tapar el resto
                    continue
    # ST: dispositivos y etiquetas por linea; antes de ':=' es escritura
    etiq = {v["nombre"] for v in info.values() if v["nombre"] and v["clase"] != "MIEMBRO"}
    for prog, texto in st.items():
        for num, linea in enumerate(texto.replace("\r\n", "\n").split("\n"), 1):
            codigo = re.sub(r"//.*|\(\*.*?\*\)", "", linea)
            izq = codigo.split(":=")[0] if ":=" in codigo else ""
            for t in re.findall(r"[A-Za-z_][\w.]*(?:\[[^\]]*\])?", codigo):
                base = t.split("[")[0]
                if _DISP.fullmatch(base) or base.split(".")[0] in etiq:
                    rol = "escritura ST" if izq and re.search(rf"(?<![\w.]){re.escape(t)}(?![\w.])", izq) else "lectura ST"
                    usos.append((t, prog, f"L{num}", rol))
    return usos, info, st, n_renglones


def _orden_natural(texto: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", texto)]


@mcp.tool()
def referencia_cruzada(ruta_gx3: str, filtro: str = "") -> str:
    """Donde se usa cada dispositivo o etiqueta en todo el proyecto (ladder y ST), con su rol:
    contacto NA/NC, flanco, comparacion, bobina, SET, RST, OUT timer, fuente, destino, argumento FB, lectura/escritura ST.

    filtro: texto o prefijo para limitar la salida (ej. "Y", "D10", "Motor"). Lugar: R<n> renglon, L<n> linea ST.
    """
    usos, _, _, _ = _usos(ruta_gx3)
    grupos: dict[str, list[str]] = {}
    for operando, prog, lugar, rol in usos:
        if filtro and not operando.upper().startswith(filtro.upper()):
            continue
        grupos.setdefault(operando, []).append(f"{prog} {lugar} {rol}")
    if not grupos:
        return "Sin usos" + (f" que empiecen con '{filtro}'." if filtro else ".")
    salida = []
    for operando in sorted(grupos, key=_orden_natural):
        salida.append(f"{operando}")
        salida += [f"    {u}" for u in sorted(set(grupos[operando]), key=_orden_natural)]
    return "\n".join(salida)


_FB_TIEMPO = re.compile(r"^(TON|TOF|TP|CTU|CTD|CTUD|TIMER_\w+|COUNTER_\w+)(_10)?$")


@mcp.tool()
def revisar_ld(ruta_gx3: str, programa: str = "") -> str:
    """Revisa el proyecto (o un programa): dobles bobinas, OUT mezclado con SET/RST, SET sin RST, timers usados
    sin su OUT, salidas Y leidas pero nunca escritas, y timers/contadores FB llamados dentro de IF en ST
    (precaucion del manual: si la condicion es FALSE no se ejecutan)."""
    usos, info, st, n_renglones = _usos(ruta_gx3, programa)
    globales = {v["nombre"] for v in info.values() if v["pou"] == "GLOBAL"}
    por = {}
    for operando, prog, lugar, rol in usos:
        base = re.split(r"[.\[]", operando)[0]
        # dispositivos y globales son de todo el proyecto; una local con el mismo nombre en otro programa es otra variable
        clave = operando if _DISP.fullmatch(base) or base in globales else f"{prog[:6]}:{operando}"
        por.setdefault(clave, []).append((prog, lugar, rol))

    # rangos borrados de golpe (ZRST a[K0] a[K15]): dos destinos del mismo arreglo en el mismo renglon
    rangos = {}
    for operando, prog, lugar, rol in usos:
        m = re.fullmatch(r"(.+)\[K(\d+)\]", operando)
        if rol == "destino" and m:
            rangos.setdefault((prog, lugar, m.group(1)), []).append(int(m.group(2)))

    def borrado_en_rango(clave):
        m = re.fullmatch(r"(.+)\[K(\d+)\]", clave)
        return bool(m) and any(b.endswith(base) and min(i) <= int(m.group(2)) <= max(i)
                               for (_, _, base), i in rangos.items() if len(i) == 2
                               for b in [m.group(1)])

    def donde(op, roles):
        return ", ".join(f"{p[:6]}:{l}" for p, l, r in por.get(op, []) if r in roles)

    avisos = []
    for op, lista in por.items():
        bobinas = [x for x in lista if x[2] in ("bobina", "OUT timer")]
        if len(bobinas) > 1:
            avisos.append(f"DOBLE BOBINA {op}: OUT en {donde(op, ('bobina', 'OUT timer'))} (solo cuenta el ultimo)")
        roles = {r for _, _, r in lista}
        if bobinas and roles & {"SET", "RST"}:
            avisos.append(f"MEZCLA {op}: OUT en {donde(op, ('bobina', 'OUT timer'))} y SET/RST en {donde(op, ('SET', 'RST'))}")
        if "SET" in roles and not roles & {"RST", "bobina", "destino", "escritura ST", "argumento FB"} \
                and not borrado_en_rango(op):
            avisos.append(f"SET SIN RST {op}: se activa en {donde(op, ('SET',))} y nada lo desactiva")
        if re.fullmatch(r"Y[0-7]+", op) and not roles & (_ESCRIBE | {"argumento FB"}):  # un pin de salida de FB tambien escribe
            avisos.append(f"SALIDA SIN ESCRIBIR {op}: se lee en {donde(op, roles)} pero ningun renglon ni ST la escribe "
                          "(revisar si la escribe un equipo externo por red o si falta su bobina)")
        m = re.fullmatch(r"T(\d+)", op)
        if m and "OUT timer" not in roles and not any(r == "escritura ST" for r in roles):
            avisos.append(f"TIMER SIN ARRANCAR {op}: se usa en {donde(op, roles)} pero no hay OUT {op}")
    # ST: timers/contadores FB dentro de IF (cap. 6 del manual de programacion)
    instancias = {v["nombre"] for v in info.values() if _FB_TIEMPO.match(v["tipo_dato"] or "")}
    for prog, texto in st.items():
        nivel = 0
        for num, linea in enumerate(texto.replace("\r\n", "\n").split("\n"), 1):
            codigo = re.sub(r"//.*|\(\*.*?\*\)", "", linea).upper()
            for inst in instancias:
                if nivel > 0 and re.search(rf"\b{re.escape(inst.upper())}\s*\(", codigo):
                    avisos.append(f"FB DE TIEMPO EN IF {inst}: {prog[:6]}:L{num} (si la condicion es FALSE no se ejecuta; "
                                  "llamalo fuera del IF y pasa la condicion como entrada)")
            nivel += len(re.findall(r"\bIF\b", codigo)) - len(re.findall(r"\bEND_IF\b", codigo))  # \bIF\b no cuenta END_IF ni ELSIF
            nivel = max(nivel, 0)
    lineas_st = sum(t.count("\n") + 1 for t in st.values())
    return "\n".join(sorted(set(avisos))) or (
        f"Sin problemas en {n_renglones} renglones y {lineas_st} lineas ST (dobles bobinas, OUT+SET/RST, SET sin RST, "
        "timers sin OUT, salidas sin escribir, FB de tiempo en IF).")


def _leer_dispositivos(ip: str, puerto: int, dispositivos: list[str], protocolo: str) -> list[list[int]]:
    cliente = {"slmp": plc_slmp, "modbus": plc_modbus}.get(protocolo.lower())
    if not cliente:
        raise ToolError("protocolo debe ser 'slmp' o 'modbus'.")
    try:
        return cliente.leer(ip, puerto, dispositivos)
    except ValueError as e:
        raise ToolError(str(e))
    except (OSError, ConnectionError) as e:
        raise ToolError(f"No se pudo leer el PLC en {ip}:{puerto} por {protocolo}: {e}. "
                        f"Verifica que el PLC tenga {protocolo.upper()} habilitado en ese puerto.")


@mcp.tool()
def leer_plc(ip: str, puerto: int, dispositivos: list[str], protocolo: str = "slmp") -> str:
    """Lee en vivo dispositivos de un PLC FX5. SOLO LECTURA.

    protocolo: "slmp" (3E binario TCP; "SLMP Connection Module" con codigo Binary) o
      "modbus" (Modbus/TCP, normalmente puerto 502; "MODBUS/TCP Connection Module", asignacion inicial FX5).
    dispositivos: ej. ["D10", "Y0:8", "M0:4", "X0:16", "TN0"]; 'nombre:cantidad' lee varios seguidos.
    X/Y en octal como en GX Works3.
    """
    lecturas = _leer_dispositivos(ip, puerto, dispositivos, protocolo)
    salida = []
    for disp, valores in zip(dispositivos, lecturas):
        tipo, inicio = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", disp.strip().upper().split(":")[0]).groups()
        inicio = int(inicio, {"X": 8, "Y": 8, "B": 16, "W": 16}.get(tipo, 10))
        base = {"X": "o", "Y": "o", "B": "X", "W": "X"}.get(tipo, "d")  # mostrar como GX Works3
        salida += [f"{tipo}{format(inicio + i, base)} = {v}" for i, v in enumerate(valores)]
    return "\n".join(salida)


@mcp.tool()
def monitorear_ld(ruta_gx3: str, programa: str, ip: str, puerto: int, protocolo: str = "slmp") -> str:
    """Dibuja el ladder con el estado EN VIVO del PLC (como el monitor de GX Works3). SOLO LECTURA.

    ■ contacto que conduce / bobina activa, □ no conduce / inactiva; comparaciones e instrucciones muestran valores.
    Etiquetas: se leen si tienen dispositivo asignado (global o miembro de estructura); si no, van sin marca.
    El proyecto debe ser el mismo que esta cargado en el PLC.
    """
    usos, info, _, _ = _usos(ruta_gx3, programa)
    alias = {v["nombre"]: v["dispositivo_asignado"] for v in info.values()
             if v["dispositivo_asignado"] and not v["dispositivo_asignado"].startswith(("GV:", "LV:", "LLV:", "LLP"))}
    alias.update(dict(m.split(" = ") for m in _dispositivos_de_miembros(ruta_gx3, {k: v["nombre"] for k, v in info.items()})))
    pedidos = {}  # (operando, es_palabra) -> dispositivo PLC
    for operando, _, lugar, rol in usos:
        if lugar.startswith("R"):
            palabra = rol in ("comparacion", "fuente", "destino", "operando", "lectura", "argumento FB", "OUT timer") \
                or _es_palabra(alias.get(operando, operando))
            clave = _clave_plc(operando, palabra, alias)
            if clave:
                pedidos[(operando, palabra)] = clave
                if rol in ("SET", "RST", "bobina", "contacto NA", "contacto NC"):
                    pedidos[(operando, False)] = _clave_plc(operando, False, alias)
    # shortcut: una peticion por dispositivo; agrupar rangos contiguos si un programa grande resulta lento
    unicos = sorted(set(pedidos.values()), key=_orden_natural)
    lecturas = dict(zip(unicos, (v[0] for v in _leer_dispositivos(ip, puerto, unicos, protocolo)))) if unicos else {}
    valores = {k: lecturas[d] for k, d in pedidos.items()}
    with _abrir(ruta_gx3) as z, tempfile.TemporaryDirectory() as d:
        c = sqlite3.connect(z.extract(f"{programa}_LDDB.db", d))
        try:
            bloques = c.execute("select blocktype, data from LadderBlocks order by pos").fetchall()
        finally:
            c.close()
    nombres = {k: v["nombre"] or k for k, v in info.items()}
    clases = {k: v["clase"] for k, v in info.items()}
    salida, n = [f"Monitor {ip}:{puerto} ({protocolo}) - {len(unicos)} dispositivos leidos. ■ conduce/activo  □ no"], 0
    for tipo, data in bloques:
        try:
            lineas = _leer_bloque(data, nombres, clases, valores)
        except Exception as e:
            lineas = [f"(no se pudo leer este bloque: {e})"]
        if tipo in (0, 4):
            n += 1
            lineas[0] = f"R{n:<3}" + lineas[0]
            lineas[1:] = ["    " + l for l in lineas[1:]]
        salida += lineas
    return "\n".join(salida)


@mcp.tool()
def estado_plc(ip: str, puerto: int, protocolo: str = "slmp") -> str:
    """Diagnostico del PLC FX5 (SOLO LECTURA): estado de la CPU, error y su hora, bateria, caida de alimentacion,
    tiempos de ciclo y reloj. SM/SD segun los comentarios de sistema de GX Works3."""
    (cpu,), (sm0, sm1), err, (sm51, sm52, sm53), reloj, ciclo = _leer_dispositivos(
        ip, puerto, ["SD203", "SM0:2", "SD0:7", "SM51:3", "SD210:7", "SD520:6"], protocolo)
    # shortcut: solo RUN=0 esta verificado en vivo; 2 STOP y 3 PAUSE segun la convencion iQ-F (confirmar en STOP)
    estado = {0: "RUN", 2: "STOP", 3: "PAUSE"}.get(cpu & 0x0F, f"desconocido ({cpu})")
    ms = lambda i: ciclo[i] + ciclo[i + 1] / 1000  # SD520 ms + SD521 us, etc.
    lineas = [
        f"CPU: {estado} (SD203={cpu})",
        f"Reloj del PLC: {reloj[0]:04d}-{reloj[1]:02d}-{reloj[2]:02d} {reloj[3]:02d}:{reloj[4]:02d}:{reloj[5]:02d}",
        f"Ciclo: actual {ms(0):.3f} ms, minimo {ms(2):.3f} ms, maximo {ms(4):.3f} ms",
    ]
    if sm0 or sm1:
        lineas.append(f"ERROR detectado (SM0={sm0}, SM1={sm1}): codigo {err[0] & 0xFFFF:04X}H "
                      f"a las {err[1]:04d}-{err[2]:02d}-{err[3]:02d} {err[4]:02d}:{err[5]:02d}:{err[6]:02d} "
                      "(ver el codigo en el Diagnostico de CPU de GX Works3)")
    else:
        lineas.append("Sin error (SM0=0, SM1=0)")
    lineas.append("Bateria: " + ("BAJA ahora (SM52)" if sm52 else "BAJA en algun momento (SM51)" if sm51 else "OK"))
    if sm53:
        lineas.append("Hubo caida de alimentacion AC/DC (SM53)")
    return "\n".join(lineas)


# Modbus RTU por canal (FX5 User's Manual (Communication) p557-564, p863-865; numeros confirmados con los
# comentarios de sistema de GX Works3). Canal n usa la base + 10*(n-1).
_MODOS = {0: "MELSOFT/MC", 3: "N:N", 5: "sin protocolo", 6: "enlace paralelo", 7: "inversor", 9: "MODBUS RTU",
          12: "protocolo predefinido"}
_ERRORES_RTU = {
    0x7001: "el canal lo usa una comunicacion distinta de MODBUS RTU", 0x7010: "error de paridad, overrun o framing",
    0x7304: "error de CRC", 0x7305: "overrun: se recibieron 256 bytes o mas", 0x7306: "longitud recibida incorrecta",
    0x7307: "codigo de funcion no soportado", 0x7308: "direccion de dispositivo invalida",
    0x7309: "TIMEOUT: el esclavo no respondio a tiempo (cableado, n.o de esclavo, velocidad/paridad o esclavo apagado)",
    0x730A: "respuesta de excepcion del esclavo", 0x730B: "respondio otro n.o de esclavo",
    0x730C: "la respuesta trae otro codigo de funcion", 0x730D: "lectura enviada como broadcast",
    0x730E: "valor fuera de rango en la peticion", 0x7311: "puerto duplicado con otra funcion"}
_EXCEPCIONES = {1: "funcion ilegal", 2: "direccion no asignada en el esclavo", 3: "dato ilegal en la peticion"}
_BAUDIOS = {3: 300, 4: 600, 5: 1200, 6: 2400, 7: 4800, 8: 9600, 9: 19200, 10: 38400, 11: 57600, 13: 115200}


def _formato_serie(v: int) -> str:
    """SD8502: b1-b2 paridad (00 ninguna, 01 impar, 11 par), b3 parada, b4-b7 baudios (p562)."""
    paridad = {0: "sin paridad", 1: "impar", 3: "par"}.get((v >> 1) & 3, "?")
    return f"{_BAUDIOS.get((v >> 4) & 0xF, '?')} bps, 8 bits, {paridad}, {2 if v >> 3 & 1 else 1} bit de parada"


@mcp.tool()
def diagnosticar_modbus_rtu(ip: str, puerto: int, canal: int = 0, protocolo: str = "slmp") -> str:
    """Diagnostico de los canales serie MODBUS RTU (ADPRW) del FX5, SOLO LECTURA: modo del canal, formato
    (bps/paridad), comunicacion en curso, reintentos, timeouts y el ultimo error traducido.

    canal: 1-4 (CH1 RS-485 integrado, CH2 placa, CH3/CH4 adaptadores); 0 = los cuatro.
    """
    canales = [canal] if canal else [1, 2, 3, 4]
    if not all(1 <= c <= 4 for c in canales):
        raise ToolError("canal debe ser 1, 2, 3, 4 o 0 (todos).")
    pedidos = []
    for c in canales:
        o = 10 * (c - 1)
        pedidos += [f"SM{8800 + o}:3", f"SM{8500 + o}", f"SD{8500 + o}:4", f"SD{8800 + o}", f"SD{8861 + o}:5"]
    lecturas = _leer_dispositivos(ip, puerto, pedidos, protocolo)
    salida = []
    for i, c in enumerate(canales):
        (comunicando, reintento, timeout), (err_serie,), (codigo, detalle, formato, modo), (reint_act,), conf = \
            lecturas[5 * i:5 * i + 5]
        codigo, detalle = codigo & 0xFFFF, detalle & 0xFFFF
        if modo == -1:  # FFFFH: visto en vivo en canales sin placa/adaptador instalado
            salida.append(f"CH{c}: sin placa o adaptador instalado (SD{8503 + 10 * (c - 1)}=FFFFH)")
            continue
        lineas = [f"CH{c}: modo {_MODOS.get(modo, f'desconocido ({modo})')} (SD{8503 + 10 * (c - 1)}={modo})"]
        if modo != 9:
            salida += lineas + ["    no esta configurado como MODBUS RTU (ADPRW no hace nada en este canal)"]
            continue
        estacion, t_resp, _, t_msg, reintentos = conf
        lineas.append(f"    formato: {_formato_serie(formato)}; estacion propia {estacion} ({'maestro' if estacion == 0 else 'esclavo'})")
        lineas.append(f"    timeout de respuesta {t_resp} ms, retardo entre mensajes {t_msg} ms, reintentos {reintentos}")
        lineas.append(f"    ahora: {'comunicando' if comunicando else 'en reposo'}"
                      + (", REINTENTANDO" if reintento else "") + (", TIMEOUT" if timeout else "")
                      + f"; reintentos en curso {reint_act}")
        if codigo or err_serie:
            texto = _ERRORES_RTU.get(codigo, "codigo no documentado")
            if codigo == 0x730A:
                texto += f": FC {detalle >> 8:02X}H, excepcion {detalle & 0xFF:02X}H ({_EXCEPCIONES.get(detalle & 0xFF, '?')})"
            elif codigo in (0x730B, 0x730C):
                texto += f" (pedido {detalle >> 8:02X}H, recibido {detalle & 0xFF:02X}H)"
            lineas.append(f"    ULTIMO ERROR {codigo:04X}H: {texto}"
                          + (" [queda registrado hasta OFF->ON, reset, STOP->RUN o SM50]" if err_serie else ""))
        else:
            lineas.append("    sin error registrado")
        salida += lineas
    return "\n".join(salida)


if __name__ == "__main__":
    mcp.run()
