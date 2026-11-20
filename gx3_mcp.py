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
import tempfile
import time
import uuid
import zipfile

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
        return n["s"].split("_")[0] + str(n["v"])  # 'K_1' + v=30 -> K30
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


def _celda(e: dict, etiquetas: dict) -> str:
    s = e["s"]
    if s == "-":  # linea horizontal explicita (ramas que llegan mas lejos que sus contactos)
        return "-"
    if not isinstance(s, dict) or s.get("_") != "ce":
        return f"[{s.get('_')}]"
    op = s["op"]
    args = [_operando(a, etiquetas) for a in s.get("args", [])]
    nombre, flanco = op["op"], _FLANCO.get(op.get("ct", "a"), "?")
    if op["_"] == "ct":
        if nombre in ("a", "b"):
            return f"[{'/' if nombre == 'b' else ''}{flanco}{args[0]}]"
        return f"[{nombre} {' '.join(args)}]"  # contacto de comparacion: [> D0 K10]
    if nombre == "c":
        return f"({args[0]})"
    nombre = nombre.replace("OUT__16", "OUT")
    if flanco == "↑" and not nombre.endswith("P"):  # INCP ya trae la P en el nombre
        nombre += "P"
    return f"[{nombre} {' '.join(args)}]"


def _dibujar(fg: dict, etiquetas: dict) -> list[str]:
    """Dibuja una rejilla fg{dim, es, vs} como lineas de texto. Las lineas horizontales son implicitas."""
    cols, filas = (int(x) for x in fg["dim"].split("x")) if "x" in fg.get("dim", "") else (1, 1)
    cols, filas = max(cols, 1), max(filas, 1)
    celdas = {}
    for e in fg.get("es", []):
        c, r = (int(x) for x in e["pos"].split(","))
        celdas[(c, r)] = _celda(e, etiquetas)
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


def _leer_bloque(data: str, etiquetas: dict, clases: dict | None = None) -> list[str]:
    """etiquetas: _lid -> nombre; clases: _lid -> clase (VAR_INPUT, VAR_OUTPUT...) para la direccion de pines de FB."""
    cadenas, texto = _separar(data)
    arbol = _arbol(texto, cadenas)
    t = arbol.get("_")
    if t == "st":
        return [f"// {arbol['st']['t']}"]
    if t == "end":
        return ["[END]"]
    if t == "cb":
        return _dibujar(arbol["fg"], etiquetas)
    if t == "fbcs":
        # llamada a FB: cbag = pin de bit alimentado por un circuito; vag = pin de valor con su argumento
        fb = arbol["fb"]
        lineas = [f"[FB {fb.get('dn')}  instancia {_operando({'_': 'l', 'id': fb.get('in')}, etiquetas)}]"]
        for pin in arbol.get("ig", []):
            nombre = _operando(pin["l"], etiquetas)
            flecha = "=>" if (clases or {}).get(pin["l"].get("id"), "").startswith("VAR_OUTPUT") else ":="
            if pin["_"] == "vag":
                lineas.append(f"    {nombre} {flecha} {_operando(pin['v'], etiquetas)}")
            else:
                circuito = _dibujar(pin["fg"], etiquetas)
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
_COMPARA = ("<>", ">=", "<=", "=", ">", "<")  # contactos de comparacion 16 bits con signo (LD=, LD>...)
# instruccion -> (ct, tipos de operando); ancho en celdas = 1 + operandos. Formatos copiados de proyectos reales.
_INSTRUCCIONES = {"MOV": ("a", ["A16", "A16"]), "INCP": ("p", ["A16s"])}


def _palabra(texto: str) -> tuple[str, str]:
    """Operando de palabra -> (cadena, nodo). K constante o dispositivo D/R/W/SD."""
    t = texto.strip().upper()
    if re.fullmatch(r"K\d+", t):
        # shortcut: solo K positivas (0..32767); negativas/H/E sin ejemplo real verificado
        if int(t[1:]) > 32767:
            raise ToolError(f"{texto}: la constante K debe estar entre 0 y 32767.")
        return "K_1", f"c{{s=#:v={int(t[1:])}:si=s}}"
    tipo, num = _dispositivo(t, _PALABRAS)
    return tipo, f"d{{s=#:a={num}:vt=nn}}"


def _contacto(texto: str) -> tuple[list[str], str, int]:
    """'X0' NA, '/X0' NC, '↑X0' flanco subida, '↓X0' bajada, '>= D10 K100' comparacion -> (cadenas, s, celdas)."""
    t = texto.strip()
    partes = t.split()
    if len(partes) == 3 and partes[0] in _COMPARA:
        (c1, n1), (c2, n2) = _palabra(partes[1]), _palabra(partes[2])
        s = f"ce{{op=ct{{op=#:ct=a:as=[as{{vt=A16s}}:as{{vt=A16s}}]}}:args=[{n1}:{n2}]}}"
        return [partes[0], c1, c2], s, 3
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
        ct, tipos = _INSTRUCCIONES[partes[0]]
        if len(partes) - 1 != len(tipos):
            raise ToolError(f"{partes[0]} lleva {len(tipos)} operando(s), ej. 'MOV K0 D10' o 'INCP D10'.")
        ops = [_palabra(p) for p in partes[1:]]
        if ops[-1][0] == "K_1":
            raise ToolError(f"{texto}: el destino no puede ser una constante.")
        s = (f"ce{{op=cl{{op=#:ct={ct}:as=[{':'.join(f'as{{vt={v}}}' for v in tipos)}]}}:"
             f"args=[{':'.join(n for _, n in ops)}]}}")
        return [partes[0]] + [c for c, _ in ops], s, 1 + len(tipos)
    if len(partes) == 1:
        op, celdas = "c", 1  # bobina OUT
    elif len(partes) == 2 and partes[0] in ("SET", "RST"):
        op, celdas = partes[0], 2
    else:
        raise ToolError("salida: 'Y0', 'SET M0', 'RST M0', 'OUT T0 K30', 'MOV K0 D10' o 'INCP D10'.")
    tipo, num = _dispositivo(partes[-1], _BOBINAS)
    s = f"ce{{op=cl{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[d{{s=#:a={num}:vt=nn}}]}}"
    return [op, tipo], s, celdas


def _renglon(columnas: list[list[str]], salida: str) -> tuple[str, int]:
    """Devuelve (data, filas). columnas en serie (AND); cada una con contactos en paralelo (OR)."""
    if not columnas or any(not col for col in columnas):
        raise ToolError("columnas necesita al menos una columna y ninguna vacia, ej. [['X0','Y0'],['X1']]")
    # shortcut: OR solo en la primera columna (unico caso verificado en proyectos reales); ampliar si se necesita OR a media linea
    if any(len(col) > 1 for col in columnas[1:]):
        raise ToolError("Por ahora los contactos en paralelo (OR) solo se permiten en la primera columna.")
    cadenas, elementos, x = [], [], 0
    for col in columnas:
        hechos = [_contacto(t) for t in col]
        ancho_col = max(h[2] for h in hechos)
        for y, (cad, s, celdas) in enumerate(hechos):
            cadenas += cad
            elementos.append(f"e{{s={s}:pos={x},{y}}}")
            # rama mas corta que la columna: lineas horizontales explicitas hasta la union
            elementos += [f"e{{s=-:pos={x + k},{y}}}" for k in range(celdas, ancho_col)]
        x += ancho_col
    cad, s, celdas = _salida(salida)
    cadenas += cad
    elementos.append(f"e{{s={s}:pos={x},0}}")
    filas = len(columnas[0])
    union = max(_contacto(t)[2] for t in columnas[0])  # columna donde se cierra el OR
    verticales = (":vs=[" + ":".join(f"v{{pos={union},{y}}}" for y in range(1, filas)) + "]") if filas > 1 else ""
    arbol = f"cb{{fg=fg{{dim={x + celdas}x{filas}:es=[{':'.join(elementos)}]{verticales}}}}}"
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


@mcp.tool()
def revisar_ld(ruta_gx3: str, programa: str) -> str:
    """Revisa el ladder: dobles bobinas (misma salida OUT en varios renglones) y salidas usadas con OUT y SET/RST a la vez."""
    with _abrir(ruta_gx3) as z, tempfile.TemporaryDirectory() as d:
        nombre = f"{programa}_LDDB.db"
        if nombre not in z.namelist():
            raise ToolError(f"No hay programa LD '{programa}'. Usa listar_programas.")
        c = sqlite3.connect(z.extract(nombre, d))
        try:
            filas = c.execute("select blocktype, data from LadderBlocks where blocktype in (0, 4) order by pos").fetchall()
        finally:
            c.close()
    out, setrst = {}, {}
    for n, (tipo, data) in enumerate(filas, 1):
        if tipo != 0:
            continue
        cad, texto = _separar(data)
        arbol = _arbol(texto, cad)
        for e in arbol.get("fg", {}).get("es", []) if arbol.get("_") == "cb" else []:
            s = e["s"]
            if not isinstance(s, dict) or s["op"]["_"] != "cl" or not s.get("args"):
                continue
            destino = _operando(s["args"][0], {})
            op = s["op"]["op"]
            if op in ("c", "OUT__16"):
                out.setdefault(destino, []).append(n)
            elif op in ("SET", "RST"):
                setrst.setdefault(destino, []).append(n)
    avisos = [f"DOBLE BOBINA {k}: OUT en R{', R'.join(map(str, v))} (solo cuenta el ultimo)"
              for k, v in out.items() if len(v) > 1]
    avisos += [f"MEZCLA {k}: OUT en R{', R'.join(map(str, out[k]))} y SET/RST en R{', R'.join(map(str, v))}"
               for k, v in setrst.items() if k in out]
    return "\n".join(avisos) or f"Sin problemas en {len(filas)} renglones (dobles bobinas y OUT+SET/RST)."


if __name__ == "__main__":
    mcp.run()
