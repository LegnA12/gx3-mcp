"""Verifica el codificador ladder contra renglones reales. Uso: python test_ladder.py <carpeta con .gx3> <proyecto LD.gx3>"""
import glob
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile

import gx3_mcp as g

# 1) Todos los renglones reales de los respaldos
reales = set()
d = tempfile.mkdtemp()
for f in glob.glob(os.path.join(sys.argv[1], "**", "*.gx3"), recursive=True):
    with zipfile.ZipFile(f) as z:
        for n in z.namelist():
            if n.endswith("_LDDB.db"):
                c = sqlite3.connect(z.extract(n, d))
                reales |= {r[0] for r in c.execute("select data from LadderBlocks")}
                c.close()
print(len(reales), "bloques reales")

# 2) Lo generado debe existir tal cual en proyectos reales
assert g._renglon([["/Y0"]], "Y5")[0] in reales          # contacto NC + bobina
assert g._renglon([["T50"]], "RST M704")[0] in reales     # RST ocupa 2 celdas
assert g._renglon([["SM413"]], "M1109")[0] in reales
assert g._renglon([["X10"]], "M1108")[0] in reales        # X10 octal se guarda a=16 (0x10)
assert g._renglon([["M704"]], "OUT T50 K10")[0] in reales  # timer, 3 celdas
print("codificacion identica a renglones reales")
assert g._bloque(["Solucion 1"], "st{st=c{t=#:st=s}:dim=0x1}") == "V1:1:10:Solucion 1:st{st=c{t=#:st=s}:dim=0x1}"

# 3) OR en primera columna: misma estructura que el renglon real T110 OR T120 (cambiando la bobina)
data, filas = g._renglon([["T110", "T120"]], "M0")
real = next(r for r in reales if "a=110" in r and "a=120" in r and "dim=2x2" in r)
arbol_real = real[real.index("cb{"):].replace("args=[l{id=#}]", "args=[d{s=#:a=0:vt=nn}]")
assert data.endswith(arbol_real) and filas == 2, (data, real)
print("OR igual al real")

# 4) Errores claros
for cols, sal in [([["X8"]], "Y0"), ([["X0"]], "ZRST M0 D5"), ([["D0"]], "Y0"), ([["X0"]], "MOV D0"), ([["X0"]], "OUT M0 K10"),
                  ([["X0"]], "OUT T0 K0"), ([["X0"]], "EMOV K5 D20"), ([["X0"]], "BMOV D0 K5 K8"), ([["E< D10 K5"]], "Y0")]:
    try:
        g._renglon(cols, sal)
        raise AssertionError(f"debio fallar {cols} {sal}")
    except g.ToolError as e:
        print("ok error:", e)

# 5) Agregar sobre una COPIA del proyecto: queda antes de END y nada mas cambia
copia = os.path.join(d, "copia.gx3")
shutil.copy(sys.argv[2], copia)
prog = g.listar_programas(copia).split()[0]
print(g.agregar_renglon_ld(copia, prog, [["X0", "Y0"], ["X1"]], "Y0", "Arranque y paro", os.path.join(d, "out.gx3")))
with zipfile.ZipFile(copia) as a, zipfile.ZipFile(os.path.join(d, "out.gx3")) as b:
    assert [n for n in a.namelist() if a.read(n) != b.read(n)] == [f"{prog}_LDDB.db"]
    c = sqlite3.connect(b.extract(f"{prog}_LDDB.db", os.path.join(d, "x")))
    filas = c.execute("select blocktype, pos, rowsize, translated, data from LadderBlocks order by pos").fetchall()
for r in filas:
    print(r)
assert [r[0] for r in filas[-3:]] == [1, 0, 5]                       # comentario, renglon, END
assert "Arranque y paro" in filas[-3][4] and filas[-2][3] == 0         # el nuevo queda sin convertir
assert [r[1] for r in filas] == sorted(r[1] for r in filas)             # orden por pos intacto

# 6) Lectura: lo que escribimos se lee igual, y ningun bloque real rompe el lector
import re
fila0, fila1 = (re.sub("-+", "-", l) for l in g._leer_bloque(g._renglon([["X0", "Y0"], ["/X1"]], "Y0")[0], {}))
assert (fila0, fila1) == ("|-[X0]-+-[/X1]-(Y0)-", "|-[Y0]-+"), (fila0, fila1)
assert "[OUT T0 K30]" in g._leer_bloque(g._renglon([["X10"]], "OUT T0 K30")[0], {})[0]
assert "[X10]" in g._leer_bloque(g._renglon([["X10"]], "Y0")[0], {})[0]  # octal de ida y vuelta
for data in reales:
    g._leer_bloque(data, {})
print("lectura ok en", len(reales), "bloques reales")

# 7) Formatos nuevos: cada elemento generado existe en un renglon real (numeros normalizados)
def norm(t):
    return re.sub(r"\b(v|a)=\d+", r"\1=N", t)
reales_norm = {norm(r) for r in reales}
def existe(trozo):
    return any(norm(trozo) in r for r in reales_norm)
assert existe(g._contacto(">= D10 K100")[1].split(":args=")[0])  # op de comparacion (sus ejemplos usan etiquetas)
assert existe("op=ct{op=#:ct=p:as=[as{vt=Abl}]}") and existe("op=ct{op=#:ct=f:as=[as{vt=Abl}]}")  # flancos
assert existe(g._salida("MOV K0 D10")[1]), g._salida("MOV K0 D10")[1]        # MOV constante -> D, completo
assert existe(g._salida("INCP D10")[1].split(":args=")[0])                     # INCP (sus ejemplos usan etiquetas)
assert g._salida("MOV K0 D10")[0] == ["MOV", "K_1", "D"]
assert existe("e{s=-:pos=")                                                    # relleno horizontal
# rama corta junto a una comparacion (3 celdas): se rellena y la union queda en la columna 3
data, _ = g._renglon([[">= D10 K5", "X0"]], "Y4")
assert "e{s=-:pos=1,1}" in data and "e{s=-:pos=2,1}" in data and "v{pos=3,1}" in data, data
print("\n".join(g._leer_bloque(data, {})))
for malo in [([["↑/X0"]], "Y0"), ([[">= D10 K99999"]], "Y0"), ([["X0"]], "MOV D0 K5"), ([["X0"]], "INCP D1 D2")]:
    try:
        g._renglon(*malo)
        raise AssertionError(f"debio fallar {malo}")
    except g.ToolError as e:
        print("ok error:", e)

# 8) Insertar antes de un renglon, borrar y revisar, sobre una copia
c2 = os.path.join(d, "c2.gx3")
shutil.copy(sys.argv[2], c2)
prog = g.listar_programas(c2).split()[0]
antes = g.leer_ld(c2, prog)
g.agregar_renglon_ld(c2, prog, [["SM402"]], "MOV K5 D20", "INSERTADO", c2, antes_de_renglon=1)
despues = g.leer_ld(c2, prog)
assert despues.startswith("// INSERTADO\nR1  |") and "[MOV K5 D20]" in despues.splitlines()[1], despues[:300]
g.borrar_renglon_ld(c2, prog, 1, c2)
assert g.leer_ld(c2, prog) == antes, "borrar no dejo el programa como estaba"
assert g.revisar_ld(c2, prog).startswith("Sin problemas")
g.agregar_renglon_ld(c2, prog, [["X7"]], "Y0", "duplicada", c2)
assert "DOBLE BOBINA Y0" in g.revisar_ld(c2, prog), g.revisar_ld(c2, prog)
print("insertar/borrar/revisar ok")

# 9) y 10) Opcional, 3er argumento: un proyecto con etiquetas, estructuras y FB
if len(sys.argv) > 3:
    ctrl = sys.argv[3]
    texto = "\n".join(g.leer_ld(ctrl, l.split()[0]) for l in g.listar_programas(ctrl).splitlines() if "  LD  " in l)
    sin_nombre = texto.count("_lid/")
    print("Etiquetas:", len(texto.splitlines()), "lineas,", texto.count("[FB "), "llamadas FB, refs sin nombre:", sin_nombre)
    assert sin_nombre == 0
    et = g.leer_etiquetas(ctrl)
    assert "== GLOBAL" in et and "== ESTRUCTURA " in et, et[:300]
    devs = [l.split(" = ")[1] for l in et.splitlines() if " = " in l]
    clave = [[int(t) if t.isdigit() else t for t in re.split(r"(\d+)", d)] for d in devs]
    assert clave == sorted(clave), "dispositivos sin orden natural"
    print("etiquetas ok:", len(et.splitlines()), "lineas")

# 11) Monitor: marcas de estado con valores conocidos (sin PLC)
def una_fila(cols, sal, valores):
    return re.sub("-+", "-", g._leer_bloque(g._renglon(cols, sal)[0], {}, None, valores)[0])
assert una_fila([["X0"], ["/X1"]], "Y0", {("X0", False): 1, ("X1", False): 1, ("Y0", False): 0}) == "|-[X0]■-[/X1]□-(Y0)□-"
assert "[>= D10=7 K5]■" in una_fila([[">= D10 K5"]], "Y4", {("D10", True): 7, ("Y4", False): 1})
assert "[>= D10=3 K5]□" in una_fila([[">= D10 K5"]], "Y4", {("D10", True): 3})
assert "[OUT T0 K30]{T0=12}" in una_fila([["X0"]], "OUT T0 K30", {("T0", True): 12})
assert g._clave_plc("T0", False, {}) == "TS0" and g._clave_plc("T0", True, {}) == "TN0"
assert g._clave_plc("Motor.Marcha", False, {"Motor.Marcha": "M5"}) == "M5" and g._clave_plc("DEC1", True, {}) is None

# 12) Referencia cruzada y revision con reglas nuevas, sobre una copia
c3 = os.path.join(d, "c3.gx3")
shutil.copy(sys.argv[2], c3)
prog = g.listar_programas(c3).split()[0]
rc = g.referencia_cruzada(c3, "X1")
assert rc.startswith("X1\n") and "contacto" in rc, rc
for cols, sal in ([["X10"]], "SET M5"), ([["T9"]], "Y6"), ([["Y7"]], "M7"):
    g.agregar_renglon_ld(c3, prog, cols, sal, "prueba de revision", c3)
rev = g.revisar_ld(c3)
for esperado in ("SET SIN RST M5", "TIMER SIN ARRANCAR T9", "SALIDA SIN ESCRIBIR Y7"):
    assert esperado in rev, (esperado, rev)
print("referencia/revision ok")

# 13) estado_plc y monitorear_ld con un PLC simulado (se reemplaza el cliente SLMP)
original = g.plc_slmp.leer
try:
    g.plc_slmp.leer = lambda ip, puerto, ds, timeout=3.0: [
        [0], [1, 0], [0x2220, 2026, 11, 20, 9, 30, 5, 4], [0, 1, 0], [2026, 11, 20, 9, 31, 0, 4], [1, 279, 0, 175, 2, 475]]
    est = g.estado_plc("127.0.0.1", 5000)
    assert "CPU: RUN" in est and "2220H" in est and "BAJA ahora" in est and "actual 1.279 ms" in est, est
    g.plc_slmp.leer = lambda ip, puerto, ds, timeout=3.0: [[1] for _ in ds]
    mon = g.monitorear_ld(c3, prog, "127.0.0.1", 5000)
    assert mon.startswith("Monitor") and "■" in mon, mon[:200]
finally:
    g.plc_slmp.leer = original
print("estado/monitor ok")

# 14) Formatos nuevos contra renglones reales
# OR a mitad de renglon: identico al real |-[Y1]-+-[/M505]-+-(Y2)  (verticales que abren y cierran)
assert g._renglon([["Y1"], ["/M505", "SM413"]], "Y2")[0] in reales, g._renglon([["Y1"], ["/M505", "SM413"]], "Y2")[0]
assert existe(g._salida("EMOV E1.5 D20")[1].split(":args=")[0])                # EMOV (REAL)
assert existe(g._salida("ZRST M0 M15")[1].split(":args=")[0])                  # ZRST
assert existe(g._salida("BMOV D0 D100 K8")[1].split(":args=")[0])              # BMOV origen destino cantidad
cad, s, _ = g._contacto("E< D10 E17.0")
assert cad == ["<", "D", "E_2n", "17.0"] and "c{s=#:v=41880000:t=#}" in s      # constante REAL como en el real
assert existe(s.split(":args=")[0]) and any("c{s=#:v=41880000:t=#}" in r for r in reales)
assert g._salida("ZRST M0 M15")[0] == ["ZRST", "M", "M"] and g._salida("BMOV D0 D100 K8")[0] == ["BMOV", "D", "D", "K_1"]
# lectura de vuelta: lo escrito se lee igual
for cols, sal, esperado in [([["E< D10 E17.0"]], "SET M1", "[E< D10 E17.0]"), ([["X0"]], "EMOV E1.5 D20", "[EMOV E1.5 D20]"),
                            ([["X0"]], "ZRST M0 M15", "[ZRST M0 M15]"), ([["X0"]], "BMOV D0 D100 K8", "[BMOV D0 D100 K8]")]:
    assert esperado in g._leer_bloque(g._renglon(cols, sal)[0], {})[0], (esperado, g._leer_bloque(g._renglon(cols, sal)[0], {}))
assert g._operando({"_": "c", "s": "H_1", "v": "12290"}, {}) == "H3002"
print("instrucciones nuevas y OR a media linea ok")

# 15) Diagnostico Modbus RTU con un PLC simulado
original = g.plc_slmp.leer
try:
    # CH1: MODBUS RTU maestro, 19200 bps par 1 parada (0b1001_0110), con timeout 7309H registrado
    g.plc_slmp.leer = lambda ip, puerto, ds, timeout=3.0: [[0, 1, 1], [1], [0x7309, 0, 0x96, 9], [2], [0, 500, 0, 5, 3]]
    diag = g.diagnosticar_modbus_rtu("127.0.0.1", 5000, 1)
    for esperado in ("modo MODBUS RTU", "19200 bps", "par", "maestro", "REINTENTANDO", "7309H", "TIMEOUT", "reintentos 3"):
        assert esperado in diag, (esperado, diag)
    # excepcion 730AH: FC 03H, codigo 02H
    g.plc_slmp.leer = lambda ip, puerto, ds, timeout=3.0: [[0, 0, 0], [1], [0x730A, 0x0302, 0x96, 9], [0], [0, 500, 0, 5, 3]]
    assert "FC 03H, excepcion 02H (direccion no asignada en el esclavo)" in g.diagnosticar_modbus_rtu("127.0.0.1", 5000, 1)
    g.plc_slmp.leer = lambda ip, puerto, ds, timeout=3.0: [[0, 0, 0], [0], [0, 0, 0, -1], [0], [0, 0, 0, 0, 0]]
    assert "sin placa" in g.diagnosticar_modbus_rtu("127.0.0.1", 5000, 2)
finally:
    g.plc_slmp.leer = original
print("diagnostico Modbus RTU ok")

# 16) Simulador: renglones generados por el codificador, ejecutados ciclo a ciclo (10 ms)
R = lambda cols, sal: g._renglon(cols, sal)[0]
sim = g._Simulador([R([["X0", "Y0"], ["/X1"]], "Y0"), R([["Y0"]], "OUT T0 K30"), R([["T0"]], "Y1"),
                    R([["X2"]], "INCP D10"), R([[">= D10 K2"]], "SET M0"), R([["X3"]], "ZRST M0 M15"),
                    R([["X4"], ["X5", "X6"], ["/X7"]], "Y2"), R([["E> D20 E0.5"]], "Y3"), R([["X0"]], "EMOV E1.0 D20"),
                    R([["X0"]], "BMOV D10 D100 K2"), R([["↓X4"]], "Y4")], {}, {}, 10)
m = sim.mem
def correr(n=1, **pon):
    for k, v in pon.items():
        sim.escribir(k, False, v)
    for _ in range(n):
        sim.ciclo()
correr(X0=1); assert m["Y0"] == 1 and m["D100"] == 0 and m["Y3"] == 0  # Y3 lee D20 antes del EMOV
correr(X0=0); assert m["Y0"] == 1 and m["Y3"] == 1                     # enclavado; EMOV ya dejo 1.0
correr(297); assert m["TS0"] == 0 and m["TN0"] == 29 and m["Y1"] == 0  # 299 ciclos x 10 ms = 2.99 s
correr(); assert m["TS0"] == 1 and m["TN0"] == 30 and m["Y1"] == 1     # 3.0 s: T0 K30
correr(X1=1); assert m["Y0"] == 0 and m["TS0"] == 0 and m["Y1"] == 0   # paro: timer se reinicia
correr(3, X2=1); assert m["D10"] == 1                                   # INCP: una vez por flanco
correr(X2=0); correr(X2=1); assert m["D10"] == 2 and m["M0"] == 1      # >= D10 K2 -> SET M0
correr(X3=1); assert m["M0"] == 0                                       # ZRST va despues del SET
correr(X0=1, X1=0); assert m["D100"] == 2 and m["D101"] == 0           # BMOV D10 D100 K2
correr(X4=1, X5=0, X6=1, X7=0); assert m["Y2"] == 1                    # OR a media linea
correr(X6=0); assert m["Y2"] == 0
correr(X7=1, X6=1); assert m["Y2"] == 0
correr(X4=0); assert m["Y4"] == 1                                       # flanco de bajada: un ciclo
correr(); assert m["Y4"] == 0
assert not sim.avisos, sim.avisos
# herramienta completa sobre la copia con renglones agregados
sal = g.simular_ld(c3, prog, [{"X10": 1}, {"segundos": 0.5}, {"X77": 1}], mostrar=["M5", "T0"])
assert sal.startswith("Simulacion") and "Paso 3" in sal and "M5=1" in sal and "TN0=" in sal and "X77" in sal, sal
print(sal)
print("simulador ok")

# 17) escribir_plc: sin lista de permitidos o fuera de ella no conecta siquiera
for permitidos, disp in (("", "D101"), ("D101-D105", "D106"), ("D101-D105", "Y0")):
    os.environ["GX3_ESCRITURA_PERMITIDA"] = permitidos
    try:
        g.escribir_plc("127.0.0.1", 1, disp, [1])
        raise AssertionError(disp)
    except g.ToolError as e:
        assert ("deshabilitada" if not permitidos else "fuera de los permitidos") in str(e), e
os.environ.pop("GX3_ESCRITURA_PERMITIDA")
print("escritura protegida ok")
print("TODO OK")
