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
WINDOWS = os.name == "nt"


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
            # resolve(): no Windows a pasta temporária vem com o nome curto do
            # DOS (RUNNER~1), e o programa mostra o caminho longo
            self.dir = Path(tempfile.mkdtemp(prefix="xm-%s-" % caso)).resolve()
        self.app = self.dir / "app"
        self.bin = self.dir / "bin"
        self.app.mkdir()
        self.bin.mkdir()
        shutil.copy(PROGRAMA, self.app / "xiso_manager.py")
        _executavel(FALSO)
        for nome in ("extract-xiso-pt", "extract-xiso", "iso2god"):
            if WINDOWS:
                # O Windows não executa um .py pelo nome nem segue o "#!": a
                # ferramenta falsa é um .cmd que chama o Python com ela e diz
                # com que nome foi chamada.
                self.ferramenta(nome).write_text(
                    '@set "XMF_NOME=%s"\n@"%s" "%s" %%*\n@exit /b %%ERRORLEVEL%%\n'
                    % (nome, sys.executable, FALSO), encoding="oem")
            else:
                self.ferramenta(nome).symlink_to(FALSO)
        self.pids = self.dir / "pids.txt"
        self.registro = self.dir / "registro.txt"
        self.config(bin_extract_xiso=str(self.ferramenta("extract-xiso" if oficial else "extract-xiso-pt")),
                    bin_iso2god=str(self.ferramenta("iso2god")),
                    pasta_padrao=str(self.dir))
        self.env = dict(os.environ, XMF_PIDS=str(self.pids), XMF_REGISTRO=str(self.registro),
                        NO_COLOR="1", PYTHONIOENCODING="utf-8")
        self.env.pop("XMF_SAIDA", None)
        self.env.pop("XMF_NOME", None)

    def ferramenta(self, nome):
        """O caminho de uma das ferramentas falsas."""
        return self.bin / (nome + ".cmd" if WINDOWS else nome)

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
            matar_arvore(processo)
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
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        processo = k32.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
        if not processo:
            return False
        try:
            codigo = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(processo, ctypes.byref(codigo))) \
                and codigo.value == 259                     # STILL_ACTIVE
        finally:
            k32.CloseHandle(processo)
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


def link_de_pasta(link, alvo):
    """Outro caminho para a mesma pasta: um link simbólico ou, no Windows
    sem permissão para criar um, uma junção do NTFS."""
    try:
        os.symlink(alvo, link, target_is_directory=True)
    except OSError:
        if not WINDOWS:
            raise
        import _winapi
        _winapi.CreateJunction(str(alvo), str(link))


def janela_do_console(pid):
    """Windows: a janela do console em que o processo roda (0 se não achar).
    Só um processo preso a esse console a enxerga, então quem pergunta é um
    Python à parte, que se solta do console dele e se prende ao do outro."""
    codigo = ("import ctypes, sys\n"
              "k = ctypes.WinDLL('kernel32')\n"
              "k.GetConsoleWindow.restype = ctypes.c_void_p\n"
              "k.FreeConsole()\n"
              "print((k.AttachConsole(int(sys.argv[1])) and k.GetConsoleWindow()) or 0)\n")
    r = subprocess.run([sys.executable, "-c", codigo, str(pid)],
                       capture_output=True, text=True, timeout=30)
    return int(r.stdout.strip() or 0)


def fechar_janela(janela):
    """Windows: fecha a janela como pelo X (WM_CLOSE)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    if not user32.PostMessageW(janela, 0x0010, 0, 0):
        raise OSError(ctypes.get_last_error(), "PostMessageW falhou")


def matar(pid):
    try:
        os.kill(pid, signal.SIGTERM if WINDOWS else signal.SIGKILL)
    except OSError:
        pass


def matar_arvore(processo):
    """Mata o menu e o que ele abriu (os testes o abrem numa sessão própria
    no Unix; no Windows, o taskkill segue a árvore de processos)."""
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(processo.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(processo.pid, signal.SIGKILL)
        except OSError:
            pass


def carregar_modulo(ambiente):
    """Importa a cópia do menu de um ambiente, neste processo (para testar
    funções soltas). A pasta da cópia vira a pasta do config e do log."""
    for h in list(logging.root.handlers):
        logging.root.removeHandler(h)
        h.close()
    spec = importlib.util.spec_from_file_location("xiso_manager_teste", ambiente.app / "xiso_manager.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    modulo.carregar_config()
    return modulo


def sem_cor(texto):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", texto)
