"""
Galaxia que forma el nombre "Eluney"
====================================

Una galaxia espiral gira en el espacio y, de a poco, sus estrellas se
arremolinan hasta escribir el nombre, letra por letra. El resultado se
guarda como video MP4 (H.264), listo para mandar por WhatsApp como documento.

Uso (desde PyCharm: botón Run, o en la terminal):
    python galaxia_eluney.py                # video completo -> videos/galaxia_eluney.mp4
    python galaxia_eluney.py --rapido       # prueba rápida a media resolución
    python galaxia_eluney.py --cuadro 14    # guarda solo la imagen del segundo 14 (PNG)
    python galaxia_eluney.py --sin-ventana  # no muestra la vista previa mientras renderiza

Requisitos:  pip install -r requirements.txt
"""

import argparse
import math
import time
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

AQUI = Path(__file__).resolve().parent

# ───────────────────────── configuración ─────────────────────────
NOMBRE = "Eluney"
ANCHO, ALTO = 1080, 1920        # vertical: se ve a pantalla completa en el celu
FPS = 30
DURACION = 16.0                 # segundos
SEMILLA = 2026                  # cambiala y obtenés otra galaxia
FUENTE = AQUI / "fuentes" / "Lora-BoldItalic.ttf"
SALIDA = AQUI / "videos" / "galaxia_eluney.mp4"

# Línea de tiempo (en segundos)
T_APARECER = 0.8                # fundido de entrada
T_FORMACION = 4.0               # las primeras estrellas salen hacia el nombre
T_ESCALONADO = 3.4              # demora entre la primera y la última letra
T_VIAJE = 3.2                   # lo que tarda cada estrella en llegar a su lugar
T_DESTELLOS = (11.6, 14.4)      # pasadas de brillo sobre el nombre
T_FUGAZ = 12.8                  # estrella fugaz

# Cantidad de estrellas (para 1080 px de ancho)
N_DISCO = 85_000                # brazos espirales
N_BULBO = 14_000                # núcleo
N_NOMBRE = 58_000               # cuántas viajan a formar el nombre
N_FONDO = 2_400                 # estrellas lejanas del fondo

EXPOSICION = 3.0                # más alto = todo más brillante

# Paletas (RGB 0..1)
COLOR_NUCLEO = (1.00, 0.86, 0.64)
COLORES_GALAXIA = [             # del centro hacia afuera
    (0.00, (1.00, 0.86, 0.64)),
    (0.30, (1.00, 0.58, 0.78)),
    (0.65, (0.66, 0.60, 1.00)),
    (1.00, (0.42, 0.68, 1.00)),
]
COLORES_NOMBRE = [              # de izquierda a derecha
    (0.00, (1.00, 0.45, 0.70)),
    (0.50, (0.96, 0.46, 0.96)),
    (1.00, (0.66, 0.56, 1.00)),
]


# ───────────────────────── utilidades ─────────────────────────
def suave(u):
    """Curva ease-in-out cúbica: arranca y frena despacio. u en [0, 1]."""
    u = np.clip(u, 0.0, 1.0)
    return np.where(u < 0.5, 4 * u**3, 1 - (-2 * u + 2) ** 3 / 2)


def gradiente(paradas, t):
    """Interpola colores de una lista [(posición, (r, g, b)), ...] para cada t."""
    t = np.clip(np.asarray(t, np.float32), 0, 1)
    pos = np.array([p for p, _ in paradas], np.float32)
    cols = np.array([c for _, c in paradas], np.float32)
    return np.stack([np.interp(t, pos, cols[:, k]) for k in range(3)], axis=-1)


def dibujar_puntos(ancho, alto, x, y, peso, color):
    """Suma cada estrella al lienzo, repartida entre sus 4 píxeles vecinos."""
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0).astype(np.float32)
    fy = (y - y0).astype(np.float32)
    x0 = x0.astype(np.int64)
    y0 = y0.astype(np.int64)
    ok = (x0 >= 0) & (y0 >= 0) & (x0 < ancho - 1) & (y0 < alto - 1)
    x0, y0, fx, fy, peso, color = x0[ok], y0[ok], fx[ok], fy[ok], peso[ok], color[ok]
    i = y0 * ancho + x0
    idx = np.concatenate([i, i + 1, i + ancho, i + ancho + 1])
    reparto = np.concatenate([(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy])
    reparto *= np.tile(peso, 4)
    canales = [
        np.bincount(idx, reparto * np.tile(color[:, k], 4), minlength=ancho * alto)
        for k in range(3)
    ]
    return np.stack(canales, axis=-1).reshape(alto, ancho, 3).astype(np.float32)


def resplandor(img):
    """Agrega el halo de luz (bloom) que hace brillar a las estrellas."""
    alto, ancho = img.shape[:2]

    def halo(factor, sigma):
        chico = cv2.resize(img, (ancho // factor, alto // factor), interpolation=cv2.INTER_AREA)
        chico = cv2.GaussianBlur(chico, (0, 0), sigma)
        return cv2.resize(chico, (ancho, alto), interpolation=cv2.INTER_LINEAR)

    return img + 1.6 * halo(4, 1.6) + 1.3 * halo(16, 2.2)


# ───────────────────────── la escena ─────────────────────────
class Escena:
    def __init__(self, ancho, alto):
        self.W, self.H = ancho, alto
        self.s = s = ancho / 1080                 # escala respecto de 1080 px
        densidad = s * s
        rng = np.random.default_rng(SEMILLA)
        self.cx, self.cy = ancho / 2, alto / 2
        R = self.R = 680 * s                      # radio de la galaxia

        # Orientación de la galaxia en pantalla: inclinada y girada
        self.pa = math.radians(-58)
        self.sentido = -1.0                       # giro con brazos "arrastrados"

        # ── disco con dos brazos espirales ──
        n = int(N_DISCO * densidad)
        r = rng.gamma(2.0, 0.27 * R, n * 2)
        r = r[r < 1.25 * R][:n]
        brazo = rng.integers(0, 2, n)
        r0, paso = 0.07 * R, math.tan(math.radians(16))
        ang = brazo * np.pi + np.log(np.maximum(r, r0) / r0) / paso
        en_brazo = rng.random(n) < 0.86
        ang = np.where(en_brazo, ang, rng.uniform(0, 2 * np.pi, n))
        ancho_brazo = 0.03 * R + 0.05 * r
        xd = r * np.cos(ang) + rng.normal(0, 1, n) * ancho_brazo
        yd = r * np.sin(ang) + rng.normal(0, 1, n) * ancho_brazo

        peso_d = np.where(en_brazo, 0.48, 0.22) * rng.uniform(0.35, 1.0, n) ** 2
        col_d = gradiente(COLORES_GALAXIA, np.hypot(xd, yd) / R)
        col_d *= rng.uniform(0.88, 1.12, (n, 1))
        # cúmulos brillantes rosas/celestes a lo largo de los brazos
        cumulo = en_brazo & (r > 0.3 * R) & (rng.random(n) < 0.05)
        col_d[cumulo] = np.where(rng.random((cumulo.sum(), 1)) < 0.6,
                                 (1.00, 0.40, 0.62), (0.72, 0.86, 1.00))
        peso_d[cumulo] *= 2.6
        aplaste_d = np.full(n, 0.52)            # inclinación del disco

        # ── núcleo (bulbo) ──
        nb = int(N_BULBO * densidad)
        sig = np.where(rng.random(nb) < 0.6, 0.05 * R, 0.12 * R)
        xb = rng.normal(0, 1, nb) * sig
        yb = rng.normal(0, 1, nb) * sig
        peso_b = 0.45 * rng.uniform(0.4, 1.0, nb)
        col_b = np.tile(np.array(COLOR_NUCLEO, np.float32), (nb, 1)) * rng.uniform(0.9, 1.1, (nb, 1))
        aplaste_b = np.full(nb, 0.85)

        # ── todas las estrellas de la galaxia juntas (coordenadas polares del disco) ──
        xg = np.concatenate([xd, xb])
        yg = np.concatenate([yd, yb])
        self.rg = np.hypot(xg, yg).astype(np.float32)
        self.ag = np.arctan2(yg, xg).astype(np.float32)
        self.aplaste = np.concatenate([aplaste_d, aplaste_b]).astype(np.float32)
        self.peso_g = np.concatenate([peso_d, peso_b]).astype(np.float32)
        self.col_g = np.concatenate([col_d, col_b]).astype(np.float32)
        # velocidad de giro: el centro gira un poco más rápido que los bordes
        self.omega = (0.19 + 0.22 * np.exp(-self.rg / (0.3 * R))).astype(np.float32)
        self.fase = rng.uniform(0, 2 * np.pi, len(self.rg)).astype(np.float32)
        self.titila = rng.uniform(1.2, 3.5, len(self.rg)).astype(np.float32)
        total = len(self.rg)

        # ── el nombre ──
        tx, ty, nucleo, xn = self._puntos_del_nombre(rng, int(N_NOMBRE * densidad))
        m = len(tx)

        # Elegimos qué estrellas viajan: preferimos las del centro
        prob = np.exp(-self.rg / (0.45 * R))
        prob[len(xd):] *= 3.0
        elegidas = rng.choice(total, m, replace=False, p=prob / prob.sum())

        # Emparejamos por ángulo para que el remolino sea ordenado
        t_medio = T_FORMACION + T_ESCALONADO / 2
        ex, ey = self._posicion_galaxia(elegidas, t_medio)
        orden_e = np.argsort(np.arctan2(ey - self.cy, ex - self.cx))
        orden_t = np.argsort(np.arctan2(ty - self.cy, tx - self.cx))
        elegidas = elegidas[orden_e]
        tx, ty, nucleo, xn = tx[orden_t], ty[orden_t], nucleo[orden_t], xn[orden_t]

        # Cada estrella sale en su momento: las letras se forman de izquierda a derecha
        self.t_sale = (T_FORMACION + T_ESCALONADO * xn + rng.uniform(0, 0.9, m)).astype(np.float32)
        self.t_viaje = (T_VIAJE * rng.uniform(0.85, 1.15, m)).astype(np.float32)

        # Dónde está (en polares de pantalla) y cómo se mueve justo al salir
        dt = 1e-2
        x1, y1 = self._posicion_galaxia(elegidas, self.t_sale)
        x2, y2 = self._posicion_galaxia(elegidas, self.t_sale + dt)
        self.r_sale = np.hypot(x1 - self.cx, y1 - self.cy)
        self.a_sale = np.arctan2(y1 - self.cy, x1 - self.cx)
        r2 = np.hypot(x2 - self.cx, y2 - self.cy)
        a2 = np.arctan2(y2 - self.cy, x2 - self.cx)
        self.vr_sale = (r2 - self.r_sale) / dt
        self.va_sale = np.angle(np.exp(1j * (a2 - self.a_sale))) / dt
        self.r_meta = np.hypot(tx - self.cx, ty - self.cy)
        a_meta = np.arctan2(ty - self.cy, tx - self.cx)
        # giro total del remolino: entre 0,3 y 1,3 vueltas, en el sentido de la galaxia
        base = 0.6 * np.pi
        d = (self.sentido * (a_meta - self.a_sale) - base) % (2 * np.pi) + base
        self.giro = self.sentido * d

        self.elegidas = elegidas
        self.tx, self.ty, self.xn = tx.astype(np.float32), ty.astype(np.float32), xn
        self.peso_n = (0.30 + 0.34 * nucleo).astype(np.float32)
        blanco = np.ones(3, np.float32)
        col_n = gradiente(COLORES_NOMBRE, xn)
        self.col_n = (col_n + (blanco - col_n) * (0.32 * nucleo)[:, None]).astype(np.float32)
        self.libres = np.setdiff1d(np.arange(total), elegidas)

        # ── fondo: estrellas lejanas y nebulosa ──
        nf = int(N_FONDO * densidad)
        self.fx = rng.uniform(0, ancho, nf).astype(np.float32)
        self.fy = rng.uniform(0, alto, nf).astype(np.float32)
        self.peso_f = (0.10 + 1.1 * rng.random(nf) ** 7).astype(np.float32)
        tonos = np.array([(1.0, 1.0, 1.0), (0.75, 0.85, 1.0), (1.0, 0.9, 0.75), (1.0, 0.8, 0.9)], np.float32)
        self.col_f = tonos[rng.integers(0, len(tonos), nf)]
        self.fase_f = rng.uniform(0, 2 * np.pi, nf).astype(np.float32)
        self.vel_f = rng.uniform(0.8, 3.0, nf).astype(np.float32)
        self.nebulosa = self._nebulosa(rng)

        # viñeta suave en los bordes
        yy, xx = np.mgrid[0:alto, 0:ancho].astype(np.float32)
        d2 = ((xx - self.cx) / (0.75 * ancho)) ** 2 + ((yy - self.cy) / (0.75 * alto)) ** 2
        self.vineta = np.clip(1 - 0.35 * d2, 0.45, 1)[..., None].astype(np.float32)

    # ── piezas de la escena ──
    def _puntos_del_nombre(self, rng, n):
        """Dibuja el nombre en una imagen y reparte n estrellas sobre sus trazos."""
        W, H = self.W, self.H
        tam = int(300 * self.s)
        fuente = ImageFont.truetype(str(FUENTE), tam)
        x0, y0, x1, y1 = fuente.getbbox(NOMBRE)
        tam = int(tam * 0.84 * W / (x1 - x0))
        fuente = ImageFont.truetype(str(FUENTE), tam)
        mascara = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mascara).text((W / 2, H / 2), NOMBRE, font=fuente, fill=255, anchor="mm")
        alfa = np.asarray(mascara, np.float32) / 255
        dist = cv2.distanceTransform((alfa > 0.5).astype(np.uint8), cv2.DIST_L2, 5)
        grosor = np.percentile(dist[dist > 0], 97)
        ys, xs = np.nonzero(alfa > 0.35)
        p = alfa[ys, xs]
        idx = rng.choice(len(xs), n, p=p / p.sum())
        tx = xs[idx] + rng.random(n)
        ty = ys[idx] + rng.random(n)
        nucleo = np.clip(dist[ys[idx], xs[idx]] / grosor, 0, 1)
        xn = (tx - tx.min()) / (tx.max() - tx.min())
        return tx, ty, nucleo, xn

    def _nebulosa(self, rng):
        """Nubes de color muy tenues detrás de todo."""
        W, H = self.W, self.H

        def ruido(celdas):
            base = rng.random((max(2, int(celdas * H / W)), celdas)).astype(np.float32)
            return cv2.resize(base, (W, H), interpolation=cv2.INTER_CUBIC)

        n1 = 0.55 * ruido(4) + 0.30 * ruido(9) + 0.15 * ruido(20)
        n2 = 0.55 * ruido(5) + 0.30 * ruido(11) + 0.15 * ruido(23)
        n1 = np.clip((n1 - 0.42) * 2.2, 0, 1) ** 2
        n2 = np.clip((n2 - 0.45) * 2.2, 0, 1) ** 2
        violeta = np.array([0.30, 0.10, 0.45], np.float32)
        azul = np.array([0.05, 0.14, 0.40], np.float32)
        neb = 0.09 * n1[..., None] * violeta + 0.09 * n2[..., None] * azul
        return cv2.GaussianBlur(neb, (0, 0), 25 * self.s).astype(np.float32)

    def _proyectar(self, r, a, aplaste):
        """Del plano de la galaxia a la pantalla (inclinación + rotación)."""
        x = r * np.cos(a)
        y = r * np.sin(a) * aplaste
        c, s = math.cos(self.pa), math.sin(self.pa)
        return self.cx + x * c - y * s, self.cy + x * s + y * c

    def _posicion_galaxia(self, cuales, t, expansion=1.0):
        a = self.ag[cuales] + self.sentido * self.omega[cuales] * t
        return self._proyectar(self.rg[cuales] * expansion, a, self.aplaste[cuales])

    # ── un cuadro ──
    def cuadro(self, t):
        """Devuelve la imagen (RGB uint8) del instante t."""
        s = self.s
        xs, ys, ps, cs = [], [], [], []

        # 1) estrellas de fondo, titilando
        brillo_f = self.peso_f * (0.7 + 0.3 * np.sin(self.fase_f + t * self.vel_f))
        xs.append(self.fx); ys.append(self.fy); ps.append(brillo_f); cs.append(self.col_f)

        # 2) la galaxia que queda girando (se apaga un poco y se abre para enmarcar el nombre)
        avance = float(suave((t - T_FORMACION) / 6.0))
        lib = self.libres
        x, y = self._posicion_galaxia(lib, t, expansion=1.0 + 0.10 * avance)
        tit = 0.85 + 0.15 * np.sin(self.fase[lib] + t * self.titila[lib])
        xs.append(x); ys.append(y)
        ps.append(self.peso_g[lib] * tit * (1 - 0.40 * avance)); cs.append(self.col_g[lib])

        # 3) las estrellas que forman el nombre
        el = self.elegidas
        u = (t - self.t_sale) / self.t_viaje
        e = suave(u).astype(np.float32)
        ya_salio = u > 0
        x, y = self._posicion_galaxia(el, t)
        # viaje en remolino (polares alrededor del centro de la pantalla)
        dtv = np.maximum(t - self.t_sale, 0)
        ang = self.a_sale + self.va_sale * dtv * (1 - e) + self.giro * e
        rad = self.r_sale + (self.r_meta - self.r_sale) * e
        rad = rad * (1 - 0.22 * np.sin(np.pi * e)) + self.vr_sale * dtv * (1 - e) ** 2
        xv = self.cx + rad * np.cos(ang)
        yv = self.cy + rad * np.sin(ang)
        # una vez en su lugar, respiran apenas
        quieto = np.clip((u - 1) * 2, 0, 1)
        xv += quieto * 0.5 * s * np.sin(self.fase[el] + 1.7 * t)
        yv += quieto * 0.5 * s * np.cos(self.fase[el] * 1.3 + 1.3 * t)
        x = np.where(ya_salio, xv, x)
        y = np.where(ya_salio, yv, y)

        tit = 0.82 + 0.18 * np.sin(self.fase[el] + t * self.titila[el])
        peso = self.peso_g[el] + (self.peso_n - self.peso_g[el]) * e
        peso = peso * (1 + 0.6 * np.sin(np.pi * e)) * tit
        # destellos que recorren el nombre de izquierda a derecha
        for t0 in T_DESTELLOS:
            pos = (t - t0) / 1.3 * 1.4 - 0.2
            if -0.3 < pos < 1.3:
                peso = peso * (1 + 1.1 * e * np.exp(-((self.xn - pos) / 0.06) ** 2))
        color = self.col_g[el] + (self.col_n - self.col_g[el]) * e[:, None]
        xs.append(x); ys.append(y); ps.append(peso); cs.append(color)

        # 4) estrella fugaz
        uf = (t - T_FUGAZ) / 0.9
        if 0 <= uf <= 1:
            p0 = np.array([0.96 * self.W, 0.10 * self.H])
            p1 = np.array([0.28 * self.W, 0.25 * self.H])
            cabeza = p0 + (p1 - p0) * uf
            direc = (p1 - p0) / np.linalg.norm(p1 - p0)
            k = np.linspace(0, 1, int(160 * s) + 2)
            estela = cabeza[None, :] - direc[None, :] * (k[:, None] * 0.22 * self.W * min(1, uf * 3))
            alfa = math.sin(math.pi * uf)
            xs.append(estela[:, 0]); ys.append(estela[:, 1])
            ps.append((1.6 * alfa * (1 - k) ** 2).astype(np.float32))
            cs.append(np.tile(np.array([0.85, 0.92, 1.0], np.float32), (len(k), 1)))

        luz = dibujar_puntos(self.W, self.H, np.concatenate(xs), np.concatenate(ys),
                             np.concatenate(ps).astype(np.float32), np.concatenate(cs))
        luz = resplandor(luz) + self.nebulosa
        img = 1 - np.exp(-luz * EXPOSICION)
        img *= self.vineta
        img *= 0.15 + 0.85 * float(suave(t / T_APARECER))
        return (np.clip(img, 0, 1) ** 0.95 * 255).astype(np.uint8)


# ───────────────────────── programa ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Galaxia que forma el nombre " + NOMBRE)
    ap.add_argument("--rapido", action="store_true", help="media resolución, para probar")
    ap.add_argument("--cuadro", type=float, help="guardar solo la imagen de este segundo (PNG)")
    ap.add_argument("--sin-ventana", action="store_true", help="no mostrar vista previa")
    args = ap.parse_args()

    ancho, alto = (ANCHO // 2, ALTO // 2) if args.rapido else (ANCHO, ALTO)
    print(f"Preparando la galaxia ({ancho}x{alto})...")
    escena = Escena(ancho, alto)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)

    if args.cuadro is not None:
        ruta = SALIDA.with_name(f"{SALIDA.stem}_{args.cuadro:g}s.png")
        Image.fromarray(escena.cuadro(args.cuadro)).save(ruta)
        print(f"Listo: {ruta}")
        return

    salida = SALIDA.with_name(SALIDA.stem + "_rapido.mp4") if args.rapido else SALIDA
    escritor = imageio_ffmpeg.write_frames(
        str(salida), (ancho, alto), fps=FPS, codec="libx264", quality=None,
        macro_block_size=4, pix_fmt_in="rgb24", pix_fmt_out="yuv420p",
        output_params=["-crf", "21", "-preset", "slow", "-profile:v", "high",
                       "-movflags", "+faststart"],
    )
    escritor.send(None)

    ventana = not args.sin_ventana
    total = int(DURACION * FPS)
    inicio = time.time()
    for i in range(total):
        img = escena.cuadro(i / FPS)
        escritor.send(np.ascontiguousarray(img))
        if ventana:
            try:
                vista = cv2.resize(img, (360, 640), interpolation=cv2.INTER_AREA)
                cv2.imshow(f"Galaxia {NOMBRE} (Q para cerrar la vista)", cv2.cvtColor(vista, cv2.COLOR_RGB2BGR))
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    ventana = False
                    cv2.destroyAllWindows()
            except cv2.error:
                ventana = False
        if (i + 1) % FPS == 0 or i + 1 == total:
            hecho = (i + 1) / total
            resta = (time.time() - inicio) / hecho * (1 - hecho)
            print(f"  {i + 1}/{total} cuadros ({hecho:4.0%}) - faltan ~{resta:3.0f} s")
    escritor.close()
    if ventana:
        cv2.destroyAllWindows()
    print(f"Listo: {salida}  ({salida.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
