"""O pacote do Windows como chega ao usuário: só o xiso-manager.exe, numa
pasta com espaço e acento no caminho.

Só roda no Windows. O teste do erro fatal (V-4) usa um executável compilado
com a feature "teste-erro-fatal", que falha de propósito na abertura:

    cargo build --release
    cargo build --release --features teste-erro-fatal --target-dir target\\erro-fatal
    set XM_PROGRAMA_ERRO_FATAL=target\\erro-fatal\\release\\xiso-manager.exe
    python -m unittest tests.test_pacote_windows -v

O workflow .github/workflows/testes.yml faz exatamente isso.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apoio import PROGRAMA, RAIZ, WINDOWS, ConsoleProprio, matar_arvore  # noqa: E402

ERRO_FATAL = os.environ.get("XM_PROGRAMA_ERRO_FATAL")


def versao():
    for linha in (RAIZ / "Cargo.toml").read_text(encoding="utf-8").splitlines():
        if linha.startswith("version"):
            return linha.split('"')[1]
    raise AssertionError("versão não encontrada no Cargo.toml")


@unittest.skipUnless(WINDOWS and PROGRAMA.is_file(), "precisa do Windows e do xiso-manager.exe compilado")
class PacoteWindows(unittest.TestCase):

    def setUp(self):
        raiz = Path(tempfile.mkdtemp(prefix="xm-pacote-")).resolve()
        self.addCleanup(shutil.rmtree, raiz, True)
        self.pasta = raiz / "Jogos do Zé" / "xiso manager"
        self.pasta.mkdir(parents=True)
        self.exe = self.pasta / "xiso-manager.exe"
        shutil.copy(PROGRAMA, self.exe)
        # o programa acha o config e o log pelo próprio caminho, não pela pasta atual
        self.fora = raiz / "outra pasta"
        self.fora.mkdir()

    def test_abre_o_menu_e_sai(self):
        # sem as ferramentas no pacote, o menu pergunta onde estão as duas
        # (ENTER pula) e depois mostra o menu; "0" sai
        r = subprocess.run([str(self.exe)], input="\n\n0\n".encode("utf-8"), capture_output=True,
                           cwd=str(self.fora), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        texto = r.stdout.decode("utf-8")
        self.assertIn("xiso-manager  ·  v" + versao(), texto)
        self.assertIn("FERRAMENTA NÃO ENCONTRADA", texto)
        self.assertNotIn("Erro inesperado", texto)
        self.assertTrue((self.pasta / "xiso-manager.log").is_file(), "o log fica ao lado do programa")

    # ── V-4: erro fatal com a janela aberta com dois cliques ─────────────

    def na_janela(self, argumentos, nome, env=None):
        """Roda num console só dele, como um programa aberto com dois
        cliques (ou o cmd.exe, como um terminal aberto antes). A entrada é
        um pipe que fica aberto: um ENTER esperado à toa trava o teste."""
        tela = self.fora / nome
        arquivo = open(tela, "wb")
        self.addCleanup(arquivo.close)
        console = ConsoleProprio(argumentos, dict(os.environ, **(env or {})), arquivo, cwd=self.fora)
        self.addCleanup(lambda: matar_arvore(console))
        return console, tela

    @staticmethod
    def ler(tela):
        return tela.read_text(encoding="utf-8", errors="replace")

    def com_erro_fatal(self):
        if not ERRO_FATAL:
            self.skipTest("precisa do executável com a feature teste-erro-fatal (XM_PROGRAMA_ERRO_FATAL)")
        shutil.copy(ERRO_FATAL, self.exe)
        return {"XM_ERRO_FATAL": "1"}

    def test_erro_fatal_na_janela_propria_espera_enter(self):
        env = self.com_erro_fatal()
        console, tela = self.na_janela([str(self.exe)], "tela.txt", env)
        limite = time.time() + 60
        while "Aperte ENTER para fechar..." not in self.ler(tela) and time.time() < limite:
            time.sleep(0.1)
        self.assertIn("Aperte ENTER para fechar...", self.ler(tela))
        self.assertIn("falhou de propósito", self.ler(tela))
        self.assertFalse(console.esperar(1), "a janela não pode fechar antes do ENTER")
        console.digitar([""])
        self.assertTrue(console.esperar(20), "o ENTER fecha a janela")
        self.assertEqual(console.codigo(), 1)

    def test_saida_normal_fecha_na_hora(self):
        console, tela = self.na_janela([str(self.exe)], "tela.txt")
        console.digitar(["", "", "0"])
        self.assertTrue(console.esperar(60), "não pode esperar ENTER: " + self.ler(tela))
        self.assertEqual(console.codigo(), 0)
        self.assertNotIn("Aperte ENTER", self.ler(tela))

    def test_erro_fatal_num_terminal_aberto_antes_nao_espera(self):
        # o console é do cmd.exe, que continua aberto com a mensagem
        env = self.com_erro_fatal()
        console, tela = self.na_janela(["cmd.exe", "/d", "/c", str(self.exe)], "tela.txt", env)
        self.assertTrue(console.esperar(60), "não pode esperar ENTER: " + self.ler(tela))
        self.assertEqual(console.codigo(), 1)
        self.assertIn("falhou de propósito", self.ler(tela))
        self.assertNotIn("Aperte ENTER", self.ler(tela))


if __name__ == "__main__":
    unittest.main()
