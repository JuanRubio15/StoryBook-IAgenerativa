"""
probar_voz.py — Escucha varias voces de Piper con el mismo texto, sin generar un cuento.

    uv run python probar_voz.py                         # voces candidatas por defecto
    uv run python probar_voz.py es_MX-claude-high       # solo las que indiques
    uv run python probar_voz.py --original es_MX-ald-medium   # parámetros por defecto de Piper (sin estilo cuento)

Guarda assets\\pruebas_voz\\<voz>.wav. Cuando elijas una, cambia VOZ_PIPER en config.py.
Si el nombre de una voz no existe, se avisa y se sigue con las demás.
"""
import sys
from dataclasses import replace

import generator as g
from config import PRESETS, RAIZ

CANDIDATAS = ["es_MX-claude-high", "es_AR-daniela-high", "es_ES-davefx-medium", "es_MX-ald-medium"]
TEXTO = ("Había una vez, al borde de un bosque dormido, una niña que descubrió un secreto. "
         "Nadie le creyó… pero ella nunca dejó de buscar la respuesta.")
SALIDA = RAIZ / "assets" / "pruebas_voz"


def main(voces: list[str], original: bool) -> None:
    from piper import PiperVoice
    cfg = PRESETS["A_consistente"]
    if original:     # parámetros por defecto de Piper, sin pausas
        cfg = replace(cfg, velocidad_voz=1.0, variacion_tono_voz=0.667, variacion_ritmo_voz=0.8, pausa_oracion=0.0)
    SALIDA.mkdir(parents=True, exist_ok=True)
    for nombre in voces:
        try:
            voz = PiperVoice.load(str(g.asegurar_voz(nombre)))
            ruta = SALIDA / f"{nombre}{'_original' if original else ''}.wav"
            g.sintetizar_texto(voz, TEXTO, ruta, cfg)
            print("OK   ", ruta)
        except Exception as e:  # noqa: BLE001 - un nombre mal escrito no debe frenar las demás
            print(f"FALLÓ {nombre}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    original = "--original" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--original"]
    main(args or CANDIDATAS, original)
