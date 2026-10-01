#!/usr/bin/env python3
"""Ferramenta falsa para os testes do xiso-manager.

Imita, pelo nome com que é chamada, o extract-xiso-pt, o extract-xiso oficial
e o iso2god (o em Rust, em português). Não lê ISO nenhuma: só reproduz o que
o menu enxerga de cada uma — os argumentos que elas aceitam, os eventos do
--progresso-json, o texto que imprimem, os arquivos que criam no destino, a
demora, as falhas e a reação aos sinais:

- extract-xiso-pt e iso2god: o primeiro Ctrl+C, o SIGTERM e o SIGHUP (se não
  vier ignorado, como no nohup) pedem cancelamento: a ferramenta para no
  próximo passo, apaga o que criou e sai com 130. O segundo Ctrl+C sai na
  hora, sem limpar.
- extract-xiso oficial: sem tratador nenhum, como o programa em C: o sinal o
  mata no meio. Na reescrita (-r) ele renomeia o original para <nome>.old
  antes de começar, grava o novo com O_TRUNC e, só se falhar por erro (não por
  sinal), apaga o novo pela metade.

Variáveis de ambiente:
  XMF_PASSOS    passos de trabalho (padrão 4)
  XMF_PAUSA     segundos entre passos (padrão 0.02)
  XMF_FALHAR    "1": falha no meio do trabalho, com mensagem de erro
  XMF_SAIDA     saídas especiais: tipos_errados, linha_enorme, utf8_partido,
                controle, json_invalido
  XMF_INFO      texto que o `info --json` imprime no stdout
  XMF_PIDS      arquivo onde o PID é anotado quando o trabalho começa
  XMF_REGISTRO  arquivo onde ficam anotados os sinais recebidos e o fim
"""

import json
import os
import signal
import sys
import time

NOME = os.path.basename(sys.argv[0])
ARGS = sys.argv[1:]
PT = "extract-xiso-pt" in NOME or "iso2god" in NOME
OFICIAL = not PT

PASSOS = int(os.environ.get("XMF_PASSOS", "4"))
PAUSA = float(os.environ.get("XMF_PAUSA", "0.02"))
FALHAR = os.environ.get("XMF_FALHAR") == "1"
SAIDA = os.environ.get("XMF_SAIDA", "")

cancelado = False


def anotar(texto):
    caminho = os.environ.get("XMF_REGISTRO")
    if caminho:
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def escrever(texto, fd=1):
    """Escreve e ignora falha de escrita (como as ferramentas -pt)."""
    try:
        os.write(fd, texto.encode("utf-8", "surrogateescape"))
    except OSError:
        pass


def evento(**campos):
    escrever(json.dumps(campos, ensure_ascii=False) + "\n")


def _sinal(numero, _quadro):
    global cancelado
    anotar("sinal %d" % numero)
    if numero == signal.SIGINT and cancelado:
        anotar("saida imediata")
        os._exit(130)
    cancelado = True


if PT:
    signal.signal(signal.SIGINT, _sinal)
    signal.signal(signal.SIGTERM, _sinal)
    if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:
        signal.signal(signal.SIGHUP, _sinal)
else:
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def comecou():
    caminho = os.environ.get("XMF_PIDS")
    if caminho:
        with open(caminho, "a", encoding="utf-8") as f:
            f.write("%d\n" % os.getpid())


def saidas_especiais():
    if SAIDA == "tipos_errados":
        for linha in (
            {"evento": "progresso", "bytes": "muito", "total_bytes": "x"},
            {"evento": "progresso", "bytes": None, "total_bytes": 10},
            {"evento": "progresso", "bytes": 5, "total_bytes": [1]},
            {"evento": "fase", "fase": 1, "mensagem": 123},
            {"evento": "concluido", "pasta": 7, "mensagem": None},
            {"evento": "erro", "mensagem": ["lista"]},
            {"evento": "verificado", "hashes": "x", "avisos": 5, "dat": "y"},
            {"evento": "verificado", "hashes": {"sha1": 3}, "avisos": [1, None], "dat": {"situacao": 1}},
            {"evento": None},
        ):
            evento(**linha)
        escrever("[1, 2, 3]\n{\"evento\": \"progresso\"}\n")
    elif SAIDA == "json_invalido":
        escrever("{\"evento\": \"progresso\", \"bytes\": \n{nem json}\n{\"a\":1}\n")
    elif SAIDA == "linha_enorme":
        bloco = "x" * (1024 * 1024)
        for _ in range(int(os.environ.get("XMF_MIB", "32"))):
            escrever(bloco)
        escrever("\nfim da linha enorme\n")
    elif SAIDA == "utf8_partido":
        # o "ç" (2 bytes) cai exatamente na divisa de 4096 bytes da leitura
        escrever("a" * 4095 + "çé fim\n")
    elif SAIDA == "controle":
        escrever("título \x1b]0;janela\x07 com \x1b[2J limpar \x9b31m e \u202eetxet\n")


def trabalhar(passo, limpar, *, progresso=True):
    """Roda os passos; no cancelamento chama `limpar` e sai com 130."""
    comecou()
    total = PASSOS * 1000
    for i in range(PASSOS):
        if cancelado:
            limpar()
            anotar("limpou")
            if "--progresso-json" in ARGS:
                evento(evento="erro", mensagem="Operação cancelada.")
            sys.exit(130)
        passo(i)
        if FALHAR and i == PASSOS // 2:
            limpar()
            anotar("falhou")
            return False
        if progresso and "--progresso-json" in ARGS:
            evento(evento="progresso", blocos=i + 1, total_blocos=PASSOS,
                   bytes=(i + 1) * 1000, total_bytes=total,
                   velocidade_bps=1000.0, eta_segundos=1.0)
        time.sleep(PAUSA)
    if cancelado:
        limpar()
        anotar("limpou")
        if "--progresso-json" in ARGS:
            evento(evento="erro", mensagem="Operação cancelada.")
        sys.exit(130)
    anotar("terminou")
    return True


def opcao(nome):
    if nome in ARGS:
        i = ARGS.index(nome)
        return ARGS[i + 1]
    return None


def posicionais():
    pulos = {"-d", "-s", "--titulo", "-j", "--padding"}
    saida, pular = [], False
    for a in ARGS[1:] if PT else ARGS:
        if pular:
            pular = False
            continue
        if a in pulos:
            pular = True
            continue
        if a.startswith("-"):
            continue
        saida.append(a)
    return saida


def erro_pt(mensagem):
    if "--progresso-json" in ARGS:
        texto_antes = os.environ.get("XMF_ERRO_TEXTO_ANTES") == "1"
        if texto_antes and ARGS[0] == "verificar":
            escrever("X  %s\n" % mensagem, fd=2)     # console antigo do Windows
        evento(evento="erro", mensagem=mensagem)
        if ARGS[0] == "verificar" and not texto_antes:
            escrever("\u274c %s\n" % mensagem, fd=2)
    else:
        escrever("\u274c %s\n" % mensagem, fd=2)
    sys.exit(1)


# ── extract-xiso-pt ─────────────────────────────────────────────────────────

def pt_extrair():
    iso = posicionais()[0]
    pasta = opcao("-d")
    criou = not os.path.exists(pasta)
    os.makedirs(pasta, exist_ok=True)
    criados = []

    def passo(i):
        caminho = os.path.join(pasta, "arquivo%d.bin" % i)
        with open(caminho, "wb") as f:
            f.write(b"x" * 1000)
        criados.append(caminho)

    def limpar():
        for c in criados:
            try:
                os.remove(c)
            except OSError:
                pass
        if criou:
            try:
                os.rmdir(pasta)
            except OSError:
                pass

    evento(evento="fase", fase="extraindo", mensagem="Extraindo %s" % os.path.basename(iso))
    if not trabalhar(passo, limpar):
        erro_pt("falha de leitura no setor 1234")
    evento(evento="concluido", pasta=pasta, duracao_segundos=0.1, mensagem="Extraído.")


def pt_gravar_imagem(origem_bytes, saida, rotulo):
    parcial = saida + ".extract-xiso-pt.parcial"
    partes = []

    def passo(i):
        partes.append(origem_bytes[i::PASSOS])
        with open(parcial, "wb") as f:
            f.write(b"REESCRITO\n" + b"".join(partes))

    def limpar():
        try:
            os.remove(parcial)
        except OSError:
            pass

    evento(evento="fase", fase=rotulo, mensagem=rotulo)
    if not trabalhar(passo, limpar):
        erro_pt("não foi possível gravar %s: não há espaço no disco" % parcial)
    with open(parcial, "wb") as f:
        f.write(b"REESCRITO\n" + origem_bytes)
    os.replace(parcial, saida)
    evento(evento="concluido", pasta=saida, duracao_segundos=0.1, mensagem="Gravado.")


def pt_reescrever():
    origem = posicionais()[0]
    if "--substituir" in ARGS:
        saida = origem
    else:
        saida = opcao("-s")
        if os.path.exists(saida) and "--sobrescrever" not in ARGS:
            erro_pt("%s já existe; escolha outro nome ou use --sobrescrever" % saida)
    with open(origem, "rb") as f:
        dados = f.read()
    pt_gravar_imagem(dados, saida, "reescrevendo")


def pt_criar():
    pasta = posicionais()[0]
    saida = opcao("-s")
    dados = b"".join(
        open(os.path.join(pasta, n), "rb").read()
        for n in sorted(os.listdir(pasta)) if os.path.isfile(os.path.join(pasta, n)))
    pt_gravar_imagem(dados, saida, "criando")


def pt_verificar():
    iso = posicionais()[0]
    evento(evento="fase", fase="verificando", mensagem="Verificando %s" % os.path.basename(iso))
    if not trabalhar(lambda i: None, lambda: None):
        erro_pt("a imagem está truncada: termina no setor 1000, o volume declara 2000")
    evento(evento="verificado", layout="XISO", disco_completo=None, arquivos=3,
           diretorios=1, avisos=[], hashes={"crc32": "1a2b3c4d", "md5": "0" * 32,
                                              "sha1": "1" * 40}, dats_usados=[])


def pt_listar():
    comecou()
    for i in range(PASSOS):
        escrever("arquivo%d.bin\n" % i)
        time.sleep(PAUSA)


# ── iso2god ─────────────────────────────────────────────────────────────────

def god_converter():
    iso, destino = posicionais()[-2:]
    pacote = os.path.join(destino, "4D5308BF", "00007000")
    dados = os.path.join(pacote, "FE40C7D9CF2D599EB911.data")
    criadas = [p for p in (os.path.join(destino, "4D5308BF"), pacote)
               if not os.path.exists(p)]
    os.makedirs(dados, exist_ok=True)
    parte = os.path.join(dados, "Data0000")

    def passo(i):
        with open(parte, "ab") as f:
            f.write(b"g" * 1000)

    def limpar():
        import shutil
        shutil.rmtree(dados, ignore_errors=True)
        for p in reversed(criadas):
            try:
                os.rmdir(p)
            except OSError:
                pass

    evento(evento="fase", fase="iniciando", mensagem="Convertendo %s -> %s" % (iso, destino))
    evento(evento="fase", fase="convertendo", mensagem="Convertendo ISO para GOD...")
    if not trabalhar(passo, limpar):
        evento(evento="erro", mensagem="não foi possível ler %s: falha de E/S" % iso)
        sys.exit(1)
    with open(os.path.join(pacote, "FE40C7D9CF2D599EB911"), "wb") as f:
        f.write(b"LIVE")
    evento(evento="concluido", pasta=pacote, duracao_segundos=0.1,
           mensagem="Concluído em 0m0s. Pacote GOD gravado em: %s" % pacote)


def god_info():
    padrao = {"tipo_disco": "Xgd3", "plataforma_detectada": "xbox360",
              "title_id": "4D5308BF", "media_id": "AABBCCDD", "titulo": "Jogo de Ação",
              "disco": 1, "total_discos": 1, "tamanho_volume": 7 * 1024 ** 3}
    escrever(os.environ.get("XMF_INFO", json.dumps(padrao, ensure_ascii=False)) + "\n")


# ── extract-xiso oficial ────────────────────────────────────────────────────

def oficial_reescrever():
    iso = posicionais()[-1]
    pasta = opcao("-d") or os.getcwd()
    antigo = iso + ".old"
    if os.path.exists(antigo):
        escrever("%s already exists, cannot rewrite %s\n" % (antigo, iso))
        return
    os.rename(iso, antigo)
    with open(antigo, "rb") as f:
        dados = f.read()
    novo = os.path.join(pasta, os.path.basename(iso))
    escrever("rewriting %s:\n\n" % iso)
    comecou()
    with open(novo, "wb") as f:                       # O_TRUNC, como o original
        for i in range(PASSOS):
            f.write(dados[i::PASSOS])
            f.flush()
            escrever("adding file%d.bin (%d bytes) [OK]\n" % (i, len(dados) // PASSOS))
            if FALHAR and i == PASSOS // 2:
                f.close()
                os.remove(novo)                       # if ( err ) unlink( xiso_path )
                escrever("write error: No space left on device\n")
                escrever("failed to rewrite xbox iso image %s\n" % os.path.basename(iso))
                sys.exit(1)
            time.sleep(PAUSA)
    anotar("terminou")
    if "-D" in ARGS:
        os.remove(antigo)
    escrever("\n%s successfully rewritten as %s\n" % (iso, novo))


def oficial_extrair():
    pasta = opcao("-d")
    os.makedirs(pasta, exist_ok=True)
    comecou()
    for i in range(PASSOS):
        with open(os.path.join(pasta, "arquivo%d.bin" % i), "wb") as f:
            f.write(b"x" * 1000)
        escrever("extracting arquivo%d.bin (1000 bytes) [OK]\n" % i)
        time.sleep(PAUSA)


def main():
    if not ARGS:
        sys.exit(2)
    saidas_especiais()
    if "iso2god" in NOME:
        if ARGS[0] == "--help":
            escrever("Uso: iso2god [COMANDO]\n\nComandos:\n  converter\n  info\n")
        elif ARGS[0] == "converter":
            god_converter()
        elif ARGS[0] == "info":
            god_info()
        else:
            sys.exit(2)
    elif PT:
        comando = ARGS[0]
        {"extrair": pt_extrair, "reescrever": pt_reescrever, "criar": pt_criar,
         "verificar": pt_verificar, "listar": pt_listar,
         "dats": lambda: escrever("dat instalado\n")}.get(comando, lambda: sys.exit(2))()
    else:
        if "-r" in ARGS:
            oficial_reescrever()
        elif "-x" in ARGS:
            oficial_extrair()
        elif "-l" in ARGS:
            pt_listar()
        else:
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
