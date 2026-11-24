"""
sonidos_sinteticos.py — Ambientes sonoros generados proceduralmente (NumPy + SciPy).

Por qué existe: AudioLDM-small producía siseo / señal casi plana para sonidos
agudos de textura (pájaros, hojas, grillos). Aquí cada sonido se SINTETIZA con
ruido filtrado, envolventes aleatorias y eventos (chirridos, crujidos, pasos…),
100 % local, sin descargas ni licencias de terceros (generación propia).

Uso: sintetizar("birds, rustling leaves, soft wind", duracion=10, semilla=7)
Las palabras clave del prompt (en inglés) eligen qué capas se mezclan.
"""
from __future__ import annotations

import re

import numpy as np
from scipy.signal import butter, sosfilt

SR = 22050


# ------------------------------- utilidades -------------------------------- #
def _bp(x, lo, hi, orden=2):
    return sosfilt(butter(orden, [lo, hi], "bandpass", fs=SR, output="sos"), x)


def _lp(x, hz, orden=2):
    return sosfilt(butter(orden, hz, "lowpass", fs=SR, output="sos"), x)


def _norm(x):
    return x / (np.max(np.abs(x)) + 1e-9)


def _lento(n, hz, rng):
    """Envolvente aleatoria suave en [0, 1] que varía ~hz veces por segundo."""
    e = _lp(rng.standard_normal(n), hz)
    return (e - e.min()) / (e.max() - e.min() + 1e-9)


def _poner(x, evento, t0):
    i = int(t0 * SR)
    if 0 <= i < len(x):
        fin = min(len(x), i + len(evento))
        x[i:fin] += evento[:fin - i]


def _rafagas(n, rng, por_segundo, tau, amp_exp=1.0):
    """Ráfagas cortas de ruido blanco con caída exponencial (crujidos, gotas)."""
    x = np.zeros(n)
    m = int(tau * 6 * SR)
    caida = np.exp(-np.arange(m) / (tau * SR))
    for _ in range(int(n / SR * por_segundo)):
        amp = rng.exponential(1.0) ** amp_exp
        _poner(x, rng.standard_normal(m) * caida * amp, rng.uniform(0, n / SR))
    return x


# --------------------------------- capas ----------------------------------- #
def viento(n, rng):
    """Tres bandas ESTRECHAS con envolventes independientes: el timbre cambia y suena a ráfaga, no a estática."""
    x = np.zeros(n)
    for fc, ancho, g in ((220, 0.5, 1.0), (450, 0.4, 0.8), (800, 0.35, 0.5)):
        banda = _norm(_bp(rng.standard_normal(n), fc * (1 - ancho / 2), fc * (1 + ancho / 2)))
        x += banda * g * (0.1 + 0.9 * _lento(n, rng.uniform(0.12, 0.3), rng) ** 1.5)
    return _norm(x)


def hojas(n, rng):
    return _norm(_bp(_rafagas(n, rng, 14, 0.012), 1800, 5500))


def fuego(n, rng):
    """Poco rumor de fondo y chasquidos marcados e irregulares (lo que da identidad al fuego)."""
    rumor = _norm(_bp(rng.standard_normal(n), 60, 300)) * (0.4 + 0.6 * _lento(n, 1.0, rng))
    chasquidos = _norm(_bp(_rafagas(n, rng, 10, 0.005, 1.8), 700, 4500))
    return _norm(rumor * 0.3 + chasquidos)


def pajaros(n, rng):
    x, dur = np.zeros(n), n / SR
    t = rng.uniform(0.3, 1.2)
    while t < dur - 0.3:
        f0 = rng.uniform(2200, 3800)
        for _ in range(int(rng.integers(2, 6))):          # un "canto" = 2-5 notas
            d = rng.uniform(0.06, 0.16)
            ts = np.arange(int(d * SR)) / SR
            f = f0 + rng.uniform(-900, 900) * ts / d       # barrido de frecuencia
            ev = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.hanning(len(ts)) * (1 + 0.3 * np.sin(2 * np.pi * 30 * ts))
            _poner(x, ev * rng.uniform(0.4, 1.0), t)
            t += d + rng.uniform(0.03, 0.1)
        t += rng.uniform(1.0, 3.5)
    return x


def buho(n, rng):
    x, dur = np.zeros(n), n / SR
    t = rng.uniform(1.0, 3.0)
    while t < dur - 1.5:
        for k in range(2):
            d = 0.35
            ts = np.arange(int(d * SR)) / SR
            f = 420 - 40 * ts / d
            ev = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.hanning(len(ts))
            _poner(x, ev * (1.0 if k == 0 else 0.8), t + k * 0.55)
        t += rng.uniform(4, 7)
    return x


def grillos(n, rng):
    t = np.arange(n) / SR
    pulsos = _lp((np.sin(2 * np.pi * 28 * t) > 0.2).astype(float), 120)
    grupos = _lp((np.sin(2 * np.pi * rng.uniform(2.2, 3.0) * t + rng.uniform(0, 6.28)) > 0.1).astype(float), 30)
    return np.sin(2 * np.pi * 4300 * t) * pulsos * grupos * (0.5 + 0.5 * _lento(n, 0.4, rng))


def madera(n, rng):
    x, dur = np.zeros(n), n / SR
    t = rng.uniform(0.5, 2.0)
    while t < dur - 1.0:
        d = rng.uniform(0.25, 0.6)
        ts = np.arange(int(d * SR)) / SR
        pulsos = (np.sin(2 * np.pi * (35 + 25 * ts / d) * ts) > 0).astype(float)
        _poner(x, rng.standard_normal(len(ts)) * pulsos * np.hanning(len(ts)), t)
        t += d + rng.uniform(1.5, 4.0)
    return _norm(_bp(x, 150, 1000))


def pasos(n, rng):
    x, dur = np.zeros(n), n / SR
    m = int(0.12 * SR)
    t = rng.uniform(0.2, 0.8)
    while t < dur - 0.2:
        _poner(x, rng.standard_normal(m) * np.exp(-np.arange(m) / (0.03 * SR)) * rng.uniform(0.7, 1.0), t)
        t += rng.uniform(0.55, 0.8)
    return _norm(_bp(x, 60, 350))


def lluvia(n, rng):
    base = _norm(_bp(rng.standard_normal(n), 1200, 6500)) * (0.7 + 0.3 * _lento(n, 2.0, rng))
    gotas = _norm(_bp(_rafagas(n, rng, 120, 0.002), 2000, 7000))
    return _norm(base * 0.6 + gotas * 0.5)


def agua(n, rng):
    """Corriente suave + burbujas (barridos ascendentes cortos) para que no suene a estática."""
    base = _norm(_bp(rng.standard_normal(n), 250, 1800)) * (0.3 + 0.7 * _lento(n, 3.0, rng))
    burb = np.zeros(n)
    for _ in range(int(n / SR * 10)):
        d = rng.uniform(0.03, 0.09)
        ts = np.arange(int(d * SR)) / SR
        f = rng.uniform(300, 700) * (1 + rng.uniform(1, 2.5) * ts / d)
        _poner(burb, np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-ts / (d / 2)) * rng.uniform(0.4, 1), rng.uniform(0, n / SR))
    return _norm(base * 0.6 + _norm(burb) * 0.8)


def zumbido(n, rng):
    """Zumbido grave con deriva lenta de tono y batido entre dos osciladores (menos 'tono fijo')."""
    t = np.arange(n) / SR
    deriva = 1 + 0.01 * np.sin(2 * np.pi * 0.1 * t)
    x = sum(a * (np.sin(2 * np.pi * np.cumsum(f0 * deriva) / SR) + np.sin(2 * np.pi * np.cumsum((f0 + 1.3) * deriva) / SR))
            for f0, a in ((110, 1.0), (220, 0.4), (330, 0.15)))
    return _norm(x) * (0.6 + 0.4 * _lento(n, 0.2, rng))


def campanilla(n, rng):
    x, dur = np.zeros(n), n / SR
    ts = np.arange(int(2.0 * SR)) / SR
    nota = sum(a * np.sin(2 * np.pi * f * ts) for f, a in ((1318, 1.0), (1975, 0.5), (2637, 0.25))) * np.exp(-ts / 0.8)
    for _ in range(int(rng.integers(1, 3))):
        _poner(x, nota, rng.uniform(0.5, max(0.6, dur - 2.5)))
    return x


# Palabra clave (inglés) -> (capa, ganancia)
CAPAS = [
    (r"wind|breeze|gust", viento, 0.7),
    (r"leaves|leaf|rustl|foliage", hojas, 0.6),
    (r"bird|chirp|tweet", pajaros, 0.7),
    (r"owl|hoot", buho, 0.5),
    (r"cricket|insect|cicada", grillos, 0.35),
    (r"fire|crackl|campfire", fuego, 1.0),
    (r"creak|wood|door", madera, 0.8),
    (r"footstep|walking|steps", pasos, 0.8),
    (r"rain|drizzle|storm", lluvia, 0.9),
    (r"water|river|stream|brook|wave", agua, 0.9),
    (r"hum|drone|buzz", zumbido, 0.25),
    (r"chime|bell", campanilla, 0.6),
]
LEJANAS = {pajaros, buho, campanilla}      # se atenúan si el prompt dice "distant/far/faint"


def sintetizar(prompt: str, duracion: float = 10.0, semilla: int = 0) -> np.ndarray:
    """Devuelve audio mono float32 a SR=22050 mezclando las capas que pide el prompt."""
    p = prompt.lower()
    rng = np.random.default_rng(semilla)
    n = int(duracion * SR)
    lejano = re.search(r"distant|far|faint", p) is not None
    mezcla = np.zeros(n)
    usadas = 0
    for patron, capa, ganancia in CAPAS:
        if re.search(patron, p):
            g = ganancia * (0.5 if (lejano and capa in LEJANAS) else 1.0)
            mezcla += capa(n, rng) * g
            usadas += 1
    if usadas == 0:                                        # sin palabra reconocida: viento suave
        mezcla = viento(n, rng)
    return (mezcla / (np.max(np.abs(mezcla)) + 1e-9)).astype(np.float32)
