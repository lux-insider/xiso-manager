"""Testes dos achados do AUDITORIA.md: cada um monta o cenário do item e
falha na versão 3.2.5.

Rode com:  python3 -m unittest discover -s tests
"""

import os
import signal
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apoio import Ambiente, esperar_morrer, matar, sem_cor, vivo  # noqa: E402

ORIGINAL = b"ISO ORIGINAL " * 1000


class Base(unittest.TestCase):
    oficial = False

    def setUp(self):
        self.amb = Ambiente(self.id().rsplit(".", 1)[-1], oficial=self.oficial)
        self.addCleanup(self.amb.limpar)
        self.jogos = self.amb.dir / "jogos"
        self.iso = self.amb.iso("Halo.iso", ORIGINAL, pasta=self.jogos)

    def arquivos(self, pasta):
        """{nome: conteúdo} de uma pasta, com o conteúdo resumido: "ORIGINAL",
        "REESCRITO" (a ISO original reescrita) ou o tamanho."""
        pasta = Path(pasta)
        if not pasta.exists():
            return {}
        resumo = {}
        for p in sorted(pasta.iterdir()):
            if p.is_file():
                dados = p.read_bytes()
                resumo[p.name] = ("ORIGINAL" if dados == ORIGINAL else
                                  "REESCRITO" if dados == b"REESCRITO\n" + ORIGINAL else
                                  "%d bytes" % len(dados))
        return resumo


# ── 1. Arquivos ─────────────────────────────────────────────────────────────

class R1DestinoQueEhAMesmaPasta(Base):
    """R-1: o destino digitado é a pasta do original por outro caminho (um
    link). Antes, a ISO reescrita substituía o original e o menu a apagava
    em seguida: a pasta ficava vazia."""

    def setUp(self):
        super().setUp()
        self.atalho = self.amb.dir / "atalho"
        self.atalho.symlink_to(self.jogos)

    def reescrever(self, apagar):
        respostas = ["6", "c", str(self.atalho / "Halo.iso"), str(self.atalho)]
        respostas += ["s", "s"] if apagar else [""]
        # confirmar; "já existe. Substituir?" (se perguntar); ENTER; sair
        respostas += ["", "s", "", "0"]
        codigo, saida, erros = self.amb.rodar(respostas)
        self.assertEqual(codigo, 0, erros)
        return self.arquivos(self.jogos)

    def test_apagando_o_antigo_fica_a_iso_reescrita(self):
        arquivos = self.reescrever(apagar=True)
        self.assertEqual(list(arquivos), ["Halo.iso"], "a pasta não pode ficar vazia")
        self.assertEqual(arquivos["Halo.iso"], "REESCRITO")

    def test_sem_apagar_o_original_fica_intacto(self):
        arquivos = self.reescrever(apagar=False)
        self.assertEqual(arquivos.get("Halo.iso"), "ORIGINAL", "o original não pode mudar")
        self.assertEqual(arquivos.get("Halo.xiso.iso"), "REESCRITO")


class R2OficialComDestinoEmOutraPasta(Base):
    """R-2: o extract-xiso oficial renomeia o original para <nome>.old na
    pasta do original, mesmo com -d em outra pasta. O menu procurava o .old
    no destino: na falha não devolvia o nome, no sucesso não avisava."""

    oficial = True

    def reescrever(self, env=None):
        destino = self.amb.dir / "otimizados"
        # destino; apagar? não; sem patch? não; silencioso? não; confirmar; ENTER
        codigo, saida, erros = self.amb.rodar(
            ["6", "c", str(self.iso), str(destino), "", "", "", "", "", "0"], env=env)
        self.assertEqual(codigo, 0, erros)
        return sem_cor(saida)

    def test_falha_devolve_o_nome_do_original(self):
        saida = self.reescrever({"XMF_FALHAR": "1"})
        self.assertEqual(self.arquivos(self.jogos), {"Halo.iso": "ORIGINAL"})
        self.assertIn("o ISO original voltou ao nome de antes", saida)

    def test_sucesso_diz_onde_ficou_o_original(self):
        saida = self.reescrever()
        self.assertEqual(self.arquivos(self.jogos), {"Halo.iso.old": "ORIGINAL"})
        self.assertIn("O ISO original ficou em %s.old" % self.iso, saida)


LENTO = {"XMF_PASSOS": "400", "XMF_PAUSA": "0.05"}


def interromper(amb, processo, sinal, grupo=True, espera=0.3):
    """Manda o sinal quando a ferramenta já está no meio do trabalho: ao
    grupo (Ctrl+C, terminal fechado) ou só ao menu (SIGTERM)."""
    filho = amb.esperar_ferramenta()[0]
    time.sleep(espera)
    (os.killpg if grupo else os.kill)(processo.pid, sinal)
    return filho


def terminar(processo, prazo=40):
    try:
        saida, erros = processo.communicate(timeout=prazo)
    except Exception:
        os.killpg(processo.pid, signal.SIGKILL)
        saida, erros = processo.communicate()
        raise AssertionError("o menu não terminou em %ss" % prazo)
    return processo.returncode, sem_cor(saida.decode("utf-8", "replace"))


class R3OficialInterrompido(Base):
    """R-3: o extract-xiso oficial morre no Ctrl+C e deixa o arquivo novo
    pela metade com o nome do original (que ficou como .old). Antes, o menu
    não desfazia nada, porque "existe um arquivo com o nome do original"."""

    oficial = True

    def ctrl_c(self, destino=""):
        respostas = ["6", "c", str(self.iso), destino, "", "", "", "", "", "0"]
        processo = self.amb.iniciar(respostas, env=LENTO)
        interromper(self.amb, processo, signal.SIGINT)
        codigo, saida = terminar(processo)
        self.assertEqual(codigo, 0)
        return saida

    def test_mesma_pasta(self):
        saida = self.ctrl_c()
        self.assertEqual(self.arquivos(self.jogos), {"Halo.iso": "ORIGINAL"})
        self.assertIn("o ISO original voltou ao nome de antes", saida)

    def test_outra_pasta(self):
        destino = self.amb.dir / "otimizados"
        saida = self.ctrl_c(str(destino))
        self.assertEqual(self.arquivos(self.jogos), {"Halo.iso": "ORIGINAL"})
        self.assertEqual(self.arquivos(destino), {}, "a sobra pela metade sai do destino")
        self.assertIn("o ISO original voltou ao nome de antes", saida)

    def test_old_que_ja_existia_nao_e_mexido(self):
        """Com um .old de antes, o oficial se recusa e o menu não toca em nada."""
        outro = self.amb.iso("Halo.iso.old", b"OUTRO ARQUIVO", pasta=self.jogos)
        codigo, saida, _ = self.amb.rodar(["6", "c", str(self.iso), "", "", "", "", "", "", "0"],
                                          env={"XMF_FALHAR": "1"})
        self.assertEqual(self.arquivos(self.jogos),
                         {"Halo.iso": "ORIGINAL", "Halo.iso.old": "13 bytes"})
        self.assertEqual(outro.read_bytes(), b"OUTRO ARQUIVO")


# ── 2. Processos filhos ─────────────────────────────────────────────────────

GOD = ["1", "c", None, None, "", "", "", "", "", "0"]


class P1Sigterm(Base):
    """P-1: SIGTERM só para o menu (um `kill`, o desligamento). Antes, ele
    morria na hora e a ferramenta seguia convertendo escondida."""

    def test_espera_a_ferramenta_limpar_e_sai_com_sigterm(self):
        destino = self.amb.dir / "GOD"
        respostas = GOD[:2] + [str(self.iso), str(destino)] + GOD[4:]
        processo = self.amb.iniciar(respostas, env=LENTO)
        filho = interromper(self.amb, processo, signal.SIGTERM, grupo=False)
        codigo, saida = terminar(processo, prazo=20)
        try:
            self.assertEqual(codigo, -signal.SIGTERM, "sai como antes: morto por SIGTERM")
            self.assertTrue(esperar_morrer(filho, 5), "a ferramenta não pode ficar rodando")
            self.assertIn("limpou", self.amb.registro_texto())
            self.assertEqual(list(destino.rglob("*")) if destino.exists() else [], [])
        finally:
            matar(filho)

    def test_parado_numa_pergunta_sai_na_hora(self):
        processo = self.amb.iniciar([])
        time.sleep(0.5)
        os.kill(processo.pid, signal.SIGTERM)
        codigo, _ = terminar(processo, prazo=10)
        self.assertEqual(codigo, -signal.SIGTERM)

    def test_reescrita_oficial_volta_ao_nome(self):
        self.amb.config(bin_extract_xiso=str(self.amb.bin / "extract-xiso"))
        processo = self.amb.iniciar(["6", "c", str(self.iso), "", "", "", "", "", "", "0"],
                                    env=LENTO)
        interromper(self.amb, processo, signal.SIGTERM, grupo=False)
        codigo, _ = terminar(processo, prazo=20)
        self.assertEqual(codigo, -signal.SIGTERM)
        self.assertEqual(self.arquivos(self.jogos), {"Halo.iso": "ORIGINAL"})


if __name__ == "__main__":
    unittest.main()
