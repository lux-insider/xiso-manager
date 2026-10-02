"""As telas do menu, gravadas antes de qualquer correção da auditoria.

A aparência do menu, as opções, as teclas e os textos mostrados não podem
mudar. Cada teste conduz o menu de verdade (com as ferramentas falsas) por
um caminho e compara tudo o que ele imprimiu com o que a versão 3.2.5
imprimia: menus, perguntas, tabelas, painéis, blocos de saída e resumos.
Os menus estáticos também são conferidos em cores, num terminal de verdade
(pty). Só o que muda sozinho de uma execução para outra é mascarado: horas,
durações, velocidades e o espaço livre do disco.

No Windows as mesmas telas valem: a pasta base tem o mesmo comprimento da
do Linux (as linhas longas são cortadas no mesmo ponto), as barras dos
caminhos são trocadas por "/" e o ".cmd" das ferramentas falsas sai do nome.

Para regravar (só de propósito, explicando no commit o que mudou):
    XM_GRAVAR_TELAS=1 python3 -m unittest tests.test_telas
"""

import os
import re
import select
import struct
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apoio import WINDOWS, Ambiente  # noqa: E402

TELAS = Path(__file__).resolve().parent / "telas"
# o mesmo comprimento nos dois: "/tmp/xm-telas" e "C:\xm-telas-w"
BASE = Path(os.environ.get("SystemDrive", "C:") + "\\xm-telas-w") if WINDOWS else Path("/tmp/xm-telas")
GRAVAR = os.environ.get("XM_GRAVAR_TELAS") == "1"


def normalizar(texto):
    texto = texto.replace("\r\n", "\n").replace(str(BASE), "<BASE>")
    if WINDOWS:
        texto = texto.replace("\\", "/")
        texto = re.sub(r"(<BASE>/bin/[\w-]+)\.cmd\b", r"\1", texto)
    # um caminho cortado logo no começo da pasta base ("/t…" ou "C:…")
    texto = re.sub(r"(?<!\S)(\S+)…",
                   lambda m: "<BASE>…" if str(BASE).replace("\\", "/").startswith(m.group(1))
                   else m.group(0),
                   texto)
    texto = re.sub(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", "<DATA>", texto)
    texto = re.sub(r"\b\d\d:\d\d(:\d\d)?\b", "<T>", texto)
    texto = re.sub(r"\(\d+\.\d\d (MiB|GiB)/s\)", "(<V>)", texto)
    linhas = []
    for linha in texto.split("\n"):
        if any(r in linha for r in ("Disponível", "Uso atual", "Available", "Current use")):
            linha = re.sub(r"\d", "#", linha)
            linha = re.sub(r"[\u2588\u2591]+", "<BARRA>", linha)
        linhas.append(linha.rstrip())
    # o espaço livre muda de máquina para máquina, inclusive em quantos
    # dígitos tem (95 GiB ou 190 GiB): a largura do número também é ignorada
    return re.sub(r" *#+\.## GiB", " #.## GiB", "\n".join(linhas))


class Telas(unittest.TestCase):
    maxDiff = None

    def preparar(self, oficial=False):
        self.amb = Ambiente("telas", oficial=oficial, pasta=BASE)
        self.addCleanup(self.amb.limpar)
        casa = BASE / "casa"
        casa.mkdir()
        self.trabalho = BASE / "trabalho"
        self.trabalho.mkdir()
        self.jogos = BASE / "jogos"
        self.amb.iso("Halo.iso", b"ISO ORIGINAL " * 4000, pasta=self.jogos)
        self.amb.iso("Jogo de Ação.iso", b"ISO " * 3000, pasta=self.jogos)
        jogo = BASE / "pasta-do-jogo"
        jogo.mkdir()
        (jogo / "default.xbe").write_bytes(b"XBEH" + b"\0" * 4000)
        (jogo / "dados.bin").write_bytes(b"d" * 9000)
        self.amb.config(pasta_padrao=str(self.jogos))
        self.amb.env["HOME"] = str(casa)
        self.amb.env["USERPROFILE"] = str(casa)     # a pasta pessoal no Windows
        self.amb.env["XMF_PAUSA"] = "0"
        (BASE / "app" / "xiso-manager.log").unlink(missing_ok=True)

    def conferir(self, nome, texto):
        texto = normalizar(texto)
        arquivo = TELAS / (nome + ".txt")
        if GRAVAR:
            TELAS.mkdir(exist_ok=True)
            arquivo.write_text(texto, encoding="utf-8")
            return
        self.assertEqual(arquivo.read_text(encoding="utf-8"), texto,
                         "a tela %r mudou (veja tests/test_telas.py)" % nome)

    def caminho(self, nome, entradas, env=None, oficial=False, codigo=0):
        self.preparar(oficial)
        entradas = [e.replace("<BASE>", str(BASE)) for e in entradas]
        retorno, saida, erros = self.amb.rodar(entradas, env=env, cwd=self.trabalho)
        self.assertEqual(retorno, codigo, erros)
        self.conferir(nome, saida)

    # ── caminhos pelo menu, sem cor ───────────────────────────────────────

    def test_menu_configuracoes_sobre_log(self):
        self.caminho("menu", ["c", "0", "s", "", "l", "", "x", "", "0"])

    def test_menu_com_extract_xiso_oficial(self):
        self.caminho("menu_oficial", ["0"], oficial=True)

    def test_converter_para_god(self):
        self.caminho("god", ["1", "1", "<BASE>/GOD", "", "", "", "", "", "0"])

    def test_converter_para_god_com_falha(self):
        self.caminho("god_falha", ["1", "1", "<BASE>/GOD", "", "", "", "", "", "0"],
                     env={"XMF_FALHAR": "1"})

    def test_analisar(self):
        self.caminho("info", ["2", "1", "", "0"])

    def test_extrair(self):
        self.caminho("extrair", ["3", "1", "", "", "", "", "0"])

    def test_extrair_com_falha(self):
        self.caminho("extrair_falha", ["3", "1", "", "", "", "", "0"], env={"XMF_FALHAR": "1"})

    def test_listar(self):
        self.caminho("listar", ["4", "2", "", "0"])

    def test_criar(self):
        pasta = BASE / "pasta-do-jogo"
        self.caminho("criar", ["5", str(pasta), "", "", "", "", "", "0"])

    def test_reescrever_mesma_pasta(self):
        self.caminho("reescrever", ["6", "1", "", "", "", "", "0"])

    def test_reescrever_apagando(self):
        self.caminho("reescrever_apagando", ["6", "1", "", "s", "s", "", "", "0"])

    def test_reescrever_com_extract_xiso_oficial(self):
        self.caminho("reescrever_oficial", ["6", "1", "", "", "", "", "", "", "0"], oficial=True)

    def test_reescrever_oficial_com_falha(self):
        self.caminho("reescrever_oficial_falha", ["6", "1", "", "", "", "", "", "", "0"],
                     oficial=True, env={"XMF_FALHAR": "1"})

    def test_verificar(self):
        self.caminho("verificar", ["v", "1", "", "", "0"])

    def test_verificar_com_erro(self):
        self.caminho("verificar_erro", ["v", "1", "", "", "0"], env={"XMF_FALHAR": "1"})

    def test_assistente(self):
        self.caminho("assistente", ["7", "1", "0", "0"])

    def test_lote(self):
        self.caminho("lote", ["8", "", "0", "0"])

    def test_manual(self):
        self.caminho("manual", ["9", "2", "info --json x.iso", "", "0"])

    def test_navegador_entradas_invalidas(self):
        self.caminho("navegador", ["3", "abc,99", "", "..", "0", "0"])

    # ── os menus em cores, num terminal ──────────────────────────────────

    @unittest.skipIf(WINDOWS, "o terminal de verdade (pty) só existe no Unix")
    def test_menus_em_cores(self):
        self.preparar()
        self.conferir("menu_cores", rodar_no_terminal(self.amb, ["c", "0", "s", "", "0"]))


def rodar_no_terminal(amb, entradas, colunas=100, linhas=40):
    """Roda o menu num pseudoterminal (cores ligadas), sem eco da entrada."""
    import fcntl
    import pty
    import termios
    mestre, escravo = pty.openpty()
    fcntl.ioctl(escravo, termios.TIOCSWINSZ, struct.pack("HHHH", linhas, colunas, 0, 0))
    atributos = termios.tcgetattr(escravo)
    atributos[3] &= ~termios.ECHO
    termios.tcsetattr(escravo, termios.TCSANOW, atributos)
    env = dict(amb.env, TERM="xterm-256color", COLUMNS=str(colunas), LINES=str(linhas))
    env.pop("NO_COLOR", None)
    processo = subprocess.Popen([sys.executable, str(amb.app / "xiso_manager.py")],
                                stdin=escravo, stdout=escravo, stderr=escravo, env=env,
                                cwd=str(BASE / "trabalho"), start_new_session=True)
    os.close(escravo)
    os.write(mestre, "".join(e + "\n" for e in entradas).encode("utf-8"))
    saida = b""
    limite = time.time() + 30
    while time.time() < limite:
        prontos, _, _ = select.select([mestre], [], [], 0.2)
        if prontos:
            try:
                pedaco = os.read(mestre, 65536)
            except OSError:
                break
            if not pedaco:
                break
            saida += pedaco
        elif processo.poll() is not None:
            break
    processo.wait(timeout=10)
    os.close(mestre)
    return saida.decode("utf-8", "replace")


if __name__ == "__main__":
    unittest.main()
