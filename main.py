"""
main.py — Fase 2: interfaz en Pygame que simula un libro físico.

Estados:  portada  ->  generando  ->  libro (páginas 1..5)
          portada  ->  biblioteca (elegir un cuento guardado)  ->  libro

Audio (requisito de la rúbrica): dos canales INDEPENDIENTES de pygame.mixer
    Canal 0 -> voz de Piper TTS      (volumen alto)
    Canal 1 -> ambiente de AudioLDM  (en bucle, volumen suave)

La generación pesada corre en un hilo aparte para que la ventana no se congele.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

import pygame

from config import DIR_GENERADO, GENEROS, PRESETS

# --------------------------------------------------------------------------- #
# Constantes visuales
# --------------------------------------------------------------------------- #
ANCHO, ALTO, FPS = 1200, 720, 60
DURACION_TRANSICION = 0.5          # segundos que dura el "pasar de página"
LADO_IMAGEN = 480
FILAS_BIB = 6                      # filas visibles en la biblioteca

C_MESA = (58, 38, 28)
C_CUBIERTA = (96, 52, 40)
C_PAPEL = (245, 236, 214)
C_PAPEL_DER = (250, 242, 222)
C_TINTA = (50, 38, 30)
C_TINTA_SUAVE = (110, 95, 80)
C_DORADO = (212, 175, 55)
C_BOTON = (120, 72, 50)
C_BOTON_HOVER = (150, 92, 64)
C_BOTON_OFF = (160, 148, 135)
C_TEXTO_BOTON = (255, 248, 230)
C_ERROR = (190, 50, 50)


# --------------------------------------------------------------------------- #
# Utilidades de dibujo
# --------------------------------------------------------------------------- #
def ajustar_texto(texto: str, fuente: pygame.font.Font, ancho_max: int) -> list[str]:
    """Divide el texto en líneas que caben en `ancho_max` píxeles."""
    lineas: list[str] = []
    for parrafo in texto.split("\n"):
        actual = ""
        for palabra in parrafo.split():
            prueba = f"{actual} {palabra}".strip()
            if fuente.size(prueba)[0] <= ancho_max:
                actual = prueba
            else:
                if actual:
                    lineas.append(actual)
                actual = palabra
        lineas.append(actual)
    return lineas


def dibujar_texto(pantalla, texto, fuente, color, x, y, ancho_max=None, interlineado=1.25,
                  centrado=False, max_lineas=None) -> int:
    """Dibuja texto (con salto de línea automático). Devuelve la y final."""
    lineas = ajustar_texto(texto, fuente, ancho_max) if ancho_max else [texto]
    if max_lineas:
        lineas = lineas[:max_lineas]
    alto_linea = int(fuente.get_linesize() * interlineado)
    for linea in lineas:
        surf = fuente.render(linea, True, color)
        rect = surf.get_rect(midtop=(x + (ancho_max or 0) // 2, y)) if centrado else surf.get_rect(topleft=(x, y))
        pantalla.blit(surf, rect)
        y += alto_linea
    return y


class Boton:
    """Botón rectangular con texto mutable y acción al hacer clic."""

    def __init__(self, rect, texto, accion, fuente):
        self.rect, self.texto, self.accion, self.fuente = pygame.Rect(rect), texto, accion, fuente
        self.activo = True
        self.seleccionado = False      # para los "chips" de género

    def dibujar(self, pantalla, pos_mouse):
        if not self.activo:
            color = C_BOTON_OFF
        elif self.seleccionado:
            color = C_DORADO
        elif self.rect.collidepoint(pos_mouse):
            color = C_BOTON_HOVER
        else:
            color = C_BOTON
        pygame.draw.rect(pantalla, color, self.rect, border_radius=8)
        pygame.draw.rect(pantalla, C_DORADO, self.rect, 2, border_radius=8)
        txt = self.fuente.render(self.texto, True, C_TINTA if self.seleccionado else C_TEXTO_BOTON)
        pantalla.blit(txt, txt.get_rect(center=self.rect.center))

    def clic(self, pos) -> bool:
        if self.activo and self.rect.collidepoint(pos):
            self.accion()
            return True
        return False


class Deslizador:
    """Control de volumen horizontal (0.0 – 1.0) que se arrastra con el mouse."""

    def __init__(self, x, y, ancho, valor):
        self.rect = pygame.Rect(x, y, ancho, 8)
        self.valor = valor
        self.arrastrando = False

    def _mover(self, x):
        self.valor = min(1.0, max(0.0, (x - self.rect.x) / self.rect.width))

    def evento(self, e):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.rect.inflate(0, 28).collidepoint(e.pos):
            self.arrastrando = True
            self._mover(e.pos[0])
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.arrastrando = False
        elif e.type == pygame.MOUSEMOTION and self.arrastrando:
            self._mover(e.pos[0])

    def dibujar(self, pantalla):
        pygame.draw.rect(pantalla, C_TINTA_SUAVE, self.rect, border_radius=4)
        lleno = self.rect.copy()
        lleno.width = int(self.rect.width * self.valor)
        pygame.draw.rect(pantalla, C_BOTON, lleno, border_radius=4)
        pygame.draw.circle(pantalla, C_DORADO, (self.rect.x + lleno.width, self.rect.centery), 9)
        pygame.draw.circle(pantalla, C_TINTA, (self.rect.x + lleno.width, self.rect.centery), 9, 2)


# --------------------------------------------------------------------------- #
# Audio: dos canales independientes
# --------------------------------------------------------------------------- #
class GestorAudio:
    def __init__(self):
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        pygame.mixer.set_num_channels(8)
        self.canal_voz = pygame.mixer.Channel(0)       # voz (Piper)
        self.canal_amb = pygame.mixer.Channel(1)       # ambiente (AudioLDM)
        self.voces: list[pygame.mixer.Sound] = []
        self.ambientes: list[pygame.mixer.Sound] = []
        self.vol_voz, self.vol_amb = 1.0, 0.35         # voz fuerte, ambiente suave
        self.voz_pausada = False
        self.ambiente_silenciado = False

    def cargar(self, carpeta: Path, escenas: list[dict]):
        """Precarga los 10 audios en memoria (pesan pocos MB)."""
        self.voces = [pygame.mixer.Sound(str(carpeta / e["voz"])) for e in escenas]
        self.ambientes = [pygame.mixer.Sound(str(carpeta / e["ambiente"])) for e in escenas]

    def reproducir_escena(self, i: int):
        """Al entrar a una página: ambiente en bucle (canal 1) + voz (canal 0)."""
        self.detener()
        self.canal_amb.play(self.ambientes[i], loops=-1)
        self.canal_voz.play(self.voces[i])
        self.aplicar_volumenes()

    def aplicar_volumenes(self):
        # Se aplica cada frame: pygame reinicia el volumen del canal al reproducir un sonido nuevo.
        self.canal_voz.set_volume(self.vol_voz)
        self.canal_amb.set_volume(0.0 if self.ambiente_silenciado else self.vol_amb)

    def alternar_voz(self, i: int):
        if self.voz_pausada:
            self.canal_voz.unpause()
            self.voz_pausada = False
        elif self.canal_voz.get_busy():
            self.canal_voz.pause()
            self.voz_pausada = True
        else:                                           # la voz ya terminó: reproducir de nuevo
            self.canal_voz.play(self.voces[i])

    def reiniciar_voz(self, i: int):
        self.canal_voz.stop()
        self.voz_pausada = False
        self.canal_voz.play(self.voces[i])

    def alternar_ambiente(self):
        self.ambiente_silenciado = not self.ambiente_silenciado

    def detener(self):
        self.canal_voz.stop()
        self.canal_amb.stop()
        self.voz_pausada = False

    def texto_boton_voz(self) -> str:
        if self.voz_pausada:
            return "Reanudar voz"
        return "Pausar voz" if self.canal_voz.get_busy() else "Escuchar voz"


# --------------------------------------------------------------------------- #
# Generación en segundo plano
# --------------------------------------------------------------------------- #
class TareaGeneracion(threading.Thread):
    def __init__(self, idea: str, genero: str, preset: str):
        super().__init__(daemon=True)           # daemon: no bloquea el cierre de la ventana
        self.idea, self.genero, self.preset = idea, genero, preset
        self.fraccion, self.mensaje = 0.0, "Iniciando…"
        self.resultado: Path | None = None
        self.error: str | None = None
        self.terminada = False
        self.inicio = time.time()

    def _progreso(self, fraccion: float, mensaje: str):
        self.fraccion, self.mensaje = fraccion, mensaje

    def run(self):
        try:
            # Import perezoso: torch tarda en cargar y la portada debe abrir al instante.
            from generator import generar_cuento
            self.resultado = generar_cuento(self.idea, self.genero, PRESETS[self.preset], self._progreso)
        except Exception as e:  # noqa: BLE001 - mostramos cualquier fallo en la UI
            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.terminada = True


# --------------------------------------------------------------------------- #
# Aplicación
# --------------------------------------------------------------------------- #
class Aplicacion:
    def __init__(self):
        pygame.init()
        self.pantalla = pygame.display.set_mode((ANCHO, ALTO))
        pygame.display.set_caption("Storybook — cuentos con IA generativa local")
        self.reloj = pygame.time.Clock()
        self.audio = GestorAudio()

        sf = pygame.font.SysFont
        self.f_portada = sf("georgia", 54, bold=True)
        self.f_titulo = sf("georgia", 30, bold=True)
        self.f_texto = sf("georgia", 22)
        self.f_ui = sf("georgia", 18, bold=True)
        self.f_peq = sf("georgia", 15, italic=True)

        self.estado = "portada"
        self.idea = ""
        self.genero = "Fantasía"
        self.preset = "A_consistente"
        self.error = ""
        self.tarea: TareaGeneracion | None = None
        self.biblioteca: list[dict] = []     # cuentos guardados (más reciente primero)
        self.scroll_bib = 0                  # primera fila visible de la biblioteca

        # Datos del cuento abierto
        self.cuento: dict = {}
        self.imagenes: list[pygame.Surface] = []
        self.pagina = 0
        self.transicion: dict | None = None

        # Sombra del lomo del libro (se precalcula una vez)
        self.sombra_lomo = pygame.Surface((70, 660), pygame.SRCALPHA)
        for x in range(70):
            pygame.draw.line(self.sombra_lomo, (0, 0, 0, int(80 * (1 - abs(x - 35) / 35))), (x, 0), (x, 660))

        self._crear_widgets()

    # ---------------------------- widgets ---------------------------------- #
    def _crear_widgets(self):
        # Portada
        self.chips = []
        for k, nombre in enumerate(GENEROS):
            x, y = 260 + (k % 3) * 235, 405 + (k // 3) * 50
            b = Boton((x, y, 210, 40), nombre, lambda n=nombre: self._elegir_genero(n), self.f_ui)
            self.chips.append(b)
        self.b_preset = Boton((260, 510, 680, 36), "", self._alternar_preset, self.f_peq)
        self.b_crear = Boton((260, 570, 330, 50), "Crear cuento", self.iniciar_generacion, self.f_ui)
        self.b_biblioteca = Boton((610, 570, 330, 50), "Mis cuentos", self.abrir_biblioteca, self.f_ui)
        self.b_volver_bib = Boton((260, 600, 200, 44), "< Volver", self.volver_a_portada, self.f_ui)
        self._elegir_genero(self.genero)
        self._actualizar_texto_preset()

        # Libro (a la derecha, página derecha: x de 640 a 1120)
        self.b_voz = Boton((640, 478, 160, 38), "", lambda: self.audio.alternar_voz(self.pagina), self.f_ui)
        self.b_repetir = Boton((810, 478, 110, 38), "Repetir", lambda: self.audio.reiniciar_voz(self.pagina), self.f_ui)
        self.d_voz = Deslizador(950, 500, 160, self.audio.vol_voz)
        self.b_amb = Boton((640, 528, 280, 38), "", self.audio.alternar_ambiente, self.f_ui)
        self.d_amb = Deslizador(950, 550, 160, self.audio.vol_amb)
        self.b_ant = Boton((640, 610, 150, 44), "< Anterior", lambda: self.ir_a_pagina(self.pagina - 1), self.f_ui)
        self.b_portada = Boton((815, 610, 130, 44), "Portada", self.volver_a_portada, self.f_ui)
        self.b_sig = Boton((970, 610, 150, 44), "Siguiente >", lambda: self.ir_a_pagina(self.pagina + 1), self.f_ui)

    def _elegir_genero(self, nombre: str):
        self.genero = nombre
        for c in self.chips:
            c.seleccionado = (c.texto == nombre)

    def _alternar_preset(self):
        nombres = list(PRESETS)
        self.preset = nombres[(nombres.index(self.preset) + 1) % len(nombres)]
        self._actualizar_texto_preset()

    def _actualizar_texto_preset(self):
        desc = {"A_consistente": "A: ficha de personaje + semilla fija (recomendada)",
                "B_base": "B: solo estilo, sin ficha y semilla variable (línea base)"}
        self.b_preset.texto = f"Consistencia visual  -  {desc[self.preset]}   (clic para cambiar)"

    # ---------------------------- acciones --------------------------------- #
    def iniciar_generacion(self):
        if not self.idea.strip():
            self.error = "Escribe primero una idea para tu cuento."
            return
        self.error = ""
        self.tarea = TareaGeneracion(self.idea.strip(), self.genero, self.preset)
        self.tarea.start()
        self.estado = "generando"

    def abrir_biblioteca(self):
        """Lista todos los cuentos guardados en assets/generated (el más reciente primero)."""
        self.biblioteca = []
        carpetas = sorted(DIR_GENERADO.glob("cuento_*/historia.json"), reverse=True) if DIR_GENERADO.exists() else []
        for ruta in carpetas:
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
                _, dia, hora, preset = ruta.parent.name.split("_", 3)
                fecha = f"{dia[6:8]}/{dia[4:6]}/{dia[:4]} {hora[:2]}:{hora[2:4]}"
                self.biblioteca.append({"carpeta": ruta.parent, "titulo": datos.get("titulo", "(sin título)"),
                                        "genero": datos.get("genero", "?"), "preset": preset, "fecha": fecha})
            except Exception:  # noqa: BLE001 - una carpeta dañada no debe romper la lista
                continue
        self.scroll_bib = 0
        self.error = ""
        self.estado = "biblioteca"

    def abrir_libro(self, carpeta: Path):
        try:
            cuento = json.loads((carpeta / "historia.json").read_text(encoding="utf-8"))
            self.imagenes = []
            for esc in cuento["escenas"]:
                img = pygame.image.load(str(carpeta / esc["imagen"])).convert()
                w, h = img.get_size()
                k = LADO_IMAGEN / max(w, h)
                self.imagenes.append(pygame.transform.smoothscale(img, (int(w * k), int(h * k))))
            self.audio.cargar(carpeta, cuento["escenas"])
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.error = f"No se pudo abrir el cuento: {e}"
            self.estado = "portada"
            return
        self.cuento, self.pagina, self.transicion, self.error = cuento, 0, None, ""
        self.estado = "libro"
        self.audio.reproducir_escena(0)

    def volver_a_portada(self):
        self.audio.detener()
        self.estado = "portada"

    def ir_a_pagina(self, destino: int):
        if self.transicion or not 0 <= destino < len(self.cuento["escenas"]):
            return
        self.transicion = {"inicio": time.time(), "destino": destino, "cambiado": False}

    # ---------------------------- eventos ---------------------------------- #
    def manejar_evento(self, e) -> bool:
        if e.type == pygame.QUIT:
            return False
        if self.estado == "portada":
            if e.type == pygame.TEXTINPUT and len(self.idea) < 300:
                self.idea += e.text                      # TEXTINPUT soporta tildes y ñ
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_BACKSPACE:
                    self.idea = self.idea[:-1]
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.iniciar_generacion()
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                for b in (*self.chips, self.b_preset, self.b_crear, self.b_biblioteca):
                    if b.clic(e.pos):
                        break
        elif self.estado == "biblioteca":
            if e.type == pygame.MOUSEWHEEL:
                self.scroll_bib = min(max(0, len(self.biblioteca) - FILAS_BIB), max(0, self.scroll_bib - e.y))
            elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                self.volver_a_portada()
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if not self.b_volver_bib.clic(e.pos):
                    for k, fila in self._filas_visibles():
                        if fila.collidepoint(e.pos):
                            self.abrir_libro(self.biblioteca[k]["carpeta"])
                            break
        elif self.estado == "libro":
            self.d_voz.evento(e)
            self.d_amb.evento(e)
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_RIGHT:
                    self.ir_a_pagina(self.pagina + 1)
                elif e.key == pygame.K_LEFT:
                    self.ir_a_pagina(self.pagina - 1)
                elif e.key == pygame.K_SPACE:
                    self.audio.alternar_voz(self.pagina)
                elif e.key == pygame.K_ESCAPE:
                    self.volver_a_portada()
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                for b in (self.b_voz, self.b_repetir, self.b_amb, self.b_ant, self.b_portada, self.b_sig):
                    if b.clic(e.pos):
                        break
        return True

    # ---------------------------- actualización ---------------------------- #
    def actualizar(self):
        if self.estado == "generando" and self.tarea and self.tarea.terminada:
            if self.tarea.error:
                self.error, self.estado = self.tarea.error, "portada"
            else:
                self.abrir_libro(self.tarea.resultado)
        elif self.estado == "libro":
            self.audio.vol_voz, self.audio.vol_amb = self.d_voz.valor, self.d_amb.valor
            self.audio.aplicar_volumenes()
            n = len(self.cuento["escenas"])
            self.b_ant.activo = self.pagina > 0 and not self.transicion
            self.b_sig.activo = self.pagina < n - 1 and not self.transicion
            self.b_voz.texto = self.audio.texto_boton_voz()
            self.b_amb.texto = "Activar ambiente" if self.audio.ambiente_silenciado else "Silenciar ambiente"
            if self.transicion:
                t = (time.time() - self.transicion["inicio"]) / DURACION_TRANSICION
                if t >= 0.5 and not self.transicion["cambiado"]:     # a mitad del fundido cambia la página
                    self.pagina = self.transicion["destino"]
                    self.transicion["cambiado"] = True
                    self.audio.reproducir_escena(self.pagina)
                if t >= 1.0:
                    self.transicion = None

    # ---------------------------- dibujo ----------------------------------- #
    def dibujar(self):
        mouse = pygame.mouse.get_pos()
        {"portada": self._dibujar_portada, "generando": self._dibujar_generando,
         "biblioteca": self._dibujar_biblioteca, "libro": self._dibujar_libro}[self.estado](mouse)
        pygame.display.flip()

    def _dibujar_cubierta(self, titulo_sub: str):
        p = self.pantalla
        p.fill(C_MESA)
        cubierta = pygame.Rect(200, 40, 800, 640)
        pygame.draw.rect(p, C_CUBIERTA, cubierta, border_radius=18)
        pygame.draw.rect(p, C_DORADO, cubierta.inflate(-30, -30), 3, border_radius=14)
        t = self.f_portada.render("STORYBOOK", True, C_DORADO)
        p.blit(t, t.get_rect(center=(600, 105)))
        s = self.f_peq.render(titulo_sub, True, (230, 210, 170))
        p.blit(s, s.get_rect(center=(600, 150)))

    def _dibujar_portada(self, mouse):
        p = self.pantalla
        self._dibujar_cubierta("Cuentos ilustrados, narrados y ambientados por IA — 100 % local")
        p.blit(self.f_ui.render("Tu idea para el cuento:", True, C_TEXTO_BOTON), (260, 190))
        caja = pygame.Rect(260, 220, 680, 150)
        pygame.draw.rect(p, C_PAPEL, caja, border_radius=8)
        pygame.draw.rect(p, C_DORADO, caja, 2, border_radius=8)
        cursor = "|" if int(time.time() * 2) % 2 == 0 else " "
        if self.idea:
            dibujar_texto(p, self.idea + cursor, self.f_texto, C_TINTA, caja.x + 12, caja.y + 10, caja.width - 24, max_lineas=4)
        else:
            dibujar_texto(p, "Ej.: Una niña que descubre que su sombra sabe volar…" + cursor,
                          self.f_texto, C_TINTA_SUAVE, caja.x + 12, caja.y + 10, caja.width - 24)
        p.blit(self.f_ui.render("Tipo de cuento:", True, C_TEXTO_BOTON), (260, 375))
        for b in (*self.chips, self.b_preset, self.b_crear, self.b_biblioteca):
            b.dibujar(p, mouse)
        if self.error:
            dibujar_texto(p, self.error, self.f_peq, (255, 170, 160), 260, 632, 680, max_lineas=2)

    def _dibujar_generando(self, mouse):
        p = self.pantalla
        self._dibujar_cubierta("Creando tu cuento… esto puede tardar varios minutos")
        t = self.tarea
        dibujar_texto(p, f"“{t.idea}”  ·  {t.genero}", self.f_texto, C_TEXTO_BOTON, 260, 230, 680, centrado=True, max_lineas=3)
        barra = pygame.Rect(260, 360, 680, 30)
        pygame.draw.rect(p, C_TINTA, barra, border_radius=15)
        lleno = barra.copy()
        lleno.width = max(10, int(barra.width * t.fraccion))
        pygame.draw.rect(p, C_DORADO, lleno, border_radius=15)
        pygame.draw.rect(p, C_TEXTO_BOTON, barra, 2, border_radius=15)
        dibujar_texto(p, f"{t.mensaje}   ({t.fraccion:.0%})", self.f_ui, C_TEXTO_BOTON, 260, 405, 680, centrado=True)
        seg = int(time.time() - t.inicio)
        dibujar_texto(p, f"Tiempo transcurrido: {seg // 60:02d}:{seg % 60:02d}", self.f_peq, (230, 210, 170), 260, 450, 680, centrado=True)
        dibujar_texto(p, "La primera vez se descargan los modelos (varios GB). No cierres la ventana.",
                      self.f_peq, (230, 210, 170), 260, 520, 680, centrado=True)

    def _filas_visibles(self):
        """(índice, rect) de las filas de la biblioteca que se ven ahora."""
        visibles = range(self.scroll_bib, min(len(self.biblioteca), self.scroll_bib + FILAS_BIB))
        return [(k, pygame.Rect(260, 190 + (k - self.scroll_bib) * 66, 680, 58)) for k in visibles]

    def _dibujar_biblioteca(self, mouse):
        p = self.pantalla
        self._dibujar_cubierta("Mis cuentos — haz clic en uno para abrirlo")
        if not self.biblioteca:
            dibujar_texto(p, "Aún no hay cuentos generados.", self.f_texto, C_TEXTO_BOTON, 260, 330, 680, centrado=True)
        for k, fila in self._filas_visibles():
            c = self.biblioteca[k]
            pygame.draw.rect(p, (255, 247, 225) if fila.collidepoint(mouse) else C_PAPEL, fila, border_radius=8)
            pygame.draw.rect(p, C_DORADO, fila, 2, border_radius=8)
            dibujar_texto(p, c["titulo"], self.f_ui, C_TINTA, fila.x + 14, fila.y + 7, fila.width - 28, max_lineas=1)
            dibujar_texto(p, f"{c['genero']}  ·  consistencia {c['preset']}  ·  {c['fecha']}",
                          self.f_peq, C_TINTA_SUAVE, fila.x + 14, fila.y + 33, fila.width - 28, max_lineas=1)
        if len(self.biblioteca) > FILAS_BIB:
            fin = min(len(self.biblioteca), self.scroll_bib + FILAS_BIB)
            dibujar_texto(p, f"{self.scroll_bib + 1}–{fin} de {len(self.biblioteca)}  (rueda del mouse para ver más)",
                          self.f_peq, (230, 210, 170), 480, 612, 460, centrado=True)
        self.b_volver_bib.dibujar(p, mouse)
        if self.error:
            dibujar_texto(p, self.error, self.f_peq, (255, 170, 160), 260, 650, 680, max_lineas=1)

    def _dibujar_libro(self, mouse):
        p = self.pantalla
        esc = self.cuento["escenas"][self.pagina]
        p.fill(C_MESA)
        libro = pygame.Rect(40, 30, 1120, 660)
        pygame.draw.rect(p, C_CUBIERTA, libro.inflate(24, 24), border_radius=16)
        izq = pygame.Rect(libro.x, libro.y, libro.w // 2, libro.h)
        der = pygame.Rect(libro.centerx, libro.y, libro.w // 2, libro.h)
        pygame.draw.rect(p, C_PAPEL, izq, border_top_left_radius=10, border_bottom_left_radius=10)
        pygame.draw.rect(p, C_PAPEL_DER, der, border_top_right_radius=10, border_bottom_right_radius=10)
        p.blit(self.sombra_lomo, (libro.centerx - 35, libro.y))

        # ---- Página izquierda: ilustración + sonido + número de página ----
        img = self.imagenes[self.pagina]
        marco = img.get_rect(center=(izq.centerx, 70 + LADO_IMAGEN // 2))
        pygame.draw.rect(p, C_TINTA, marco.inflate(12, 12), border_radius=4)
        p.blit(img, marco)
        dibujar_texto(p, f"Ambiente: {esc['sonido']}", self.f_peq, C_TINTA_SUAVE, izq.x + 50, 590, izq.width - 100, centrado=True, max_lineas=2)
        dibujar_texto(p, f"— {self.pagina + 1} —", self.f_texto, C_TINTA, izq.x, 650, izq.width, centrado=True)

        # ---- Página derecha: título, texto y controles ----
        y = dibujar_texto(p, self.cuento["titulo"], self.f_titulo, C_TINTA, 640, 55, 480, interlineado=1.1, centrado=True, max_lineas=2)
        dibujar_texto(p, f"Escena {self.pagina + 1} de {len(self.cuento['escenas'])}", self.f_peq, C_TINTA_SUAVE, 640, y + 2, 480, centrado=True)
        pygame.draw.line(p, C_DORADO, (660, y + 32), (1100, y + 32), 2)
        dibujar_texto(p, esc["texto"], self.f_texto, C_TINTA, 640, y + 48, 480, interlineado=1.3)

        p.blit(self.f_peq.render("Volumen voz", True, C_TINTA_SUAVE), (950, 474))
        p.blit(self.f_peq.render("Volumen ambiente", True, C_TINTA_SUAVE), (950, 524))
        for b in (self.b_voz, self.b_repetir, self.b_amb, self.b_ant, self.b_portada, self.b_sig):
            b.dibujar(p, mouse)
        self.d_voz.dibujar(p)
        self.d_amb.dibujar(p)

        # ---- Fundido al pasar de página ----
        if self.transicion:
            t = (time.time() - self.transicion["inicio"]) / DURACION_TRANSICION
            alpha = int(255 * max(0.0, 1.0 - abs(2 * min(t, 1.0) - 1.0)))
            velo = pygame.Surface((libro.w, libro.h))
            velo.fill(C_PAPEL)
            velo.set_alpha(alpha)
            p.blit(velo, libro.topleft)

    # ---------------------------- bucle principal -------------------------- #
    def ejecutar(self):
        corriendo = True
        while corriendo:
            for e in pygame.event.get():
                corriendo = self.manejar_evento(e) and corriendo
            self.actualizar()
            self.dibujar()
            self.reloj.tick(FPS)
        self.audio.detener()
        pygame.quit()


if __name__ == "__main__":
    Aplicacion().ejecutar()
