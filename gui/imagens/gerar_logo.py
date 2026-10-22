"""Gera a identidade do xiso-manager: o disco com os dados saindo em bits.

    python3 gerar_logo.py

Saídas (ao lado deste script, e o banner em docs/):
    logo.png            384x384, disco + nome, para a barra lateral
    icone.png           256x256, só o disco com trilhas grossas (janela)
    xiso-manager.ico    16 a 256 px, ícone dos .exe no Windows
    ../../docs/banner.png   1600x640, banner do README

Precisa do Pillow e das fontes DejaVu (Linux).
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

AQUI = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(AQUI, "..", "..", "docs")
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
NEGRITO = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

VERDE = (74, 222, 128)       # o mesmo #4ade80 da janela
CLARO = (232, 245, 236)
FUNDO = (18, 22, 26)         # perto do #14171c da janela

random.seed(7)


def brilho(camada, raio, forca):
    g = camada.filter(ImageFilter.GaussianBlur(raio))
    g.putalpha(g.getchannel("A").point(lambda v: int(min(255, v * forca))))
    return g


def disco(tam, bits):
    """O disco de frente, desenhado em 2x e reduzido (bordas suaves).

    bits=True: trilhas de 0 e 1 (banner); False: trilhas grossas (ícone).
    """
    s = tam * 2
    c = s // 2
    r = int(s * 0.48)
    miolo = int(r * 0.36)
    furo = int(r * 0.13)
    camada = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # corpo grafite com o reflexo em cone
    corpo = Image.new("RGBA", (s, s))
    px = corpo.load()
    for y in range(s):
        for x in range(s):
            b = (0.5 + 0.5 * math.cos(2 * (math.atan2(y - c, x - c) + 0.7))) ** 3
            px[x, y] = (int(34 + 120 * b), int(40 + 150 * b), int(40 + 130 * b), 255)
    mascara = Image.new("L", (s, s), 0)
    dm = ImageDraw.Draw(mascara)
    dm.ellipse((c - r, c - r, c + r, c + r), fill=255)
    dm.ellipse((c - miolo, c - miolo, c + miolo, c + miolo), fill=0)
    camada.paste(corpo, (0, 0), mascara)

    if bits:
        fonte = ImageFont.truetype(MONO, int(s * 0.022))
        lado = int(s * 0.03)
        raio = r * 0.93
        while raio > r * 0.42:
            n = int(2 * math.pi * raio / (s * 0.0165))
            for i in range(n):
                a = 2 * math.pi * i / n
                t = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
                ImageDraw.Draw(t).text(
                    (lado / 2, lado / 2), random.choice("01"), font=fonte, anchor="mm",
                    fill=VERDE + (int(150 + 100 * random.random()),))
                giro = -math.degrees(a) - 90
                if math.sin(a) > 0:      # metade de baixo: de pé para quem lê
                    giro += 180
                t = t.rotate(giro, resample=Image.BICUBIC)
                camada.alpha_composite(
                    t, (int(c + raio * math.cos(a) - lado / 2), int(c + raio * math.sin(a) - lado / 2)))
            raio -= s * 0.026
    else:
        trilhas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        dt = ImageDraw.Draw(trilhas)
        for fr in (0.83, 0.69, 0.55):
            rr = int(r * fr)
            dt.arc((c - rr, c - rr, c + rr, c + rr), start=200, end=500,
                   fill=VERDE + (255,), width=int(s * 0.035))
        camada.alpha_composite(trilhas)

    d = ImageDraw.Draw(camada)
    linha = max(2, s // 300)
    d.ellipse((c - r, c - r, c + r, c + r), outline=(150, 240, 180, 255), width=linha)
    d.ellipse((c - miolo, c - miolo, c + miolo, c + miolo),
              fill=(16, 24, 20, 255), outline=(90, 200, 130, 255), width=linha)
    d.ellipse((c - furo, c - furo, c + furo, c + furo),
              fill=(3, 6, 4, 255), outline=(70, 150, 100, 255), width=linha)
    return camada.resize((tam, tam), Image.LANCZOS)


def placa(tam, raio_canto):
    """Quadrado arredondado escuro com borda verde, desenhado em 2x."""
    p = Image.new("RGBA", (tam * 2, tam * 2), (0, 0, 0, 0))
    ImageDraw.Draw(p).rounded_rectangle(
        (8, 8, tam * 2 - 8, tam * 2 - 8), radius=raio_canto * 2,
        fill=FUNDO + (255,), outline=(60, 140, 90, 255), width=max(4, tam // 64))
    return p.resize((tam, tam), Image.LANCZOS)


def icone(tam):
    im = placa(tam, int(tam * 0.2))
    d = disco(int(tam * 0.86), bits=False)
    pos = ((tam - d.width) // 2, (tam - d.height) // 2)
    im.alpha_composite(brilho(d, max(2, tam // 32), 0.6), pos)
    im.alpha_composite(d, pos)
    return im


def logo(tam=384):
    im = placa(tam, int(tam * 0.16))
    d = disco(int(tam * 0.66), bits=False)
    pos = ((tam - d.width) // 2, int(tam * 0.06))
    im.alpha_composite(brilho(d, tam // 40, 0.6), pos)
    im.alpha_composite(d, pos)
    fonte = ImageFont.truetype(NEGRITO, int(tam * 0.1))
    texto = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    dt = ImageDraw.Draw(texto)
    y = int(tam * 0.86)
    l1 = dt.textlength("xiso", font=fonte)
    l2 = dt.textlength("-manager", font=fonte)
    x = (tam - l1 - l2) / 2
    dt.text((x, y), "xiso", font=fonte, fill=CLARO + (255,), anchor="lm")
    dt.text((x + l1, y), "-manager", font=fonte, fill=VERDE + (255,), anchor="lm")
    im.alpha_composite(brilho(texto, 4, 0.5))
    im.alpha_composite(texto)
    return im


def banner(w=1600, h=640):
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            k = max(0, 1 - math.hypot((x - w * 0.36) / w, (y - h * 0.5) / h) * 1.6)
            px[x, y] = (int(10 + 8 * k), int(13 + 22 * k), int(15 + 14 * k))
    im = im.convert("RGBA")
    d = ImageDraw.Draw(im)
    for y in range(20, h, 32):
        for x in range(20, w, 32):
            d.point((x, y), fill=(30, 60, 40, 255))

    t = 500
    dx, dy = 180, (h - t) // 2
    dc = disco(t, bits=True)
    im.alpha_composite(brilho(dc, 28, 0.6), (dx, dy))
    im.alpha_composite(dc, (dx, dy))

    # os dados saem do disco em bits e viram arquivos
    fonte = ImageFont.truetype(MONO, 18)
    cx, cy = dx + t // 2, dy + t // 2
    for linha in range(7):
        y = cy - 150 + linha * 50
        x = int(cx + math.sqrt(max(0, (t * 0.49) ** 2 - (y - cy) ** 2))) + 18
        while x < 1040:
            a = int(60 + 180 * (x - (dx + t)) / (1040 - (dx + t)))
            d.text((x, y), random.choice("01"), font=fonte, anchor="lm",
                   fill=VERDE + (max(40, min(255, a)),))
            x += 16
        bx = 1060 + (linha % 2) * 10
        arq = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        da = ImageDraw.Draw(arq)
        da.rounded_rectangle((bx, y - 15, bx + 46, y + 15), radius=5,
                             outline=CLARO + (255,), width=2, fill=(40, 50, 45, 200))
        da.polygon([(bx + 34, y - 15), (bx + 46, y - 3), (bx + 34, y - 3)], fill=CLARO + (255,))
        im.alpha_composite(brilho(arq, 8, 0.8))
        im.alpha_composite(arq)

    titulo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dt = ImageDraw.Draw(titulo)
    dt.text((1140, h // 2 - 40), "xiso", font=ImageFont.truetype(NEGRITO, 92),
            fill=CLARO + (255,), anchor="lm")
    dt.text((1144, h // 2 + 40), "manager", font=ImageFont.truetype(MONO, 26),
            fill=VERDE + (255,), anchor="lm")
    im.alpha_composite(brilho(titulo, 10, 0.7))
    im.alpha_composite(titulo)
    return im.convert("RGB")


if __name__ == "__main__":
    logo().save(os.path.join(AQUI, "logo.png"), optimize=True)
    ic = icone(256)
    ic.save(os.path.join(AQUI, "icone.png"), optimize=True)
    # cada tamanho desenhado de novo, não só reduzido: fica mais nítido
    tamanhos = [16, 24, 32, 48, 64, 128, 256]
    imagens = [icone(n) for n in tamanhos]
    imagens[-1].save(os.path.join(AQUI, "xiso-manager.ico"),
                     sizes=[(n, n) for n in tamanhos], append_images=imagens[:-1])
    os.makedirs(DOCS, exist_ok=True)
    banner().save(os.path.join(DOCS, "banner.png"), optimize=True)
    print("logo.png, icone.png, xiso-manager.ico e docs/banner.png gerados")
