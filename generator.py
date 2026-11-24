"""
generator.py — Fase 1: generación multimodal 100 % local.

Flujo de datos (una etapa a la vez para no desbordar los 6 GB de VRAM):

    idea + género
        │
        ▼
    [1] Qwen3-4B-Instruct (4-bit)  ──►  JSON: título, ficha del personaje,
        │                               escenario y 5 escenas (texto, acción
        │                               visual, sonido)         ── libera GPU
        ├─► [2] Piper TTS (CPU)     ──►  escena_XX_voz.wav
        ├─► [3] AudioLDM            ──►  escena_XX_ambiente.wav (bucle suave)
        └─► [4] SDXL (fp16+offload) ──►  escena_XX.png   (prompt = acción +
                                          ficha persistente + estilo del género)

Consistencia: el LLM produce UNA ficha visual del protagonista y del escenario;
ese texto se inyecta idéntico en el prompt de las 5 imágenes junto con un
estilo fijo por género y (opcionalmente) la misma semilla.

Uso por consola (útil para las pruebas del informe):
    uv run python generator.py --idea "Un faro que habla" --genero Misterio --preset A_consistente
"""
from __future__ import annotations

import argparse
import gc
import json
import platform
import time
import wave
from dataclasses import asdict, replace
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import torch
from scipy.io import wavfile
from scipy.signal import butter, sosfiltfilt

from sonidos_sinteticos import SR as SR_SINTETICO, sintetizar
from config import (DIR_GENERADO, DIR_VOCES, GENEROS, ID_AUDIO, ID_LLM, ID_SDXL,
                    PRESETS, VOZ_PIPER, ConfigGeneracion)

# Callback de progreso: (fracción 0..1, mensaje)
Progreso = Callable[[float, str], None]

HAY_CUDA = torch.cuda.is_available()
DISPOSITIVO = "cuda" if HAY_CUDA else "cpu"
DTYPE = torch.float16 if HAY_CUDA else torch.float32   # fp16 ahorra VRAM en la RTX 4050


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _sub(progreso: Progreso, ini: float, fin: float) -> Progreso:
    """Mapea el progreso local de una etapa (0..1) a un tramo del progreso total."""
    return lambda f, m: progreso(ini + (fin - ini) * f, m)


def liberar_memoria() -> None:
    """Libera RAM y VRAM entre etapas (clave en GPUs de 6 GB)."""
    gc.collect()
    if HAY_CUDA:
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def info_hardware() -> dict:
    """Datos de hardware y versiones para la sección 'hardware' del informe."""
    def ver(paquete: str) -> str:
        try:
            return metadata.version(paquete)
        except metadata.PackageNotFoundError:
            return "no instalado"

    info = {
        "python": platform.python_version(),
        "sistema": platform.platform(),
        "cpu": platform.processor(),
        "torch": torch.__version__,
        "cuda_disponible": HAY_CUDA,
        "versiones": {p: ver(p) for p in ("transformers", "diffusers", "accelerate",
                                           "bitsandbytes", "piper-tts", "pygame")},
    }
    if HAY_CUDA:
        props = torch.cuda.get_device_properties(0)
        info["gpu"] = props.name
        info["vram_gb"] = round(props.total_memory / 1024**3, 1)
    return info


# --------------------------------------------------------------------------- #
# [1] Guion con Qwen3-4B-Instruct
# --------------------------------------------------------------------------- #
SYSTEM_GUION = ("Eres un escritor de cuentos ilustrados. Respondes ÚNICAMENTE con un "
                "objeto JSON válido, sin texto adicional ni bloques de código.")

PLANTILLA_USUARIO = """Escribe un cuento en español.
Idea: {idea}
Género: {genero}

Reglas:
- Exactamente 5 escenas: la 1 es el inicio, la 2-4 el desarrollo y la 5 el desenlace.
- El MISMO protagonista y el MISMO lugar principal en toda la historia; no cambies nombres ni rasgos.
- Cada "texto": entre 40 y 70 palabras, en español, con el tono del género.
- "personaje_visual": descripción física FIJA del protagonista EN INGLÉS (máx. 20 palabras): edad, pelo, ropa, colores.
- "escenario_visual": el lugar principal EN INGLÉS (máx. 10 palabras).
- "accion_imagen": lo que se ve en la escena EN INGLÉS (máx. 15 palabras), sin repetir la descripción del personaje.
- "sonido": ambiente sonoro de la escena EN INGLÉS con 2 a 4 elementos separados por coma, elegidos SOLO de esta lista: wind, breeze, leaves, birds, owl, crickets, fire, creaking wood, footsteps, rain, water, hum, chime. Incluye SIEMPRE al menos un sonido puntual (birds, owl, crickets, leaves, footsteps, creaking wood, chime, fire) además del fondo (wind, rain, water, hum). Puedes anteponer "distant" o "soft" (ej.: "soft wind, distant birds, leaves").

Formato EXACTO:
{{"titulo": "...", "personaje_visual": "...", "escenario_visual": "...", "escenas": [{{"texto": "...", "accion_imagen": "...", "sonido": "..."}}]}}
La lista "escenas" debe tener exactamente 5 elementos."""


def extraer_json(texto: str) -> dict:
    """Extrae el primer objeto JSON de la respuesta (tolera ```json ... ```)."""
    ini, fin = texto.find("{"), texto.rfind("}")
    if ini == -1 or fin == -1:
        raise ValueError("La respuesta del modelo no contiene JSON.")
    return json.loads(texto[ini:fin + 1])


def validar_historia(d: dict) -> None:
    """Verifica la estructura exigida: título, ficha visual y EXACTAMENTE 5 escenas."""
    for clave in ("titulo", "personaje_visual", "escenario_visual", "escenas"):
        if not d.get(clave):
            raise ValueError(f"Falta el campo '{clave}'.")
    if not isinstance(d["escenas"], list) or len(d["escenas"]) != 5:
        raise ValueError("Se requieren exactamente 5 escenas.")
    for i, esc in enumerate(d["escenas"], 1):
        for campo in ("texto", "accion_imagen", "sonido"):
            if not str(esc.get(campo, "")).strip():
                raise ValueError(f"Escena {i}: falta '{campo}'.")


def generar_guion(idea: str, genero: str, cfg: ConfigGeneracion, progreso: Progreso) -> dict:
    """Carga Qwen3-4B, genera el guion en JSON (con reintentos) y libera la GPU."""
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    progreso(0.0, "Cargando modelo de texto (Qwen3-4B)…")
    tok = AutoTokenizer.from_pretrained(ID_LLM)
    if HAY_CUDA and cfg.cuantizar_llm_4bit:
        # 4B en fp16 ocupa ~8 GB: no cabe en 6 GB. NF4 lo reduce a ~2.5 GB.
        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                 bnb_4bit_compute_dtype=torch.float16,
                                 bnb_4bit_use_double_quant=True)
        modelo = AutoModelForCausalLM.from_pretrained(ID_LLM, quantization_config=bnb,
                                                      device_map={"": 0})
    elif HAY_CUDA:
        modelo = AutoModelForCausalLM.from_pretrained(ID_LLM, torch_dtype=DTYPE, device_map="auto")
    else:
        modelo = AutoModelForCausalLM.from_pretrained(ID_LLM, torch_dtype=DTYPE)

    try:
        ultimo_error: Optional[Exception] = None
        for intento in range(1, cfg.reintentos_llm + 1):
            progreso(0.4, f"Escribiendo el cuento (intento {intento}/{cfg.reintentos_llm})…")
            mensajes = [
                {"role": "system", "content": SYSTEM_GUION},
                {"role": "user", "content": PLANTILLA_USUARIO.format(idea=idea, genero=genero)},
            ]
            prompt = tok.apply_chat_template(mensajes, tokenize=False, add_generation_prompt=True)
            entradas = tok([prompt], return_tensors="pt").to(modelo.device)
            with torch.inference_mode():
                salida = modelo.generate(
                    **entradas, max_new_tokens=cfg.max_tokens, do_sample=True,
                    temperature=max(0.3, cfg.temperatura - 0.15 * (intento - 1)),  # más estricto al reintentar
                    top_p=0.8, top_k=20, repetition_penalty=1.05)
            respuesta = tok.decode(salida[0][entradas.input_ids.shape[1]:], skip_special_tokens=True)
            try:
                historia = extraer_json(respuesta)
                validar_historia(historia)
                progreso(1.0, "Guion listo.")
                return historia
            except (ValueError, json.JSONDecodeError) as e:
                ultimo_error = e
                print(f"[guion] intento {intento} inválido: {e}")
        raise RuntimeError(f"No se obtuvo un guion válido tras {cfg.reintentos_llm} intentos: {ultimo_error}")
    finally:
        del modelo, tok
        liberar_memoria()


# --------------------------------------------------------------------------- #
# [2] Voz con Piper TTS (CPU, muy rápido)
# --------------------------------------------------------------------------- #
def _subdir_voz(nombre: str) -> str:
    """'es_MX-claude-high' -> 'es/es_MX/claude/high' (estructura del repo rhasspy/piper-voices)."""
    region, voz, calidad = nombre.split("-", 2)
    return f"{region.split('_')[0]}/{region}/{voz}/{calidad}"


def asegurar_voz(nombre: Optional[str] = None) -> Path:
    """Devuelve la ruta del .onnx de la voz; la descarga una sola vez si falta."""
    nombre = nombre or VOZ_PIPER
    subdir = _subdir_voz(nombre)
    ruta = DIR_VOCES / subdir / f"{nombre}.onnx"
    if not ruta.exists():
        from huggingface_hub import hf_hub_download
        for ext in (".onnx", ".onnx.json"):
            hf_hub_download("rhasspy/piper-voices", f"{subdir}/{nombre}{ext}", local_dir=DIR_VOCES)
    return ruta


def sintetizar_texto(voz, texto: str, ruta: Path, cfg: ConfigGeneracion) -> None:
    """
    Sintetiza `texto` a un .wav con estilo "cuentacuentos": más lento, ritmo más variable
    y una pausa entre oraciones (Piper entrega una oración por fragmento).
    """
    try:
        from piper import SynthesisConfig
    except ImportError:                      # piper-tts antiguo (1.2): sin parámetros de estilo
        with wave.open(str(ruta), "wb") as wf:
            voz.synthesize(texto, wf)
        return
    syn = SynthesisConfig(length_scale=cfg.velocidad_voz, noise_scale=cfg.variacion_tono_voz,
                          noise_w_scale=cfg.variacion_ritmo_voz)
    with wave.open(str(ruta), "wb") as wf:
        primero = True
        for chunk in voz.synthesize(texto, syn_config=syn):
            if primero:
                wf.setnchannels(chunk.sample_channels)
                wf.setsampwidth(chunk.sample_width)
                wf.setframerate(chunk.sample_rate)
                primero = False
            else:                            # pausa entre oraciones
                n = int(chunk.sample_rate * cfg.pausa_oracion)
                wf.writeframes(bytes(n * chunk.sample_width * chunk.sample_channels))
            wf.writeframes(chunk.audio_int16_bytes)


def sintetizar_voces(escenas: list[dict], carpeta: Path, cfg: ConfigGeneracion, progreso: Progreso) -> None:
    from piper import PiperVoice

    voz = PiperVoice.load(str(asegurar_voz(cfg.voz)))
    for i, esc in enumerate(escenas, 1):
        progreso((i - 1) / len(escenas), f"Narrando escena {i}/5…")
        ruta = carpeta / f"escena_{i:02d}_voz.wav"
        sintetizar_texto(voz, esc["texto"], ruta, cfg)
        esc["voz"] = ruta.name
    progreso(1.0, "Voces listas.")


def regenerar_voces(carpeta: Path, cfg: Optional[ConfigGeneracion] = None) -> None:
    """Rehace SOLO las voces de un cuento ya generado (imágenes y ambientes quedan igual)."""
    cfg = cfg or PRESETS["A_consistente"]
    ruta_json = carpeta / "historia.json"
    historia = json.loads(ruta_json.read_text(encoding="utf-8"))
    sintetizar_voces(historia["escenas"], carpeta, cfg, lambda f, m: print(f"[{f:4.0%}] {m}"))
    ruta_json.write_text(json.dumps(historia, ensure_ascii=False, indent=2), encoding="utf-8")
    ruta_meta = carpeta / "meta.json"
    if ruta_meta.exists():
        meta = json.loads(ruta_meta.read_text(encoding="utf-8"))
        meta.setdefault("modelos", {})["voz"] = cfg.voz
        ruta_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# [3] Ambiente sonoro con AudioLDM
# --------------------------------------------------------------------------- #
def _puntaje_ambiente(audio: np.ndarray, sr: int) -> float:
    """
    Heurística para elegir el mejor candidato de AudioLDM:
      - penaliza el "siseo" (energía concentrada sobre 4 kHz),
      - premia la variación en el tiempo (pájaros, crujidos = eventos; ruido blanco = plano).
    """
    espectro = np.abs(np.fft.rfft(audio)) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / sr)
    ratio_grave = float(espectro[freqs < 4000].sum() / (espectro.sum() + 1e-12))
    ventana = sr // 4
    rms = [np.sqrt(np.mean(audio[i:i + ventana] ** 2)) for i in range(0, len(audio) - ventana, ventana)]
    variacion = float(np.std(rms) / (np.mean(rms) + 1e-9))
    return ratio_grave + min(variacion, 1.0)


def _guardar_ambiente_en_bucle(audio: np.ndarray, sr: int, ruta: Path, fundido: float = 0.5,
                               rms_objetivo: float = 0.10) -> None:
    """
    Postproceso del ambiente: filtra el siseo, hace el audio "loopeable" (crossfade
    final→inicio, sin clic) y lo normaliza por RMS (no por pico) para que suene
    como fondo y no tape la voz.
    """
    audio = np.asarray(audio, dtype=np.float32).flatten()
    # Filtros: quita rumble (<80 Hz) y siseo agudo (>5 kHz)
    audio = sosfiltfilt(butter(2, 80, "highpass", fs=sr, output="sos"), audio)
    audio = sosfiltfilt(butter(4, 5000, "lowpass", fs=sr, output="sos"), audio)
    n = int(sr * fundido)
    if len(audio) > 4 * n:
        rampa = np.linspace(0.0, 1.0, n, dtype=np.float32)
        cuerpo = audio[:-n].copy()
        cuerpo[:n] = cuerpo[:n] * rampa + audio[-n:] * (1.0 - rampa)
        audio = cuerpo
    audio = audio * (rms_objetivo / (float(np.sqrt(np.mean(audio ** 2))) + 1e-9))
    audio = audio * min(1.0, 0.9 / (float(np.max(np.abs(audio))) + 1e-9))   # límite de pico
    wavfile.write(str(ruta), sr, (audio * 32767).astype(np.int16))


def _crear_pipeline_audio():
    from diffusers import AudioLDMPipeline
    # float32 a propósito: en fp16 el vocoder de AudioLDM puede producir siseo. El modelo
    # es pequeño (~1 GB) y cabe de sobra en 6 GB de VRAM.
    return AudioLDMPipeline.from_pretrained(ID_AUDIO, torch_dtype=torch.float32).to(DISPOSITIVO)


def _generar_un_ambiente(pipe, sonido: str, ruta: Path, cfg: ConfigGeneracion, semilla: int) -> None:
    """Genera el ambiente con el motor elegido y lo guarda (con postproceso y bucle)."""
    if cfg.motor_ambiente == "sintetico":
        _guardar_ambiente_en_bucle(sintetizar(sonido, cfg.duracion_ambiente, semilla), SR_SINTETICO, ruta)
        return
    gen = torch.Generator(DISPOSITIVO).manual_seed(semilla)
    candidatos = pipe(
        prompt=sonido,
        negative_prompt="low quality, average quality, speech, human voice, music",
        num_inference_steps=cfg.pasos_audio,
        guidance_scale=cfg.guidance_audio,
        audio_length_in_s=cfg.duracion_ambiente,
        num_waveforms_per_prompt=cfg.candidatos_audio,
        generator=gen,
    ).audios
    mejor = max(candidatos, key=lambda a: _puntaje_ambiente(np.asarray(a, dtype=np.float32), 16000))
    _guardar_ambiente_en_bucle(mejor, 16000, ruta)   # AudioLDM trabaja a 16 kHz


def generar_ambientes(escenas: list[dict], carpeta: Path, cfg: ConfigGeneracion,
                      progreso: Progreso) -> None:
    progreso(0.0, "Preparando ambientes sonoros…")
    pipe = _crear_pipeline_audio() if cfg.motor_ambiente == "audioldm" else None
    try:
        for i, esc in enumerate(escenas, 1):
            progreso((i - 1) / len(escenas), f"Creando ambiente sonoro {i}/5…")
            ruta = carpeta / f"escena_{i:02d}_ambiente.wav"
            _generar_un_ambiente(pipe, esc["sonido"], ruta, cfg, cfg.semilla + i)
            esc["ambiente"] = ruta.name
    finally:
        del pipe
        liberar_memoria()
    progreso(1.0, "Ambientes listos.")


def regenerar_ambientes(carpeta: Path, cfg: Optional[ConfigGeneracion] = None,
                        escenas_a_rehacer: Optional[list[int]] = None) -> None:
    """
    Rehace SOLO los ambientes de un cuento ya generado (sin tocar imágenes ni voz).
    Puedes editar el campo "sonido" de cada escena en historia.json antes de ejecutar.
    """
    cfg = cfg or PRESETS["A_consistente"]
    ruta_json = carpeta / "historia.json"
    historia = json.loads(ruta_json.read_text(encoding="utf-8"))
    pipe = _crear_pipeline_audio() if cfg.motor_ambiente == "audioldm" else None
    try:
        for i, esc in enumerate(historia["escenas"], 1):
            if escenas_a_rehacer and i not in escenas_a_rehacer:
                continue
            print(f"[ambiente] escena {i}: {esc['sonido']}")
            ruta = carpeta / f"escena_{i:02d}_ambiente.wav"
            _generar_un_ambiente(pipe, esc["sonido"], ruta, cfg, cfg.semilla + i)
            esc["ambiente"] = ruta.name
    finally:
        del pipe
        liberar_memoria()
    ruta_json.write_text(json.dumps(historia, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# [4] Ilustraciones con SDXL
# --------------------------------------------------------------------------- #
def construir_prompt_imagen(historia: dict, escena: dict, estilo: str, cfg: ConfigGeneracion) -> str:
    """
    Estrategia de consistencia: la parte persistente (ficha del personaje,
    escenario y estilo del género) se repite idéntica en las 5 escenas.
    Lo único que cambia es la acción de la escena.
    """
    if cfg.usar_ficha_personaje:
        partes = [escena["accion_imagen"], historia["personaje_visual"],
                  historia["escenario_visual"], estilo]
    else:   # configuración B (línea base): solo la acción y el estilo
        partes = [escena["accion_imagen"], estilo]
    return ", ".join(p.strip().rstrip(".") for p in partes)


def generar_imagenes(historia: dict, genero: str, carpeta: Path, cfg: ConfigGeneracion,
                     progreso: Progreso) -> None:
    from diffusers import StableDiffusionXLPipeline

    progreso(0.0, "Cargando modelo de imagen (SDXL)…")
    kwargs = dict(torch_dtype=DTYPE, use_safetensors=True)
    if HAY_CUDA:
        kwargs["variant"] = "fp16"
    pipe = StableDiffusionXLPipeline.from_pretrained(ID_SDXL, **kwargs)
    if HAY_CUDA:
        # Mueve cada submodelo a la GPU solo mientras se usa: SDXL cabe en 6 GB de VRAM.
        pipe.enable_model_cpu_offload()
    pipe.vae.enable_slicing()
    pipe.vae.enable_tiling()

    estilo = GENEROS.get(genero, f"{genero} story, storybook illustration")
    try:
        for i, esc in enumerate(historia["escenas"], 1):
            progreso((i - 1) / 5, f"Dibujando escena {i}/5…")
            prompt = construir_prompt_imagen(historia, esc, estilo, cfg)
            n_tokens = len(pipe.tokenizer(prompt).input_ids)
            if n_tokens > 77:
                print(f"[imagen] AVISO escena {i}: {n_tokens} tokens (>77), CLIP recortará el final.")
            semilla = cfg.semilla if cfg.semilla_fija else cfg.semilla + 1000 * i
            img = pipe(prompt=prompt, negative_prompt=cfg.prompt_negativo,
                       width=cfg.ancho, height=cfg.alto,
                       num_inference_steps=cfg.pasos_imagen, guidance_scale=cfg.guidance,
                       generator=torch.Generator("cpu").manual_seed(semilla)).images[0]
            ruta = carpeta / f"escena_{i:02d}.png"
            img.save(ruta)
            esc["imagen"], esc["prompt_imagen"] = ruta.name, prompt
    finally:
        del pipe
        liberar_memoria()
    progreso(1.0, "Ilustraciones listas.")


# --------------------------------------------------------------------------- #
# Orquestador
# --------------------------------------------------------------------------- #
def generar_cuento(idea: str, genero: str, cfg: Optional[ConfigGeneracion] = None,
                   progreso: Optional[Progreso] = None, dir_base: Optional[Path] = None) -> Path:
    """
    Genera el cuento completo y devuelve la carpeta con historia.json + assets.
    Lanza ValueError si idea o género están vacíos (requisito de validación).
    """
    idea, genero = (idea or "").strip(), (genero or "").strip()
    if not idea:
        raise ValueError("La idea del cuento no puede estar vacía.")
    if not genero:
        raise ValueError("Debes seleccionar el tipo de cuento.")
    cfg = cfg or PRESETS["A_consistente"]
    progreso = progreso or (lambda f, m: print(f"[{f:5.0%}] {m}"))

    carpeta = (dir_base or DIR_GENERADO) / f"cuento_{datetime.now():%Y%m%d_%H%M%S}_{cfg.nombre}"
    carpeta.mkdir(parents=True, exist_ok=True)
    tiempos: dict[str, float] = {}

    def cronometrar(nombre: str, fn, *args):
        t0 = time.perf_counter()
        r = fn(*args)
        tiempos[nombre] = round(time.perf_counter() - t0, 1)
        return r

    # Etapas y su peso en la barra de progreso (SDXL es la más lenta).
    historia = cronometrar("guion_s", generar_guion, idea, genero, cfg, _sub(progreso, 0.00, 0.15))
    historia.update({"idea": idea, "genero": genero, "config": asdict(cfg)})
    escenas = historia["escenas"]
    for n, esc in enumerate(escenas, 1):
        esc["numero"] = n

    cronometrar("voz_s", sintetizar_voces, escenas, carpeta, cfg, _sub(progreso, 0.15, 0.22))
    cronometrar("ambiente_s", generar_ambientes, escenas, carpeta, cfg, _sub(progreso, 0.22, 0.45))
    cronometrar("imagenes_s", generar_imagenes, historia, genero, carpeta, cfg, _sub(progreso, 0.45, 1.0))

    (carpeta / "historia.json").write_text(json.dumps(historia, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {"hardware": info_hardware(), "tiempos": tiempos,
            "modelos": {"llm": ID_LLM, "imagen": ID_SDXL, "audio": ID_AUDIO, "voz": cfg.voz}}
    (carpeta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    progreso(1.0, "¡Cuento terminado!")
    return carpeta


# --------------------------------------------------------------------------- #
# CLI (pruebas del informe: mismas ideas con preset A y B)
# --------------------------------------------------------------------------- #
def _cli() -> None:
    ap = argparse.ArgumentParser(description="Genera un cuento Storybook sin interfaz.")
    ap.add_argument("--idea")
    ap.add_argument("--regenerar-ambientes", metavar="CARPETA",
                    help="rehace solo los ambientes de un cuento existente")
    ap.add_argument("--regenerar-voces", metavar="CARPETA",
                    help="rehace solo las voces de un cuento existente")
    ap.add_argument("--voz", help="nombre de voz Piper, ej. es_MX-claude-high (sobrescribe config)")
    ap.add_argument("--motor-ambiente", choices=["sintetico", "audioldm"],
                    help="sobrescribe el motor de ambiente del preset")
    ap.add_argument("--escenas", type=int, nargs="*", help="con lo anterior: solo estas escenas (1-5)")
    ap.add_argument("--genero", default="Fantasía")
    ap.add_argument("--preset", choices=list(PRESETS), default="A_consistente")
    args = ap.parse_args()
    cfg = PRESETS[args.preset]
    if args.motor_ambiente:
        cfg = replace(cfg, motor_ambiente=args.motor_ambiente)
    if args.voz:
        cfg = replace(cfg, voz=args.voz)
    if args.regenerar_voces:
        regenerar_voces(Path(args.regenerar_voces), cfg)
        return
    if args.regenerar_ambientes:
        regenerar_ambientes(Path(args.regenerar_ambientes), cfg, args.escenas)
        return
    if not args.idea:
        ap.error("--idea es obligatoria (o usa --regenerar-ambientes / --regenerar-voces CARPETA)")
    print("Hardware:", json.dumps(info_hardware(), ensure_ascii=False))
    print("Carpeta:", generar_cuento(args.idea, args.genero, cfg))


if __name__ == "__main__":
    _cli()
