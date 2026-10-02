"""O pacote do Windows montado como chega ao usuário: o xiso-manager.exe (o
lançador), a pasta python\\ com o Python portátil oficial e o
xiso_manager.py, numa pasta com espaço e acento no caminho.

Só roda no Windows, com o lançador compilado e o zip do Python portátil
("embeddable", de python.org) já baixado:

    set XM_LANCADOR=windows\\lancador\\target\\release\\xiso-manager.exe
    set XM_PYTHON_PORTATIL=python-3.14.0-embed-amd64.zip
    python -m unittest tests.test_pacote_windows -v

O workflow .github/workflows/testes.yml faz exatamente isso.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apoio import PROGRAMA, WINDOWS  # noqa: E402

LANCADOR = os.environ.get("XM_LANCADOR")
PORTATIL = os.environ.get("XM_PYTHON_PORTATIL")


@unittest.skipUnless(WINDOWS and LANCADOR and PORTATIL,
                     "precisa do Windows, do lançador compilado (XM_LANCADOR) "
                     "e do Python portátil (XM_PYTHON_PORTATIL)")
class PacoteWindows(unittest.TestCase):

    def setUp(self):
        raiz = Path(tempfile.mkdtemp(prefix="xm-pacote-")).resolve()
        self.addCleanup(shutil.rmtree, raiz, True)
        self.pasta = raiz / "Jogos do Zé" / "xiso manager"
        self.pasta.mkdir(parents=True)
        shutil.copy(LANCADOR, self.pasta / "xiso-manager.exe")
        shutil.copy(PROGRAMA, self.pasta / "xiso_manager.py")
        with zipfile.ZipFile(PORTATIL) as z:
            z.extractall(self.pasta / "python")
        self.python = self.pasta / "python"
        # o lançador acha tudo pelo próprio caminho, não pela pasta atual
        self.fora = raiz / "outra pasta"
        self.fora.mkdir()

    def rodar(self, entradas=""):
        r = subprocess.run([str(self.pasta / "xiso-manager.exe")],
                           input=entradas.encode("utf-8"), capture_output=True,
                           cwd=str(self.fora), timeout=120)
        return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")

    def um(self, padrao):
        achados = sorted(self.python.glob(padrao))
        self.assertEqual(len(achados), 1, "%s em %s" % (padrao, [p.name for p in self.python.iterdir()]))
        return achados[0]

    def test_abre_o_menu_e_sai(self):
        # sem as ferramentas no pacote, o menu pergunta onde estão as duas
        # (ENTER pula) e depois mostra o menu; "0" sai
        codigo, saida, erros = self.rodar("\n\n0\n")
        self.assertEqual(codigo, 0, erros)
        texto = saida.decode("utf-8")       # -X utf8: a saída é UTF-8 de verdade
        versao = [l for l in PROGRAMA.read_text(encoding="utf-8").splitlines()
                  if l.startswith("APP_VERSAO")][0].split('"')[1]
        self.assertIn("xiso-manager  ·  v" + versao, texto)
        self.assertIn("FERRAMENTA NÃO ENCONTRADA", texto)
        self.assertNotIn("Erro inesperado", texto)
        self.assertNotIn("Traceback", erros)
        self.assertTrue((self.pasta / "xiso-manager.log").is_file(), "o log fica ao lado do programa")

    def falta(self, arquivo):
        guardado = arquivo.with_name(arquivo.name + ".fora")
        arquivo.rename(guardado)
        self.addCleanup(guardado.rename, arquivo)
        codigo, saida, erros = self.rodar("\n")
        self.assertEqual(codigo, 1, erros)
        self.assertIn("não achei o Python portátil em %s." % arquivo, erros)
        self.assertIn("Descompacte a pasta inteira do xiso-manager", erros)
        self.assertIn("Aperte ENTER para fechar...", erros)
        self.assertNotIn("Fatal Python error", erros)

    def test_falta_a_biblioteca_padrao(self):
        self.falta(self.um("python3*.zip"))

    def test_falta_a_dll_do_python(self):
        self.falta(self.um("python3?*.dll"))

    def test_falta_o_unicodedata(self):
        self.falta(self.um("unicodedata.pyd"))

    def test_falta_o_python_exe(self):
        self.falta(self.python / "python.exe")


if __name__ == "__main__":
    unittest.main()
