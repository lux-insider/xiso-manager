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
        # uma pasta pessoal só do teste: no Windows a pasta temporária fica
        # dentro da pessoal, e o menu mostraria os caminhos como "~\\..."
        casa = self.dir / "casa"
        casa.mkdir()
        self.env = dict(os.environ, XMF_PIDS=str(self.pids), XMF_REGISTRO=str(self.registro),
                        NO_COLOR="1", PYTHONIOENCODING="utf-8",
                        HOME=str(casa), USERPROFILE=str(casa))
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


class ConsoleProprio:
    """Windows: um processo num console só dele, como um programa aberto
    numa janela. O console é um pseudoconsole (o mesmo do Windows Terminal),
    e fechar() é fechar a janela ou a aba: o Windows manda CTRL_CLOSE_EVENT a
    todos os processos presos ao console. A entrada e a saída padrão são um
    pipe e um arquivo, como nos outros testes; o console serve para o aviso.

    (Mandar WM_CLOSE para a janela do console não serve: no runner do
    GitHub a janela é de fachada e ignora a mensagem.)"""

    def __init__(self, argumentos, env, saida, cwd=None):
        import ctypes
        import msvcrt
        import threading
        from ctypes import wintypes as w

        class COORD(ctypes.Structure):
            _fields_ = [("X", w.SHORT), ("Y", w.SHORT)]

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                        ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                        ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                        ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                        ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE),
                        ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]

        class STARTUPINFOEXW(ctypes.Structure):
            _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

        k32 = self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreatePipe.argtypes = (ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE),
                                   ctypes.c_void_p, w.DWORD)
        k32.CreatePseudoConsole.argtypes = (COORD, w.HANDLE, w.HANDLE, w.DWORD,
                                            ctypes.POINTER(ctypes.c_void_p))
        k32.CreatePseudoConsole.restype = ctypes.c_long
        k32.ClosePseudoConsole.argtypes = (ctypes.c_void_p,)
        k32.ClosePseudoConsole.restype = None
        k32.InitializeProcThreadAttributeList.argtypes = (ctypes.c_void_p, w.DWORD, w.DWORD,
                                                          ctypes.POINTER(ctypes.c_size_t))
        k32.UpdateProcThreadAttribute.argtypes = (ctypes.c_void_p, w.DWORD, ctypes.c_size_t,
                                                  ctypes.c_void_p, ctypes.c_size_t,
                                                  ctypes.c_void_p, ctypes.c_void_p)
        k32.CreateProcessW.argtypes = (w.LPCWSTR, ctypes.c_wchar_p, ctypes.c_void_p,
                                       ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p,
                                       w.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
                                       ctypes.POINTER(PROCESS_INFORMATION))
        k32.ReadFile.argtypes = (w.HANDLE, ctypes.c_void_p, w.DWORD,
                                 ctypes.POINTER(w.DWORD), ctypes.c_void_p)
        k32.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
        k32.WaitForSingleObject.restype = w.DWORD
        k32.CloseHandle.argtypes = (w.HANDLE,)

        def conferir(ok, oque):
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error(), oque)

        # o pseudoconsole: a "tela" dele é lida e descartada numa thread
        entrada_pc, self._entrada_pc = w.HANDLE(), w.HANDLE()
        self._saida_pc, saida_pc = w.HANDLE(), w.HANDLE()
        conferir(k32.CreatePipe(ctypes.byref(entrada_pc), ctypes.byref(self._entrada_pc), None, 0),
                 "CreatePipe")
        conferir(k32.CreatePipe(ctypes.byref(self._saida_pc), ctypes.byref(saida_pc), None, 0),
                 "CreatePipe")
        self._console = ctypes.c_void_p()
        resultado = k32.CreatePseudoConsole(COORD(120, 40), entrada_pc, saida_pc, 0,
                                            ctypes.byref(self._console))
        if resultado != 0:
            raise OSError("CreatePseudoConsole: 0x%08x" % (resultado & 0xFFFFFFFF))
        k32.CloseHandle(entrada_pc)
        k32.CloseHandle(saida_pc)

        def escoar():
            pedaco, lido = ctypes.create_string_buffer(4096), w.DWORD()
            while k32.ReadFile(self._saida_pc, pedaco, 4096, ctypes.byref(lido), None) and lido.value:
                pass
        threading.Thread(target=escoar, daemon=True).start()

        # a entrada padrão (um pipe) e a saída (o arquivo), herdadas pelo processo
        ler, self._escrever = os.pipe()
        manuseio_entrada = msvcrt.get_osfhandle(ler)
        manuseio_saida = msvcrt.get_osfhandle(saida.fileno())
        for manuseio in (manuseio_entrada, manuseio_saida):
            os.set_handle_inheritable(manuseio, True)

        tamanho = ctypes.c_size_t()
        k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(tamanho))
        self._lista = ctypes.create_string_buffer(tamanho.value)
        lista = ctypes.cast(self._lista, ctypes.c_void_p)
        conferir(k32.InitializeProcThreadAttributeList(lista, 1, 0, ctypes.byref(tamanho)),
                 "InitializeProcThreadAttributeList")
        conferir(k32.UpdateProcThreadAttribute(lista, 0, 0x00020016,   # PSEUDOCONSOLE
                                               self._console, ctypes.sizeof(ctypes.c_void_p),
                                               None, None), "UpdateProcThreadAttribute")
        inicio = STARTUPINFOEXW()
        inicio.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        inicio.StartupInfo.dwFlags = 0x100                         # STARTF_USESTDHANDLES
        inicio.StartupInfo.hStdInput = manuseio_entrada
        inicio.StartupInfo.hStdOutput = manuseio_saida
        inicio.StartupInfo.hStdError = manuseio_saida
        inicio.lpAttributeList = lista
        bloco = "".join("%s=%s\0" % (k, v)
                        for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
        ambiente = ctypes.create_unicode_buffer(bloco, len(bloco.encode("utf-16-le")) // 2 + 1)
        linha = ctypes.create_unicode_buffer(subprocess.list2cmdline(argumentos))
        processo = PROCESS_INFORMATION()
        conferir(k32.CreateProcessW(None, linha, None, None, True,
                                    0x00080000 | 0x00000400,   # STARTUPINFOEX, env Unicode
                                    ctypes.cast(ambiente, ctypes.c_void_p),
                                    str(cwd) if cwd else None,
                                    ctypes.byref(inicio), ctypes.byref(processo)),
                 "CreateProcessW")
        os.close(ler)
        k32.CloseHandle(processo.hThread)
        self._processo = processo.hProcess
        self.pid = processo.dwProcessId

    def digitar(self, respostas):
        os.write(self._escrever, "".join(r + "\n" for r in respostas).encode("utf-8"))

    def fechar(self):
        """Fecha o console, como o X da janela. O ClosePseudoConsole pode
        esperar os processos saírem: roda numa thread."""
        import threading
        threading.Thread(target=self._k32.ClosePseudoConsole, args=(self._console,),
                         daemon=True).start()

    def esperar(self, prazo):
        """True se o processo saiu dentro do prazo (em segundos)."""
        return self._k32.WaitForSingleObject(self._processo, int(prazo * 1000)) == 0


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
