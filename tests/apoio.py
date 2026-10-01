"""Apoio dos testes: uma pasta temporária com uma cópia do xiso_manager.py,
um config.json apontando para as ferramentas falsas (tests/falsos) e funções
para conduzir o menu de verdade pela entrada padrão, mandar sinais e
conferir o que sobrou no disco.

Nada aqui usa rede nem ISO de verdade.
"""

import importlib.util
import json
import logging
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PROGRAMA = RAIZ / "xiso_manager.py"
FALSO = RAIZ / "tests" / "falsos" / "ferramenta_falsa.py"


def _executavel(caminho):
    caminho.chmod(caminho.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Ambiente:
    """Uma instalação descartável do xiso-manager com as ferramentas falsas."""

    def __init__(self, caso, oficial=False, pasta=None):
        if pasta:
            shutil.rmtree(pasta, ignore_errors=True)
            Path(pasta).mkdir(parents=True)
            self.dir = Path(pasta)
        else:
            self.dir = Path(tempfile.mkdtemp(prefix="xm-%s-" % caso))
        self.app = self.dir / "app"
        self.bin = self.dir / "bin"
        self.app.mkdir()
        self.bin.mkdir()
        shutil.copy(PROGRAMA, self.app / "xiso_manager.py")
        _executavel(FALSO)
        for nome in ("extract-xiso-pt", "extract-xiso", "iso2god"):
            (self.bin / nome).symlink_to(FALSO)
        self.pids = self.dir / "pids.txt"
        self.registro = self.dir / "registro.txt"
        self.config(bin_extract_xiso=str(self.bin / ("extract-xiso" if oficial else "extract-xiso-pt")),
                    bin_iso2god=str(self.bin / "iso2god"),
                    pasta_padrao=str(self.dir))
        self.env = dict(os.environ, XMF_PIDS=str(self.pids), XMF_REGISTRO=str(self.registro),
                        NO_COLOR="1", PYTHONIOENCODING="utf-8")
        self.env.pop("XMF_SAIDA", None)

    def config(self, **valores):
        caminho = self.app / "config.json"
        dados = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
        dados.update(valores)
        caminho.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    def limpar(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def iso(self, nome, conteudo=b"ISO ORIGINAL " * 100, pasta=None):
        pasta = Path(pasta) if pasta else self.dir / "jogos"
        pasta.mkdir(parents=True, exist_ok=True)
        caminho = pasta / nome
        caminho.write_bytes(conteudo)
        return caminho

    # ── conduzir o menu ──────────────────────────────────────────────────

    def iniciar(self, entradas, env=None, argumentos=(), cwd=None):
        """Roda o menu (o programa inteiro, pelo main) numa sessão própria,
        como num terminal: um sinal para o grupo chega ao menu e à
        ferramenta, como o Ctrl+C."""
        ambiente = dict(self.env, **(env or {}))
        processo = subprocess.Popen(
            [sys.executable, str(self.app / "xiso_manager.py"), *argumentos],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=ambiente, start_new_session=True, cwd=cwd)
        processo.stdin.write("".join(e + "\n" for e in entradas).encode("utf-8"))
        processo.stdin.flush()
        return processo

    def iniciar_no_terminal(self, entradas, env=None):
        """Roda o menu num pseudoterminal que é o terminal de controle da
        sessão, como numa janela de terminal. Devolve (processo, mestre):
        fechar o `mestre` é fechar a janela (o sistema manda SIGHUP)."""
        import fcntl
        import pty
        import termios
        mestre, escravo = pty.openpty()
        atributos = termios.tcgetattr(escravo)
        atributos[3] &= ~termios.ECHO
        termios.tcsetattr(escravo, termios.TCSANOW, atributos)

        def controlar():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        processo = subprocess.Popen(
            [sys.executable, str(self.app / "xiso_manager.py")],
            stdin=escravo, stdout=escravo, stderr=escravo,
            env=dict(self.env, **(env or {})), preexec_fn=controlar)
        os.close(escravo)
        os.write(mestre, "".join(e + "\n" for e in entradas).encode("utf-8"))
        return processo, mestre

    def rodar(self, entradas, env=None, prazo=60, cwd=None):
        processo = self.iniciar(entradas, env, cwd=cwd)
        try:
            saida, erros = processo.communicate(timeout=prazo)
        except subprocess.TimeoutExpired:
            os.killpg(processo.pid, signal.SIGKILL)
            saida, erros = processo.communicate()
            raise AssertionError("o menu não terminou em %ss:\n%s\n%s"
                                 % (prazo, saida.decode("utf-8", "replace")[-3000:],
                                    erros.decode("utf-8", "replace")[-3000:]))
        return processo.returncode, saida.decode("utf-8", "replace"), erros.decode("utf-8", "replace")

    def esperar_ferramenta(self, quantas=1, prazo=20):
        """PIDs das ferramentas falsas que já começaram o trabalho."""
        limite = time.time() + prazo
        while time.time() < limite:
            if self.pids.exists():
                pids = [int(l) for l in self.pids.read_text().split()]
                if len(pids) >= quantas:
                    return pids
            time.sleep(0.02)
        raise AssertionError("a ferramenta falsa não começou em %ss" % prazo)

    def registro_texto(self):
        return self.registro.read_text() if self.registro.exists() else ""


def vivo(pid):
    """O processo ainda existe (e não é só um zumbi esperando o pai)?"""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except (OSError, IndexError):
        return False


def esperar_morrer(pid, prazo=10):
    limite = time.time() + prazo
    while time.time() < limite:
        if not vivo(pid):
            return True
        time.sleep(0.02)
    return False


def matar(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def carregar_modulo(ambiente):
    """Importa a cópia do menu de um ambiente, neste processo (para testar
    funções soltas). A pasta da cópia vira a pasta do config e do log."""
    for h in list(logging.root.handlers):
        logging.root.removeHandler(h)
    spec = importlib.util.spec_from_file_location("xiso_manager_teste", ambiente.app / "xiso_manager.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    modulo.carregar_config()
    return modulo


def sem_cor(texto):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", texto)
