# Storybook — cuentos ilustrados con IA generativa local

Proyecto integrador (IA Generativa, Ingeniería de Sistemas, Universidad Santo Tomás Tunja).
Dado una **idea** y un **tipo de cuento**, genera **exactamente 5 escenas** con texto, ilustración,
narración con voz sintética y ambiente sonoro. Todo corre **en local**, sin APIs de pago.

| Componente | Modelo / librería |
|---|---|
| Guion (JSON de 5 escenas) | `Qwen/Qwen3-4B-Instruct-2507` (Transformers, 4-bit NF4) |
| Ilustraciones | `stabilityai/stable-diffusion-xl-base-1.0` (Diffusers, fp16 + CPU offload) |
| Voz | Piper TTS, voz `es_MX-claude-high` (model card: Apache-2.0) |
| Ambiente sonoro | Síntesis procedural propia (NumPy/SciPy, `sonidos_sinteticos.py`); AudioLDM `cvssp/audioldm-s-full-v2` queda como motor alternativo (`--motor-ambiente audioldm`) |
| Interfaz | Pygame (libro con 2 canales de audio independientes) |

## Requisitos

- Windows 10/11, PowerShell, [`uv`](https://docs.astral.sh/uv/) instalado.
- GPU NVIDIA con controlador reciente (probado con RTX 4050 Laptop, **6 GB de VRAM**) y 16 GB de RAM.
- ~25 GB libres en disco para los pesos de los modelos.

## Instalación (PowerShell)

```powershell
git clone <URL-DE-TU-REPO> storybook
cd storybook

# Crea .venv e instala todo (PyTorch con CUDA 12.6 ya está configurado en pyproject.toml)
uv sync

# Verifica la GPU
uv run python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# Descarga única de modelos (necesita Internet)
uv run python descargar_modelos.py
```

Los pesos van al caché de Hugging Face (`%USERPROFILE%\.cache\huggingface`) y la voz de Piper a `models\piper\`.

## Ejecución

```powershell
uv run python main.py
```

1. En la portada escribe la idea y elige el género.
2. Pulsa **Crear cuento**. Se generan texto, voces, ambientes e imágenes (varios minutos) en `assets\generated\cuento_<fecha>_<preset>\`.
3. Navega con **Anterior / Siguiente** (o ← →). `Espacio` pausa la voz, `Esc` vuelve a la portada.
4. **Mis cuentos** lista todos los cuentos guardados (título, género, configuración A/B y fecha) para abrir cualquiera sin regenerar.

Sin conexión: `$env:HF_HUB_OFFLINE = "1"` antes de ejecutar.

## Pruebas de consistencia

Cada cuento guarda `historia.json` (prompts, parámetros) y `meta.json` (hardware, versiones, tiempos).
Compara las dos estrategias con la misma idea:

```powershell
uv run python generator.py --idea "Un faro que habla con las estrellas" --genero Misterio --preset A_consistente
uv run python generator.py --idea "Un faro que habla con las estrellas" --genero Misterio --preset B_base
```

- **A_consistente**: ficha visual del protagonista + escenario + estilo del género en cada prompt, semilla fija.
- **B_base**: solo acción + estilo, semilla distinta por escena.

Para automatizarlo: `uv run python pruebas.py` corre 3 ideas/géneros con A y B (edita `PRUEBAS` dentro del archivo) y deja en `assets\pruebas\informe\` el reporte `evidencias.md`, hojas de contacto con las 5 escenas y `resultados.csv`. Es reanudable; `--solo-reporte` rehace solo el reporte.

## Estructura

```
storybook/
├── main.py                # Fase 2: interfaz Pygame
├── generator.py           # Fase 1: guion, voz, ambiente, imágenes
├── config.py              # rutas, modelos, estilos por género, presets
├── sonidos_sinteticos.py  # ambientes procedurales (viento, pájaros, fuego…)
├── probar_sonido.py       # prueba prompts de ambiente sueltos
├── probar_voz.py          # escucha voces de Piper con el mismo texto
├── pruebas.py             # pruebas A/B de consistencia + reporte de evidencias
├── descargar_modelos.py   # descarga única de pesos
├── pyproject.toml
├── models/piper/          # voz Piper (se descarga sola)
└── assets/generated/      # cuentos generados
```

## Solución de problemas

- **`CUDA out of memory`**: cierra otras apps que usen la GPU; baja `ancho/alto` a 640 en `config.py`.
- **`bitsandbytes` falla en Windows**: actualiza el controlador NVIDIA y ejecuta `uv sync --upgrade-package bitsandbytes`.
- **Imágenes tardan**: reduce `pasos_imagen` a 20 o la resolución; con SDXL a 1024 px cada imagen tarda bastante más.
- **Aviso "tokens (>77)"**: CLIP recorta el prompt; acorta la ficha del personaje o usa la librería `compel`.

