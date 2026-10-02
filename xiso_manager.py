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
#   0 sucesso | 1 erro fatal | 130 Ctrl+C

import os
import re
import sys
import json
import codecs
import time
import shutil
import logging
import unicodedata
import subprocess
import collections
import signal
import struct
import threading
from pathlib import Path

WINDOWS = os.name == "nt"


def _preparar_console_windows():
    """No Windows: UTF-8 na saída e sequências ANSI ligadas no console.

    Sem o UTF-8, o primeiro emoji impresso derruba o programa com
    UnicodeEncodeError (o console usa uma página de código antiga). Sem o
    modo de terminal virtual, as cores saem como lixo tipo "←[38;2;..m".
    Devolve True se o console aceitou as cores.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
        handle = k32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        modo = ctypes.c_uint32()
        if not k32.GetConsoleMode(handle, ctypes.byref(modo)):
            return False
        # ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(k32.SetConsoleMode(handle, modo.value | 0x0001 | 0x0004))
    except Exception:
        return False


_ANSI_WINDOWS = _preparar_console_windows() if WINDOWS else False


def _preparar_saida_unix():
    """Nome de arquivo que não é UTF-8 (Latin-1, de uma cópia do Windows)
    chega ao Python com "substitutos", que uma saída estrita não sabe
    escrever: imprimir o nome derrubava a tela inteira. Como no Windows, o
    que não dá para escrever vira "?"; o resto sai igual."""
    for fluxo in (sys.stdout, sys.stderr):
        try:
            if getattr(fluxo, "errors", "") == "strict":
                fluxo.reconfigure(errors="replace")
        except Exception:
            pass


if not WINDOWS:
    _preparar_saida_unix()

EXIT_OK = 0
EXIT_ERRO = 1
EXIT_INTERROMPIDO = 130

APP_NOME = "xiso-manager"
APP_VERSAO = "3.2.6"


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
LIMITE_LOG = 1024 * 1024       # 1 MiB antes de rotacionar

LARGURA = 66            # mesma largura das caixas do baixar-video
MARGEM_ESPACO = 1.15    # 15% de folga na checagem de disco

CONFIG_PADRAO = {
    "idioma": "pt",
    "bin_extract_xiso": "",
    "bin_iso2god": "",
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
        # um caractere nulo é JSON válido, mas nenhum caminho o aceita
        return valor if isinstance(valor, str) and "\x00" not in valor else padrao
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
                # chave que não existe mais (ex.: bin_xgdtool das versões
                # antigas): regrava o arquivo sem ela
                if set(dados) - set(CONFIG_PADRAO):
                    salvar_config()
    except Exception:
        pass
    return _config


def salvar_config():
    # Grava num temporário ao lado e troca de uma vez: regravar no lugar
    # trunca o arquivo antes, e uma queda no meio o deixava pela metade. Se
    # o config.json for um link, o arquivo trocado é o de verdade.
    temporario = None
    try:
        DIR_BASE.mkdir(parents=True, exist_ok=True)
        destino = os.path.realpath(ARQ_CONFIG)
        temporario = destino + ".tmp"
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        try:
            shutil.copymode(destino, temporario)
        except OSError:
            pass
        os.replace(temporario, destino)
        return True
    except Exception as e:
        if temporario:
            try:
                os.remove(temporario)
            except OSError:
                pass
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
_FORMATO_LOG = dict(
    filename=str(ARQ_LOG),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
try:
    # O que não couber na codificação do arquivo (um nome que não é UTF-8)
    # vira \xNN, em vez de o logging despejar um traceback na tela.
    logging.basicConfig(errors="backslashreplace", **_FORMATO_LOG)
except (TypeError, ValueError):     # Python anterior ao 3.9, sem `errors`
    try:
        logging.basicConfig(**_FORMATO_LOG)
    except Exception:
        _log_ok = False
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
    if WINDOWS:
        # o Windows não define TERM; vale o que o console aceitou
        return _ANSI_WINDOWS
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


def dica(texto):
    print(f"{EMO['dica']}  {cor(texto, C.CIANO, C.ITAL)}")


def prompt(texto):
    """Prompt no estilo '  ▸ Digite o número: '."""
    return f"\n  {cor(SIM_SETA, C.CIANO)} {texto}: "


# ═════════════════════════════════════════════════════════════════════════
# ENCERRAMENTO POR SINAL (SIGTERM, terminal fechado)
# ═════════════════════════════════════════════════════════════════════════

class Encerramento(BaseException):
    """O menu recebeu um pedido para sair (SIGTERM, SIGHUP). Não é Exception
    nem KeyboardInterrupt: nada no caminho a engole, e ele chega ao fim do
    programa, que sai pelo mesmo sinal."""

    def __init__(self, sinal):
        super().__init__(sinal)
        self.sinal = sinal


_ferramenta = None      # a ferramenta externa rodando agora (Popen)
_encerrar = None        # o sinal de saída recebido, se algum
_pendentes = []         # reescritas do extract-xiso oficial em andamento
PRAZO_LIMPEZA = 30      # quanto esperar a ferramenta limpar o que criou
PRAZO_JANELA = 4        # Windows: o sistema dá 5 s depois de fechar a janela
# O tratador do console do Windows roda numa thread própria: desfazer uma
# reescrita é feito por uma thread só de cada vez.
_trava_pendentes = threading.RLock()
_tratador_console = None    # o ctypes não guarda a função: ela fica aqui


def _forcar_fim(processo):
    """A ferramenta não saiu no prazo depois do pedido: encerra à força."""
    try:
        if processo.poll() is None:
            processo.terminate()
            try:
                processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                processo.kill()
    except OSError:
        pass


def _ao_receber_sinal(numero, _quadro):
    """SIGTERM ou SIGHUP. Sem ferramenta rodando, sai na hora. Com uma
    rodando, pede a ela que pare (SIGTERM: as ferramentas -pt cancelam e
    apagam o que criaram) e deixa o menu esperá-la terminar, desfazer o que
    for preciso e só então sair. Morrer na hora deixava a ferramenta
    trabalhando escondida, sem ninguém lendo o que ela imprime."""
    global _encerrar
    primeiro = _encerrar is None
    _encerrar = numero
    if numero == getattr(signal, "SIGHUP", None):
        _silenciar_saida()
    filho = _ferramenta
    if filho is not None and filho.poll() is None:
        if primeiro:
            try:
                filho.send_signal(signal.SIGTERM)
            except OSError:
                pass
            vigia = threading.Timer(PRAZO_LIMPEZA, _forcar_fim, (filho,))
            vigia.daemon = True
            vigia.start()
        return
    raise Encerramento(numero)


def _silenciar_saida():
    """O terminal foi fechado: escrever nele dá erro de E/S, e um erro no
    meio do caminho impediria a limpeza. Daqui em diante a saída vai para o
    nada (o log continua)."""
    try:
        nulo = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        return
    sys.stdout = sys.stderr = nulo


def _ao_fechar_console(evento):
    """Windows: a janela foi fechada, a sessão vai sair ou o sistema vai
    desligar. O Windows avisa cada processo do console e encerra o menu
    assim que esta função volta; as ferramentas -pt cancelam e apagam o que
    criaram por conta própria. Aqui o menu espera a ferramenta sair (até
    PRAZO_JANELA, dentro do prazo do Windows) e desfaz a reescrita pendente
    do extract-xiso oficial. Ctrl+C e Ctrl+Break seguem para o Python."""
    global _encerrar
    if evento not in (2, 5, 6):     # CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN
        return False
    try:
        if _encerrar is None:
            _encerrar = signal.SIGTERM
        _silenciar_saida()
        filho = _ferramenta
        if filho is not None:
            try:
                filho.wait(timeout=PRAZO_JANELA)
            except subprocess.TimeoutExpired:
                pass
        _desfazer_pendentes()
        log_evento("info", "Sessão encerrada")
    except Exception:
        pass
    return True


def _instalar_tratador_console():
    global _tratador_console
    try:
        import ctypes
        from ctypes import wintypes
        tipo = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        _tratador_console = tipo(_ao_fechar_console)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_tratador_console, True)
    except Exception:
        pass


def instalar_sinais():
    if WINDOWS:
        _instalar_tratador_console()
        return
    signal.signal(signal.SIGTERM, _ao_receber_sinal)
    # Terminal fechado: o mesmo cuidado do SIGTERM. Se o SIGHUP já chega
    # ignorado (nohup), quem rodou assim quer que o menu continue.
    if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:
        signal.signal(signal.SIGHUP, _ao_receber_sinal)


def _desfazer_pendentes():
    """Desfaz as reescritas do extract-xiso oficial que não terminaram
    (chamado na saída por sinal, que pode pular o caminho normal)."""
    with _trava_pendentes:
        while _pendentes:
            _desfazer_reescrita(_pendentes.pop())


def _ler(texto):
    """input() que não espera resposta depois de um pedido de saída."""
    if _encerrar is not None:
        raise Encerramento(_encerrar)
    return input(texto)


def _sair_pelo_sinal(sinal):
    """Sai como sairia sem o tratador: morto pelo mesmo sinal."""
    filho = _ferramenta
    if filho is not None and filho.poll() is None:
        try:
            filho.send_signal(signal.SIGTERM)
            filho.wait(timeout=PRAZO_LIMPEZA)
        except Exception:
            _forcar_fim(filho)
    _desfazer_pendentes()
    log_evento("info", "Sessão encerrada")
    try:
        sys.stdout.flush()
    except Exception:
        pass
    signal.signal(sinal, signal.SIG_DFL)
    os.kill(os.getpid(), sinal)
    os._exit(128 + sinal)


def pausar():
    try:
        _ler(f"\n  {cor(SIM_SETA, C.CINZA)} {cor(t('enter_continuar'), C.CINZA)}")
    except (EOFError, KeyboardInterrupt):
        print()


# ═════════════════════════════════════════════════════════════════════════
# FORMATAÇÃO
# ═════════════════════════════════════════════════════════════════════════


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
        "r_original_restaurado": "A reescrita falhou; o ISO original voltou ao nome de antes.",
        "r_original_em": "O ISO original ficou em",
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
        "m_verificar": "Verificar integridade do ISO",
        "d_verificar": "Confere a estrutura, lê o ISO inteiro e calcula CRC32/MD5/SHA-1; com um .dat do Redump, diz se é a imagem original.",
        "p_dat": "Instalar um .dat ou .zip do Redump? Caminho do arquivo (ENTER = usar os já instalados)",
        "dat_nao_existe": "Arquivo .dat não encontrado:",
        "p_liberar_midia": "Liberar o default.xbe para rodar de qualquer mídia (HD, DVD gravado)? Muda só a cópia no ISO novo",
        "p_sobrescrever_pasta": "já tem arquivos. Extrair por cima?",
        "p_sobrescrever_iso": "já existe. Substituir?",
        "pulado": "pulado",
        "verificando": "Verificando",
        "instalando_dat": "Instalando .dat",
        "precisa_pt": "Esta função precisa do extract-xiso-pt (configure o caminho em Configurações).",
        "r_substituido": "O original foi trocado pelo ISO reescrito.",

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

        # navegador
        "nav_titulo": "SELECIONAR ARQUIVO",
        "nav_pasta_atual": "Pasta atual",
        "nav_subir": "Subir um nível",
        "nav_digitar": "Digitar um caminho",
        "nav_todos": "Selecionar TODOS os ISOs desta pasta",
        "nav_nenhum_iso": "Nenhum arquivo .iso encontrado nesta pasta.",
        "nav_caminho": "Digite o caminho completo",
        "nav_multi": "Números separados por vírgula (ex: 1,3,5) ou intervalo (1-4)",
        "nav_invalido": "Entrada ignorada:",
        "nav_nada": "Nada foi selecionado.",

        # analise
        "tipo_detectado": "Tipo detectado",
        "iso2god_incompativel": "Este iso2god não é compatível: o xiso-manager precisa do iso2god em Rust (subcomandos converter/info).",
        "iso2god_onde_baixar": "Baixe em https://github.com/lux-insider/xiso-manager/releases e informe o caminho em Configurações.",
        "i_plataforma": "Plataforma",
        "i_titulo": "Título",
        "i_disco": "Disco",
        "i_volume": "Volume",
        "i_nao_detectada": "não detectada",
        "i_sem_executavel": "sem default.xex/default.xbe",
        "t_xbox": "Xbox clássico (XISO)",
        "t_xbox360": "Xbox 360 (XGD)",
        "t_desconhecido": "Não reconhecido como imagem Xbox",
        "sug_xbox": "Este ISO é do Xbox clássico. Use extrair, listar ou reescrever.",
        "sug_360": "Este ISO é do Xbox 360. Use a conversão para GOD.",
        "sug_nada": "Não consegui identificar. Você ainda pode tentar as opções manualmente.",
        "acoes_sugeridas": "Ações disponíveis para este arquivo",

        # disco
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
        "executando": "Executando",
        "extraindo": "Extraindo",
        "listando": "Lendo conteúdo",
        "criando": "Criando imagem",
        "reescrevendo": "Otimizando",
        "convertendo": "Convertendo para GOD",
        "concluido": "concluído em",
        "erro_codigo": "O programa terminou com código de erro",
        "erro_bin_sumiu": "Binário não encontrado na hora de executar.",
        "erro_permissao": "Permissão negada ao executar o binário.",
        "erro_inesperado": "Erro inesperado",
        "interrompido": "Interrompido com Ctrl+C. Pode haver arquivos incompletos no destino.",

        # resumo
        "resumo": "RESUMO",
        "r_sucesso": "Sucesso",
        "r_falhas": "Falhas",
        "r_tempo": "Tempo",
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

        # config
        "cfg_titulo": "CONFIGURAÇÕES",
        "c_idioma": "Idioma / Language",
        "c_extract": "Caminho do extract-xiso",
        "c_iso2god": "Caminho do iso2god",
        "c_pasta": "Pasta padrão",
        "c_threads": "Threads para conversão GOD",
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
        "log_erro_ler": "Não foi possível ler o log:",
        "sobre_1": "Interface unificada para as ferramentas extract-xiso-pt e iso2god.",
        "sobre_2": "extract-xiso-pt lista, extrai, cria, otimiza e verifica ISOs de Xbox e Xbox 360.",
        "sobre_3": "iso2god converte imagens do Xbox 360 para o formato GOD.",
        "sobre_4": "Nenhuma biblioteca externa é necessária.",
        "sobre_config": "Configuração",
        "sobre_log": "Log",

        # binarios
        "bin_faltando_titulo": "FERRAMENTA NÃO ENCONTRADA",
        "bin_procurei": "Procurei no PATH e nas pastas comuns.",
        "bin_informe": "Informe o caminho completo agora (ENTER para pular)",
        "bin_pulado": "Você pode configurar depois no menu Configurações.",
        "bin_necessario": "Esta ação precisa do binário",
        "bin_configure": "Configure o caminho no menu de configurações.",
    },
    "en": {
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
        "r_original_restaurado": "The rewrite failed; the original ISO got its old name back.",
        "r_original_em": "The original ISO was kept at",
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
        "m_verificar": "Verify ISO integrity",
        "d_verificar": "Checks the structure, reads the whole ISO and computes CRC32/MD5/SHA-1; with a Redump .dat, tells whether it is the original image.",
        "p_dat": "Install a Redump .dat or .zip? File path (ENTER = use the installed ones)",
        "dat_nao_existe": ".dat file not found:",
        "p_liberar_midia": "Let default.xbe run from any media (HDD, burned DVD)? Changes only the copy in the new ISO",
        "p_sobrescrever_pasta": "already has files. Extract over it?",
        "p_sobrescrever_iso": "already exists. Replace it?",
        "pulado": "skipped",
        "verificando": "Verifying",
        "instalando_dat": "Installing .dat",
        "precisa_pt": "This needs extract-xiso-pt (set its path in Settings).",
        "r_substituido": "The original was replaced by the rewritten ISO.",

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

        "nav_titulo": "SELECT FILE",
        "nav_pasta_atual": "Current folder",
        "nav_subir": "Go up one level",
        "nav_digitar": "Type a path",
        "nav_todos": "Select ALL ISOs in this folder",
        "nav_nenhum_iso": "No .iso files found in this folder.",
        "nav_caminho": "Type the full path",
        "nav_multi": "Numbers separated by comma (e.g. 1,3,5) or range (1-4)",
        "nav_invalido": "Ignored input:",
        "nav_nada": "Nothing selected.",

        "tipo_detectado": "Detected type",
        "iso2god_incompativel": "This iso2god is not compatible: xiso-manager needs the Rust iso2god (converter/info subcommands).",
        "iso2god_onde_baixar": "Download it from https://github.com/lux-insider/xiso-manager/releases and set its path in Settings.",
        "i_plataforma": "Platform",
        "i_titulo": "Title",
        "i_disco": "Disc",
        "i_volume": "Volume",
        "i_nao_detectada": "not detected",
        "i_sem_executavel": "no default.xex/default.xbe",
        "t_xbox": "Original Xbox (XISO)",
        "t_xbox360": "Xbox 360 (XGD)",
        "t_desconhecido": "Not recognized as an Xbox image",
        "sug_xbox": "This is an original Xbox ISO. Use extract, list or rewrite.",
        "sug_360": "This is an Xbox 360 ISO. Use the GOD conversion.",
        "sug_nada": "Could not identify it. You can still try the options manually.",
        "acoes_sugeridas": "Available actions for this file",

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

        "executando": "Running",
        "extraindo": "Extracting",
        "listando": "Reading contents",
        "criando": "Creating image",
        "reescrevendo": "Optimizing",
        "convertendo": "Converting to GOD",
        "concluido": "done in",
        "erro_codigo": "The program exited with error code",
        "erro_bin_sumiu": "Binary not found at execution time.",
        "erro_permissao": "Permission denied running the binary.",
        "erro_inesperado": "Unexpected error",
        "interrompido": "Interrupted with Ctrl+C. There may be incomplete files at the destination.",

        "resumo": "SUMMARY",
        "r_sucesso": "Succeeded",
        "r_falhas": "Failed",
        "r_tempo": "Time",
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

        "cfg_titulo": "SETTINGS",
        "c_idioma": "Idioma / Language",
        "c_extract": "extract-xiso path",
        "c_iso2god": "iso2god path",
        "c_pasta": "Default folder",
        "c_threads": "Threads for GOD conversion",
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
        "log_erro_ler": "Could not read the log:",
        "sobre_1": "Unified interface for the extract-xiso-pt and iso2god tools.",
        "sobre_2": "extract-xiso-pt lists, extracts, creates, optimizes and verifies Xbox and Xbox 360 ISOs.",
        "sobre_3": "iso2god converts Xbox 360 images into the GOD format.",
        "sobre_4": "No external libraries required.",
        "sobre_config": "Config",
        "sobre_log": "Log",

        "bin_faltando_titulo": "TOOL NOT FOUND",
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
                 alvo_arquivo=False, descontar_inicio=True):
        self.destino = Path(destino) if destino else None
        # Vigiar uma PASTA custa um os.walk a cada tique. Quando o alvo é
        # um arquivo só — caso do -c, que grava um .iso — vigiar a pasta
        # inteira pode significar varrer a home do usuário 4x por segundo.
        self.alvo_arquivo = alvo_arquivo
        # Na reescrita na mesma pasta o arquivo de saída começa com o tamanho
        # do ISO original (o extract-xiso o renomeia para .old e grava o novo
        # do zero): descontar esse tamanho deixaria a barra parada em 0.
        self.descontar_inicio = descontar_inicio
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
        self._base = self._medir() if self.descontar_inicio else 0
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

NOMES_EXTRACT = ["extract-xiso-pt", "extract-xiso", "extract_xiso", "extract-xiso-linux"]
NOMES_ISO2GOD = ["iso2god", "iso2god-linux-x86_64"]
if WINDOWS:
    # dentro das pastas o nome precisa do .exe (o shutil.which já resolve)
    NOMES_EXTRACT = ["extract-xiso-pt.exe", "extract-xiso.exe"] + NOMES_EXTRACT
    NOMES_ISO2GOD = ["iso2god.exe"] + NOMES_ISO2GOD


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
    if mudou:
        salvar_config()

    if not interativo:
        return

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
    """O iso2god configurado é o compatível (em Rust, em português), com os
    subcomandos `converter`/`info` e o protocolo `--progresso-json`? Outros
    programas com o mesmo nome têm outra linha de comando e não servem.
    A resposta é guardada por binário: o `--help` só roda uma vez."""
    binario = binario or bin_god()
    if binario not in _ISO2GOD_PROPRIO:
        try:
            ajuda = subprocess.run([binario, "--help"], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=10).stdout
        except Exception:
            ajuda = ""
        _ISO2GOD_PROPRIO[binario] = "converter" in ajuda and "info" in ajuda
    return _ISO2GOD_PROPRIO[binario]


def exigir_iso2god():
    """O iso2god configurado existe e é o compatível? Senão, explica o porquê."""
    if not exigir_binario(bin_god(), "iso2god"):
        return False
    if iso2god_proprio():
        return True
    erro(t("iso2god_incompativel"))
    dica(t("iso2god_onde_baixar"))
    return False


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


def _executavel_na_raiz(f, base):
    """Procura default.xex / default.xbe na tabela do diretório raiz.

    Devolve "xbox360", "xbox" ou None. É a prova de verdade de qual console
    é o disco; o tamanho do arquivo é só um palpite (um XISO de 360
    reconstruído ou enxugado fica facilmente abaixo de 6 GB).
    """
    try:
        f.seek(base + 32 * 2048 + len(MAGIC_XBOX))
        setor, tamanho = struct.unpack("<II", f.read(8))
        if not 0 < tamanho <= 4 * 1024 * 1024:
            return None
        f.seek(base + setor * 2048)
        tabela = f.read(tamanho)
    except (OSError, struct.error):
        return None

    # Árvore binária de entradas: esquerda/direita são deslocamentos em
    # palavras de 4 bytes dentro da tabela; 0 = sem filho.
    achados, pendentes, vistos = set(), [0], set()
    while pendentes and len(vistos) < 4096:
        pos = pendentes.pop()
        if pos in vistos or pos + 14 > len(tabela):
            continue
        vistos.add(pos)
        esq, dir_, _setor, _tam, _attr, n = struct.unpack_from("<HHIIBB", tabela, pos)
        if esq == 0xFFFF:
            continue
        achados.add(tabela[pos + 14:pos + 14 + n].decode("latin-1").lower())
        pendentes += [x * 4 for x in (esq, dir_) if x]
    if "default.xex" in achados:
        return "xbox360"
    if "default.xbe" in achados:
        return "xbox"
    return None


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
                        if offset == 0x10000:
                            # XISO cru pode ser dos dois consoles: a raiz diz
                            # qual (default.xex ou default.xbe); o tamanho é
                            # só o último recurso, se a raiz não puder ser lida
                            tipo = _executavel_na_raiz(f, 0) or (
                                "xbox360" if tamanho > 6 * 1024 ** 3 else "xbox")
                            rotulo = "XISO"
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
RE_QUEBRA = re.compile(r"[\r\n]")
# Uma linha de saída maior que isto é cortada: a tela mostra 58 colunas e
# os eventos JSON têm poucas centenas de bytes.
LIMITE_LINHA = 256 * 1024

# O extract-xiso anima o próprio progresso com \b, e outras ferramentas
# emitem cor. Cru, isso vaza como lixo na nossa tabela. O título do jogo vem
# de dentro da ISO: as sequências de escape inteiras (cor, limpar a tela,
# trocar o título da janela), os controles C1 (\x9b abre uma sequência em
# alguns terminais) e os que invertem a direção do texto também saem.
RE_CONTROLE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x9b[0-?]*[ -/]*[@-~]"
    r"|[\x1b][\]PX^_][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)"
    r"|[\x9d\x90\x98\x9e\x9f][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)"
    r"|\x1b[ -/]*[0-~]"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _sem_controle(texto):
    """Texto de outro programa sem nada que o terminal interprete; ao
    contrário de `_limpar_linha_externa`, mantém os espaços como estão."""
    return RE_CONTROLE.sub("", re.sub(r"[\t\r\n]", " ", str(texto)))


def _evento_json(linha):
    """Um evento do `--progresso-json` do iso2god próprio, ou None."""
    if not (linha.startswith("{") and linha.endswith("}")):
        return None
    try:
        evento = json.loads(linha)
    except ValueError:
        return None
    return evento if isinstance(evento, dict) and "evento" in evento else None


def _numero(valor):
    """O valor, se for um número de verdade; senão None. Os eventos vêm de
    outro programa: um campo de tipo errado não pode derrubar a leitura."""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return valor
    return None


def _texto(valor):
    return valor if isinstance(valor, str) else ""


def _linhas_verificado(evento):
    """Relatório do `verificar --progresso-json` do extract-xiso-pt."""
    h = evento.get("hashes")
    h = h if isinstance(h, dict) else {}
    completo = evento.get("disco_completo")
    linhas = ["%s · %s" % (evento.get("layout", "?"),
                           "disco completo" if completo else
                           "enxuta" if completo is None else "tamanho fora do padrão"),
              "estrutura íntegra · %s arquivos em %s pastas"
              % (evento.get("arquivos", "?"), evento.get("diretorios", "?"))]
    avisos = evento.get("avisos")
    linhas += ["aviso: " + a for a in (avisos if isinstance(avisos, list) else [])
               if isinstance(a, str)]
    for nome in ("crc32", "md5", "sha1"):
        if h.get(nome):
            linhas.append("%-6s %s" % (nome.upper(), h[nome]))
    dat = evento.get("dat")
    if isinstance(dat, dict) and dat:
        situacao = dat.get("situacao")
        if situacao == "confere":
            linhas.append("ORIGINAL: idêntica ao Redump — %s" % dat.get("jogo", "?"))
        elif situacao == "enxuta":
            linhas.append("cópia enxuta de %s: íntegra, mas o Redump guarda o hash do "
                          "disco completo, então não tem como conferir" % dat.get("jogo", "?"))
        elif situacao == "nao_confere":
            linhas.append("NÃO CONFERE: o Redump tem %s com outro SHA-1 "
                          "(modificada, corrompida ou outra versão)" % dat.get("rom", "?"))
        else:
            linhas.append("não está no Redump"
                          + (" (ISO enxuta nunca confere)" if completo is None else ""))
    elif not evento.get("dats_usados"):
        linhas.append("sem .dat do Redump instalado: só a integridade foi conferida")
    return linhas


def _texto_evento(evento):
    """Texto legível de um evento `fase`/`concluido`/`erro` do iso2god."""
    tipo = evento.get("evento")
    mensagem = _texto(evento.get("mensagem", ""))
    pasta = _texto(evento.get("pasta"))
    if tipo == "concluido" and pasta and pasta not in mensagem:
        return "%s %s" % (mensagem, pasta) if mensagem else pasta
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


def _escoar(processo):
    """Lê e descarta o resto da saída da ferramenta."""
    try:
        while os.read(processo.stdout.fileno(), 65536):
            pass
    except (OSError, ValueError):
        pass


def _esperar_ferramenta(processo, prazo=None):
    """Espera a ferramenta sair, esvaziando o pipe enquanto isso: sem
    ninguém lendo, uma ferramenta que estava imprimindo fica presa na
    escrita e nunca chega a cancelar e limpar. Passado o prazo, força."""
    leitor = threading.Thread(target=_escoar, args=(processo,), daemon=True)
    leitor.start()
    try:
        processo.wait(timeout=PRAZO_LIMPEZA if prazo is None else prazo)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        _forcar_fim(processo)       # passou do prazo, ou segundo Ctrl+C
    leitor.join(timeout=1.0)


def executar(cmd, label, emoji="", destino=None, total=0, mostrar_saida=True,
             max_linhas=10, alvo_arquivo=False, descontar_inicio=True):
    """Roda a ferramenta externa com a barra de progresso por cima."""
    resultado = Resultado()

    # O comando completo vai só para o log. Truncado na tela ele não
    # informa nada e ainda polui a área de trabalho do usuário.
    log_evento("info", "cmd: " + " ".join(cmd))

    fiscal = Fiscal(label, destino, total, emoji=emoji,
                    alvo_arquivo=alvo_arquivo, descontar_inicio=descontar_inicio)
    fiscal.iniciar()

    processo = None
    inicio = time.time()

    global _ferramenta
    try:
        # A entrada é o teclado do menu: a ferramenta não a disputa com ele.
        processo = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, bufsize=0)
        _ferramenta = processo
        # O verificar --progresso-json do extract-xiso-pt manda o erro duas
        # vezes: como evento JSON e como texto ("❌ <mensagem>", ou "X" no
        # console antigo do Windows). Fica só a primeira que chegar.
        erros_evento, erros_texto = set(), set()

        def tratar(linha):
            # iso2god próprio com --progresso-json: uma linha JSON por
            # evento. O progresso vira a barra; o resto, texto legível.
            evento = _evento_json(linha)
            if evento is not None and evento.get("evento") == "erro":
                mensagem = _limpar_linha_externa(_texto(evento.get("mensagem")))
                if mensagem in erros_texto:
                    return
                erros_evento.add(mensagem)
            elif evento is None:
                marca, _, mensagem = linha.partition(" ")
                if marca in ("\u274C", "X") and mensagem:
                    if mensagem in erros_evento:
                        return
                    erros_texto.add(mensagem)
            if evento is not None:
                if evento.get("evento") == "verificado":
                    for l in _linhas_verificado(evento):
                        resultado.saida.append(_sem_controle(l))
                        resultado.total_linhas += 1
                    return
                if evento.get("evento") == "progresso":
                    total_bytes = _numero(evento.get("total_bytes")) or 0
                    feito = _numero(evento.get("bytes", 0)) or 0
                    if total_bytes > 0:
                        fiscal.definir_percentual(
                            max(0.0, min(1.0, feito / total_bytes)))
                    return
                linha = _sem_controle(_texto_evento(evento))
                if not linha:
                    return
            # ferramentas que usam \r repetem a mesma linha várias vezes
            if resultado.saida and resultado.saida[-1] == linha:
                return
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

        # A linha em montagem fica em pedaços, e a quebra é procurada só no
        # pedaço novo. Antes, cada pedaço de 4 KiB era somado a um texto que
        # crescia sem limite e varrido desde o começo: uma linha de 32 MiB
        # sem quebra levava 8 minutos e 400 MiB de memória.
        pendente, guardado = [], 0
        # Um caractere UTF-8 pode vir partido entre duas leituras: o
        # decodificador incremental guarda o começo dele para a próxima.
        decodificar = codecs.getincrementaldecoder("utf-8")("replace").decode
        while True:
            try:
                pedaco = os.read(processo.stdout.fileno(), 4096)
            except (OSError, ValueError):
                break
            if not pedaco:
                break
            texto = decodificar(pedaco)
            inicio = 0
            for marca in RE_QUEBRA.finditer(texto):
                if guardado < LIMITE_LINHA:
                    pendente.append(texto[inicio:marca.start()][:LIMITE_LINHA - guardado])
                linha = _limpar_linha_externa("".join(pendente))
                pendente, guardado = [], 0
                inicio = marca.end()
                if linha:
                    tratar(linha)
            if guardado < LIMITE_LINHA:
                trecho = texto[inicio:][:LIMITE_LINHA - guardado]
                pendente.append(trecho)
                guardado += len(trecho)

        resto = _limpar_linha_externa("".join(pendente) + decodificar(b"", True))
        if resto:
            resultado.saida.append(resto)
            resultado.total_linhas += 1

        processo.wait()
        resultado.codigo = processo.returncode
        resultado.ok = (processo.returncode == 0)
        if _encerrar is not None:
            # parou porque o menu recebeu SIGTERM/SIGHUP: é um cancelamento
            resultado.ok = False
            resultado.cancelado = True
            resultado.erro = t("interrompido")

    except FileNotFoundError:
        resultado.erro = t("erro_bin_sumiu")
    except PermissionError:
        resultado.erro = t("erro_permissao")
    except KeyboardInterrupt:
        resultado.cancelado = True
        resultado.erro = t("interrompido")
        if processo:
            # O Ctrl+C já chegou ao filho também (mesmo grupo de processos /
            # mesmo console). O iso2god, ao recebê-lo, para e APAGA a saída
            # incompleta: terminar o processo agora interromperia justamente
            # essa limpeza e deixaria um GOD pela metade no destino. Então
            # primeiro esperamos; só se ele não sair é que forçamos. A espera
            # esvazia o pipe: sem isso, uma ferramenta que estava imprimindo
            # ficava presa na escrita, nunca cancelava e morria com SIGKILL.
            _esperar_ferramenta(processo)
    except Exception as e:
        resultado.erro = f"{t('erro_inesperado')}: {e}"
        log_evento("erro", str(e))
        if processo is not None and processo.poll() is None:
            # Sem a leitura, a ferramenta ficaria presa no pipe, escondida:
            # pede para ela parar (as -pt cancelam e limpam) e espera.
            try:
                processo.send_signal(signal.SIGTERM)
            except OSError:
                pass
            _esperar_ferramenta(processo)
    finally:
        _ferramenta = None
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
            resp = _ler(prompt(f"{cor(texto, C.BRANC)}{sufixo}")).strip()
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


def perguntar_sim_nao(texto, padrao_sim=False, no_ctrl_c=None):
    """Pergunta de sim/não. `no_ctrl_c` é a resposta quando o usuário aperta
    Ctrl+C (por padrão, a mesma do ENTER). Nas confirmações vale "não":
    quem aperta Ctrl+C quer desistir, e o padrão "sim" começava a operação."""
    sufixo = t("sim_nao_s") if padrao_sim else t("sim_nao_n")
    try:
        resp = _ler(prompt(f"{cor(texto, C.BRANC)} {cor('[' + sufixo + ']', C.CINZA)}")).strip().lower()
    except EOFError:
        print()
        return padrao_sim
    except KeyboardInterrupt:
        print()
        return padrao_sim if no_ctrl_c is None else no_ctrl_c
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
    try:
        nomes = os.listdir(pasta)
    except OSError:
        return []
    return sorted(os.path.join(pasta, n) for n in nomes
                  if n.lower().endswith((".iso", ".xiso"))
                  and os.path.isfile(os.path.join(pasta, n)))


def _inteiro(texto):
    """O número digitado, ou None. Só algarismos decimais: "²".isdigit() é
    verdadeiro, mas o int o recusa, como recusa um número com milhares de
    algarismos."""
    texto = texto.strip()
    if not texto.isdecimal():
        return None
    try:
        return int(texto)
    except ValueError:
        return None


def _expandir_selecao(texto, total):
    """'1,3,5-7' -> [0,2,4,5,6]"""
    indices, invalidos = [], []
    for parte in str(texto).split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte and not parte.startswith("-"):
            a_txt, _, b_txt = parte.partition("-")
            a, b = _inteiro(a_txt), _inteiro(b_txt)
            if a is not None and b is not None:
                if a > b:
                    a, b = b, a
                # Só a parte que existe é percorrida: "1-999999999" não pode
                # virar um bilhão de itens. Acima do total, até 1000 números
                # saem um a um, como sempre; mais que isso, num item só.
                if a == 0:
                    invalidos.append("0")
                indices.extend(range(max(a, 1) - 1, min(b, total)))
                acima = max(a, total + 1)
                if b - acima >= 1000:
                    invalidos.append("%d-%d" % (acima, b))
                else:
                    invalidos.extend(str(n) for n in range(acima, b + 1))
                continue
        n = _inteiro(parte)
        if n is not None and 1 <= n <= total:
            indices.append(n - 1)
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

def eh_pt():
    """O extrator configurado é o extract-xiso-pt (senão, o oficial)."""
    return "extract-xiso-pt" in os.path.basename(bin_extract() or "").lower()


def opcoes_comuns():
    if eh_pt():
        return []  # o extract-xiso-pt fala por eventos JSON, não tem modo silencioso
    return ["-q"] if perguntar_sim_nao(t("p_silencioso"), padrao_sim=False) else []


def _tem_xbe(pasta):
    try:
        return any(n.lower() == "default.xbe" and os.path.isfile(os.path.join(pasta, n))
                   for n in os.listdir(pasta))
    except OSError:
        return False


def _pode_sobrescrever(caminho, eh_pasta):
    """Destino já ocupado: pergunta antes (o extract-xiso-pt nunca grava por cima sozinho)."""
    if eh_pasta:
        ocupado = os.path.isdir(caminho) and bool(os.listdir(caminho))
        chave = "p_sobrescrever_pasta"
    else:
        ocupado = os.path.exists(caminho)
        chave = "p_sobrescrever_iso"
    if not ocupado:
        return False, True
    sim = perguntar_sim_nao("%s %s" % (encurtar_home(caminho), t(chave)), padrao_sim=False)
    return sim, sim


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

    # Pasta de cada ISO, sempre explícita (-d): sem ela o extract-xiso
    # extrai na pasta em que o programa foi ABERTO, não na do ISO — e a
    # barra, a checagem de espaço e o resumo olhariam para o lugar errado.
    # Com -d ele extrai direto dentro da pasta, então vários ISOs para o
    # mesmo destino ganham uma subpasta cada (senão os jogos se misturam).
    def pasta_de(arquivo):
        base = Path(arquivo).stem
        if not destino:
            return os.path.join(os.path.dirname(os.path.abspath(arquivo)), base)
        return destino if len(arquivos) == 1 else os.path.join(destino, base)

    # ── 4. Verificação ───────────────────────────────────────────────────
    print(etapa(4, t("e_verificacao"), total=4))
    if not verificar_espaco(destino or pasta_de(arquivos[0]), total):
        pausar()
        return
    if destino and not preparar_destino(destino):
        pausar()
        return
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True, no_ctrl_c=False):
        aviso(t("cancelado"))
        pausar()
        return

    # ── Execução ─────────────────────────────────────────────────────────
    print(etapa("", t("e_execucao")))
    ok = falhas = 0
    gerados = []
    inicio = time.time()

    for i, arquivo in enumerate(arquivos, 1):
        alvo = pasta_de(arquivo)
        if len(arquivos) > 1:
            print(campo("%d/%d" % (i, len(arquivos)),
                        cortar(os.path.basename(arquivo), 40),
                        EMO["arquivo"], largura_rotulo=8))

        if eh_pt():
            sobrescrever, seguir = _pode_sobrescrever(alvo, eh_pasta=True)
            if not seguir:
                print(resposta(t("pulado"), C.CINZA))
                continue
            args = [bin_extract(), "extrair", arquivo, "-d", alvo, "--progresso-json"]
            if pular:
                args.append("-s")
            if sobrescrever:
                args.append("--sobrescrever")
        else:
            args = [bin_extract(), "-x", "-d", alvo]
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
        cmd = [bin_extract(), "listar", arquivo] if eh_pt() else [bin_extract(), "-l", arquivo]
        r = executar(cmd, t("listando"),
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
    sem_patch = liberar = False
    if eh_pt():
        # só jogos de Xbox têm default.xbe; e só mexe se o usuário pedir
        if any(_tem_xbe(tr["origem"]) for tr in trabalhos):
            liberar = perguntar_sim_nao(t("p_liberar_midia"), padrao_sim=False)
    else:
        sem_patch = perguntar_sim_nao(t("p_sem_patch"), padrao_sim=False)
    flags = opcoes_comuns()

    # ── 4. Verificação ───────────────────────────────────────────────────
    print(etapa(4, t("e_verificacao"), total=4))
    total = sum(tr["tamanho"] for tr in trabalhos)
    destino_check = os.path.dirname(trabalhos[0]["alvo"]) or "."
    if not verificar_espaco(destino_check, total):
        pausar()
        return
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True, no_ctrl_c=False):
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

        if eh_pt():
            sobrescrever, seguir = _pode_sobrescrever(tr["alvo"], eh_pasta=False)
            if not seguir:
                print(resposta(t("pulado"), C.CINZA))
                continue
            args = [bin_extract(), "criar", tr["origem"], "-s", tr["alvo"], "--progresso-json"]
            if liberar and _tem_xbe(tr["origem"]):
                args.append("--liberar-midia")
            if sobrescrever:
                args.append("--sobrescrever")
        else:
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


def _marcar_original(origem, antigo, saida):
    """Antes da reescrita com o extract-xiso oficial: o que é preciso para
    reconhecer o original depois, se ele ficar como "<nome>.old".

    O oficial renomeia o original para .old antes de começar e não trata
    sinal nenhum: um Ctrl+C, um SIGTERM ou o terminal fechado o matam no
    meio, deixando o ISO novo pela metade com o nome do original. Então não
    basta olhar se o nome do original está livre: o .old é o original se
    não existia antes e tem o tamanho e a data dele (o rename preserva os
    dois)."""
    try:
        st = os.stat(origem)
    except OSError:
        return None
    return {"origem": origem, "antigo": antigo, "saida": saida,
            "assinatura": (st.st_size, st.st_mtime_ns),
            "havia_antigo": os.path.lexists(antigo),
            "havia_saida": os.path.lexists(saida)}


def _desfazer_reescrita(marca):
    """Depois de uma reescrita que não terminou bem: devolve o nome ao
    original. Devolve (erro, desfeito): `desfeito` é True quando o original
    voltou ao nome; `erro` é a falha do rename quando ele não pôde voltar."""
    if not marca or marca["havia_antigo"]:
        return None, False
    origem, antigo, saida = marca["origem"], marca["antigo"], marca["saida"]
    try:
        st = os.stat(antigo)
    except OSError:
        return None, False          # o original nem chegou a ser renomeado
    if (st.st_size, st.st_mtime_ns) != marca["assinatura"]:
        return None, False
    # A sobra pela metade em outra pasta, criada por esta reescrita, sai.
    # Na mesma pasta ela tem o nome do original e é coberta pelo rename.
    if not marca["havia_saida"] and not _mesma_pasta(os.path.dirname(saida),
                                                      os.path.dirname(origem)):
        try:
            os.remove(saida)
        except OSError:
            pass
    try:
        os.replace(antigo, origem)
    except OSError as e:
        return e, False
    return None, True


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
        if not perguntar_sim_nao(t("confirmar"), padrao_sim=False, no_ctrl_c=False):
            apagar = False
            print(resposta(t("cancelado")))
    sem_patch = liberar = False
    if eh_pt():
        if any(detectar_tipo_iso(a)[0] == "xbox" for a in arquivos):
            liberar = perguntar_sim_nao(t("p_liberar_midia"), padrao_sim=False)
    else:
        sem_patch = perguntar_sim_nao(t("p_sem_patch"), padrao_sim=False)
    flags = opcoes_comuns()

    print(etapa(4, t("e_verificacao"), total=4))
    # O original já está no disco; o que precisa estar livre é o espaço do
    # ISO novo, que nunca é maior que o original — apagando ou não no fim
    # (o -D só apaga depois que o novo foi gravado).
    if not verificar_espaco(destino or os.path.dirname(os.path.abspath(arquivos[0])), total):
        pausar()
        return
    if destino and not preparar_destino(destino):
        pausar()
        return
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True, no_ctrl_c=False):
        aviso(t("cancelado"))
        pausar()
        return

    print(etapa("", t("e_execucao")))
    if eh_pt():
        _reescrever_pt(arquivos, destino, apagar, liberar)
        return
    ok = falhas = 0
    inertes = 0
    gerados = []
    inicio = time.time()

    for i, arquivo in enumerate(arquivos, 1):
        if len(arquivos) > 1:
            print(campo("%d/%d" % (i, len(arquivos)),
                        cortar(os.path.basename(arquivo), 40),
                        EMO["arquivo"], largura_rotulo=8))

        # Saída sempre com -d: sem ele o ISO novo iria para a pasta em que o
        # programa foi aberto. O extract-xiso renomeia o original para
        # "<nome>.old" NA PASTA DO ORIGINAL, qualquer que seja o -d, e grava
        # o novo com o nome de sempre — então o resultado se reconhece pelo
        # NOME, não por um arquivo novo aparecendo na pasta.
        origem = os.path.abspath(arquivo)
        pasta_saida = os.path.abspath(destino) if destino else os.path.dirname(origem)
        saida = os.path.join(pasta_saida, os.path.basename(origem))
        antigo = origem + ".old"
        try:
            antes = os.stat(saida).st_mtime_ns
        except OSError:
            antes = None
        marca = _marcar_original(origem, antigo, saida)
        if marca:
            _pendentes.append(marca)

        args = [bin_extract(), "-r", "-d", pasta_saida]
        if apagar:
            args.append("-D")
        if sem_patch:
            args.append("-m")
        args += flags + [origem]

        try:
            esperado = os.path.getsize(origem)
        except OSError:
            esperado = 0

        r = executar(args, t("reescrevendo"), EMO["otimizar"],
                     destino=saida, total=esperado,
                     alvo_arquivo=True, descontar_inicio=False)

        try:
            depois = os.stat(saida).st_mtime_ns
        except OSError:
            depois = None
        gravou = depois is not None and depois != antes

        # Falha ou interrupção no meio da reescrita: o original ficou como
        # "<nome>.old", e no lugar dele pode haver o ISO novo pela metade.
        # Desfaz o nome (em qualquer pasta de destino).
        falha, desfeito = None, False
        with _trava_pendentes:
            if marca in _pendentes:
                _pendentes.remove(marca)
            if not r.ok:
                falha, desfeito = _desfazer_reescrita(marca)
        if desfeito:
            aviso(t("r_original_restaurado"))
        elif falha is not None:
            erro("%s %s (%s)" % (t("r_original_em"), antigo, falha))

        # O extract-xiso pula ISOs que já estão otimizados e sai com
        # código 0, sem gravar nada.
        if r.ok and not gravou:
            ja_otimizado = any("already optimized" in l.lower()
                               or "skipping" in l.lower() for l in r.saida)
            print(resposta(t("nada_a_fazer") if ja_otimizado else t("sem_saida"),
                           C.CINZA))
            inertes += 1
        elif r.ok:
            gerados.append(saida)
            if os.path.exists(antigo) and not apagar:
                print(resposta("%s %s" % (t("r_original_em"), encurtar_home(antigo)), C.CINZA))

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


def _mesma_pasta(a, b):
    """As duas pastas são a mesma, por qualquer caminho?

    Comparar o texto não basta: um link simbólico, uma pasta montada em dois
    lugares, um atalho de rede ou um nome curto 8.3 do Windows dão textos
    diferentes para a mesma pasta. Tomar a pasta do original por "outra"
    fazia a reescrita gravar por cima dele e o menu apagar o resultado.
    """
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _pode_apagar_original(origem, saida):
    """O ISO novo existe e não é o próprio original (por outro caminho)?"""
    try:
        return os.path.isfile(saida) and not os.path.samefile(saida, origem)
    except (OSError, ValueError):
        return False


def _reescrever_pt(arquivos, destino, apagar, liberar):
    """Reescrita com o extract-xiso-pt.

    Ele nunca mexe no original durante a gravação: o ISO novo é gravado à
    parte, relido e só então ganha o nome final. Sem destino e sem apagar,
    o novo fica ao lado como "<nome>.xiso.iso"; apagando na mesma pasta, o
    --substituir troca o original só no fim; com destino, o novo vai para
    lá com o mesmo nome e o original só é apagado depois do sucesso.
    """
    ok = falhas = 0
    gerados = []
    inicio = time.time()

    for i, arquivo in enumerate(arquivos, 1):
        if len(arquivos) > 1:
            print(campo("%d/%d" % (i, len(arquivos)),
                        cortar(os.path.basename(arquivo), 40),
                        EMO["arquivo"], largura_rotulo=8))
        origem = os.path.abspath(arquivo)
        pasta_saida = os.path.abspath(destino) if destino else os.path.dirname(origem)
        saida = os.path.join(pasta_saida, os.path.basename(origem))
        mesma_pasta = _mesma_pasta(pasta_saida, os.path.dirname(origem))

        args = [bin_extract(), "reescrever", origem, "--progresso-json"]
        if mesma_pasta and apagar:
            args.append("--substituir")
            saida = origem
        else:
            if mesma_pasta:
                saida = os.path.join(pasta_saida, Path(origem).stem + ".xiso.iso")
            sobrescrever, seguir = _pode_sobrescrever(saida, eh_pasta=False)
            if not seguir:
                print(resposta(t("pulado"), C.CINZA))
                continue
            args += ["-s", saida]
            if sobrescrever:
                args.append("--sobrescrever")
        if liberar and detectar_tipo_iso(origem)[0] == "xbox":
            args.append("--liberar-midia")

        try:
            esperado = os.path.getsize(origem)
        except OSError:
            esperado = 0
        r = executar(args, t("reescrevendo"), EMO["otimizar"],
                     destino=saida, total=esperado,
                     alvo_arquivo=True, descontar_inicio=False)
        if r.ok:
            gerados.append(saida)
            if saida == origem:
                print(resposta(t("r_substituido"), C.CINZA))
            elif apagar and _pode_apagar_original(origem, saida):
                try:
                    os.remove(origem)
                except OSError as e:
                    erro("%s (%s)" % (origem, e))
        if r.cancelado:
            falhas += 1
            break
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1

    extras = [(t("p_apagar_antigo"), "sim" if apagar else "não", EMO["lixo"])]
    mostrar_resumo(t("m_reescrever"), ok, falhas, time.time() - inicio,
                   extras=extras, produzidos=gerados)
    pausar()


def acao_verificar(arquivos=None):
    tela(t("m_verificar"), EMO["analisar"], t("d_verificar"), C.CIANO)
    if not exigir_binario(bin_extract(), "extract-xiso-pt"):
        pausar()
        return
    if not eh_pt():
        aviso(t("precisa_pt"))
        pausar()
        return

    if arquivos is None:
        arquivos = navegador()
    if not arquivos:
        return

    print(etapa(1, t("e_selecao"), total=3))
    resumo_selecao(arquivos)

    print(etapa(2, t("e_opcoes"), total=3))
    # Os .dat ficam instalados no extract-xiso-pt; aqui só se instala um
    # novo, se o usuário quiser. O verificar usa todos os instalados.
    dat = perguntar(t("p_dat"), obrigatorio=False)
    dat = os.path.expanduser(dat.strip().strip('"').strip("'")) if dat else ""
    if dat:
        if not os.path.isfile(dat):
            erro("%s %s" % (t("dat_nao_existe"), dat))
            pausar()
            return
        r = executar([bin_extract(), "dats", "instalar", dat], t("instalando_dat"),
                     EMO["analisar"], max_linhas=4)
        if not r.ok:
            pausar()
            return

    print(etapa(3, t("e_resultado"), total=3))
    ok = falhas = 0
    inicio = time.time()
    for i, arquivo in enumerate(arquivos, 1):
        if len(arquivos) > 1:
            print(campo("%d/%d" % (i, len(arquivos)),
                        cortar(os.path.basename(arquivo), 40),
                        EMO["arquivo"], largura_rotulo=8))
        args = [bin_extract(), "verificar", arquivo, "--progresso-json"]
        try:
            esperado = os.path.getsize(arquivo)
        except OSError:
            esperado = 0
        # código 2: o Redump tem este jogo com outro SHA-1 (conta como falha)
        r = executar(args, t("verificando"), EMO["analisar"],
                     total=esperado, max_linhas=16)
        if r.cancelado:
            falhas += 1
            break
        ok += 1 if r.ok else 0
        falhas += 0 if r.ok else 1

    mostrar_resumo(t("m_verificar"), ok, falhas, time.time() - inicio)
    pausar()


# ═════════════════════════════════════════════════════════════════════════
# AÇÕES — ISO2GOD
# ═════════════════════════════════════════════════════════════════════════

def acao_god(arquivos=None):
    tela(t("m_god"), EMO["god"], t("d_god"), C.AZUL)
    if not exigir_iso2god():
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
    if not perguntar_sim_nao(t("confirmar"), padrao_sim=True, no_ctrl_c=False):
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

        # "cortar o espaço não usado do fim" é o padding "parcial"
        args = [bin_god(), "converter", "--progresso-json",
                "-j", str(threads),
                "--padding", "parcial" if cortar_fim else "nenhuma"]
        if titulo_jogo:
            args += ["--titulo", titulo_jogo]
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


def mostrar_info_iso2god(arquivo):
    """Análise pelo iso2god próprio: `info --json`, mostrado com os mesmos
    campos alinhados do resto da tela (em vez das caixas do iso2god, que
    ficam tortas dentro do quadro de saída)."""
    cmd = [bin_god(), "info", "--json", arquivo]
    log_evento("info", "cmd: " + " ".join(cmd))
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=300)
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
        info = None
    if not isinstance(info, dict):
        # o motivo já saiu acima, pelo stderr do iso2god
        if not r.stderr.strip():
            erro(t("erro_inesperado"))
        return False

    def valor(chave):
        v = info.get(chave)
        return _sem_controle(v) if v not in (None, "") else None

    # Os campos vêm de outro programa: um tipo errado vale como ausente, em
    # vez de derrubar a análise das outras ISOs da seleção.
    plataforma = {"xbox360": "Xbox 360", "xbox": "Xbox"}.get(
        _texto(info.get("plataforma_detectada")), None)
    if plataforma:
        print(campo(t("i_plataforma"), plataforma, EMO["jogo"], largura_rotulo=16))
    else:
        marca_nd(t("i_plataforma"), "%s (%s)" % (t("i_nao_detectada"), t("i_sem_executavel")))
    for rotulo, chave in (("Title ID", "title_id"), ("Media ID", "media_id"),
                          (t("i_titulo"), "titulo")):
        if valor(chave):
            print(campo(rotulo, valor(chave), EMO["info"], largura_rotulo=16))
    if info.get("disco"):
        print(campo(t("i_disco"), "%s/%s" % (valor("disco"), valor("total_discos") or "?"),
                    EMO["disco"], largura_rotulo=16))
    if _numero(info.get("tamanho_volume")):
        print(campo(t("i_volume"), "%s  %s" % (fmt_bytes(info["tamanho_volume"]),
                                              cor("(" + (valor("tipo_disco") or "") + ")", C.CINZA)),
                    EMO["disco"], largura_rotulo=16))
    print()
    return r.returncode == 0


def acao_info_god(arquivos=None):
    tela(t("m_info_god"), EMO["analisar"], t("d_info_god"), C.CIANO)
    if not exigir_iso2god():
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
        deu_certo = mostrar_info_iso2god(arquivo)
        ok += 1 if deu_certo else 0
        falhas += 0 if deu_certo else 1

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
        if eh_pt():
            escolhas += [
                (EMO["extrair"], t("m_extrair"), t("d_extrair"), lambda: acao_extrair(arquivos)),
                (EMO["otimizar"], t("m_reescrever"), t("d_reescrever"), lambda: acao_reescrever(arquivos)),
            ]
    if eh_pt() and tipo in ("xbox", "xbox360"):
        escolhas.append((EMO["analisar"], t("m_verificar"), t("d_verificar"),
                         lambda: acao_verificar(arquivos)))
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
    numero = _inteiro(escolha)
    if numero is not None and 1 <= numero <= len(escolhas):
        escolhas[numero - 1][3]()
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
    numero = _inteiro(escolha)
    if numero is not None and 1 <= numero <= len(escolhas):
        escolhas[numero - 1][2]()


def acao_manual():
    tela(t("m_manual"), EMO["manual"], t("d_manual"), C.AMARE)

    print(opcao("1", "extract-xiso-pt" if eh_pt() else "extract-xiso", EMO["extrair"]))
    print(opcao("2", "iso2god", EMO["god"]))
    print(opcao("0", t("voltar"), EMO["sair"], C.VERM))

    qual = perguntar(t("escolha"), obrigatorio=False).strip()
    if qual == "1":
        if eh_pt():
            binario, nome = bin_extract(), "extract-xiso-pt"
            exemplo = "extrair jogo.iso -d ~/Extraidos"
        else:
            binario, nome = bin_extract(), "extract-xiso"
            exemplo = "-x -s -d ~/Extraidos jogo.iso"
    elif qual == "2":
        binario, nome = bin_god(), "iso2god"
        exemplo = "converter -j 4 --padding parcial jogo.iso ~/GOD"
    else:
        return

    ok_bin = exigir_iso2god() if nome == "iso2god" else exigir_binario(binario, nome)
    if not ok_bin:
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
        ("v", EMO["analisar"], t("m_verificar"), acao_verificar),
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
    if eh_pt():
        # o extract-xiso-pt também cria e reescreve ISOs de 360
        return [
            (t("g_360"), "iso2god", "", ["1", "2"]),
            (t("g_ambos"), "extract-xiso-pt", "", ["3", "4", "5", "6", "v"]),
            (t("g_geral"), "", "", ["7", "8", "9"]),
            (t("g_sistema"), "", "", ["c", "l", "s"]),
        ]
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
                                    (bin_extract(), "extract-xiso-pt" if eh_pt() else "extract-xiso", C.VERDE)):
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
    instalar_sinais()
    carregar_config()
    log_evento("info", "=" * 30)
    log_evento("info", f"Sessão iniciada — {APP_NOME} v{APP_VERSAO}")

    resolver_binarios(interativo=True)

    while True:
        mostrar_menu()
        try:
            escolha = _ler(prompt(t("escolha"))).strip().lower()
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
    except Encerramento as pedido:
        _sair_pelo_sinal(pedido.sinal)
    except KeyboardInterrupt:
        print()
        print(f"  {EMO['sair']}  {t('ate_logo')}")
        sys.exit(EXIT_INTERROMPIDO)
    except Exception as fatal:
        print()
        print(f"  {EMO['erro']}  {fatal}")
        log_evento("erro", f"Erro fatal: {fatal}")
        sys.exit(EXIT_ERRO)
