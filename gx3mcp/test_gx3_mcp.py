"""Prueba sobre una COPIA de un .gx3 real. Uso: python test_gx3_mcp.py <proyecto.gx3>"""
import glob
import os
import shutil
import sys
import tempfile
import zipfile

import gx3_mcp as g

d = tempfile.mkdtemp()
fuente = os.path.join(d, "prueba.gx3")
shutil.copy(sys.argv[1], fuente)

lista = g.listar_programas(fuente)
print(lista)
prog = next(l.split()[0] for l in lista.splitlines() if "  ST  " in l)
st = g.leer_st(fuente, prog)
primera = next(l.split("| ", 1)[1] for l in st.splitlines() if "| " in l and l.split("| ", 1)[1].strip())

# reemplazo unico -> archivo nuevo
salida = os.path.join(d, "salida.gx3")
nuevo = "// EDITADO POR MCP\n" + primera
print(g.editar_st(fuente, prog, primera, nuevo, salida))
assert "// EDITADO POR MCP" in g.leer_st(salida, prog)

# todo lo demas queda identico
a, b = zipfile.ZipFile(fuente), zipfile.ZipFile(salida)
assert a.namelist() == b.namelist()
cambiados = [n for n in a.namelist() if a.read(n) != b.read(n)]
assert cambiados == [f"{prog}_STDB.db"], cambiados
a.close(); b.close()

# texto ambiguo o inexistente -> error, sin escribir
for malo in ["\n", "NO_EXISTE_XYZ"]:
    try:
        g.editar_st(salida, prog, malo, "x", salida)
        raise AssertionError("debio fallar")
    except g.ToolError as e:
        print("ok error:", e)

# editar en el mismo archivo -> crea respaldo
print(g.editar_st(salida, prog, "// EDITADO POR MCP", "// EDITADO DOS VECES", salida))
assert "// EDITADO DOS VECES" in g.leer_st(salida, prog)
assert glob.glob(os.path.join(d, "salida.*.bak"))
print("TODO OK")
