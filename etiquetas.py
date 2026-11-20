"""Resuelve las referencias `_lid/<LabelID>/<RowID>` de un proyecto GX Works3 (.gx3).

Solo lectura: el .gx3 (un ZIP) nunca se modifica; las bases SQLite se extraen a un
directorio temporal. Formato documentado en formato_gx3.md (misma carpeta).

    from etiquetas import nombres_etiquetas
    eti = nombres_etiquetas(r"C:\\...\\proyecto.gx3")
    eti["_lid/4566376985116216867/4"]
    # {'nombre': 'Prueb', 'tipo_dato': 'REAL', 'clase': 'VAR_GLOBAL',
    #  'dispositivo_asignado': 'D700', 'comentario': '', 'pou': 'GLOBAL'}
"""
import collections
import os
import re
import sqlite3
import tempfile
import zipfile

# ColumnID 3 (tipo de dato), ColumnIntValue -> nombre IEC. 99 = tipo de usuario (nombre en ColumnStrValue).
TIPOS = {1: "BOOL", 2: "WORD", 3: "DWORD", 4: "INT", 5: "DINT", 6: "REAL", 8: "TIME", 210: "ANY_NUM"}
# ColumnID 1 (clase), ColumnIntValue -> clase; se usa cuando ColumnStrValue viene vacio (EN/ENO/retorno de FUN).
CLASES = {1: "VAR_GLOBAL", 3: "VAR_GLOBAL_RETAIN", 4: "VAR", 5: "VAR_CONSTANT", 6: "VAR_RETAIN",
          7: "VAR_INPUT", 9: "VAR_OUTPUT", 10: "VAR_OUTPUT_RETAIN"}
# LabelTbl.LabelTypeID -> a que pertenece la tabla de etiquetas.
TIPO_TABLA = {5121: "PROGRAMA", 5122: "GLOBAL", 5123: "ESTRUCTURA", 5125: "FUN", 5126: "FB"}
_POU_DB = re.compile(r"^([0-9a-f]{16})_(LDDB|STDB|MilDB)\.db$")
_TABLA_POU = {"LDDB": "LadderBlocks", "STDB": "StBlocks", "MilDB": "MIL"}


def _partir(data):
    """'V1:<n>:<len...>:<str...>:<arbol>' -> (cadenas, arbol)."""
    p = data.split(":", 2)
    n = int(p[1])
    partes = p[2].split(":", n)
    s, cadenas, i = partes[n], [], 0
    for largo in map(int, partes[:n]):
        cadenas.append(s[i:i + largo])
        i += largo + 1
    return cadenas, s[i:]


def _refs_propias(data):
    """LabelIDs usados como etiqueta base (no como miembro `pl`/`m=l` ni como pin `cbag/vag l=l`)."""
    cadenas, arbol = _partir(data)
    ids = set()
    for s, m in zip(cadenas, re.finditer("#", arbol)):
        ctx = arbol[max(0, m.start() - 7):m.start()]
        if s.startswith("_lid/") and ctx.endswith("l{id=") and not ctx.endswith(("pl{id=", "m=l{id=", "l=l{id=")):
            ids.add(s.split("/")[1])
    return ids


def _nombres_qpg(b):
    """Cadenas UTF-16LE con prefijo de longitud (bytes, incluye el nulo) que son identificadores."""
    salida, i = [], 0
    while i < len(b) - 4:
        largo = b[i]
        if b[i + 1] == 0 and largo >= 4 and largo % 2 == 0 and b[i + largo:i + largo + 2] == b"\x00\x00":
            try:
                s = b[i + 2:i + largo].decode("utf-16-le")
            except UnicodeDecodeError:
                s = ""
            if re.fullmatch(r"\w+", s):
                salida.append(s)
                i += largo + 2
                continue
        i += 1
    return salida


def _nombres_pou(z, d, bloque_a_hash):
    """hash16 del cuerpo -> nombre del POU, desde ConvertData (solo si el proyecto fue convertido).

    shortcut: depende de datos compilados (pueden faltar o estar desfasados tras editar sin convertir);
    el nombre fiable esta en _Project.txc, que esta cifrado.
    """
    nl = z.namelist()
    pou_hash = {}
    for n in nl:
        if not n.endswith("_StepInfo.db"):
            continue
        c = sqlite3.connect(z.extract(n, d))
        try:
            cuerpos = {"%016x" % (b & (2 ** 64 - 1)) for (b,) in c.execute("select BodyID from T_Block") if b}
            votos = collections.Counter(bloque_a_hash.get("_guid/" + g.strip("{}").lower())
                                        for (g,) in c.execute("select BlockID from T_Block"))
        finally:
            c.close()
        votos.pop(None, None)
        if len(cuerpos) == 1:
            pou_hash[n.split("_")[0]] = cuerpos.pop()
        elif len(votos) == 1:
            pou_hash[n.split("_")[0]] = next(iter(votos))
    nombres, prg_sin_hash = {}, []
    for n in nl:
        if not n.endswith("/PouLinkOrder.info"):
            continue
        carpeta = n.rsplit("/", 1)[0]
        ids = z.read(n).decode("ascii", "replace").split()
        if carpeta + "/Program.qpg" not in nl:
            continue
        qpg = z.read(carpeta + "/Program.qpg")
        nom = _nombres_qpg(qpg[100:])
        if len(nom) < len(ids):
            continue
        for pou_id, nombre in zip(ids, nom):
            if pou_id in pou_hash:
                nombres[pou_hash[pou_id]] = nombre
            elif "#FT_FB_#".encode("utf-16-le") not in qpg[:200]:
                prg_sin_hash.append(nombre)
    return nombres, prg_sin_hash


def nombres_etiquetas(ruta_gx3):
    """Mapa '_lid/<LabelID>/<RowID>' -> {nombre, tipo_dato, clase, dispositivo_asignado, comentario, pou}."""
    with zipfile.ZipFile(ruta_gx3) as z, tempfile.TemporaryDirectory() as d:
        if "LabelData.db" not in z.namelist():
            return {}
        # 1) Que POU usa cada tabla de etiquetas como propia (programa/FB) y bloque->POU.
        duenos = collections.defaultdict(set)
        bloque_a_hash = {}
        texto_st = collections.defaultdict(str)
        for n in z.namelist():
            m = _POU_DB.match(n)
            if not m:
                continue
            c = sqlite3.connect(z.extract(n, d))
            try:
                for bid, data in c.execute(f"select id, data from {_TABLA_POU[m.group(2)]}"):
                    bloque_a_hash[str(bid).lower()] = m.group(1)
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", "replace")
                    if m.group(2) == "STDB":
                        texto_st[m.group(1)] += (data or "") + "\n"
                    elif data and data.startswith("V1:"):
                        for lid in _refs_propias(data):
                            duenos[lid].add(m.group(1))
            finally:
                c.close()
        nombre_pou, prg_sin_hash = _nombres_pou(z, d, bloque_a_hash)
        cuerpos = {_POU_DB.match(n).group(1) for n in z.namelist()
                   if _POU_DB.match(n) and _POU_DB.match(n).group(2) != "MilDB"}

        # 2) Tablas de etiquetas.
        c = sqlite3.connect(z.extract("LabelData.db", d))
        try:
            tipo_tabla = dict(c.execute("select LabelID, LabelTypeID from LabelTbl"))
            col = collections.defaultdict(dict)
            for lid, rid, cid, s, v in c.execute(
                    "select LabelID, RowID, ColumnID, ColumnStrValue, ColumnIntValue from ColumnDataTbl"):
                col[(lid, rid)][cid] = (s or "", v)
            dims = collections.defaultdict(list)
            for lid, rid, cuenta, inicio in c.execute(
                    "select LabelID, RowID, ArrayCount, StartNo from ArrayInfoTbl order by ArrayNo"):
                dims[(lid, rid)].append(f"{inicio}..{inicio + cuenta - 1}")
            auto = {(lid, rid): dev for lid, rid, dev in c.execute(
                "select LabelID, RowID, MELSECDevice from DeviceAssignTbl "
                "where RelatedLabelID = LabelID and RelatedRowID = RowID and MELSECDevice <> ''")}
            # Nombre de un tipo de usuario (estructura/FB): fila que lo usa -> ColumnID 3 texto.
            nombre_tipo = {}
            for rel, s in c.execute(
                    "select r.RelatedLabelID, d.ColumnStrValue from RowRelationTbl r join ColumnDataTbl d "
                    "on d.LabelID = r.LabelID and d.RowID = r.RowID and d.ColumnID = 3"):
                nombre_tipo.setdefault(rel, s)
            filas = c.execute("select LabelID, RowID from RowTbl").fetchall()
        finally:
            c.close()

    # 3) POU ST sin MIL util (no convertidos): la tabla propia es la que mas nombres comparte con el texto ST.
    # shortcut: heuristica por nombres (umbral 50 %, ganador unico); falla si dos POU ST usan nombres casi iguales.
    usados = {h for hs in duenos.values() for h in hs}
    palabras = {h: set(re.findall(r"\w+", t)) for h, t in texto_st.items() if h not in usados}
    for lid, t in tipo_tabla.items():
        if t not in (5121, 5126) or duenos.get(lid) or not palabras:
            continue
        nombres = {col[(l2, r)].get(2, ("",))[0] for l2, r in filas if l2 == lid and r > 0}
        if not nombres:
            continue
        puntos = sorted(((len(nombres & p) / len(nombres), h) for h, p in palabras.items()), reverse=True)
        if puntos[0][0] >= 0.5 and (len(puntos) == 1 or puntos[1][0] < puntos[0][0]):
            duenos[lid].add(puntos[0][1])
    # 4) Por eliminacion: un solo programa sin nombre y un solo cuerpo sin nombre que no es de FB.
    de_fb = {h for lid, hs in duenos.items() if tipo_tabla.get(lid) == 5126 for h in hs}
    libres = cuerpos - set(nombre_pou) - de_fb
    if len(prg_sin_hash) == 1 and len(libres) == 1:
        nombre_pou[libres.pop()] = prg_sin_hash[0]

    def pou_de(lid):
        clase = TIPO_TABLA.get(tipo_tabla.get(lid), f"TABLA{tipo_tabla.get(lid)}")
        if clase == "GLOBAL":
            return clase
        if clase == "FUN":
            return f"FUN {col[(lid, -1)].get(2, ('?',))[0]}"
        hashes = sorted(duenos.get(lid, ())) if clase in ("PROGRAMA", "FB") else []
        nombre = nombre_tipo.get(lid) or next((nombre_pou[h] for h in hashes if h in nombre_pou), "?")
        return f"{clase} {nombre}" + (f" [{', '.join(hashes)}]" if hashes else "")

    pous = {lid: pou_de(lid) for lid in tipo_tabla}
    salida = {}
    for lid, rid in filas:
        cd = col[(lid, rid)]
        s3, v3 = cd.get(3, ("", None))
        tipo = s3 if v3 == 99 else TIPOS.get(v3, f"tipo#{v3}")
        if dims.get((lid, rid)):
            tipo = f"ARRAY[{','.join(dims[(lid, rid)])}] OF {tipo}"
        s1, v1 = cd.get(1, ("", None))
        clase = s1 or ("RETURN" if rid == -1 else CLASES.get(v1, "MIEMBRO" if v1 is None else f"clase#{v1}"))
        salida[f"_lid/{lid}/{rid}"] = {
            "nombre": cd.get(2, ("",))[0],
            "tipo_dato": tipo,
            "clase": clase,
            "dispositivo_asignado": cd.get(7, ("",))[0] or auto.get((lid, rid), ""),
            "comentario": next((cd[k][0] for k in range(9, 29) if cd.get(k, ("",))[0]), ""),
            "pou": pous[lid],
        }
    return salida


if __name__ == "__main__":
    import glob
    import hashlib
    import sys

    # Uso: python etiquetas.py <carpeta con proyectos .gx3>
    raiz = sys.argv[1] if len(sys.argv) > 1 else "."
    proyectos = glob.glob(os.path.join(raiz, "**", "*.gx3"), recursive=True)
    assert proyectos, f"no se encontraron .gx3 en {raiz}"

    for ruta in proyectos:
        antes = hashlib.sha256(open(ruta, "rb").read()).hexdigest()
        eti = nombres_etiquetas(ruta)
        assert hashlib.sha256(open(ruta, "rb").read()).hexdigest() == antes, "el .gx3 cambio"
        # Toda referencia _lid del ladder/MIL debe resolverse a una fila de LabelData.
        refs = set()
        with zipfile.ZipFile(ruta) as z, tempfile.TemporaryDirectory() as d:
            for n in z.namelist():
                m = _POU_DB.match(n)
                if m and m.group(2) != "STDB":
                    c = sqlite3.connect(z.extract(n, d))
                    for (data,) in c.execute(f"select data from {_TABLA_POU[m.group(2)]}"):
                        refs.update(re.findall(r"_lid/\d+/-?\d+", data or ""))
                    c.close()
        faltan = {r for r in refs if r not in eti}
        assert not faltan, (ruta, sorted(faltan)[:5])
        assert all(v["nombre"] for v in eti.values()), ruta
        print(f"OK {len(eti):5d} etiquetas, {len(refs):4d} refs resueltas  {os.path.basename(ruta)}")
    print("autoprueba completa")
