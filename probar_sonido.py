"""
probar_sonido.py — Prueba prompts de ambiente sin generar un cuento completo.

    uv run python probar_sonido.py "soft wind, distant birds, leaves" "fire, creaking wood"
    uv run python probar_sonido.py --audioldm "soft wind"      # compara con AudioLDM

Guarda en assets\\pruebas_sonido\\ NN_crudo.wav (sin postproceso) y NN_final.wav (como lo usa el libro).
"""
import sys

import numpy as np
from scipy.io import wavfile

import generator as g
from config import PRESETS, RAIZ
from sonidos_sinteticos import SR as SR_SINT, sintetizar

SALIDA = RAIZ / "assets" / "pruebas_sonido"


def _guardar_crudo(audio, sr, ruta):
    a = np.asarray(audio, dtype=np.float32)
    wavfile.write(str(ruta), sr, (a / (np.abs(a).max() + 1e-9) * 0.5 * 32767).astype(np.int16))


def main(prompts: list[str], usar_audioldm: bool) -> None:
    cfg = PRESETS["A_consistente"]
    SALIDA.mkdir(parents=True, exist_ok=True)
    pipe = g._crear_pipeline_audio() if usar_audioldm else None
    try:
        for n, prompt in enumerate(prompts, 1):
            if usar_audioldm:
                gen = g.torch.Generator(g.DISPOSITIVO).manual_seed(cfg.semilla + n)
                cands = pipe(prompt=prompt, negative_prompt="low quality, average quality, speech, human voice, music",
                             num_inference_steps=cfg.pasos_audio, guidance_scale=cfg.guidance_audio,
                             audio_length_in_s=cfg.duracion_ambiente,
                             num_waveforms_per_prompt=cfg.candidatos_audio, generator=gen).audios
                audio, sr = max(cands, key=lambda a: g._puntaje_ambiente(np.asarray(a, dtype=np.float32), 16000)), 16000
            else:
                audio, sr = sintetizar(prompt, cfg.duracion_ambiente, cfg.semilla + n), SR_SINT
            _guardar_crudo(audio, sr, SALIDA / f"{n:02d}_crudo.wav")
            g._guardar_ambiente_en_bucle(audio, sr, SALIDA / f"{n:02d}_final.wav")
            print(f"{n:02d} '{prompt}' -> {SALIDA}")
    finally:
        del pipe
        g.liberar_memoria()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--audioldm"]
    if not args:
        sys.exit('Uso: uv run python probar_sonido.py [--audioldm] "prompt 1" "prompt 2" ...')
    main(args, "--audioldm" in sys.argv)
