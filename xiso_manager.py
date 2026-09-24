#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ═════════════════════════════════════════════════════════════════════════
#  xiso-manager — gerenciador de imagens Xbox e Xbox 360
#  Arquitetura: Python 3 (interface + orquestração) + extract-xiso + iso2god
#
#  Filosofia: a ferramenta certa para cada disco, sem o usuário precisar
#  saber qual é. Detecta o tipo do ISO e só oferece o que faz sentido.
#
#  Instalação:  chmod +x xiso_manager.py && ./xiso_manager.py
#  Dependências: python3 (biblioteca padrão), extract-xiso, iso2god
# ═════════════════════════════════════════════════════════════════════════

# ── Códigos de retorno ───────────────────────────────────────────────────
#   0 sucesso | 1 erro geral | 2 ferramenta ausente | 3 espaço insuficiente
#   4 arquivo inválido | 130 Ctrl+C

import os
import re
import sys
import json
import time
import glob
import shutil
import logging
import unicodedata
import subprocess
import collections
import threading
from pathlib import Path

EXIT_OK = 0
EXIT_ERRO = 1
EXIT_FERRAMENTA = 2
EXIT_ESPACO = 3
EXIT_ARQUIVO = 4
EXIT_INTERROMPIDO = 130

APP_NOME = "xiso-manager"
APP_VERSAO = "3.0"


def _pasta_base():
    """
    A ferramenta é autocontida: config e log ficam na própria pasta do
    script. Assim dá para mover ~/Ferramentas/xiso-manager/ inteira para
    outra máquina e tudo continua no lugar.

    Se essa pasta não for gravável (script instalado em /usr/local/bin,
    por exemplo), cai no padrão XDG.
    """
    try:
        aqui = Path(__file__).resolve().parent
        teste = aqui / ".escrita_ok"
        teste.touch()
        teste.unlink()
        return aqui
    except Exception:
        alternativa = Path.home() / ".config" / "xiso-manager"
        try:
            alternativa.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return alternativa


DIR_BASE = _pasta_base()
ARQ_CONFIG = DIR_BASE / "config.json"
ARQ_LOG = DIR_BASE / "xiso-manager.log"
DIR_CONFIG = DIR_BASE          # compatibilidade interna
LIMITE_LOG = 1024 * 1024       # 1 MiB antes de rotacionar

LARGURA = 66            # mesma largura das caixas do baixar-video
MARGEM_ESPACO = 1.15    # 15% de folga na checagem de disco

CONFIG_PADRAO = {
    "idioma": "pt",
    "bin_extract_xiso": "",
    "bin_iso2god": "",
    "bin_xgdtool": "",
    "pasta_padrao": str(Path.home()),
    "threads_god": 4,
    "cor": True,
}

_config = dict(CONFIG_PADRAO)


def _validar_config(chave, valor):
    """Aceita o valor só se ele for utilizável; senão devolve o padrão.

    Um config.json editado à mão ou corrompido por queda de energia pode
    trazer qualquer coisa. Sem esta checagem, um threads_god com texto
    virava "-j muitas" na linha de comando do iso2god, e um idioma
    inexistente deixava a interface caindo no dicionário de reserva a
    cada frase.
    """
    padrao = CONFIG_PADRAO[chave]

    if chave == "idioma":
        return valor if valor in TEXTOS else padrao
    if chave == "threads_god":
        try:
            return max(1, min(32, int(valor)))
        except (TypeError, ValueError):
            return padrao
    if chave == "cor":
        return bool(valor) if isinstance(valor, bool) else padrao
    if chave in ("bin_extract_xiso", "bin_iso2god", "pasta_padrao"):
        return valor if isinstance(valor, str) else padrao
    return valor if isinstance(valor, type(padrao)) else padrao


def carregar_config():
    global _config
    _config = dict(CONFIG_PADRAO)
    try:
        if ARQ_CONFIG.is_file():
            with open(ARQ_CONFIG, "r", encoding="utf-8") as f:
                dados = json.load(f)
            if isinstance(dados, dict):
                for chave in CONFIG_PADRAO:
                    if chave in dados:
                        _config[chave] = _validar_config(chave, dados[chave])
    except Exception:
        pass
    return _config


def salvar_config():
    try:
        DIR_BASE.mkdir(parents=True, exist_ok=True)
        with open(ARQ_CONFIG, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log_evento("erro", f"Falha ao salvar config: {e}")
        return False


def cfg(chave, padrao=None):
    return _config.get(chave, CONFIG_PADRAO.get(chave, padrao))


def set_cfg(chave, valor):
    _config[chave] = valor
    salvar_config()


# ═════════════════════════════════════════════════════════════════════════
# LOG
# ═════════════════════════════════════════════════════════════════════════

def _rotacionar_log():
    """Guarda o log anterior como .old quando ele passa de 1 MiB."""
    try:
        if ARQ_LOG.is_file() and ARQ_LOG.stat().st_size > LIMITE_LOG:
            antigo = ARQ_LOG.with_suffix(".log.old")
            if antigo.exists():
                antigo.unlink()
            ARQ_LOG.rename(antigo)
    except Exception:
        pass


try:
    DIR_BASE.mkdir(parents=True, exist_ok=True)
    _rotacionar_log()
except Exception:
    pass

_log_ok = True
try:
    logging.basicConfig(
        filename=str(ARQ_LOG),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
except Exception:
    _log_ok = False

_log = logging.getLogger("xiso_manager")


def log_evento(nivel, msg):
    if not _log_ok:
        return
    try:
        n = (nivel or "info").lower()
        if n in ("erro", "error"):
            _log.error(msg)
        elif n in ("aviso", "warning"):
            _log.warning(msg)
        else:
            _log.info(msg)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════
# CORES E ESTILOS
# ═════════════════════════════════════════════════════════════════════════

RE_ANSI = re.compile(r"\033\[[0-9;]*m")


def _suporta_cor():
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.environ.get("TERM", "") in ("dumb", ""):
        return False
    return True


_COR_TERMINAL = _suporta_cor()


def _rgb(r, g, b):
    return f"\033[38;2;{int(r)};{int(g)};{int(b)}m"


class C:
    """Paleta e estilos. Mesma nomenclatura curta do baixar-video."""
    RESET = "\033[0m"
    NEG = "\033[1m"
    FRACO = "\033[2m"
    ITAL = "\033[3m"

    AZUL = _rgb(56, 150, 255)
    VERDE = _rgb(46, 214, 130)
    CIANO = _rgb(92, 200, 220)
    AMARE = _rgb(255, 186, 72)
    VERM = _rgb(255, 95, 110)
    ROXO = _rgb(170, 130, 255)
    CINZA = _rgb(128, 138, 150)
    BRANC = _rgb(235, 240, 245)


# extremos do gradiente da barra de progresso
_G_INI = (56, 150, 255)   # azul
_G_FIM = (46, 214, 130)   # verde


def cor(texto, *estilos):
    """Aplica cor/estilo só quando o terminal suporta."""
    if not (_COR_TERMINAL and cfg("cor", True)):
        return str(texto)
    prefixo = "".join(estilos)
    if not prefixo:
        return str(texto)
    return f"{prefixo}{texto}{C.RESET}"


def gradiente(pos):
    """Cor interpolada azul → verde. pos de 0.0 a 1.0."""
    pos = max(0.0, min(1.0, pos))
    return _rgb(
        _G_INI[0] + (_G_FIM[0] - _G_INI[0]) * pos,
        _G_INI[1] + (_G_FIM[1] - _G_INI[1]) * pos,
        _G_INI[2] + (_G_FIM[2] - _G_INI[2]) * pos,
    )


def texto_gradiente(texto, fase=0.0):
    """Pinta cada letra com um tom do gradiente."""
    if not (_COR_TERMINAL and cfg("cor", True)):
        return texto
    saida = []
    total = max(1, len(texto) - 1)
    for i, ch in enumerate(texto):
        if ch == " ":
            saida.append(ch)
            continue
        p = ((i / total) + fase) % 1.0
        onda = p * 2 if p < 0.5 else (1.0 - p) * 2
        saida.append(gradiente(onda) + ch)
    saida.append(C.RESET)
    return "".join(saida)


# ── Emoji por situação ───────────────────────────────────────────────────
EMO = {
    "app": "\U0001F4BF",
    "ok": "\u2705",
    "erro": "\u274C",
    "aviso": "\u26A0\uFE0F",
    "info": "\u2139\uFE0F",
    "dica": "\U0001F4A1",
    "extrair": "\U0001F4E6",
    "listar": "\U0001F4CB",
    "criar": "\U0001F6E0\uFE0F",
    "otimizar": "\U0001F504",
    "god": "\U0001F3AE",
    "analisar": "\U0001F50D",
    "lote": "\U0001F680",
    "manual": "\u2328\uFE0F",
    "config": "\u2699\uFE0F",
    "log": "\U0001F9FE",
    "sobre": "\U0001F4D6",
    "sair": "\U0001F6AA",
    "pasta": "\U0001F4C1",
    "arquivo": "\U0001F4C4",
    "disco": "\U0001F4BE",
    "tempo": "\u23F1\uFE0F",
    "resumo": "\U0001F4CA",
    "assistente": "\U0001F9ED",
    "idioma": "\U0001F310",
    "lixo": "\U0001F5D1\uFE0F",
    "jogo": "\U0001F579\uFE0F",
    "estrela": "\u2B50",
}


# ═════════════════════════════════════════════════════════════════════════
# PRIMITIVAS DE LAYOUT
# ═════════════════════════════════════════════════════════════════════════

def _largura_visivel(texto):
    """Colunas ocupadas pelo texto, ignorando ANSI. Emoji conta 2."""
    texto = RE_ANSI.sub("", str(texto))
    largura = 0
    i = 0
    n = len(texto)
    while i < n:
        ch = texto[i]
        cp = ord(ch)
        prox = texto[i + 1] if i + 1 < n else ""
        if prox == "\uFE0F":
            largura += 2
            i += 2
            continue
        if ch == "\uFE0E":
            i += 1
            continue
        if cp == 0x200D:
            i += 2
            continue
        if unicodedata.combining(ch):
            i += 1
            continue
        ea = unicodedata.east_asian_width(ch)
        if ea in ("W", "F"):
            largura += 2
        elif 0x1F300 <= cp <= 0x1FAFF or 0x1F004 <= cp <= 0x1F0CF:
            largura += 2
        else:
            largura += 1
        i += 1
    return largura


def pad(texto, largura, alinhamento="<"):
    """Preenche até a largura visível, respeitando emoji e ANSI."""
    falta = largura - _largura_visivel(texto)
    if falta <= 0:
        return texto
    if alinhamento == ">":
        return " " * falta + str(texto)
    if alinhamento == "^":
        esq = falta // 2
        return " " * esq + str(texto) + " " * (falta - esq)
    return str(texto) + " " * falta


def cortar(texto, limite):
    if _largura_visivel(texto) <= limite:
        return texto
    saida = ""
    for ch in str(texto):
        if _largura_visivel(saida + ch) > limite - 1:
            break
        saida += ch
    return saida + "\u2026"


def limpar_tela():
    """
    NÃO limpa a tela.

    Limpar a cada passo isola o conteúdo no topo de um terminal vazio, e
    um bloco de 66 colunas cercado de nada parece uma janelinha perdida.
    As outras ferramentas da casa apenas imprimem e deixam o conteúdo
    fluir junto com o histórico — é assim que se comporta um programa de
    terminal. Aqui só marcamos a separação entre um passo e o outro.
    """
    print()


def _borda_gradiente(largura, esquerdo, direito):
    """Linha de borda pintada com o gradiente azul → verde."""
    if not (_COR_TERMINAL and cfg("cor", True)):
        return esquerdo + "\u2500" * largura + direito
    partes = [gradiente(i / max(1, largura - 1)) + "\u2500"
              for i in range(largura)]
    return (gradiente(0.0) + esquerdo + "".join(partes)
            + gradiente(1.0) + direito + C.RESET)


def titulo_caixa(texto, emoji="", largura=LARGURA, cor_borda=None,
                 gradiente_titulo=False):
    """Cabeçalho de seção em caixa arredondada, com emoji e borda colorida."""
    cor_borda = cor_borda or C.AZUL
    miolo = f" {emoji}  {texto}" if emoji else f" {texto}"
    espaco = max(0, largura - 2 - _largura_visivel(miolo))
    if gradiente_titulo:
        topo = _borda_gradiente(largura - 2, "\u256D", "\u256E")
    else:
        topo = cor("\u256D" + "\u2500" * (largura - 2) + "\u256E", cor_borda)
    if gradiente_titulo:
        corpo = " %s  %s" % (emoji, texto_gradiente(texto)) if emoji \
                else " " + texto_gradiente(texto)
    else:
        corpo = cor(miolo, C.NEG, C.BRANC)
    meio = (cor("\u2502", cor_borda) + corpo
            + " " * espaco + cor("\u2502", cor_borda))
    if gradiente_titulo:
        base = _borda_gradiente(largura - 2, "\u2570", "\u256F")
    else:
        base = cor("\u2570" + "\u2500" * (largura - 2) + "\u256F", cor_borda)
    return f"\n{topo}\n{meio}\n{base}\n"


def regua(largura=LARGURA, cor_linha=None):
    return cor("\u2500" * largura, cor_linha or C.CINZA)


def regua_gradiente(largura=LARGURA):
    """Régua pintada com o gradiente azul → verde."""
    if not (_COR_TERMINAL and cfg("cor", True)):
        return "\u2500" * largura
    partes = [gradiente(i / max(1, largura - 1)) + "\u2500" for i in range(largura)]
    return "".join(partes) + C.RESET


def campo(rotulo, valor, emoji="", largura_rotulo=14):
    """
    Linha 'rótulo: valor' alinhada. A marca é normalizada para 2 colunas,
    então campo() e marca_*() alinham entre si na mesma tela.

    Rótulo mais longo que a coluna não é cortado — mas ganha um espaço,
    senão o valor cola nele e vira 'já estava otimizado1'.
    """
    marca = _marca_larga(emoji) if emoji else "  "
    rotulo_pad = pad(rotulo, largura_rotulo)
    if _largura_visivel(rotulo_pad) >= largura_rotulo and \
            not rotulo_pad.endswith(" "):
        rotulo_pad += " "
    return "  %s %s%s" % (marca, cor(rotulo_pad, C.CINZA), cor(valor, C.BRANC))


def tabela(cabecalhos, linhas, alinhamentos=None, destaque=None):
    """
    Tabela alinhada. O separador usa a largura da linha mais larga que
    realmente aparece, não a soma das colunas.
    """
    if not linhas:
        return ""
    n = len(cabecalhos)
    alinhamentos = alinhamentos or ["<"] * n
    larguras = [
        max([_largura_visivel(cabecalhos[i])] + [_largura_visivel(l[i]) for l in linhas])
        for i in range(n)
    ]

    cab = "  " + "  ".join(
        pad(cor(cabecalhos[i], C.NEG, C.CIANO), larguras[i], alinhamentos[i])
        for i in range(n)
    )

    corpo = []
    for idx, lin in enumerate(linhas):
        celulas = [pad(lin[i], larguras[i], alinhamentos[i]) for i in range(n)]
        texto = "  " + "  ".join(celulas)
        if destaque is not None and idx == destaque:
            texto = cor("\u25B8 ", C.VERDE) + "  ".join(celulas) + cor("  " + EMO["estrela"], C.AMARE)
        corpo.append(texto)

    util = max(_largura_visivel(x.rstrip()) for x in [cab] + corpo)
    sep = "  " + cor("\u2500" * max(4, util - 2), C.CINZA)
    return "\n".join([cab, sep] + corpo)


SIM_OK = "\u2713"
SIM_FALHA = "\u2717"
SIM_ND = "\u00B7"
SIM_SETA = "\u25B8"
SIM_PONTO = "\u25CF"     # bolinha de status das ferramentas


def _rotulo_seguro(rotulo, largura):
    """Garante ao menos um espaço entre rótulo e valor."""
    texto = pad(rotulo, largura)
    return texto if texto.endswith(" ") else texto + " "


def marca_ok(rotulo, valor, largura=17):
    print("  %s %s%s" % (cor(_marca_larga(SIM_OK), C.VERDE),
                         cor(_rotulo_seguro(rotulo, largura), C.CINZA),
                         cor(valor, C.BRANC)))


def marca_falha(rotulo, valor, largura=17):
    print("  %s %s%s" % (cor(_marca_larga(SIM_FALHA), C.VERM),
                         cor(_rotulo_seguro(rotulo, largura), C.CINZA),
                         cor(valor, C.VERM, C.NEG)))


def marca_nd(rotulo, valor="N/D", largura=17):
    print("  %s %s%s" % (cor(_marca_larga(SIM_ND), C.CINZA),
                         cor(_rotulo_seguro(rotulo, largura), C.CINZA),
                         cor(valor, C.CINZA)))


def opcao(chave, texto, emoji="", cor_chave=None, descricao="", cor_texto=None):
    """Linha de menu: [1]  📦  Extrair conteúdo de ISO."""
    cor_chave = cor_chave or C.VERDE
    marca = f"{emoji}  " if emoji else ""
    rotulo = pad(f"[{chave}]", 4, ">")
    linha = "  %s  %s%s" % (cor(rotulo, cor_chave, C.NEG), marca,
                            cor(texto, cor_texto or C.BRANC))
    if descricao:
        linha += cor(f"   {descricao}", C.CINZA)
    return linha


COL_ROTULO = 2 + 2      # dois espaços + marca de uma coluna + espaço


def _marca_larga(simbolo):
    """
    Normaliza a marca para SEMPRE ocupar 2 colunas.

    Sem isso, uma linha com ✓ (1 coluna) e outra com ⏱️ (2 colunas)
    empurram o valor para posições diferentes, e a lista inteira parece
    torta mesmo estando "certa".
    """
    largura = _largura_visivel(simbolo)
    if largura >= 2:
        return simbolo
    return simbolo + " "


def etapa(numero, titulo, total=None, largura=LARGURA):
    """
    Divisória de fase do fluxo:  ── 1/4 · Origem ──────────────

    Serve para o usuário saber em que ponto do processo está, em vez de
    encarar uma sequência de perguntas soltas.
    """
    prefixo = "%s/%s \u00B7 %s" % (numero, total, titulo) if total else str(titulo)
    cabeca = "  %s %s " % (cor("\u2500\u2500", C.CINZA), cor(prefixo, C.CIANO, C.NEG))
    resto = largura - _largura_visivel(cabeca)
    return "\n" + cabeca + cor("\u2500" * max(0, resto), C.CINZA)


def resposta(texto, cor_texto=None):
    """Eco do que foi aceito, recuado sob a pergunta que o gerou."""
    return "    %s %s" % (cor("\u2514", C.CINZA), cor(texto, cor_texto or C.CINZA))


def bloco_saida(linhas, limite=6, titulo=None, total_real=None):
    """
    Saída da ferramenta externa dentro de um bloco recuado e marcado.

    Solta na tela, ela parece mensagem de erro. Marcada, fica claro que é
    a voz do programa externo, não do nosso.
    """
    if not linhas:
        return ""
    partes = []
    if titulo:
        partes.append("    " + cor(titulo, C.CINZA, C.ITAL))
    mostradas = linhas[-limite:]
    for i, linha in enumerate(mostradas):
        gancho = "\u2514" if i == len(mostradas) - 1 else "\u251C"
        partes.append("    %s %s" % (cor(gancho, C.CINZA),
                                     cor(cortar(linha, LARGURA - 8), C.CINZA)))
    total = total_real if total_real is not None else len(linhas)
    if total > limite:
        partes.insert(0, "    %s" % cor("\u2026 %d linha(s) anteriores"
                                        % (total - limite), C.CINZA, C.ITAL))
    return "\n".join(partes)


def aviso(texto):
    print(f"{cor(EMO['aviso'], C.AMARE)}  {cor(texto, C.AMARE)}")
    log_evento("aviso", texto)


def erro(texto):
    print(f"{cor(EMO['erro'], C.VERM)} {cor(texto, C.VERM, C.NEG)}")
    log_evento("erro", texto)


def sucesso(texto):
    print(f"{cor(EMO['ok'], C.VERDE)} {cor(texto, C.VERDE, C.NEG)}")
    log_evento("info", texto)


def info_linha(emoji, texto, cor_texto=None):
    print(f"{emoji}  {cor(texto, cor_texto or C.BRANC)}")


def dica(texto):
    print(f"{EMO['dica']}  {cor(texto, C.CIANO, C.ITAL)}")


def prompt(texto):
    """Prompt no estilo '  ▸ Digite o número: '."""
    return f"\n  {cor(SIM_SETA, C.CIANO)} {texto}: "


def pausar():
    try:
        input(f"\n  {cor(SIM_SETA, C.CINZA)} {cor(t('enter_continuar'), C.CINZA)}")
    except (EOFError, KeyboardInterrupt):
        print()


# ═════════════════════════════════════════════════════════════════════════
# FORMATAÇÃO
# ═════════════════════════════════════════════════════════════════════════

def nd(valor, sufixo=""):
    """Devolve 'N/D' quando o dado não existe. Nunca inventa valores."""
    if valor is None or valor == "" or valor == 0:
        return "N/D"
    return f"{valor}{sufixo}"


def fmt_bytes(b):
    if not b:
        return "N/D"
    gb = b / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f} GiB"
    return f"{b / (1024 ** 2):.2f} MiB"


def fmt_tempo(s):
    try:
        s = int(s or 0)
    except (TypeError, ValueError):
        s = 0
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def fmt_velocidade(bps):
    if not bps:
        return "N/D"
    return fmt_bytes(bps) + "/s"


# ═════════════════════════════════════════════════════════════════════════
# TEXTOS (PT-BR / EN)
# ═════════════════════════════════════════════════════════════════════════

TEXTOS = {
    "pt": {
        "subtitulo": "Gerenciador de imagens Xbox e Xbox 360",
        "menu_titulo": "MENU PRINCIPAL",
        "escolha": "Escolha uma opção",
        "opcao_invalida": "Opção inválida. Tente novamente.",
        "enter_continuar": "Pressione ENTER para continuar",
        "cancelado": "Operação cancelada pelo usuário.",
        "confirmar": "Confirmar?",
        "sim_nao_s": "S/n",
        "sim_nao_n": "s/N",
        "voltar": "Voltar",
        "sair": "Sair",
        "ate_logo": "Até logo! Valeu por usar o XISO Manager.",
        "obrigatorio": "Esse campo é obrigatório.",
        "pasta_nao_existe": "A pasta não existe:",
        "arquivo_nao_existe": "O arquivo não existe:",
        "sem_permissao_leitura": "Sem permissão de leitura em:",
        "sem_permissao_escrita": "Destino sem permissão de escrita:",

        # menu
        "d_assistente": "Lê o ISO, identifica o console e mostra só o que faz sentido.",
        "sug_automatica": "ação recomendada →",
        "r_gerados": "Arquivos gerados",
        "e_origem": "Origem",
        "e_destino": "Destino",
        "e_opcoes": "Opções",
        "e_verificacao": "Verificação",
        "e_execucao": "Execução",
        "e_selecao": "Seleção",
        "arquivos": "arquivos",
        "nada_a_fazer": "nada a fazer — já estava otimizado",
        "r_inertes": "Já otimizados",
        "sem_saida": "concluiu sem gerar arquivo novo",
        "e_resultado": "Resultado",
        "g_360": "Xbox 360",
        "g_classico": "Xbox clássico",
        "g_ambos": "Os dois consoles",
        "g_geral": "Geral",
        "g_sistema": "Sistema",
        "so_le": "lê, não cria",

        "m_assistente": "Assistente (detecta o ISO e sugere o que fazer)",
        "m_extrair": "Extrair conteúdo de ISO",
        "m_listar": "Listar arquivos dentro do ISO",
        "m_criar": "Criar ISO a partir de uma pasta",
        "m_reescrever": "Reescrever / otimizar ISO",
        "m_god": "Converter ISO para GOD",
        "m_info_god": "Analisar ISO sem converter",
        "m_lote": "Lote: processar uma pasta inteira",
        "m_manual": "Comando manual (avançado)",
        "m_config": "Configurações",
        "m_log": "Ver log de execução",
        "m_sobre": "Sobre / ajuda",

        # status
        "pronto": "pronto",
        "indisponivel": "não encontrado",
        "ferramentas": "FERRAMENTAS",
        "idioma_atual": "Idioma",

        # navegador
        "nav_titulo": "SELECIONAR ARQUIVO",
        "nav_pasta_atual": "Pasta atual",
        "nav_subir": "Subir um nível",
        "nav_digitar": "Digitar um caminho",
        "nav_todos": "Selecionar TODOS os ISOs desta pasta",
        "nav_nenhum_iso": "Nenhum arquivo .iso encontrado nesta pasta.",
        "nav_ajuda": "Número = abrir/selecionar   |   .. = subir   |   c = caminho   |   0 = voltar",
        "nav_caminho": "Digite o caminho completo",
        "nav_multi": "Números separados por vírgula (ex: 1,3,5) ou intervalo (1-4)",
        "nav_invalido": "Entrada ignorada:",
        "nav_nada": "Nada foi selecionado.",
        "nav_selecionados": "selecionado(s)",

        # analise
        "analisando": "Analisando",
        "tipo_detectado": "Tipo detectado",
        "i_plataforma": "Plataforma",
        "i_titulo": "Título",
        "i_disco": "Disco",
        "i_volume": "Volume",
        "i_nao_detectada": "não detectada",
        "i_sem_executavel": "sem default.xex/default.xbe",
        "t_xbox": "Xbox clássico (XISO)",
        "t_xbox360": "Xbox 360 (XGD)",
        "t_desconhecido": "Não reconhecido como imagem Xbox",
        "sugestao": "SUGESTÃO",
        "sug_xbox": "Este ISO é do Xbox clássico. Use extrair, listar ou reescrever.",
        "sug_360": "Este ISO é do Xbox 360. Use a conversão para GOD.",
        "sug_nada": "Não consegui identificar. Você ainda pode tentar as opções manualmente.",
        "acoes_sugeridas": "Ações disponíveis para este arquivo",

        # disco
        "disco_titulo": "ESPAÇO EM DISCO",
        "disco_destino": "Destino",
        "disco_necessario": "Necessário",
        "disco_disponivel": "Disponível",
        "disco_total": "Total",
        "disco_uso": "Uso atual",
        "disco_ok": "Espaço suficiente.",
        "disco_falta": "Espaço insuficiente. Faltam cerca de",
        "disco_dica": "Libere espaço ou escolha outro destino.",
        "disco_erro": "Não foi possível verificar o espaço em disco.",
        "disco_seguir": "Continuar mesmo assim, sem garantia de espaço?",
        "margem": "com margem",

        # execucao
        "comando": "Comando",
        "executando": "Executando",
        "extraindo": "Extraindo",
        "listando": "Lendo conteúdo",
        "criando": "Criando imagem",
        "reescrevendo": "Otimizando",
        "convertendo": "Convertendo para GOD",
        "concluido": "concluído em",
        "com_erro": "falhou após",
        "saida_programa": "Saída do programa",
        "linhas_omitidas": "linha(s) anteriores omitidas",
        "sucesso_geral": "Tudo certo!",
        "erro_codigo": "O programa terminou com código de erro",
        "erro_bin_sumiu": "Binário não encontrado na hora de executar.",
        "erro_permissao": "Permissão negada ao executar o binário.",
        "erro_inesperado": "Erro inesperado",
        "interrompido": "Interrompido com Ctrl+C. Pode haver arquivos incompletos no destino.",
        "estimado": "estimado",

        # resumo
        "resumo": "RESUMO",
        "r_sucesso": "Sucesso",
        "r_falhas": "Falhas",
        "r_tempo": "Tempo",
        "r_destino": "Destino",
        "r_log": "Log completo em",
        "r_padrao": "(padrão)",

        # acoes - perguntas
        "p_pasta_iso": "Pasta onde estão os ISOs",
        "p_destino": "Pasta de destino (ENTER = padrão)",
        "p_destino_obrig": "Pasta de destino",
        "p_pular_update": "Pular a pasta $SystemUpdate?",
        "p_silencioso": "Modo silencioso do extract-xiso?",
        "p_pasta_origem": "Pasta de origem com os arquivos do jogo",
        "p_nome_saida": "Nome/caminho do ISO de saída (ENTER = automático)",
        "p_outra_pasta": "Adicionar outra pasta nesta mesma execução?",
        "p_sem_patch": "Desabilitar o patch de media enable no .xbe? (não recomendado)",
        "p_apagar_antigo": "Apagar o ISO antigo depois de reescrever?",
        "p_apagar_certeza": "ATENÇÃO: os ISOs originais serão APAGADOS. Tem certeza?",
        "p_titulo_jogo": "Título do jogo (ENTER = detectar automaticamente)",
        "p_threads": "Número de threads",
        "p_trim": "Cortar espaço não usado do fim do ISO?",
        "p_args": "Argumentos",
        "p_tentar_novamente": "Tentar novamente?",

        # descricoes de acao
        "d_extrair": "Extrai todo o conteúdo do ISO para uma pasta.",
        "d_listar": "Mostra os arquivos de dentro do ISO sem extrair nada.",
        "d_criar": "Monta um ISO novo a partir de uma pasta com os arquivos do jogo.",
        "d_reescrever": "Reorganiza o ISO deixando ele otimizado e menor.",
        "d_god": "Converte um ISO de Xbox 360 para o formato GOD (Games on Demand).",
        "d_info_god": "Lê o cabeçalho do ISO e mostra o título, sem converter.",
        "d_lote": "Processa de uma vez todos os ISOs de uma pasta.",
        "d_manual": "Passe os argumentos direto para o binário, como no terminal.",

        # totais
        "arquivos_sel": "arquivo(s) selecionado(s)",
        "total": "total",
        "processando_item": "Processando",
        "de": "de",

        # config
        "cfg_titulo": "CONFIGURAÇÕES",
        "c_idioma": "Idioma / Language",
        "c_extract": "Caminho do extract-xiso",
        "c_iso2god": "Caminho do iso2god",
        "c_pasta": "Pasta padrão",
        "c_threads": "Threads para conversão GOD",
        "c_anim": "Animação da barra",
        "c_cor": "Cores no terminal",
        "c_novo_valor": "Novo valor (ENTER = manter)",
        "c_salvo": "Configuração salva.",
        "c_ligado": "ligado",
        "c_desligado": "desligado",
        "c_nao_definido": "não definido",
        "c_bin_ok": "Binário válido.",
        "c_bin_ruim": "Esse caminho não é um executável válido.",
        "c_chmod": "Sem permissão de execução. Tentando corrigir...",
        "c_chmod_ok": "Permissão de execução concedida.",
        "c_chmod_falha": "Não consegui dar permissão. Rode: chmod +x",

        # log / sobre
        "log_titulo": "LOG DE EXECUÇÃO",
        "log_vazio": "Ainda não há log registrado.",
        "log_ultimas": "Últimas entradas",
        "log_erro_ler": "Não foi possível ler o log:",
        "sobre_titulo": "SOBRE",
        "sobre_1": "Interface unificada para as ferramentas extract-xiso e iso2god.",
        "sobre_2": "extract-xiso trabalha com imagens do Xbox clássico.",
        "sobre_3": "iso2god converte imagens do Xbox 360 para o formato GOD.",
        "sobre_4": "Nenhuma biblioteca externa é necessária.",
        "sobre_config": "Configuração",
        "sobre_log": "Log",

        # binarios
        "bin_faltando_titulo": "FERRAMENTA NÃO ENCONTRADA",
        "bin_faltando": "Não encontrei o binário:",
        "bin_procurei": "Procurei no PATH e nas pastas comuns.",
        "bin_informe": "Informe o caminho completo agora (ENTER para pular)",
        "bin_pulado": "Você pode configurar depois no menu Configurações.",
        "bin_necessario": "Esta ação precisa do binário",
        "bin_configure": "Configure o caminho no menu de configurações.",
    },
    "en": {
        "subtitulo": "Xbox and Xbox 360 disc image manager",
        "menu_titulo": "MAIN MENU",
        "escolha": "Choose an option",
        "opcao_invalida": "Invalid option. Try again.",
        "enter_continuar": "Press ENTER to continue",
        "cancelado": "Operation cancelled by the user.",
        "confirmar": "Confirm?",
        "sim_nao_s": "Y/n",
        "sim_nao_n": "y/N",
        "voltar": "Back",
        "sair": "Quit",
        "ate_logo": "See you! Thanks for using XISO Manager.",
        "obrigatorio": "This field is required.",
        "pasta_nao_existe": "Folder does not exist:",
        "arquivo_nao_existe": "File does not exist:",
        "sem_permissao_leitura": "No read permission on:",
        "sem_permissao_escrita": "Destination is not writable:",

        "d_assistente": "Reads the ISO, identifies the console and shows only what fits.",
        "sug_automatica": "recommended action →",
        "r_gerados": "Files created",
        "e_origem": "Source",
        "e_destino": "Destination",
        "e_opcoes": "Options",
        "e_verificacao": "Check",
        "e_execucao": "Running",
        "e_selecao": "Selection",
        "arquivos": "files",
        "nada_a_fazer": "nothing to do — already optimized",
        "r_inertes": "Already optimal",
        "sem_saida": "finished without creating a new file",
        "e_resultado": "Result",
        "g_360": "Xbox 360",
        "g_classico": "Original Xbox",
        "g_ambos": "Both consoles",
        "g_geral": "General",
        "g_sistema": "System",
        "so_le": "reads, cannot create",

        "m_assistente": "Wizard (detect the ISO and suggest what to do)",
        "m_extrair": "Extract ISO contents",
        "m_listar": "List files inside the ISO",
        "m_criar": "Create ISO from a folder",
        "m_reescrever": "Rewrite / optimize ISO",
        "m_god": "Convert ISO to GOD",
        "m_info_god": "Analyze ISO without converting",
        "m_lote": "Batch: process a whole folder",
        "m_manual": "Manual command (advanced)",
        "m_config": "Settings",
        "m_log": "View execution log",
        "m_sobre": "About / help",

        "pronto": "ready",
        "indisponivel": "not found",
        "ferramentas": "TOOLS",
        "idioma_atual": "Language",

        "nav_titulo": "SELECT FILE",
        "nav_pasta_atual": "Current folder",
        "nav_subir": "Go up one level",
        "nav_digitar": "Type a path",
        "nav_todos": "Select ALL ISOs in this folder",
        "nav_nenhum_iso": "No .iso files found in this folder.",
        "nav_ajuda": "Number = open/select   |   .. = up   |   c = path   |   0 = back",
        "nav_caminho": "Type the full path",
        "nav_multi": "Numbers separated by comma (e.g. 1,3,5) or range (1-4)",
        "nav_invalido": "Ignored input:",
        "nav_nada": "Nothing selected.",
        "nav_selecionados": "selected",

        "analisando": "Analyzing",
        "tipo_detectado": "Detected type",
        "i_plataforma": "Platform",
        "i_titulo": "Title",
        "i_disco": "Disc",
        "i_volume": "Volume",
        "i_nao_detectada": "not detected",
        "i_sem_executavel": "no default.xex/default.xbe",
        "t_xbox": "Original Xbox (XISO)",
        "t_xbox360": "Xbox 360 (XGD)",
        "t_desconhecido": "Not recognized as an Xbox image",
        "sugestao": "SUGGESTION",
        "sug_xbox": "This is an original Xbox ISO. Use extract, list or rewrite.",
        "sug_360": "This is an Xbox 360 ISO. Use the GOD conversion.",
        "sug_nada": "Could not identify it. You can still try the options manually.",
        "acoes_sugeridas": "Available actions for this file",

        "disco_titulo": "DISK SPACE",
        "disco_destino": "Destination",
        "disco_necessario": "Required",
        "disco_disponivel": "Available",
        "disco_total": "Total",
        "disco_uso": "Current usage",
        "disco_ok": "Enough space.",
        "disco_falta": "Not enough space. Missing about",
        "disco_dica": "Free up space or pick another destination.",
        "disco_erro": "Could not check disk space.",
        "disco_seguir": "Continue anyway, with no space guarantee?",
        "margem": "with margin",

        "comando": "Command",
        "executando": "Running",
        "extraindo": "Extracting",
        "listando": "Reading contents",
        "criando": "Creating image",
        "reescrevendo": "Optimizing",
        "convertendo": "Converting to GOD",
        "concluido": "done in",
        "com_erro": "failed after",
        "saida_programa": "Program output",
        "linhas_omitidas": "earlier line(s) omitted",
        "sucesso_geral": "All good!",
        "erro_codigo": "The program exited with error code",
        "erro_bin_sumiu": "Binary not found at execution time.",
        "erro_permissao": "Permission denied running the binary.",
        "erro_inesperado": "Unexpected error",
        "interrompido": "Interrupted with Ctrl+C. There may be incomplete files at the destination.",
        "estimado": "estimated",

        "resumo": "SUMMARY",
        "r_sucesso": "Succeeded",
        "r_falhas": "Failed",
        "r_tempo": "Time",
        "r_destino": "Destination",
        "r_log": "Full log at",
        "r_padrao": "(default)",

        "p_pasta_iso": "Folder containing the ISOs",
        "p_destino": "Destination folder (ENTER = default)",
        "p_destino_obrig": "Destination folder",
        "p_pular_update": "Skip the $SystemUpdate folder?",
        "p_silencioso": "Quiet mode for extract-xiso?",
        "p_pasta_origem": "Source folder with the game files",
        "p_nome_saida": "Output ISO name/path (ENTER = automatic)",
        "p_outra_pasta": "Add another folder in this same run?",
        "p_sem_patch": "Disable the .xbe media enable patch? (not recommended)",
        "p_apagar_antigo": "Delete the old ISO after rewriting?",
        "p_apagar_certeza": "WARNING: the original ISOs will be DELETED. Are you sure?",
        "p_titulo_jogo": "Game title (ENTER = auto detect)",
        "p_threads": "Number of threads",
        "p_trim": "Trim unused space from the end of the ISO?",
        "p_args": "Arguments",
        "p_tentar_novamente": "Try again?",

        "d_extrair": "Extracts everything inside the ISO into a folder.",
        "d_listar": "Shows the files inside the ISO without extracting.",
        "d_criar": "Builds a new ISO from a folder with the game files.",
        "d_reescrever": "Reorganizes the ISO, making it optimized and smaller.",
        "d_god": "Converts an Xbox 360 ISO into the GOD (Games on Demand) format.",
        "d_info_god": "Reads the ISO header and shows the title, without converting.",
        "d_lote": "Processes every ISO in a folder at once.",
        "d_manual": "Pass arguments straight to the binary, like in the terminal.",

        "arquivos_sel": "file(s) selected",
        "total": "total",
        "processando_item": "Processing",
        "de": "of",

        "cfg_titulo": "SETTINGS",
        "c_idioma": "Idioma / Language",
        "c_extract": "extract-xiso path",
        "c_iso2god": "iso2god path",
        "c_pasta": "Default folder",
        "c_threads": "Threads for GOD conversion",
        "c_anim": "Progress bar animation",
        "c_cor": "Terminal colors",
        "c_novo_valor": "New value (ENTER = keep)",
        "c_salvo": "Settings saved.",
        "c_ligado": "on",
        "c_desligado": "off",
        "c_nao_definido": "not set",
        "c_bin_ok": "Valid binary.",
        "c_bin_ruim": "That path is not a valid executable.",
        "c_chmod": "No execute permission. Trying to fix...",
        "c_chmod_ok": "Execute permission granted.",
        "c_chmod_falha": "Could not set permission. Run: chmod +x",

        "log_titulo": "EXECUTION LOG",
        "log_vazio": "No log recorded yet.",
        "log_ultimas": "Latest entries",
        "log_erro_ler": "Could not read the log:",
        "sobre_titulo": "ABOUT",
        "sobre_1": "Unified interface for the extract-xiso and iso2god tools.",
        "sobre_2": "extract-xiso works with original Xbox disc images.",
        "sobre_3": "iso2god converts Xbox 360 images into the GOD format.",
        "sobre_4": "No external libraries required.",
        "sobre_config": "Config",
        "sobre_log": "Log",

        "bin_faltando_titulo": "TOOL NOT FOUND",
        "bin_faltando": "Could not find the binary:",
        "bin_procurei": "Searched PATH and the usual folders.",
        "bin_informe": "Type the full path now (ENTER to skip)",
        "bin_pulado": "You can set it later in the Settings menu.",
        "bin_necessario": "This action needs the binary",
        "bin_configure": "Set the path in the settings menu.",
    },
}

def t(chave):
    """Devolve o texto no idioma atual."""
    tabela_idioma = TEXTOS.get(cfg("idioma", "pt")) or TEXTOS["pt"]
    return tabela_idioma.get(chave, TEXTOS["pt"].get(chave, chave))



# ═════════════════════════════════════════════════════════════════════════
# BARRA DE PROGRESSO — mesma anatomia do baixar-video, com gradiente
# ═════════════════════════════════════════════════════════════════════════

class Progresso:
    """
    label [████░░░░]  47.3%  1.20 GiB/2.54 GiB  8.40 MiB/s  ETA 02:41

    A parte preenchida é pintada com o gradiente azul → verde, e a onda
    desliza a cada quadro. Sem TTY, imprime uma linha limpa só no final.
    """

    LARGURA_BARRA = 24

    def __init__(self, label, emoji="", total=0):
        self.label = label
        self.emoji = emoji
        self.total = total
        self.inicio = time.time()
        self.quadro = 0
        self.interativo = sys.stdout.isatty()
        self._ultima = 0

    def _pintar(self, pct, largura=None):
        largura = largura or self.LARGURA_BARRA
        cheio = int(largura * pct)
        usar_cor = _COR_TERMINAL and cfg("cor", True)
        fase = (self.quadro * 0.03) % 1.0
        partes = []
        for i in range(largura):
            if i < cheio:
                bruto = (i / max(1, largura - 1)) + fase
                ciclo = bruto % 1.0
                onda = ciclo * 2 if ciclo < 0.5 else (1.0 - ciclo) * 2
                partes.append((gradiente(onda) if usar_cor else "") + "\u2588")
            else:
                partes.append((C.CINZA if usar_cor else "") + "\u2591")
        return "".join(partes) + (C.RESET if usar_cor else "")

    def atualizar(self, feito, final=False, detalhe=""):
        decorrido = max(0.001, time.time() - self.inicio)
        vel = feito / decorrido
        if self.total > 0:
            pct = min(1.0, feito / self.total)
            eta = (self.total - feito) / vel if vel > 0 and self.total > feito else 0
            eta_txt = fmt_tempo(eta)
        else:
            pct, eta_txt = 0.0, "N/D"

        if not self.interativo:
            if final:
                print("  %s concluído: %s em %s (%s)"
                      % (self.label, fmt_bytes(feito), fmt_tempo(decorrido),
                         fmt_velocidade(vel)))
            return

        colunas = shutil.get_terminal_size((80, 24)).columns
        rotulo = ("%s %s" % (self.emoji, self.label)) if self.emoji else self.label

        # Blocos em ordem de importância. O que não couber na tela é
        # cortado do fim para trás — uma linha maior que o terminal
        # quebra, e aí o \r não consegue mais apagar o que sobrou:
        # é assim que a barra vira lixo acumulado na tela.
        essencial = "  %s [%s] %s" % (rotulo, "." * self.LARGURA_BARRA,
                                      "100.0%")
        sobra = colunas - _largura_visivel(essencial) - 1

        extras = []
        bytes_txt = "  %s/%s" % (fmt_bytes(feito),
                                 fmt_bytes(self.total) if self.total else "N/D")
        vel_txt = "  %s" % fmt_velocidade(vel)
        eta_completo = "  ETA %s" % eta_txt
        det_txt = ("  " + cortar(detalhe, 18)) if detalhe else ""

        for texto, cor_texto in ((bytes_txt, None), (vel_txt, C.AMARE),
                                 (eta_completo, C.CINZA), (det_txt, C.CINZA)):
            if not texto:
                continue
            if _largura_visivel(texto) <= sobra:
                sobra -= _largura_visivel(texto)
                extras.append((texto, cor_texto))
            else:
                break

        # se nem o essencial couber, encolhe a barra
        largura_barra = self.LARGURA_BARRA
        while largura_barra > 8 and _largura_visivel(
                "  %s [%s] 100.0%%" % (rotulo, "." * largura_barra)) > colunas - 1:
            largura_barra -= 2

        cheio = int(largura_barra * pct)
        linha = ("  %s %s%s%s %s"
                 % (rotulo,
                    cor("[", C.CINZA),
                    self._pintar(pct, largura_barra),
                    cor("]", C.CINZA),
                    cor("%5.1f%%" % (pct * 100), C.BRANC, C.NEG)))

        for texto, cor_texto in extras:
            if texto is bytes_txt:
                pedaco = "  %s%s%s" % (
                    cor(fmt_bytes(feito), C.CIANO), cor("/", C.CINZA),
                    cor(fmt_bytes(self.total) if self.total else "N/D", C.CINZA))
            else:
                pedaco = cor(texto, cor_texto) if cor_texto else texto
            linha += pedaco

        # \033[2K apaga a linha inteira, não só o que o cursor cobre
        sys.stdout.write("\r\033[2K" + linha)
        sys.stdout.flush()
        self._ultima = _largura_visivel(linha)
        if final:
            sys.stdout.write("\n")
            sys.stdout.flush()
        self.quadro += 1

    def limpar(self):
        if self.interativo:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()


class Fiscal:
    """
    Acompanha o progresso somando o que já foi gravado no destino e
    desenha a barra numa thread. Funciona com qualquer ferramenta,
    sem depender do que ela imprime.
    """

    def __init__(self, label, destino, total, emoji="", intervalo=0.25,
                 alvo_arquivo=False):
        self.destino = Path(destino) if destino else None
        # Vigiar uma PASTA custa um os.walk a cada tique. Quando o alvo é
        # um arquivo só — caso do -c, que grava um .iso — vigiar a pasta
        # inteira pode significar varrer a home do usuário 4x por segundo.
        self.alvo_arquivo = alvo_arquivo
        self.total = max(0, int(total or 0))
        self.barra = Progresso(label, emoji=emoji, total=self.total)
        self.intervalo = intervalo
        self.detalhe = ""
        self.feito = 0
        self._base = 0
        self._pct_externo = None
        self._parar = threading.Event()
        self._thread = None
        self._trava = threading.Lock()

    LIMITE_ENTRADAS = 20000     # guarda contra varrer uma árvore gigante

    def _medir(self):
        if not self.destino:
            return 0
        if self.alvo_arquivo:
            try:
                return self.destino.stat().st_size
            except OSError:
                return 0
        if not self.destino.exists():
            return 0
        total = 0
        vistos = 0
        try:
            for raiz, _dirs, arquivos in os.walk(self.destino):
                for nome in arquivos:
                    try:
                        total += os.path.getsize(os.path.join(raiz, nome))
                    except OSError:
                        pass
                    vistos += 1
                    if vistos > self.LIMITE_ENTRADAS:
                        # árvore grande demais para medir a cada tique;
                        # afrouxa o intervalo em vez de travar a barra
                        self.intervalo = min(3.0, self.intervalo * 2)
                        return total
        except Exception:
            return 0
        return total

    def iniciar(self):
        self._base = self._medir()
        self._thread = threading.Thread(target=self._rodar, daemon=True)
        self._thread.start()

    def definir_detalhe(self, texto):
        with self._trava:
            self.detalhe = texto or ""

    def definir_percentual(self, pct):
        """Usa o percentual que a própria ferramenta imprimiu, se houver."""
        with self._trava:
            self._pct_externo = pct

    def _rodar(self):
        while not self._parar.is_set():
            with self._trava:
                pct_ext = self._pct_externo
                det = self.detalhe
            if pct_ext is not None and self.total:
                self.feito = int(self.total * pct_ext)
            else:
                self.feito = max(0, self._medir() - self._base)
            try:
                self.barra.atualizar(min(self.feito, self.total or self.feito), detalhe=det)
            except Exception:
                pass
            self._parar.wait(self.intervalo)

    def encerrar(self, ok=True):
        self._parar.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self.barra.limpar()
        return time.time() - self.barra.inicio


# ═════════════════════════════════════════════════════════════════════════
# DETECÇÃO DAS FERRAMENTAS
# ═════════════════════════════════════════════════════════════════════════

PASTAS_COMUNS = [
    DIR_BASE,                       # a própria pasta da ferramenta vem primeiro
    Path.home() / "Ferramentas" / "xiso-manager",
    Path.home() / "Downloads",
    Path.home() / "Ferramentas",
    Path.home() / "bin",
    Path.home() / ".local" / "bin",
    Path("/usr/local/bin"),
    Path("/usr/bin"),
    Path("/opt"),
    Path.cwd(),
]

NOMES_EXTRACT = ["extract-xiso", "extract_xiso", "extract-xiso-linux"]
NOMES_ISO2GOD = ["iso2god", "iso2god-x86_64-linux", "iso2god-linux", "iso2god-x86_64"]
NOMES_XGDTOOL = ["XGDTool", "xgdtool", "XGDTool-cli", "xgdtool-cli"]


def _executavel(caminho):
    try:
        p = Path(caminho)
        return p.is_file() and os.access(str(p), os.X_OK)
    except Exception:
        return False


def localizar_binario(nomes):
    for nome in nomes:
        achado = shutil.which(nome)
        if achado:
            return achado
    for pasta in PASTAS_COMUNS:
        for nome in nomes:
            alvo = pasta / nome
            if _executavel(alvo):
                return str(alvo)
    try:
        aqui = Path(__file__).resolve().parent
        for nome in nomes:
            if _executavel(aqui / nome):
                return str(aqui / nome)
    except Exception:
        pass
    return ""


def garantir_permissao(caminho):
    p = Path(caminho)
    if not p.is_file():
        return False
    if os.access(str(p), os.X_OK):
        return True
    aviso(t("c_chmod"))
    try:
        os.chmod(str(p), 0o755)
        sucesso(t("c_chmod_ok"))
        return True
    except Exception:
        erro(f"{t('c_chmod_falha')} {p}")
        return False


def resolver_binarios(interativo=True):
    mudou = False
    if not _executavel(cfg("bin_extract_xiso", "")):
        achado = localizar_binario(NOMES_EXTRACT)
        if achado:
            _config["bin_extract_xiso"] = achado
            mudou = True
    if not _executavel(cfg("bin_iso2god", "")):
        achado = localizar_binario(NOMES_ISO2GOD)
        if achado:
            _config["bin_iso2god"] = achado
            mudou = True
    if not _executavel(cfg("bin_xgdtool", "")):
        achado = localizar_binario(NOMES_XGDTOOL)
        if achado:
            _config["bin_xgdtool"] = achado
            mudou = True
    if mudou:
        salvar_config()

    if not interativo:
        return

    # O XGDTool é opcional: sem ele o programa funciona igual, só não
    # oferece a reconstrução a partir de pasta extraída. Por isso ele
    # não entra aqui — perguntar o caminho de algo opcional na primeira
    # abertura seria atrapalhar quem nem quer usar.
    faltando = []
    if not _executavel(cfg("bin_extract_xiso", "")):
        faltando.append(("extract-xiso", "bin_extract_xiso"))
    if not _executavel(cfg("bin_iso2god", "")):
        faltando.append(("iso2god", "bin_iso2god"))
    if not faltando:
        return

    limpar_tela()
    print(titulo_caixa(t("bin_faltando_titulo"), EMO["config"], cor_borda=C.AMARE))
    for nome, chave in faltando:
        marca_falha(nome, t("indisponivel"))
    print()
    print(cor(f"  {t('bin_procurei')}", C.CINZA))
    print()
    for nome, chave in faltando:
        caminho = perguntar(f"{t('bin_informe')} ({nome})", obrigatorio=False)
        if caminho:
            caminho = os.path.expanduser(caminho.strip())
            if Path(caminho).is_file() and garantir_permissao(caminho):
                _config[chave] = caminho
                salvar_config()
                sucesso(t("c_bin_ok"))
            else:
                erro(t("c_bin_ruim"))
        else:
            dica(t("bin_pulado"))
    print()


def bin_extract():
    return cfg("bin_extract_xiso", "")


def bin_god():
    return cfg("bin_iso2god", "")


_ISO2GOD_PROPRIO = {}


def iso2god_proprio(binario=None):
    """O iso2god em uso é o reescrito em Rust (lux-insider/iso2god), com os
    subcomandos `converter`/`info` e o protocolo `--progresso-json`? Os
    outros (iso2god-rs e afins) recebem ISO e destino direto, com `--trim`.
    A resposta é guardada por binário: o `--help` só roda uma vez."""
    binario = binario or bin_god()
    if binario not in _ISO2GOD_PROPRIO:
        try:
            ajuda = subprocess.run([binario, "--help"], capture_output=True,
                                   text=True, timeout=10).stdout
        except Exception:
            ajuda = ""
        _ISO2GOD_PROPRIO[binario] = "converter" in ajuda and "info" in ajuda
    return _ISO2GOD_PROPRIO[binario]


def bin_xgd():
    return cfg("bin_xgdtool", "")


def exigir_binario(caminho, nome):
    if _executavel(caminho):
        return True
    erro(f"{t('bin_necessario')}: {nome}")
    dica(t("bin_configure"))
    return False


# ═════════════════════════════════════════════════════════════════════════
# ANÁLISE DE ISO
# ═════════════════════════════════════════════════════════════════════════

MAGIC_XBOX = b"MICROSOFT*XBOX*MEDIA"

# offset da assinatura -> (tipo, rótulo do layout de disco)
OFFSETS_ISO = [
    (0x10000, "xbox", "XISO / XGD cru"),
    (0x18310000, "xbox", "XGD1"),
    (0xFDA0000, "xbox360", "XGD2"),
    (0x2090000, "xbox360", "XGD3"),
]

_cache_tipo = {}
LIMITE_CACHE_TIPO = 4000    # evita crescer sem fim numa sessão longa


def detectar_tipo_iso(caminho):
    """Lê a assinatura do ISO. Devolve (tipo, rótulo).

    O resultado é cacheado porque a tabela do navegador é redesenhada a
    cada tecla e reler o disco toda vez seria desperdício. A chave inclui
    tamanho e data de modificação: se o arquivo for substituído no mesmo
    caminho, o cache antigo não é reaproveitado — antes disso, trocar um
    ISO por outro mantinha o tipo errado na tela durante toda a sessão.
    """
    try:
        st = os.stat(caminho)
        chave = (str(caminho), st.st_size, int(st.st_mtime))
    except OSError:
        return ("desconhecido", "")

    if chave in _cache_tipo:
        return _cache_tipo[chave]
    if len(_cache_tipo) > LIMITE_CACHE_TIPO:
        _cache_tipo.clear()

    resultado = ("desconhecido", "")
    try:
        tamanho = st.st_size
        with open(caminho, "rb") as f:
            for offset, tipo, rotulo in OFFSETS_ISO:
                if offset + len(MAGIC_XBOX) > tamanho:
                    continue
                try:
                    f.seek(offset)
                    if f.read(len(MAGIC_XBOX)) == MAGIC_XBOX:
                        # XGD cru pode ser dos dois; o tamanho separa
                        if offset == 0x10000 and tamanho > 6 * 1024 ** 3:
                            resultado = ("xbox360", "XGD cru")
                        else:
                            resultado = (tipo, rotulo)
                        break
                except OSError:
                    continue
    except Exception as e:
        log_evento("aviso", f"Falha ao analisar {caminho}: {e}")

    _cache_tipo[chave] = resultado
    return resultado


def rotulo_tipo(tipo):
    return {"xbox": t("t_xbox"), "xbox360": t("t_xbox360")}.get(tipo, t("t_desconhecido"))


def cor_tipo(tipo):
    return {"xbox": C.VERDE, "xbox360": C.AZUL}.get(tipo, C.CINZA)


def etiqueta_tipo(tipo):
    """Etiqueta curta e colorida para tabelas."""
    if tipo == "xbox":
        return cor("Xbox", C.VERDE)
    if tipo == "xbox360":
        return cor("Xbox 360", C.AZUL)
    return cor("?", C.CINZA)


# ═════════════════════════════════════════════════════════════════════════
# DISCO
# ═════════════════════════════════════════════════════════════════════════

def tamanho_arquivos(lista):
    total = 0
    for a in lista:
        try:
            total += os.path.getsize(a)
        except OSError:
            pass
    return total


def tamanho_pasta(pasta):
    total = 0
    try:
        for raiz, _dirs, arquivos in os.walk(pasta):
            for nome in arquivos:
                try:
                    total += os.path.getsize(os.path.join(raiz, nome))
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _pasta_existente(caminho):
    alvo = os.path.abspath(caminho or ".")
    while not os.path.isdir(alvo):
        pai = os.path.dirname(alvo)
        if pai == alvo:
            return os.path.abspath(".")
        alvo = pai
    return alvo


def mesmo_disco(a, b):
    """Diz se dois caminhos estão no mesmo sistema de arquivos."""
    try:
        return os.stat(_pasta_existente(a)).st_dev == \
               os.stat(_pasta_existente(b)).st_dev
    except OSError:
        return False


def verificar_espaco(destino, necessario_bruto, descricao="", origem=None):
    """Painel de espaço em disco. Devolve True se pode seguir.

    Quando origem e destino estão no MESMO disco e o original vai ser
    apagado no fim, o pico de uso ainda é a soma dos dois: os dois
    arquivos coexistem até a operação terminar. Quando estão em discos
    diferentes, cada um é medido no seu.
    """
    alvo = _pasta_existente(destino or ".")
    try:
        uso = shutil.disk_usage(alvo)
    except Exception as e:
        aviso(f"{t('disco_erro')} {e}")
        return perguntar_sim_nao(t("disco_seguir"), padrao_sim=False)

    necessario = int(necessario_bruto * MARGEM_ESPACO)
    pct = ((uso.total - uso.free) / uso.total) if uso.total else 0.0
    largura_barra = 24
    cheio = int(largura_barra * pct)
    cor_uso = C.VERDE if pct < 0.75 else (C.AMARE if pct < 0.90 else C.VERM)
    barra = cor("\u2588" * cheio, cor_uso) + cor("\u2591" * (largura_barra - cheio), C.CINZA)

    print(campo(t("disco_destino"), cortar(encurtar_home(alvo), 44), EMO["pasta"]))
    print(campo(t("disco_necessario"),
                "%s  %s" % (fmt_bytes(necessario_bruto),
                            cor("(%s: %s)" % (t("margem"), fmt_bytes(necessario)),
                                C.CINZA)),
                EMO["extrair"]))
    print(campo(t("disco_disponivel"),
                cor(fmt_bytes(uso.free), C.VERDE, C.NEG)
                + cor("   %s: %s" % (t("disco_total"), fmt_bytes(uso.total)),
                      C.CINZA),
                EMO["disco"]))
    print(campo(t("disco_uso"), "%s %.1f%%" % (barra, pct * 100), EMO["resumo"]))

    if uso.free < necessario:
        erro(f"{t('disco_falta')} {fmt_bytes(necessario - uso.free)}. {descricao}")
        dica(t("disco_dica"))
        return False

    print("  %s %s" % (cor(_marca_larga(SIM_OK), C.VERDE),
                       cor(t("disco_ok"), C.VERDE)))
    return True


# ═════════════════════════════════════════════════════════════════════════
# EXECUÇÃO DE COMANDOS
# ═════════════════════════════════════════════════════════════════════════

# Só as últimas linhas são exibidas, então guardar todas é desperdício:
# extrair um jogo grande imprime dezenas de milhares de linhas, e guardar
# tudo custava memória para mostrar meia dúzia.
LIMITE_BUFFER_SAIDA = 200


class Resultado:
    def __init__(self):
        self.ok = False
        self.codigo = None
        self.duracao = 0.0
        self.saida = collections.deque(maxlen=LIMITE_BUFFER_SAIDA)
        self.total_linhas = 0
        self.erro = None
        self.cancelado = False


RE_PERCENTUAL = re.compile(r"(\d{1,3})\s?%")

# O extract-xiso anima o próprio progresso com \b, e outras ferramentas
# emitem cor. Cru, isso vaza como lixo na nossa tabela.
RE_CONTROLE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _evento_json(linha):
    """Um evento do `--progresso-json` do iso2god próprio, ou None."""
    if not (linha.startswith("{") and linha.endswith("}")):
        return None
    try:
        evento = json.loads(linha)
    except ValueError:
        return None
    return evento if isinstance(evento, dict) and "evento" in evento else None


def _texto_evento(evento):
    """Texto legível de um evento `fase`/`concluido`/`erro` do iso2god."""
    tipo = evento.get("evento")
    mensagem = evento.get("mensagem", "")
    if tipo == "concluido" and evento.get("pasta") and evento["pasta"] not in mensagem:
        return "%s %s" % (mensagem, evento["pasta"]) if mensagem else evento["pasta"]
    if tipo == "erro":
        return "erro: " + mensagem
    return mensagem


def _limpar_linha_externa(texto):
    """Aplica os backspaces e remove o resto dos caracteres de controle."""
    saida = []
    for ch in texto:
        if ch == "\b":
            if saida:
                saida.pop()
        else:
            saida.append(ch)
    limpo = RE_CONTROLE.sub("", "".join(saida))
    return re.sub(r"\s+", " ", limpo).strip()


def executar(cmd, label, emoji="", destino=None, total=0, mostrar_saida=True,
             max_linhas=10, alvo_arquivo=False):
    """Roda a ferramenta externa com a barra de progresso por cima."""
    resultado = Resultado()

    # O comando completo vai só para o log. Truncado na tela ele não
    # informa nada e ainda polui a área de trabalho do usuário.
    log_evento("info", "cmd: " + " ".join(cmd))

    fiscal = Fiscal(label, destino, total, emoji=emoji,
                    alvo_arquivo=alvo_arquivo)
    fiscal.iniciar()

    processo = None
    inicio = time.time()

    try:
        processo = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, bufsize=0)
        buffer = ""
        while True:
            try:
                pedaco = os.read(processo.stdout.fileno(), 4096)
            except (OSError, ValueError):
                break
            if not pedaco:
                break
            buffer += pedaco.decode("utf-8", "replace")
            while True:
                marca = re.search(r"[\r\n]", buffer)
                if not marca:
                    break
                linha = _limpar_linha_externa(buffer[:marca.start()])
                buffer = buffer[marca.end():]
                if not linha:
                    continue
                # iso2god próprio com --progresso-json: uma linha JSON por
                # evento. O progresso vira a barra; o resto, texto legível.
                evento = _evento_json(linha)
                if evento is not None:
                    if evento.get("evento") == "progresso":
                        total_bytes = evento.get("total_bytes") or 0
                        if total_bytes:
                            fiscal.definir_percentual(
                                min(1.0, evento.get("bytes", 0) / total_bytes))
                        continue
                    linha = _texto_evento(evento)
                    if not linha:
                        continue
                # ferramentas que usam \r repetem a mesma linha várias vezes
                if resultado.saida and resultado.saida[-1] == linha:
                    continue
                resultado.saida.append(linha)
                resultado.total_linhas += 1
                achou = RE_PERCENTUAL.search(linha)
                if achou:
                    try:
                        fiscal.definir_percentual(min(1.0, int(achou.group(1)) / 100.0))
                    except ValueError:
                        pass
                else:
                    fiscal.definir_detalhe(os.path.basename(linha)[:20])

        resto = _limpar_linha_externa(buffer)
        if resto:
            resultado.saida.append(resto)
            resultado.total_linhas += 1

        processo.wait()
        resultado.codigo = processo.returncode
        resultado.ok = (processo.returncode == 0)

    except FileNotFoundError:
        resultado.erro = t("erro_bin_sumiu")
    except PermissionError:
        resultado.erro = t("erro_permissao")
    except KeyboardInterrupt:
        resultado.cancelado = True
        resultado.erro = t("interrompido")
        if processo:
            try:
                processo.terminate()
                processo.wait(timeout=5)
            except Exception:
                try:
                    processo.kill()
                except Exception:
                    pass
    except Exception as e:
        resultado.erro = f"{t('erro_inesperado')}: {e}"
        log_evento("erro", str(e))
    finally:
        resultado.duracao = fiscal.encerrar(ok=resultado.ok)

    # linha final da barra, já com o número real
    if resultado.ok and total:
        fiscal.barra.atualizar(total, final=True)
    elif not fiscal.barra.interativo:
        pass

    if mostrar_saida and resultado.saida:
        bloco = bloco_saida(list(resultado.saida), limite=max_linhas,
                            total_real=resultado.total_linhas)
        if bloco:
            print(bloco)

    if resultado.cancelado:
        aviso(resultado.erro)
    elif resultado.erro:
        erro(resultado.erro)
    elif resultado.ok:
        marca_ok(label, f"{t('concluido')} {fmt_tempo(resultado.duracao)}")
    else:
        marca_falha(label, f"{t('erro_codigo')} {resultado.codigo}")

    return resultado


# ═════════════════════════════════════════════════════════════════════════
# ENTRADA DO USUÁRIO
# ═════════════════════════════════════════════════════════════════════════

def perguntar(texto, obrigatorio=True, padrao=None):
    while True:
        sufixo = cor(f" [{padrao}]", C.CINZA) if padrao else ""
        try:
            resp = input(prompt(f"{cor(texto, C.BRANC)}{sufixo}")).strip()
        except EOFError:
            return padrao if padrao is not None else ""
        except KeyboardInterrupt:
            print()
            return padrao if padrao is not None else ""
        if not resp and padrao is not None:
            return padrao
        if resp or not obrigatorio:
            return resp
        aviso(t("obrigatorio"))


def perguntar_sim_nao(texto, padrao_sim=False):
    sufixo = t("sim_nao_s") if padrao_sim else t("sim_nao_n")
    try:
        resp = input(prompt(f"{cor(texto, C.BRANC)} {cor('[' + sufixo + ']', C.CINZA)}")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return padrao_sim
    if not resp:
        return padrao_sim
    return resp in ("s", "sim", "y", "yes", "1")


def perguntar_inteiro(texto, padrao, minimo=1, maximo=64):
    bruto = perguntar(texto, obrigatorio=False, padrao=str(padrao))
    try:
        return max(minimo, min(maximo, int(str(bruto).strip())))
    except (TypeError, ValueError):
        return padrao


def destino_gravavel(pasta):
    if not pasta:
        return True
    p = Path(os.path.expanduser(pasta))
    if p.is_dir():
        return os.access(str(p), os.W_OK)
    pai = p.parent if str(p.parent) else Path(".")
    return pai.is_dir() and os.access(str(pai), os.W_OK)


def preparar_destino(pasta):
    if not pasta:
        return True
    p = Path(os.path.expanduser(pasta))
    try:
        p.mkdir(parents=True, exist_ok=True)
        return os.access(str(p), os.W_OK)
    except Exception as e:
        erro(f"{t('sem_permissao_escrita')} {pasta} ({e})")
        return False


def listar_isos(pasta):
    achados = []
    for padrao in ("*.iso", "*.ISO", "*.xiso", "*.XISO"):
        achados.extend(glob.glob(os.path.join(pasta, padrao)))
    return sorted(set(achados))


def _expandir_selecao(texto, total):
    """'1,3,5-7' -> [0,2,4,5,6]"""
    indices, invalidos = [], []
    for parte in str(texto).split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte and not parte.startswith("-"):
            a_txt, _, b_txt = parte.partition("-")
            if a_txt.strip().isdigit() and b_txt.strip().isdigit():
                a, b = int(a_txt), int(b_txt)
                if a > b:
                    a, b = b, a
                for n in range(a, b + 1):
                    (indices if 1 <= n <= total else invalidos).append(
                        n - 1 if 1 <= n <= total else str(n))
                continue
        if parte.isdigit():
            n = int(parte)
            if 1 <= n <= total:
                indices.append(n - 1)
            else:
                invalidos.append(parte)
        else:
            invalidos.append(parte)
    vistos, limpos = set(), []
    for i in indices:
        if i not in vistos:
            vistos.add(i)
            limpos.append(i)
    return limpos, invalidos


# ═════════════════════════════════════════════════════════════════════════
# NAVEGADOR DE ARQUIVOS
# ═════════════════════════════════════════════════════════════════════════

CAB_ISO = ["#", "ARQUIVO", "TAMANHO", "CONSOLE"]
AL_ISO = ["<", "<", ">", "<"]


def linhas_iso(itens):
    """Monta as linhas da tabela de seleção."""
    linhas = []
    for i, (rotulo, tipo_item, caminho) in enumerate(itens, 1):
        if tipo_item == "dir":
            linhas.append([cor(str(i), C.VERDE),
                           f"{EMO['pasta']}  {rotulo}",
                           cor("\u2014", C.CINZA),
                           cor("\u2014", C.CINZA)])
        else:
            try:
                tam = fmt_bytes(os.path.getsize(caminho))
            except OSError:
                tam = "N/D"
            tipo, layout = detectar_tipo_iso(str(caminho))
            marca = etiqueta_tipo(tipo)
            if layout:
                marca += cor(f" ({layout})", C.CINZA)
            linhas.append([cor(str(i), C.VERDE),
                           f"{EMO['arquivo']}  {cortar(rotulo, 32)}",
                           cor(tam, C.BRANC), marca])
    return linhas


def navegador(pasta_inicial=None, multiplo=True):
    """Escolha de ISO navegando por pastas. Devolve lista de caminhos."""
    atual = Path(os.path.expanduser(pasta_inicial or cfg("pasta_padrao"))).resolve()
    if not atual.is_dir():
        atual = Path.home()

    while True:
        limpar_tela()
        print(titulo_caixa(t("nav_titulo"), EMO["pasta"], cor_borda=C.CIANO))
        print(campo(t("nav_pasta_atual"), cortar(str(atual), 44), EMO["pasta"]))
        print()

        try:
            subpastas = sorted(
                [p for p in atual.iterdir() if p.is_dir() and not p.name.startswith(".")],
                key=lambda x: x.name.lower())[:30]
        except PermissionError:
            erro(f"{t('sem_permissao_leitura')} {atual}")
            atual = atual.parent
            pausar()
            continue
        except Exception:
            subpastas = []

        isos = [Path(p) for p in listar_isos(str(atual))]

        itens = [(p.name + "/", "dir", p) for p in subpastas]
        itens += [(p.name, "iso", p) for p in isos]

        if itens:
            print(tabela(CAB_ISO, linhas_iso(itens), AL_ISO))
        else:
            marca_nd(t("nav_nenhum_iso"), "")

        print()
        print(opcao("..", t("nav_subir"), "\u2B06\uFE0F", C.AZUL))
        print(opcao("c", t("nav_digitar"), EMO["manual"], C.AZUL))
        if isos and multiplo:
            print(opcao("t", t("nav_todos"), EMO["lote"], C.AZUL))
        print(opcao("0", t("voltar"), EMO["sair"], C.VERM))
        print()
        if multiplo and isos:
            print(cor(f"  {t('nav_multi')}", C.CINZA, C.ITAL))

        escolha = perguntar(t("escolha"), obrigatorio=False).strip()

        if not escolha or escolha == "0":
            return []
        if escolha == "..":
            atual = atual.parent
            continue
        if escolha.lower() == "c":
            caminho = perguntar(t("nav_caminho"), obrigatorio=False)
            if caminho:
                alvo = Path(os.path.expanduser(caminho.strip()))
                if alvo.is_dir():
                    atual = alvo.resolve()
                elif alvo.is_file():
                    return [str(alvo.resolve())]
                else:
                    erro(f"{t('pasta_nao_existe')} {alvo}")
                    pausar()
            continue
        if escolha.lower() == "t" and isos and multiplo:
            set_cfg("pasta_padrao", str(atual))
            return [str(p) for p in isos]

        indices, invalidos = _expandir_selecao(escolha, len(itens))
        if invalidos:
            aviso(f"{t('nav_invalido')} {', '.join(invalidos)}")
        if not indices:
            pausar()
            continue

        if itens[indices[0]][1] == "dir":
            atual = itens[indices[0]][2].resolve()
            continue

        selecionados = [str(itens[i][2]) for i in indices if itens[i][1] == "iso"]
        if not selecionados:
            aviso(t("nav_nada"))
            pausar()
            continue
        if not multiplo:
            selecionados = selecionados[:1]

        set_cfg("pasta_padrao", str(atual))
        return selecionados


def resumo_selecao(arquivos):
    """Tabela do que foi selecionado + total. Devolve o total em bytes."""
    itens = [(os.path.basename(a), "iso", a) for a in arquivos]
    print(tabela(CAB_ISO, linhas_iso(itens), AL_ISO))
    total = tamanho_arquivos(arquivos)
    print()
    print(campo(t("total"), f"{len(arquivos)} {t('arquivos_sel')}  "
                            + cor(fmt_bytes(total), C.BRANC, C.NEG), EMO["resumo"]))
    return total


# ═════════════════════════════════════════════════════════════════════════
# RESUMO FINAL
# ═════════════════════════════════════════════════════════════════════════

def mostrar_resumo(titulo, ok, falhas, duracao, extras=None, produzidos=None):
    """
    Resumo final. `produzidos` é a lista de arquivos/pastas gerados — sem
    ela o usuário sabe que deu certo mas não sabe onde a coisa foi parar,
    que é a informação que ele realmente precisa.
    """
    tudo_ok = (falhas == 0 and ok > 0)
    cor_borda = C.VERDE if tudo_ok else (C.AMARE if ok else C.VERM)
    emoji_topo = EMO["ok"] if tudo_ok else (EMO["aviso"] if ok else EMO["erro"])
    print(titulo_caixa("%s \u2014 %s" % (t("resumo"), titulo), emoji_topo,
                       cor_borda=cor_borda))

    if ok:
        marca_ok(t("r_sucesso"), str(ok))
    else:
        marca_nd(t("r_sucesso"), "0")
    if falhas:
        marca_falha(t("r_falhas"), str(falhas))
    else:
        marca_ok(t("r_falhas"), "0")
    print(campo(t("r_tempo"), fmt_tempo(duracao), EMO["tempo"], largura_rotulo=17))

    for rotulo, valor, emoji in (extras or []):
        print(campo(rotulo, valor, emoji, largura_rotulo=17))

    if produzidos:
        print()
        print("  " + cor(t("r_gerados"), C.NEG, C.BRANC))
        for caminho in produzidos[:8]:
            try:
                tam = fmt_bytes(os.path.getsize(caminho)) if os.path.isfile(caminho) \
                      else fmt_bytes(tamanho_pasta(caminho))
            except OSError:
                tam = "N/D"
            emoji_item = EMO["arquivo"] if os.path.isfile(caminho) else EMO["pasta"]
            print("    %s %s  %s" % (emoji_item,
                                     cor(cortar(encurtar_home(caminho), 44), C.BRANC),
                                     cor(tam, C.CINZA)))
        if len(produzidos) > 8:
            print("    " + cor("\u2026 +%d" % (len(produzidos) - 8), C.CINZA, C.ITAL))

    print()
    print(cor("  %s: %s" % (t("r_log"), encurtar_home(str(ARQ_LOG))), C.CINZA, C.ITAL))


def encurtar_home(caminho):
    """~/Jogos lê melhor que /home/usuario/Jogos e economiza colunas."""
    try:
        casa = str(Path.home())
        texto = str(caminho)
        if texto.startswith(casa):
            return "~" + texto[len(casa):]
        return texto
    except Exception:
        return str(caminho)


def tela(titulo, emoji, descricao, cor_borda=None):
    limpar_tela()
    print(titulo_caixa(titulo, emoji, cor_borda=cor_borda or C.AZUL))
    print(cor("  " + descricao, C.CINZA))


# ═════════════════════════════════════════════════════════════════════════
# AÇÕES — EXTRACT-XISO
# ═════════════════════════════════════════════════════════════════════════

def opcoes_comuns():
    return ["-q"] if perguntar_sim_nao(t("p_silencioso"), padrao_sim=False) else []


def acao_extrair(arquivos=None):
    tela(t("m_extrair"), EMO["extrair"], t("d_extrair"), C.VERDE)
    if not exigir_binario(bin_extract(), "extract-xiso"):
        pausar()
        return

    # ── 1. Seleção ───────────────────────────────────────────────────────
    if arquivos is None:
        arquivos = navegador()
    if not arquivos:
        return

    print(etapa(1, t("e_selecao"), total=4))
    total = resumo_selecao(arquivos)

    # ── 2. Destino ───────────────────────────────────────────────────────
    print(etapa(2, t("e_destino"), total=4))
    destino = perguntar(t("p_destino"), obrigatorio=False)
    destino = os.path.expanduser(destino) if destino else ""
    if destino and not destino_gravavel(destino):
        erro("%s %s" % (t("sem_permissao_escrita"), destino))
        pausar()
        return
    print(resposta(encurtar_home(destino) if destino else t("r_padrao"), C.CIANO))

    # ── 3. Opções ────────────────────────────────────────────────────────
    print(etapa(3, t("e_opcoes"), total=4))
    pular = perguntar_sim_nao(t("p_pular_update"), padrao_sim=False)
    flags = opcoes_comuns()

    # ── 4. Verificação ───────────────────────────────────────────────────
    print(etapa(4, t("e_verificacao"), total=4))
    if not verificar_espaco(destino or os.path.dirname(arquivos[0]) or ".", total):
        pausar()
        return
    if destino and not preparar_destino(destino):
        pausar()
        return
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True):
        aviso(t("cancelado"))
        pausar()
        return

    # ── Execução ─────────────────────────────────────────────────────────
    print(etapa("", t("e_execucao")))
    ok = falhas = 0
    gerados = []
    inicio = time.time()

    for i, arquivo in enumerate(arquivos, 1):
        alvo = destino or os.path.join(os.path.dirname(arquivo) or ".",
                                       Path(arquivo).stem)
        if len(arquivos) > 1:
            print(campo("%d/%d" % (i, len(arquivos)),
                        cortar(os.path.basename(arquivo), 40),
                        EMO["arquivo"], largura_rotulo=8))

        args = [bin_extract(), "-x"]
        if destino:
            args += ["-d", destino]
        if pular:
            args.append("-s")
        args += flags + [arquivo]

        try:
            esperado = os.path.getsize(arquivo)
        except OSError:
            esperado = 0

        r = executar(args, t("extraindo"), EMO["extrair"],
                     destino=alvo, total=esperado)
        if r.ok and os.path.isdir(alvo):
            gerados.append(alvo)
        if r.cancelado:
            falhas += 1
            break
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1

    mostrar_resumo(t("m_extrair"), ok, falhas, time.time() - inicio,
                   produzidos=gerados)
    pausar()


def acao_listar(arquivos=None):
    tela(t("m_listar"), EMO["listar"], t("d_listar"), C.CIANO)
    if not exigir_binario(bin_extract(), "extract-xiso"):
        pausar()
        return

    if arquivos is None:
        arquivos = navegador()
    if not arquivos:
        return

    print(etapa(1, t("e_selecao"), total=2))
    resumo_selecao(arquivos)

    print(etapa(2, t("e_resultado"), total=2))
    ok = falhas = 0
    inicio = time.time()

    for arquivo in arquivos:
        if len(arquivos) > 1:
            print(campo(t("nav_titulo"), cortar(os.path.basename(arquivo), 40),
                        EMO["arquivo"], largura_rotulo=10))
        r = executar([bin_extract(), "-l", arquivo], t("listando"),
                     EMO["listar"], max_linhas=40)
        if r.cancelado:
            falhas += 1
            break
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1

    mostrar_resumo(t("m_listar"), ok, falhas, time.time() - inicio)
    pausar()


def acao_criar():
    tela(t("m_criar"), EMO["criar"], t("d_criar"), C.ROXO)
    if not exigir_binario(bin_extract(), "extract-xiso"):
        pausar()
        return

    # ── 1. Origem ────────────────────────────────────────────────────────
    print(etapa(1, t("e_origem"), total=4))
    trabalhos = []
    while True:
        origem = perguntar(t("p_pasta_origem"), obrigatorio=False)
        if not origem:
            break
        origem = os.path.expanduser(origem)
        if not os.path.isdir(origem):
            erro("%s %s" % (t("pasta_nao_existe"), origem))
            if not perguntar_sim_nao(t("p_tentar_novamente"), padrao_sim=True):
                break
            continue
        if not os.access(origem, os.R_OK):
            erro("%s %s" % (t("sem_permissao_leitura"), origem))
            if not perguntar_sim_nao(t("p_tentar_novamente"), padrao_sim=True):
                break
            continue

        n_arquivos = sum(len(a) for _r, _d, a in os.walk(origem))
        tam = tamanho_pasta(origem)
        print(resposta("%s \u00B7 %d %s \u00B7 %s"
                       % (os.path.basename(os.path.normpath(origem)),
                          n_arquivos, t("arquivos"), fmt_bytes(tam)), C.VERDE))
        trabalhos.append({"origem": origem, "saida": "",
                          "arquivos": n_arquivos, "tamanho": tam})

        if not perguntar_sim_nao(t("p_outra_pasta"), padrao_sim=False):
            break

    if not trabalhos:
        aviso(t("nav_nada"))
        pausar()
        return

    # ── 2. Destino ───────────────────────────────────────────────────────
    print(etapa(2, t("e_destino"), total=4))
    for tr in trabalhos:
        padrao = os.path.basename(os.path.normpath(tr["origem"])) + ".iso"
        saida = perguntar("%s (%s)" % (t("p_nome_saida"), padrao),
                          obrigatorio=False)
        saida = os.path.expanduser(saida.strip()) if saida else ""
        if saida and not saida.lower().endswith(".iso"):
            saida += ".iso"
        if saida and not destino_gravavel(os.path.dirname(saida) or "."):
            erro("%s %s" % (t("sem_permissao_escrita"), saida))
            pausar()
            return
        tr["saida"] = saida
        tr["alvo"] = saida or os.path.join(os.getcwd(), padrao)
        print(resposta(encurtar_home(tr["alvo"]), C.CIANO))

    # ── 3. Opções ────────────────────────────────────────────────────────
    print(etapa(3, t("e_opcoes"), total=4))
    sem_patch = perguntar_sim_nao(t("p_sem_patch"), padrao_sim=False)
    flags = opcoes_comuns()

    # ── 4. Verificação ───────────────────────────────────────────────────
    print(etapa(4, t("e_verificacao"), total=4))
    total = sum(tr["tamanho"] for tr in trabalhos)
    destino_check = os.path.dirname(trabalhos[0]["alvo"]) or "."
    if not verificar_espaco(destino_check, total):
        pausar()
        return
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True):
        aviso(t("cancelado"))
        pausar()
        return

    # ── Execução ─────────────────────────────────────────────────────────
    print(etapa("", t("e_execucao")))
    ok = falhas = 0
    gerados = []
    inicio = time.time()

    for i, tr in enumerate(trabalhos, 1):
        if len(trabalhos) > 1:
            print(campo("%d/%d" % (i, len(trabalhos)),
                        os.path.basename(os.path.normpath(tr["origem"])),
                        EMO["pasta"], largura_rotulo=8))

        args = [bin_extract(), "-c", tr["origem"]]
        if tr["saida"]:
            args.append(tr["saida"])
        if sem_patch:
            args.append("-m")
        args += flags

        r = executar(args, t("criando"), EMO["criar"],
                     destino=tr["alvo"], total=tr["tamanho"],
                     alvo_arquivo=True)
        if r.ok and os.path.isfile(tr["alvo"]):
            gerados.append(tr["alvo"])
        if r.cancelado:
            falhas += 1
            break
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1

    mostrar_resumo(t("m_criar"), ok, falhas, time.time() - inicio,
                   produzidos=gerados)
    pausar()


def acao_reescrever(arquivos=None):
    tela(t("m_reescrever"), EMO["otimizar"], t("d_reescrever"), C.AMARE)
    if not exigir_binario(bin_extract(), "extract-xiso"):
        pausar()
        return

    if arquivos is None:
        arquivos = navegador()
    if not arquivos:
        return

    print(etapa(1, t("e_selecao"), total=4))
    total = resumo_selecao(arquivos)

    print(etapa(2, t("e_destino"), total=4))
    destino = perguntar(t("p_destino"), obrigatorio=False)
    destino = os.path.expanduser(destino) if destino else ""
    if destino and not destino_gravavel(destino):
        erro("%s %s" % (t("sem_permissao_escrita"), destino))
        pausar()
        return
    print(resposta(encurtar_home(destino) if destino else t("r_padrao"), C.CIANO))

    print(etapa(3, t("e_opcoes"), total=4))
    apagar = perguntar_sim_nao(t("p_apagar_antigo"), padrao_sim=False)
    if apagar:
        aviso(t("p_apagar_certeza"))
        if not perguntar_sim_nao(t("confirmar"), padrao_sim=False):
            apagar = False
            print(resposta(t("cancelado")))
    sem_patch = perguntar_sim_nao(t("p_sem_patch"), padrao_sim=False)
    flags = opcoes_comuns()

    print(etapa(4, t("e_verificacao"), total=4))
    # sem apagar o original, os dois arquivos coexistem no pico
    necessario = total if apagar else total * 2
    if not verificar_espaco(destino or os.path.dirname(arquivos[0]) or ".", necessario):
        pausar()
        return
    if destino and not preparar_destino(destino):
        pausar()
        return
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True):
        aviso(t("cancelado"))
        pausar()
        return

    print(etapa("", t("e_execucao")))
    ok = falhas = 0
    inertes = 0
    gerados = []
    inicio = time.time()

    for i, arquivo in enumerate(arquivos, 1):
        if len(arquivos) > 1:
            print(campo("%d/%d" % (i, len(arquivos)),
                        cortar(os.path.basename(arquivo), 40),
                        EMO["arquivo"], largura_rotulo=8))

        alvo_pasta = destino or (os.path.dirname(arquivo) or ".")
        antes = set(os.listdir(alvo_pasta)) if os.path.isdir(alvo_pasta) else set()

        args = [bin_extract(), "-r"]
        if destino:
            args += ["-d", destino]
        if apagar:
            args.append("-D")
        if sem_patch:
            args.append("-m")
        args += flags + [arquivo]

        try:
            esperado = os.path.getsize(arquivo)
        except OSError:
            esperado = 0

        r = executar(args, t("reescrevendo"), EMO["otimizar"],
                     destino=alvo_pasta, total=esperado)

        depois = set(os.listdir(alvo_pasta)) if os.path.isdir(alvo_pasta) else set()
        novos = [os.path.join(alvo_pasta, n) for n in sorted(depois - antes)]

        # O extract-xiso pula ISOs que já estão otimizados e sai com
        # código 0. Sem esta checagem, a tela mostraria "sucesso" e uma
        # lista de arquivos gerados vazia — e o usuário ficaria sem saber
        # se funcionou.
        if r.ok and not novos:
            ja_otimizado = any("already optimized" in l.lower()
                               or "skipping" in l.lower() for l in r.saida)
            print(resposta(t("nada_a_fazer") if ja_otimizado else t("sem_saida"),
                           C.CINZA))
            inertes += 1
        elif r.ok:
            gerados.extend(novos)

        if r.cancelado:
            falhas += 1
            break
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1

    extras = [(t("p_apagar_antigo"), "sim" if apagar else "não", EMO["lixo"])]
    if inertes:
        extras.append((t("r_inertes"), str(inertes), EMO["info"]))

    mostrar_resumo(t("m_reescrever"), ok, falhas, time.time() - inicio,
                   extras=extras, produzidos=gerados)
    pausar()


# ═════════════════════════════════════════════════════════════════════════
# AÇÕES — ISO2GOD
# ═════════════════════════════════════════════════════════════════════════

def acao_god(arquivos=None):
    tela(t("m_god"), EMO["god"], t("d_god"), C.AZUL)
    if not exigir_binario(bin_god(), "iso2god"):
        pausar()
        return

    if arquivos is None:
        arquivos = navegador()
    if not arquivos:
        return

    print(etapa(1, t("e_selecao"), total=4))
    total = resumo_selecao(arquivos)

    print(etapa(2, t("e_destino"), total=4))
    destino = perguntar(t("p_destino_obrig"), obrigatorio=True)
    destino = os.path.expanduser(destino)
    if not destino_gravavel(destino):
        erro("%s %s" % (t("sem_permissao_escrita"), destino))
        pausar()
        return
    print(resposta(encurtar_home(destino), C.CIANO))

    print(etapa(3, t("e_opcoes"), total=4))
    titulo_jogo = perguntar(t("p_titulo_jogo"), obrigatorio=False) if len(arquivos) == 1 else ""
    threads = perguntar_inteiro(t("p_threads"), cfg("threads_god", 4), 1, 32)
    cortar_fim = perguntar_sim_nao(t("p_trim"), padrao_sim=True)

    print(etapa(4, t("e_verificacao"), total=4))
    if not verificar_espaco(destino, total):
        pausar()
        return
    if not preparar_destino(destino):
        pausar()
        return
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True):
        aviso(t("cancelado"))
        pausar()
        return

    set_cfg("threads_god", threads)
    print(etapa("", t("e_execucao")))
    ok = falhas = 0
    inicio = time.time()
    antes = set(os.listdir(destino)) if os.path.isdir(destino) else set()

    for i, arquivo in enumerate(arquivos, 1):
        if len(arquivos) > 1:
            print(campo("%d/%d" % (i, len(arquivos)),
                        cortar(os.path.basename(arquivo), 40),
                        EMO["jogo"], largura_rotulo=8))

        if iso2god_proprio():
            # "cortar o fim" do iso2god-rs equivale ao padding "parcial"
            args = [bin_god(), "converter", "--progresso-json",
                    "-j", str(threads),
                    "--padding", "parcial" if cortar_fim else "nenhuma"]
            if titulo_jogo:
                args += ["--titulo", titulo_jogo]
        else:
            args = [bin_god(), "-j", str(threads)]
            if titulo_jogo:
                args += ["--game-title", titulo_jogo]
            args.append("--trim=from-end" if cortar_fim else "--trim=none")
        args += [arquivo, destino]

        try:
            esperado = os.path.getsize(arquivo)
        except OSError:
            esperado = 0

        r = executar(args, t("convertendo"), EMO["god"],
                     destino=destino, total=esperado)
        if r.cancelado:
            falhas += 1
            break
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1

    depois = set(os.listdir(destino)) if os.path.isdir(destino) else set()
    gerados = [os.path.join(destino, n) for n in sorted(depois - antes)]

    mostrar_resumo(t("m_god"), ok, falhas, time.time() - inicio,
                   produzidos=gerados)
    pausar()


def pasta_temp():
    alvo = DIR_BASE / "tmp"
    try:
        alvo.mkdir(parents=True, exist_ok=True)
        return str(alvo)
    except Exception:
        return "/tmp"


def mostrar_info_iso2god(arquivo):
    """Análise pelo iso2god próprio: `info --json`, mostrado com os mesmos
    campos alinhados do resto da tela (em vez das caixas do iso2god, que
    ficam tortas dentro do quadro de saída)."""
    cmd = [bin_god(), "info", "--json", arquivo]
    log_evento("info", "cmd: " + " ".join(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        erro(str(e))
        return False
    for linha in r.stderr.splitlines():
        linha = _limpar_linha_externa(linha)
        if linha:
            print(cor("        " + linha, C.CINZA))
    try:
        info = json.loads(r.stdout)
    except ValueError:
        # o motivo já saiu acima, pelo stderr do iso2god
        if not r.stderr.strip():
            erro(t("erro_inesperado"))
        return False

    def valor(chave):
        v = info.get(chave)
        return str(v) if v not in (None, "") else None

    plataforma = {"xbox360": "Xbox 360", "xbox": "Xbox"}.get(
        info.get("plataforma_detectada"), None)
    if plataforma:
        print(campo(t("i_plataforma"), plataforma, EMO["jogo"], largura_rotulo=16))
    else:
        marca_nd(t("i_plataforma"), "%s (%s)" % (t("i_nao_detectada"), t("i_sem_executavel")))
    for rotulo, chave in (("Title ID", "title_id"), ("Media ID", "media_id"),
                          (t("i_titulo"), "titulo")):
        if valor(chave):
            print(campo(rotulo, valor(chave), EMO["info"], largura_rotulo=16))
    if info.get("disco"):
        print(campo(t("i_disco"), "%s/%s" % (info["disco"], info.get("total_discos") or "?"),
                    EMO["disco"], largura_rotulo=16))
    if info.get("tamanho_volume"):
        print(campo(t("i_volume"), "%s  %s" % (fmt_bytes(info["tamanho_volume"]),
                                              cor("(" + str(info.get("tipo_disco", "")) + ")", C.CINZA)),
                    EMO["disco"], largura_rotulo=16))
    print()
    return r.returncode == 0


def acao_info_god(arquivos=None):
    tela(t("m_info_god"), EMO["analisar"], t("d_info_god"), C.CIANO)
    if not exigir_binario(bin_god(), "iso2god"):
        pausar()
        return

    if arquivos is None:
        arquivos = navegador()
    if not arquivos:
        return

    print(etapa(1, t("e_selecao"), total=2))
    resumo_selecao(arquivos)

    print(etapa(2, t("e_resultado"), total=2))
    ok = falhas = 0
    inicio = time.time()

    for arquivo in arquivos:
        tipo, layout = detectar_tipo_iso(arquivo)
        print(campo(t("nav_titulo"), cortar(os.path.basename(arquivo), 40),
                    EMO["arquivo"], largura_rotulo=16))
        print(campo(t("tipo_detectado"),
                    cor(rotulo_tipo(tipo), cor_tipo(tipo))
                    + (cor("  (%s)" % layout, C.CINZA) if layout else ""),
                    EMO["analisar"], largura_rotulo=16))
        if iso2god_proprio():
            deu_certo = mostrar_info_iso2god(arquivo)
            ok += 1 if deu_certo else 0
            falhas += 0 if deu_certo else 1
            continue
        r = executar([bin_god(), "--dry-run", arquivo, pasta_temp()],
                     t("analisando"), EMO["analisar"], max_linhas=25)
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1
        if r.cancelado:
            break

    mostrar_resumo(t("m_info_god"), ok, falhas, time.time() - inicio)
    pausar()


# ═════════════════════════════════════════════════════════════════════════
# ASSISTENTE — detecta o disco e só oferece o que faz sentido
# ═════════════════════════════════════════════════════════════════════════

def acao_assistente():
    tela(t("m_assistente"), EMO["assistente"], t("d_assistente"), C.ROXO)

    arquivos = navegador(multiplo=True)
    if not arquivos:
        return

    principal = arquivos[0]
    tipo, layout = detectar_tipo_iso(principal)

    limpar_tela()
    print(titulo_caixa(t("tipo_detectado"), EMO["assistente"],
                       cor_borda=cor_tipo(tipo)))
    print(campo(t("nav_titulo"), cortar(os.path.basename(principal), 44), EMO["arquivo"]))
    try:
        print(campo(t("total"), fmt_bytes(os.path.getsize(principal)), EMO["disco"]))
    except OSError:
        marca_nd(t("total"))
    print(campo(t("tipo_detectado"),
                cor(rotulo_tipo(tipo), cor_tipo(tipo), C.NEG)
                + (cor(f"   {layout}", C.CINZA) if layout else ""), EMO["analisar"]))
    if len(arquivos) > 1:
        print(campo(t("arquivos_sel"), str(len(arquivos)), EMO["lote"]))
    print()

    if tipo == "xbox":
        dica(t("sug_xbox"))
        escolhas = [
            (EMO["extrair"], t("m_extrair"), t("d_extrair"), lambda: acao_extrair(arquivos)),
            (EMO["listar"], t("m_listar"), t("d_listar"), lambda: acao_listar(arquivos)),
            (EMO["otimizar"], t("m_reescrever"), t("d_reescrever"), lambda: acao_reescrever(arquivos)),
        ]
    elif tipo == "xbox360":
        dica(t("sug_360"))
        escolhas = [
            (EMO["god"], t("m_god"), t("d_god"), lambda: acao_god(arquivos)),
            (EMO["analisar"], t("m_info_god"), t("d_info_god"), lambda: acao_info_god(arquivos)),
        ]
    else:
        dica(t("sug_nada"))
        escolhas = [
            (EMO["extrair"], t("m_extrair"), t("d_extrair"), lambda: acao_extrair(arquivos)),
            (EMO["listar"], t("m_listar"), t("d_listar"), lambda: acao_listar(arquivos)),
            (EMO["god"], t("m_god"), t("d_god"), lambda: acao_god(arquivos)),
        ]

    print()
    print(cor(f"  {t('acoes_sugeridas')}", C.NEG, C.BRANC))
    print()
    for i, (emoji, rotulo, desc, _fn) in enumerate(escolhas, 1):
        estrela = cor("  " + EMO["estrela"], C.AMARE) if i == 1 else ""
        print(opcao(str(i), rotulo, emoji) + estrela)
        print(cor(f"        {desc}", C.CINZA))
    print()
    print(f"  {cor('[ENTER]', C.VERDE, C.NEG)} "
          f"{cor(t('sug_automatica'), C.CINZA)} "
          f"{cor(escolhas[0][1], C.BRANC)}")
    print(opcao("0", t("voltar"), EMO["sair"], C.VERM))

    escolha = perguntar(t("escolha"), obrigatorio=False).strip()
    if escolha == "0":
        return
    if not escolha:
        escolhas[0][3]()
        return
    if escolha.isdigit() and 1 <= int(escolha) <= len(escolhas):
        escolhas[int(escolha) - 1][3]()
        return
    aviso(t("opcao_invalida"))
    pausar()


# ═════════════════════════════════════════════════════════════════════════
# LOTE E COMANDO MANUAL
# ═════════════════════════════════════════════════════════════════════════

def acao_lote():
    tela(t("m_lote"), EMO["lote"], t("d_lote"), C.ROXO)

    print(etapa(1, t("e_origem"), total=3))
    pasta = perguntar(t("p_pasta_iso"), padrao=cfg("pasta_padrao"))
    pasta = os.path.expanduser(pasta)
    if not os.path.isdir(pasta):
        erro("%s %s" % (t("pasta_nao_existe"), pasta))
        pausar()
        return

    arquivos = listar_isos(pasta)
    if not arquivos:
        aviso(t("nav_nenhum_iso"))
        pausar()
        return

    set_cfg("pasta_padrao", pasta)
    print(resposta("%d %s \u00B7 %s" % (len(arquivos), t("arquivos"),
                                        fmt_bytes(tamanho_arquivos(arquivos))),
                   C.VERDE))

    print(etapa(2, t("e_selecao"), total=3))
    resumo_selecao(arquivos)

    classico = [a for a in arquivos if detectar_tipo_iso(a)[0] == "xbox"]
    trezentos = [a for a in arquivos if detectar_tipo_iso(a)[0] == "xbox360"]
    outros = [a for a in arquivos if a not in classico and a not in trezentos]

    print()
    if classico:
        marca_ok(t("t_xbox"), str(len(classico)))
    else:
        marca_nd(t("t_xbox"), "0")
    if trezentos:
        marca_ok(t("t_xbox360"), str(len(trezentos)))
    else:
        marca_nd(t("t_xbox360"), "0")
    if outros:
        marca_nd(t("t_desconhecido"), str(len(outros)))

    print(etapa(3, t("e_opcoes"), total=3))
    escolhas = []
    if classico:
        escolhas.append((EMO["extrair"], "%s (%d)" % (t("m_extrair"), len(classico)),
                         lambda: acao_extrair(classico)))
        escolhas.append((EMO["otimizar"], "%s (%d)" % (t("m_reescrever"), len(classico)),
                         lambda: acao_reescrever(classico)))
    if trezentos:
        escolhas.append((EMO["god"], "%s (%d)" % (t("m_god"), len(trezentos)),
                         lambda: acao_god(trezentos)))
    escolhas.append((EMO["extrair"], "%s (%d)" % (t("m_extrair"), len(arquivos)),
                     lambda: acao_extrair(arquivos)))

    print()
    for i, (emoji, rotulo, _fn) in enumerate(escolhas, 1):
        print(opcao(str(i), rotulo, emoji))
    print(opcao("0", t("voltar"), EMO["sair"], C.VERM))

    escolha = perguntar(t("escolha"), obrigatorio=False).strip()
    if escolha.isdigit() and 1 <= int(escolha) <= len(escolhas):
        escolhas[int(escolha) - 1][2]()


def acao_manual():
    tela(t("m_manual"), EMO["manual"], t("d_manual"), C.AMARE)

    print(opcao("1", "extract-xiso", EMO["extrair"]))
    print(opcao("2", "iso2god", EMO["god"]))
    print(opcao("0", t("voltar"), EMO["sair"], C.VERM))

    qual = perguntar(t("escolha"), obrigatorio=False).strip()
    if qual == "1":
        binario, nome = bin_extract(), "extract-xiso"
        exemplo = "-x -s -d ~/Extraidos jogo.iso"
    elif qual == "2":
        binario, nome = bin_god(), "iso2god"
        exemplo = ("converter -j 4 --padding parcial jogo.iso ~/GOD"
                   if iso2god_proprio() else "-j 4 --trim=from-end jogo.iso ~/GOD")
    else:
        return

    if not exigir_binario(binario, nome):
        pausar()
        return

    print()
    print(cor(f"  {EMO['dica']}  {exemplo}", C.CINZA, C.ITAL))

    bruto = perguntar(t("p_args"), obrigatorio=False)
    if not bruto:
        return
    try:
        import shlex
        args = shlex.split(bruto)
    except Exception:
        args = bruto.split()

    print()
    r = executar([binario] + args, t("executando"), EMO["manual"], max_linhas=30)
    mostrar_resumo(t("m_manual"), 1 if r.ok else 0, 0 if r.ok else 1, r.duracao)
    pausar()


# ═════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÕES, LOG E SOBRE
# ═════════════════════════════════════════════════════════════════════════

def acao_config():
    while True:
        limpar_tela()
        print(titulo_caixa(t("cfg_titulo"), EMO["config"], cor_borda=C.CIANO))

        idioma = "Português" if cfg("idioma") == "pt" else "English"
        print(opcao("1", t("c_idioma"), EMO["idioma"]) + cor(f"   {idioma}", C.BRANC))

        for chave_menu, chave_cfg, rotulo, emoji in (
            ("2", "bin_extract_xiso", t("c_extract"), EMO["extrair"]),
            ("3", "bin_iso2god", t("c_iso2god"), EMO["god"]),
        ):
            caminho = cfg(chave_cfg, "")
            if _executavel(caminho):
                valor = cor(cortar(caminho, 34), C.VERDE)
            elif caminho:
                valor = cor(cortar(caminho, 34), C.VERM)
            else:
                valor = cor(t("c_nao_definido"), C.CINZA)
            print(opcao(chave_menu, rotulo, emoji) + f"   {valor}")

        print(opcao("4", t("c_pasta"), EMO["pasta"])
              + cor(f"   {cortar(cfg('pasta_padrao'), 34)}", C.BRANC))
        print(opcao("5", t("c_threads"), EMO["lote"])
              + cor(f"   {cfg('threads_god')}", C.BRANC))
        print(opcao("6", t("c_cor"), EMO["app"])
              + cor(f"   {t('c_ligado') if cfg('cor') else t('c_desligado')}", C.BRANC))
        print()
        print(opcao("0", t("voltar"), EMO["sair"], C.VERM))

        escolha = perguntar(t("escolha"), obrigatorio=False).strip()
        if not escolha or escolha == "0":
            return

        if escolha == "1":
            set_cfg("idioma", "en" if cfg("idioma") == "pt" else "pt")
        elif escolha in ("2", "3"):
            chave = "bin_extract_xiso" if escolha == "2" else "bin_iso2god"
            atual = cfg(chave, "")
            novo = perguntar(t("c_novo_valor"), obrigatorio=False, padrao=atual or None)
            if novo and novo != atual:
                novo = os.path.expanduser(novo.strip())
                if Path(novo).is_file() and garantir_permissao(novo):
                    set_cfg(chave, novo)
                    sucesso(t("c_bin_ok"))
                else:
                    erro(t("c_bin_ruim"))
                pausar()
        elif escolha == "4":
            novo = perguntar(t("c_novo_valor"), obrigatorio=False, padrao=cfg("pasta_padrao"))
            if novo:
                novo = os.path.expanduser(novo.strip())
                if os.path.isdir(novo):
                    set_cfg("pasta_padrao", novo)
                    sucesso(t("c_salvo"))
                else:
                    erro(f"{t('pasta_nao_existe')} {novo}")
                pausar()
        elif escolha == "5":
            set_cfg("threads_god", perguntar_inteiro(t("c_novo_valor"), cfg("threads_god"), 1, 32))
        elif escolha == "6":
            set_cfg("cor", not cfg("cor"))
        else:
            aviso(t("opcao_invalida"))
            pausar()


def acao_log():
    limpar_tela()
    print(titulo_caixa(t("log_titulo"), EMO["log"], cor_borda=C.CIANO))

    if not ARQ_LOG.is_file():
        marca_nd(t("log_vazio"), "")
        pausar()
        return

    try:
        print(campo(t("sobre_log"), cortar(str(ARQ_LOG), 44), EMO["arquivo"]))
        print(campo(t("total"), fmt_bytes(ARQ_LOG.stat().st_size), EMO["disco"]))
        print()
        print(regua())
        print()
        with open(ARQ_LOG, "r", encoding="utf-8", errors="replace") as f:
            linhas = f.readlines()
        for linha in linhas[-25:]:
            linha = linha.rstrip("\n")
            if "[ERROR]" in linha:
                print("  " + cor(cortar(linha, LARGURA), C.VERM))
            elif "[WARNING]" in linha:
                print("  " + cor(cortar(linha, LARGURA), C.AMARE))
            else:
                print("  " + cor(cortar(linha, LARGURA), C.CINZA))
    except Exception as e:
        erro(f"{t('log_erro_ler')} {e}")

    pausar()


def acao_sobre():
    limpar_tela()
    print(titulo_caixa(f"{APP_NOME} v{APP_VERSAO}", EMO["sobre"], cor_borda=C.ROXO))
    print(cor(f"  {t('sobre_1')}", C.BRANC))
    print()
    print(campo("extract-xiso", t("sobre_2"), EMO["extrair"], largura_rotulo=15))
    print(campo("iso2god", t("sobre_3"), EMO["god"], largura_rotulo=15))
    print()
    print(regua_gradiente())
    print()
    print(campo(t("sobre_config"), cortar(str(ARQ_CONFIG), 44), EMO["config"]))
    print(campo(t("sobre_log"), cortar(str(ARQ_LOG), 44), EMO["log"]))
    print()
    print(cor(f"  {t('sobre_4')}", C.CINZA, C.ITAL))
    pausar()


# ═════════════════════════════════════════════════════════════════════════
# MENU PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════

def itens_menu():
    """
    Menu agrupado por console, com a ferramenta responsável visível.

    A divisão tem TRÊS grupos, não dois, porque extrair e listar
    funcionam nos dois consoles: o extract-xiso lê XISO de 360 sem
    problema, só não sabe escrever. Colocar essas duas embaixo de
    "Xbox clássico" faria o usuário achar que não pode extrair um
    ISO de 360 — e pode.

    Cada item: (tecla, emoji, rótulo, função)
    """
    return [
        ("1", EMO["god"], t("m_god"), acao_god),
        ("2", EMO["analisar"], t("m_info_god"), acao_info_god),
        ("3", EMO["extrair"], t("m_extrair"), acao_extrair),
        ("4", EMO["listar"], t("m_listar"), acao_listar),
        ("5", EMO["criar"], t("m_criar"), acao_criar),
        ("6", EMO["otimizar"], t("m_reescrever"), acao_reescrever),
        ("7", EMO["assistente"], t("m_assistente"), acao_assistente),
        ("8", EMO["lote"], t("m_lote"), acao_lote),
        ("9", EMO["manual"], t("m_manual"), acao_manual),
        ("c", EMO["config"], t("m_config"), acao_config),
        ("l", EMO["log"], t("m_log"), acao_log),
        ("s", EMO["sobre"], t("m_sobre"), acao_sobre),
    ]


def grupos_menu():
    """
    (título, ferramenta, observação, teclas, cor)

    Cada grupo tem sua cor, e os itens herdam a cor do grupo. Assim a cor
    carrega informação — bateu o olho no azul, é Xbox 360 — em vez de ser
    enfeite. Sem isso o menu inteiro sai branco e você lê tudo para achar
    a linha certa.
    """
    # O menu inteiro é UM gradiente contínuo: o primeiro grupo começa no
    # azul e o último termina no verde. Cada grupo recebe a sua faixa da
    # escala, então tudo fica em gradiente sem que os grupos virem a mesma
    # coisa — a posição na tela também vira informação.
    return [
        (t("g_360"), "iso2god", "", ["1", "2"]),
        (t("g_ambos"), "extract-xiso", t("so_le"), ["3", "4"]),
        (t("g_classico"), "extract-xiso", "", ["5", "6"]),
        (t("g_geral"), "", "", ["7", "8", "9"]),
        (t("g_sistema"), "", "", ["c", "l", "s"]),
    ]


def _regua_gradiente_parcial(largura, inicio=0.0, fim=1.0):
    """Trecho de régua pintado com uma faixa do gradiente azul → verde."""
    if largura <= 0:
        return ""
    if not (_COR_TERMINAL and cfg("cor", True)):
        return "\u2500" * largura
    partes = []
    for i in range(largura):
        pos = inicio + (fim - inicio) * (i / max(1, largura - 1))
        partes.append(gradiente(pos) + "\u2500")
    return "".join(partes) + C.RESET


def cabeca_grupo(titulo, ferramenta="", observacao="", largura=LARGURA,
                 faixa=(0.0, 1.0)):
    """
    Divisória do grupo, pintada com uma faixa do gradiente azul → verde:

        ── Xbox 360 ──────────────────────────────  iso2god ──

    A faixa vem da posição do grupo no menu, então a tela inteira lê como
    um único gradiente descendo — e não como cinco listras iguais.
    """
    ini, fim = faixa
    esquerda = "  %s %s " % (cor("\u2500\u2500", gradiente(ini)),
                             texto_gradiente_faixa(titulo, ini, fim))

    if ferramenta:
        obs = cor(" (%s)" % observacao, C.CINZA) if observacao else ""
        direita = " %s%s %s" % (cor(ferramenta, C.CINZA), obs,
                                cor("\u2500\u2500", gradiente(fim)))
    else:
        direita = cor("\u2500\u2500", gradiente(fim))

    meio = largura - _largura_visivel(esquerda) - _largura_visivel(direita)
    return esquerda + _regua_gradiente_parcial(max(0, meio), ini, fim) + direita


def texto_gradiente_faixa(texto, inicio=0.0, fim=1.0):
    """Como texto_gradiente, mas dentro de um trecho da escala."""
    if not (_COR_TERMINAL and cfg("cor", True)):
        return texto
    total = max(1, len(texto) - 1)
    partes = []
    for i, ch in enumerate(texto):
        if ch == " ":
            partes.append(ch)
            continue
        pos = inicio + (fim - inicio) * (i / total)
        partes.append(gradiente(pos) + ch)
    partes.append(C.RESET)
    return "".join(partes)


def mostrar_menu():
    limpar_tela()
    print(titulo_caixa("%s  \u00B7  v%s" % (APP_NOME, APP_VERSAO), EMO["app"],
                       cor_borda=C.AZUL, gradiente_titulo=True))

    # Bolinha em vez de ✓: verde para pronto, vermelho para ausente.
    # A cor faz o estado saltar aos olhos sem precisar ler a palavra.
    for caminho, nome, cor_dono in ((bin_god(), "iso2god", C.AZUL),
                                    (bin_extract(), "extract-xiso", C.VERDE)):
        pronto = _executavel(caminho)
        print("  %s %s%s" % (
            cor(SIM_PONTO, C.VERDE if pronto else C.VERM),
            cor(pad(nome, 16), cor_dono),
            cor(t("pronto") if pronto else t("indisponivel"),
                C.VERDE if pronto else C.VERM)))

    print()
    print(regua_gradiente())
    print()

    itens = {chave: item for item in itens_menu() for chave in [item[0]]}
    grupos = grupos_menu()
    total = max(1, len(grupos))

    for indice, (titulo, ferramenta, observacao, teclas) in enumerate(grupos):
        ini = indice / total
        fim = (indice + 1) / total
        print(cabeca_grupo(titulo, ferramenta, observacao, faixa=(ini, fim)))
        # a chave de cada item fica no meio da faixa do seu grupo
        cor_chave = gradiente((ini + fim) / 2)
        for tecla in teclas:
            item = itens.get(tecla)
            if item:
                print(opcao(item[0], item[2], item[1],
                            cor_chave=cor_chave, cor_texto=C.BRANC))
        print()

    print(opcao("0", t("sair"), EMO["sair"], C.VERM))
    print()
    print(regua())


def main():
    carregar_config()
    log_evento("info", "=" * 30)
    log_evento("info", f"Sessão iniciada — {APP_NOME} v{APP_VERSAO}")

    resolver_binarios(interativo=True)

    while True:
        mostrar_menu()
        try:
            escolha = input(prompt(t("escolha"))).strip().lower()
        except EOFError:
            print()
            return EXIT_OK
        except KeyboardInterrupt:
            print()
            escolha = "0"

        if escolha in ("0", "q", "sair", "quit"):
            print()
            print(f"  {EMO['sair']}  {texto_gradiente(t('ate_logo'))}")
            print()
            log_evento("info", "Sessão encerrada")
            return EXIT_OK

        item = next((i for i in itens_menu() if i[0] == escolha), None)
        if item is None:
            aviso(t("opcao_invalida"))
            pausar()
            continue

        try:
            item[3]()
        except KeyboardInterrupt:
            print()
            aviso(t("cancelado"))
            pausar()
        except Exception as e:
            erro(f"{t('erro_inesperado')}: {e}")
            log_evento("erro", f"Exceção no menu: {e}")
            pausar()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        print(f"  {EMO['sair']}  {t('ate_logo')}")
        sys.exit(EXIT_INTERROMPIDO)
    except Exception as fatal:
        print()
        print(f"  {EMO['erro']}  {fatal}")
        log_evento("erro", f"Erro fatal: {fatal}")
        sys.exit(EXIT_ERRO)
