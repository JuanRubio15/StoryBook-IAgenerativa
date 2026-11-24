"""
config.py — Configuración central del proyecto Storybook.

Aquí viven las rutas, los identificadores de modelos, los estilos visuales por
género y los "presets" de generación (para comparar estrategias de consistencia
en las pruebas del informe técnico).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Rutas
# --------------------------------------------------------------------------- #
RAIZ = Path(__file__).resolve().parent
DIR_GENERADO = RAIZ / "assets" / "generated"   # aquí se guardan los cuentos
DIR_VOCES = RAIZ / "models" / "piper"          # voces de Piper (.onnx)

# --------------------------------------------------------------------------- #
# Modelos (todos se descargan una vez y luego corren sin Internet)
# --------------------------------------------------------------------------- #
ID_LLM = "Qwen/Qwen3-4B-Instruct-2507"                 # guion (Apache-2.0)
ID_SDXL = "stabilityai/stable-diffusion-xl-base-1.0"   # imágenes (OpenRAIL++-M)
ID_AUDIO = "cvssp/audioldm-s-full-v2"                  # ambiente sonoro

# Voz de Piper por defecto (repo rhasspy/piper-voices). Formato: idioma_REGIÓN-nombre-calidad.
# Pruébalas con probar_voz.py y revisa la MODEL_CARD de la elegida (licencia para el informe).
VOZ_PIPER = "es_MX-claude-high"

# --------------------------------------------------------------------------- #
# Géneros: nombre visible -> estilo visual persistente (en inglés, para SDXL).
# Se mantiene corto porque CLIP solo admite 77 tokens por prompt.
# --------------------------------------------------------------------------- #
GENEROS: dict[str, str] = {
    "Fantasía": "storybook fantasy illustration, painterly, rich colors, soft magical light",
    "Terror": "dark gothic horror illustration, moody shadows, desaturated, eerie fog",
    "Misterio": "noir mystery illustration, muted teal and amber palette, dramatic lighting",
    "Aventura": "adventure book illustration, vivid colors, dynamic composition, golden light",
    "Ciencia ficción": "retro sci-fi book illustration, neon accents, clean futuristic design",
    "Infantil": "children's picture book watercolor illustration, soft pastel colors, friendly shapes",
}


@dataclass
class ConfigGeneracion:
    """Parámetros de una corrida. Se guardan en historia.json para el informe."""
    nombre: str = "A_consistente"
    # --- Estrategia de consistencia visual ---
    usar_ficha_personaje: bool = True   # repetir personaje + escenario en cada prompt
    semilla_fija: bool = True           # misma semilla en las 5 escenas
    semilla: int = 1234
    # --- SDXL ---
    ancho: int = 768                    # 1024 da mejor calidad pero tarda más
    alto: int = 768
    pasos_imagen: int = 25
    guidance: float = 7.0
    prompt_negativo: str = ("text, watermark, signature, blurry, lowres, deformed, "
                            "extra fingers, extra limbs, cropped")
    # --- AudioLDM ---
    motor_ambiente: str = "sintetico"   # "sintetico" (NumPy, recomendado) | "audioldm"
    pasos_audio: int = 50               # más pasos = menos siseo (antes 25)
    guidance_audio: float = 4.0         # qué tanto obedece al prompt (antes 2.5 por defecto)
    candidatos_audio: int = 3           # genera N y elige el mejor con una heurística
    duracion_ambiente: float = 10.0     # segundos (se reproduce en bucle)
    # --- Voz (Piper) ---
    voz: str = VOZ_PIPER
    velocidad_voz: float = 1.05         # length_scale: >1 = más lento, tono de cuentacuentos
    variacion_tono_voz: float = 0.667    # noise_scale (por defecto de Piper: 0.667)
    variacion_ritmo_voz: float = 0.8    # noise_w_scale: más ritmo variable (Piper: 0.8)
    pausa_oracion: float = 0.45         # segundos de silencio entre oraciones
    # --- LLM ---
    cuantizar_llm_4bit: bool = True     # necesario para caber en 6 GB de VRAM
    temperatura: float = 0.7
    max_tokens: int = 1800
    reintentos_llm: int = 3


# Dos configuraciones para el experimento comparativo exigido en la rúbrica.
PRESETS: dict[str, ConfigGeneracion] = {
    "A_consistente": ConfigGeneracion(nombre="A_consistente"),
    "B_base": ConfigGeneracion(nombre="B_base", usar_ficha_personaje=False, semilla_fija=False),
}
