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
        pasta = Path(pasta)
        return {p.name: p.read_bytes() for p in sorted(pasta.iterdir()) if p.is_file()} \
            if pasta.exists() else {}


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
        self.assertEqual(arquivos["Halo.iso"], b"REESCRITO\n" + ORIGINAL)

    def test_sem_apagar_o_original_fica_intacto(self):
        arquivos = self.reescrever(apagar=False)
        self.assertEqual(arquivos.get("Halo.iso"), ORIGINAL, "o original não pode mudar")
        self.assertEqual(arquivos.get("Halo.xiso.iso"), b"REESCRITO\n" + ORIGINAL)


if __name__ == "__main__":
    unittest.main()
