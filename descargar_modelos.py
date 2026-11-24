"""
descargar_modelos.py — Descarga única de todos los pesos (necesita Internet).

Después de ejecutarlo puedes trabajar sin conexión:
    $env:HF_HUB_OFFLINE = "1"
"""
from huggingface_hub import snapshot_download

from config import ID_AUDIO, ID_LLM, ID_SDXL
from generator import asegurar_voz

print("1/4 Qwen3-4B-Instruct (~8 GB)…")
snapshot_download(ID_LLM, max_workers=1)

print("2/4 SDXL base, solo variante fp16 (~7 GB)…")
snapshot_download(ID_SDXL, allow_patterns=["*.json", "*.txt", "*.fp16.safetensors"], max_workers=1)

print("3/4 AudioLDM…")
snapshot_download(ID_AUDIO, ignore_patterns=["*.ckpt"], max_workers=1)

print("4/4 Voz de Piper…")
print("Voz en:", asegurar_voz())
print("Listo. Ya puedes ejecutar sin conexión.")
