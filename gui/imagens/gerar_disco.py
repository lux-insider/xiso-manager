"""Gera o disco.svg: o DVD com os dígitos 0 e 1 em trilhas, a identidade
do xiso-manager.

    python3 gerar_disco.py disco.svg

Os dígitos são formas (não texto), para o desenho não depender de fonte
instalada. A semente fixa faz o disco sair sempre igual.
"""
import math, random, sys
C = 256.0
rnd = random.Random(0x360)

def pt(r, a):
    return (C + r * math.cos(a), C + r * math.sin(a))

def f(v):
    return f"{v:.1f}".rstrip('0').rstrip('.')

def cunha(r, a1, a2):
    a1, a2 = math.radians(a1), math.radians(a2)
    x1, y1 = pt(r, a1); x2, y2 = pt(r, a2)
    return f"M{f(C)} {f(C)}L{f(x1)} {f(y1)}A{f(r)} {f(r)} 0 0 1 {f(x2)} {f(y2)}Z"

def poligono(pts):
    return "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts) + "Z"

def glifo(bit, r, a, alt, larg):
    """Um 0 ou 1 de pé na trilha (topo virado para fora), coordenadas já giradas."""
    radial = (math.cos(a), math.sin(a))
    tang = (-math.sin(a), math.cos(a))
    def p(u, v):  # u: ao longo da trilha, v: para fora
        cx, cy = pt(r, a)
        return (cx + tang[0] * u + radial[0] * v, cy + tang[1] * u + radial[1] * v)
    h = alt / 2
    if bit == '1':
        w = larg * 0.18
        return poligono([p(-w, -h), p(w, -h), p(w, h), p(-w, h)])
    w = larg / 2
    e = larg * 0.3
    externo = [p(w * math.cos(t), h * math.sin(t)) for t in [i * math.pi / 4 for i in range(8)]]
    interno = [p((w - e) * math.cos(t), (h - e) * math.sin(t)) for t in [i * math.pi / 4 for i in range(8)]]
    return poligono(externo) + poligono(interno[::-1])

grupos = {}
r = 104.0
trilha = 0
while r <= 222:
    alt = 7.0 + (r - 104) * 0.012
    larg = alt * 0.62
    passo = larg * 1.55
    n = int(2 * math.pi * r / passo)
    deslocamento = rnd.random() * 2 * math.pi
    i = 0
    while i < n:
        # setores: blocos de dados com intervalos
        bloco = rnd.randint(8, 30)
        lacuna = rnd.randint(2, 7)
        opac = rnd.choice([0.35, 0.5, 0.65, 0.85])
        for k in range(i, min(i + bloco, n)):
            a = deslocamento + 2 * math.pi * k / n
            bit = rnd.choice('01')
            grupos.setdefault(opac, []).append(glifo(bit, r, a, alt, larg))
        i += bloco + lacuna
    r += alt + 5.5
    trilha += 1

reflexo = []
for a1, a2, cor, op in [
    (288, 324, '#ffffff', 0.15), (310, 338, '#7df9ff', 0.10), (272, 300, '#ff7de9', 0.09), (326, 352, '#fff27d', 0.08),
    (108, 144, '#ffffff', 0.12), (130, 158, '#7df9ff', 0.08), (92, 120, '#ff7de9', 0.07), (146, 172, '#fff27d', 0.06),
]:
    passos = 8
    for k in range(passos):
        b1 = a1 + (a2 - a1) * k / passos
        b2 = a1 + (a2 - a1) * (k + 1) / passos
        meio = (k + 0.5) / passos
        fator = 1 - abs(meio - 0.5) * 2  # 0 nas bordas, 1 no meio
        reflexo.append(f'<path d="{cunha(232, b1, b2 + 0.3)}" fill="{cor}" fill-opacity="{op * fator * 1.6:.3f}"/>')

digitos = "\n".join(
    f'<path d="{"".join(ps)}" fill="#4ade80" fill-opacity="{op}" fill-rule="evenodd"/>'
    for op, ps in sorted(grupos.items())
)

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
<defs>
<radialGradient id="dados" cx="256" cy="256" r="232" gradientUnits="userSpaceOnUse">
<stop offset="0.38" stop-color="#2b323b"/><stop offset="0.7" stop-color="#1f252c"/><stop offset="1" stop-color="#151a20"/>
</radialGradient>
<linearGradient id="prata" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#f4f7fa"/><stop offset="0.5" stop-color="#9aa5b1"/><stop offset="1" stop-color="#dfe5eb"/>
</linearGradient>
<linearGradient id="miolo" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#e8edf2" stop-opacity="0.55"/><stop offset="1" stop-color="#8d98a4" stop-opacity="0.35"/>
</linearGradient>
<mask id="furo" maskUnits="userSpaceOnUse" x="0" y="0" width="512" height="512">
<rect width="512" height="512" fill="#fff"/><circle cx="256" cy="256" r="24" fill="#000"/>
</mask>
</defs>
<g mask="url(#furo)">
<circle cx="256" cy="256" r="240" fill="url(#prata)"/>
<circle cx="256" cy="256" r="232" fill="url(#dados)"/>
{digitos}
{"".join(reflexo)}
<circle cx="256" cy="256" r="94" fill="#151a20"/>
<circle cx="256" cy="256" r="92" fill="url(#miolo)"/>
<circle cx="256" cy="256" r="92" fill="none" stroke="#c3ccd5" stroke-width="2"/>
<circle cx="256" cy="256" r="62" fill="none" stroke="#ffffff" stroke-opacity="0.35" stroke-width="3"/>
<circle cx="256" cy="256" r="40" fill="none" stroke="#4ade80" stroke-width="3"/>
<circle cx="256" cy="256" r="24" fill="none" stroke="#c3ccd5" stroke-width="3"/>
</g>
<circle cx="256" cy="256" r="239.5" fill="none" stroke="#0b0d10" stroke-opacity="0.6" stroke-width="1"/>
</svg>
'''
open(sys.argv[1], 'w').write(svg)
print(len(svg), "bytes,", sum(len(v) for v in grupos.values()), "dígitos,", trilha, "trilhas")
