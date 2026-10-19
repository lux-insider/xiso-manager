"""Gera src/unicode_tabelas.rs a partir do unicodedata do Python.

As colunas do menu dependem da largura de cada caractere (emoji e
ideogramas ocupam 2 colunas, acentos combinantes ocupam 0) e a seleção
aceita algarismos de qualquer escrita ("٣" vale 3). As tabelas saem do
mesmo unicodedata que a versão em Python usava.

Uso: python3 ferramentas/tabelas_unicode.py > src/unicode_tabelas.rs
"""
import sys
import unicodedata


def faixas(teste):
    saida, inicio = [], None
    for cp in range(0x110000):
        sim = teste(chr(cp))
        if sim and inicio is None:
            inicio = cp
        elif not sim and inicio is not None:
            saida.append((inicio, cp - 1))
            inicio = None
    if inicio is not None:
        saida.append((inicio, 0x10FFFF))
    return saida


def imprime(nome, lista):
    print("pub static %s: &[(u32, u32)] = &[" % nome)
    for a, b in lista:
        print("    (0x%X, 0x%X)," % (a, b))
    print("];\n")


print("// Gerado por ferramentas/tabelas_unicode.py (Unicode %s). Não edite à mão.\n"
      % unicodedata.unidata_version)
imprime("LARGOS", faixas(lambda c: unicodedata.east_asian_width(c) in ("W", "F")))
imprime("COMBINANTES", faixas(lambda c: unicodedata.combining(c) != 0))
# algarismos decimais: cada faixa é uma sequência 0..9 (o valor é cp - início % 10)
decimais = []
for a, b in faixas(lambda c: unicodedata.decimal(c, None) is not None):
    for cp in range(a, b + 1):
        assert unicodedata.decimal(chr(cp)) == (cp - a) % 10, hex(cp)
    decimais.append((a, b))
imprime("DECIMAIS", decimais)
