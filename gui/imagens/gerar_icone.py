"""Gera o icone.svg: o disco simplificado (trilhas grossas no lugar dos
dígitos), legível em 16 e 32 px, para o ícone do programa.

    python3 gerar_icone.py icone.svg
"""
import math, sys
C = 256.0
def pt(r, a): return (C + r * math.cos(a), C + r * math.sin(a))
def f(v): return f"{v:.1f}".rstrip('0').rstrip('.')
def arco(r, a1, a2):
    a1, a2 = math.radians(a1), math.radians(a2)
    x1, y1 = pt(r, a1); x2, y2 = pt(r, a2)
    grande = 1 if (a2 - a1) > math.pi else 0
    return f"M{f(x1)} {f(y1)}A{f(r)} {f(r)} 0 {grande} 1 {f(x2)} {f(y2)}"
def cunha(r, a1, a2):
    a1r, a2r = math.radians(a1), math.radians(a2)
    x1, y1 = pt(r, a1r); x2, y2 = pt(r, a2r)
    return f"M{f(C)} {f(C)}L{f(x1)} {f(y1)}A{f(r)} {f(r)} 0 0 1 {f(x2)} {f(y2)}Z"
# trilhas: segmentos de dados, como setores gravados
trilhas = [
    (205, [(10, 75), (95, 170), (190, 260), (280, 345)]),
    (172, [(40, 120), (140, 230), (250, 330)]),
    (139, [(0, 60), (85, 200), (220, 300), (315, 350)]),
]
partes = []
for r, segs in trilhas:
    for a1, a2 in segs:
        partes.append(f'<path d="{arco(r, a1, a2)}" fill="none" stroke="#4ade80" stroke-width="16" stroke-linecap="round"/>')
reflexo = []
for a1, a2, cor, op in [(288, 324, '#ffffff', 0.22), (310, 338, '#7df9ff', 0.16), (272, 300, '#ff7de9', 0.14),
                        (108, 144, '#ffffff', 0.18), (130, 158, '#7df9ff', 0.12), (92, 120, '#ff7de9', 0.10)]:
    for k in range(8):
        b1 = a1 + (a2 - a1) * k / 8; b2 = a1 + (a2 - a1) * (k + 1) / 8
        fator = 1 - abs((k + 0.5) / 8 - 0.5) * 2
        reflexo.append(f'<path d="{cunha(232, b1, b2 + 0.3)}" fill="{cor}" fill-opacity="{op * fator * 1.6:.3f}"/>')
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
<defs>
<radialGradient id="dados" cx="256" cy="256" r="232" gradientUnits="userSpaceOnUse">
<stop offset="0.38" stop-color="#2b323b"/><stop offset="1" stop-color="#181d23"/>
</radialGradient>
<linearGradient id="prata" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#f4f7fa"/><stop offset="0.5" stop-color="#9aa5b1"/><stop offset="1" stop-color="#dfe5eb"/>
</linearGradient>
<linearGradient id="miolo" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#eef2f6"/><stop offset="1" stop-color="#9aa5b1"/>
</linearGradient>
<mask id="furo" maskUnits="userSpaceOnUse" x="0" y="0" width="512" height="512">
<rect width="512" height="512" fill="#fff"/><circle cx="256" cy="256" r="30" fill="#000"/>
</mask>
</defs>
<g mask="url(#furo)">
<circle cx="256" cy="256" r="244" fill="url(#prata)"/>
<circle cx="256" cy="256" r="232" fill="url(#dados)"/>
{"".join(partes)}
{"".join(reflexo)}
<circle cx="256" cy="256" r="100" fill="url(#miolo)"/>
<circle cx="256" cy="256" r="58" fill="none" stroke="#22a04b" stroke-width="18"/>
</g>
</svg>
'''
open(sys.argv[1], 'w').write(svg)
print(len(svg), "bytes")
