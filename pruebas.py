"""
pruebas.py — Ejecuta las pruebas de consistencia (A vs B) y arma el reporte de evidencias.

La rúbrica pide: >= 3 pruebas con ideas/géneros distintos, las 5 escenas de cada una,
comparar >= 2 configuraciones y registrar parámetros, tiempos, fallos y cambios.

Uso (PowerShell):
    uv run python pruebas.py                         # corre las 3 pruebas con A y B (varias horas)
    uv run python pruebas.py --pruebas 2             # solo la prueba 2
    uv run python pruebas.py --presets A_consistente # solo una configuración
    uv run python pruebas.py --solo-reporte          # no genera nada, rehace el reporte
    uv run python pruebas.py --solo-reporte --incluir assets\\generated\\cuento_20261115_191237_A_consistente

Es reanudable: si una combinación (prueba, configuración) ya tiene su cuento, se omite.
Si una corrida falla, se anota en errores.log y se sigue con la siguiente.

Salida:
    assets\\pruebas\\prueba_N\\cuento_*      cuentos generados (no se suben a git)
    assets\\pruebas\\informe\\evidencias.md  tablas de tiempos, prompts y evaluación (para el PDF)
    assets\\pruebas\\informe\\*.png          hojas de contacto con las 5 escenas (A sobre B)
    assets\\pruebas\\informe\\resultados.csv resumen en CSV
"""
from __future__ import annotations

import argparse
import csv
import json
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config import PRESETS, RAIZ

DIR_PRUEBAS = RAIZ / "assets" / "pruebas"
DIR_INFORME = DIR_PRUEBAS / "informe"

# --------------------------------------------------------------------------- #
# Pruebas (edítalas): (idea, género). Deben ser combinaciones DISTINTAS.
# La prueba 1 usa la idea del cuento ya generado (La Sombra que Vuela): para no repetir A, copia esa
# carpeta a assets\pruebas\prueba_1\ y el script solo generará B.
# --------------------------------------------------------------------------- #
PRUEBAS: list[tuple[str, str]] = [
    ("Una niña se obsesiona con saber por qué su sombra puede volar y vivirá toda su vida buscando la respuesta", "Misterio"),
    ("Un robot de limpieza descubre un jardín escondido en una estación espacial abandonada", "Ciencia ficción"),
    ("Un zorro pequeño sale a buscar la luna que se cayó en el bosque", "Infantil"),
]


# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #
def buscar_cuento(prueba: int, preset: str) -> Path | None:
    """Devuelve la carpeta ya generada para (prueba, preset), si existe."""
    carpetas = sorted((DIR_PRUEBAS / f"prueba_{prueba}").glob(f"cuento_*_{preset}/historia.json"))
    return carpetas[-1].parent if carpetas else None


def ejecutar(pruebas: list[int], presets: list[str]) -> None:
    from generator import generar_cuento   # import perezoso: carga torch

    DIR_PRUEBAS.mkdir(parents=True, exist_ok=True)
    for n in pruebas:
        idea, genero = PRUEBAS[n - 1]
        for preset in presets:
            if buscar_cuento(n, preset):
                print(f"[omitida] prueba {n} / {preset}: ya existe")
                continue
            print(f"\n=== Prueba {n} / {preset} · {genero} ===\n{idea}")
            try:
                generar_cuento(idea, genero, PRESETS[preset], dir_base=DIR_PRUEBAS / f"prueba_{n}")
            except Exception as e:  # noqa: BLE001 - una falla no debe detener las demás
                traceback.print_exc()
                with open(DIR_PRUEBAS / "errores.log", "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now():%Y-%m-%d %H:%M} prueba {n} / {preset}: {type(e).__name__}: {e}\n")
                print(f"[FALLÓ] prueba {n} / {preset}: {e} (anotado en errores.log)")


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #
def _fuente(tam: int):
    """Fuente TrueType (soporta tildes y ñ); si no hay ninguna, la de Pillow."""
    for nombre in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:                       # Pillow antiguo
        return ImageFont.load_default()


def leer_cuento(carpeta: Path) -> dict | None:
    """Lee historia.json y meta.json de una carpeta; None si está incompleta o dañada."""
    try:
        historia = json.loads((carpeta / "historia.json").read_text(encoding="utf-8"))
        ruta_meta = carpeta / "meta.json"
        meta = json.loads(ruta_meta.read_text(encoding="utf-8")) if ruta_meta.exists() else {}
    except Exception:  # noqa: BLE001
        return None
    return {"carpeta": carpeta, "historia": historia, "meta": meta,
            "preset": historia.get("config", {}).get("nombre", "?")}


def indicador_color(carpeta: Path, historia: dict) -> float | None:
    """
    Indicador AUXILIAR de consistencia visual: intersección media de histogramas de color
    entre escenas consecutivas (0 = nada en común, 1 = idéntico). Solo mide paleta, no la
    identidad del personaje: NO sustituye la comparación visual de las hojas de contacto.
    """
    try:
        hists = []
        for esc in historia["escenas"]:
            img = Image.open(carpeta / esc["imagen"]).convert("RGB").resize((64, 64))
            h, _ = np.histogramdd(np.asarray(img).reshape(-1, 3), bins=(8, 8, 8), range=((0, 256),) * 3)
            hists.append(h.flatten() / h.sum())
        return float(np.mean([np.minimum(a, b).sum() for a, b in zip(hists, hists[1:])]))
    except Exception:  # noqa: BLE001
        return None


def hoja_contacto(filas: list[tuple[str, Path, dict]], destino: Path, lado: int = 256) -> None:
    """Una fila de 5 miniaturas por cuento (con su etiqueta), apiladas verticalmente."""
    f_et, margen, cab = _fuente(18), 8, 30
    ancho = 5 * lado + 6 * margen
    alto = len(filas) * (cab + lado + margen) + margen
    hoja = Image.new("RGB", (ancho, alto), (250, 246, 236))
    d = ImageDraw.Draw(hoja)
    y = margen
    for etiqueta, carpeta, historia in filas:
        d.text((margen, y + 4), etiqueta, fill=(40, 30, 25), font=f_et)
        y += cab
        for i, esc in enumerate(historia["escenas"][:5]):
            x = margen + i * (lado + margen)
            try:
                img = Image.open(carpeta / esc["imagen"]).convert("RGB")
                img.thumbnail((lado, lado))
                hoja.paste(img, (x, y))
            except Exception:  # noqa: BLE001 - imagen faltante: marco rojo
                d.rectangle((x, y, x + lado, y + lado), outline=(180, 0, 0))
        y += lado + margen
    hoja.save(destino)


def recolectar(incluir: list[Path]) -> list[dict]:
    carpetas = sorted(p.parent for p in DIR_PRUEBAS.glob("prueba_*/cuento_*/historia.json"))
    carpetas += [Path(p) for p in incluir]
    cuentos = [c for c in (leer_cuento(p) for p in carpetas) if c]
    for c in cuentos:   # los que están en prueba_N se agrupan por N (aunque la idea tenga otro tipeo)
        c["grupo"] = c["carpeta"].parent.name if c["carpeta"].parent.parent == DIR_PRUEBAS else None
    return cuentos


def generar_reporte(incluir: list[Path]) -> Path:
    cuentos = recolectar(incluir)
    if not cuentos:
        raise SystemExit("No hay cuentos que reportar. Ejecuta primero las pruebas o usa --incluir.")
    DIR_INFORME.mkdir(parents=True, exist_ok=True)

    # Agrupa A y B de la misma prueba: por carpeta prueba_N, o por (idea, género) si viene de --incluir.
    grupos: dict[object, list[dict]] = {}
    for c in cuentos:
        clave = c["grupo"] or (c["historia"].get("idea", "?"), c["historia"].get("genero", "?"))
        grupos.setdefault(clave, []).append(c)

    md = [f"# Evidencias de pruebas — Storybook\n\nGenerado el {datetime.now():%Y-%m-%d %H:%M}. "
          f"{len(grupos)} prueba(s), {len(cuentos)} cuento(s).\n"]
    filas_csv = []

    hw = next((c["meta"]["hardware"] for c in cuentos if c["meta"].get("hardware")), None)
    if hw:
        md.append("## Hardware y versiones\n\n| Elemento | Valor |\n| --- | --- |")
        for k in ("sistema", "cpu", "gpu", "vram_gb", "python", "torch", "cuda_disponible"):
            if k in hw:
                md.append(f"| {k} | {hw[k]} |")
        for p, v in hw.get("versiones", {}).items():
            md.append(f"| {p} | {v} |")
        md.append("\nRAM: completar a mano (el programa no la mide).\n")

    for n, lista in enumerate(grupos.values(), 1):
        idea, genero = lista[0]["historia"].get("idea", "?"), lista[0]["historia"].get("genero", "?")
        lista.sort(key=lambda c: c["preset"])
        nombre_hoja = f"prueba_{n}_contacto.png"
        hoja_contacto([(f"Prueba {n} · {c['preset']} · {c['historia'].get('titulo', '')}", c["carpeta"], c["historia"])
                       for c in lista], DIR_INFORME / nombre_hoja)
        md.append(f"## Prueba {n} — {genero}\n\n**Idea:** {idea}\n\n![Prueba {n}]({nombre_hoja})\n")

        md.append("| Configuración | Título | Guion (s) | Voz (s) | Ambiente (s) | Imágenes (s) | Total (min) | Indicador de color* |\n"
                  "| --- | --- | --- | --- | --- | --- | --- | --- |")
        for c in lista:
            t = c["meta"].get("tiempos", {})
            total = sum(v for v in t.values() if isinstance(v, (int, float)))
            ind = indicador_color(c["carpeta"], c["historia"])
            ind_txt = "" if ind is None else f"{ind:.2f}"
            md.append(f"| {c['preset']} | {c['historia'].get('titulo', '')} | {t.get('guion_s', '')} | {t.get('voz_s', '')} | "
                      f"{t.get('ambiente_s', '')} | {t.get('imagenes_s', '')} | {total / 60:.1f} | {ind_txt} |")
            filas_csv.append({"prueba": n, "genero": genero, "configuracion": c["preset"],
                              "titulo": c["historia"].get("titulo", ""),
                              **{k: t.get(k, "") for k in ("guion_s", "voz_s", "ambiente_s", "imagenes_s")},
                              "total_min": round(total / 60, 1),
                              "indicador_color": "" if ind is None else round(ind, 3),
                              "carpeta": c["carpeta"].as_posix()})
        md.append("\n*Indicador auxiliar: intersección media de histogramas de color entre escenas consecutivas "
                  "(1 = paleta idéntica). Mide la paleta, no la identidad del personaje; la evaluación visual va en la tabla de abajo.\n")

        md.append("### Prompts de imagen por escena\n")
        for c in lista:
            md.append(f"**{c['preset']}**\n")
            for e in c["historia"]["escenas"]:
                md.append(f"{e.get('numero', '')}. `{e.get('prompt_imagen', '')}`")
            md.append("")

        md.append("### Evaluación cualitativa (completar a mano, con observaciones; la calificación la asigna el docente)\n\n"
                  "| Configuración | Narrativa | Visual | Audio y contenido | Funcionamiento | Fallos detectados | Cambios realizados |\n"
                  "| --- | --- | --- | --- | --- | --- | --- |")
        for c in lista:
            md.append(f"| {c['preset']} |  |  |  |  |  |  |")
        md.append("\nGuía: **Narrativa** = personajes, hechos y secuencia se conservan; **Visual** = el personaje y el estilo se reconocen "
                  "entre escenas; **Audio y contenido** = la voz dice el texto mostrado y el ambiente corresponde; "
                  "**Funcionamiento** = 5 escenas completas, se navega y se oye el audio.\n")

    md.append("## Conclusión (completar)\n\nConfiguración final elegida y justificación basada en las tablas anteriores.\n")
    ruta_err = DIR_PRUEBAS / "errores.log"
    if ruta_err.exists():
        md.append("## Errores durante las pruebas\n\n```\n" + ruta_err.read_text(encoding="utf-8") + "```\n")

    ruta = DIR_INFORME / "evidencias.md"
    ruta.write_text("\n".join(md), encoding="utf-8")
    with open(DIR_INFORME / "resultados.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas_csv[0]))
        w.writeheader()
        w.writerows(filas_csv)
    return ruta


def main() -> None:
    ap = argparse.ArgumentParser(description="Pruebas A/B de consistencia y reporte de evidencias.")
    ap.add_argument("--pruebas", type=int, nargs="*", default=list(range(1, len(PRUEBAS) + 1)),
                    help=f"números de prueba a correr (1..{len(PRUEBAS)})")
    ap.add_argument("--presets", nargs="*", default=list(PRESETS), choices=list(PRESETS))
    ap.add_argument("--solo-reporte", action="store_true", help="no genera, solo rehace el reporte")
    ap.add_argument("--incluir", nargs="*", default=[], help="carpetas de cuentos ya generados para añadir al reporte")
    args = ap.parse_args()

    if not args.solo_reporte:
        for n in args.pruebas:
            if not 1 <= n <= len(PRUEBAS):
                ap.error(f"--pruebas: {n} fuera de rango 1..{len(PRUEBAS)}")
        ejecutar(args.pruebas, args.presets)
    print("\nReporte:", generar_reporte([Path(p) for p in args.incluir]))


if __name__ == "__main__":
    main()
