# xiso-manager

Gerenciador interativo para **extract-xiso** (Xbox clássico) e **iso2god** (Xbox 360).
Um arquivo só, sem dependência nenhuma além do Python que já vem no Linux.

---

## Instalação

A ferramenta é autocontida: tudo mora numa pasta só.

```bash
git clone https://github.com/lux-insider/xiso-manager.git ~/Ferramentas/xiso-manager
chmod +x ~/Ferramentas/xiso-manager/xiso_manager.py
```

Ele usa duas ferramentas externas, que não vêm junto:

- **extract-xiso** (Xbox clássico) — https://github.com/XboxDev/extract-xiso
- **iso2god** (Xbox 360) — veja [Qual iso2god](#qual-iso2god)

Coloque os binários na pasta da ferramenta ou em qualquer lugar do `PATH`.

Para chamar de qualquer lugar, adicione ao `~/.bashrc`:

```bash
alias xiso='~/Ferramentas/xiso-manager/xiso_manager.py'
```

Depois `source ~/.bashrc` uma vez. Daí em diante é só `xiso`.

> É script Python, não função de shell — use `alias`, não `source`.

### Qual iso2god

Funciona com dois iso2god diferentes e descobre sozinho qual está configurado:

- **iso2god em Rust com interface em português** (subcomandos `converter` e
  `info`) — o que o autor usa. O xiso-manager chama `converter --progresso-json`
  (a barra lê o progresso real, em JSON) e `info --json` na análise.
  O binário para Linux está na página de
  [Releases](https://github.com/lux-insider/xiso-manager/releases) (`iso2god`,
  x86_64, glibc 2.34+): baixe, dê `chmod +x` e deixe na pasta da ferramenta.
- **[iso2god-rs](https://github.com/iliazeus/iso2god-rs)** — recebe ISO e destino
  direto, com `--trim`; a análise usa `--dry-run`.

### Detecção automática dos binários

Na primeira execução ele procura `extract-xiso` e `iso2god` nesta ordem:

1. `PATH`
2. A própria pasta da ferramenta
3. `~/Downloads`, `~/Ferramentas`, `~/bin`, `~/.local/bin`
4. `/usr/local/bin`, `/usr/bin`, `/opt`
5. Pasta atual

Achando, salva o caminho e não pergunta mais. Não achando, pede o caminho na hora.
Se o binário existir mas não for executável, ele tenta o `chmod +x` sozinho.

---

## Menu

```
╭────────────────────────────────────────────────────╮
│ 💿  xiso-manager  ·  v3.0                          │
╰────────────────────────────────────────────────────╯

  ✓ extract-xiso   pronto
  ✓ iso2god        pronto

──────────────────────────────────────────────────────

  ── Xbox 360 ─────────────────────────────── iso2god ──
   [1]  🎮  Converter ISO para GOD
   [2]  🔍  Analisar ISO sem converter
  ── Os dois consoles ─── extract-xiso (lê, não cria) ──
   [3]  📦  Extrair conteúdo de ISO
   [4]  📋  Listar arquivos dentro do ISO
  ── Xbox clássico ─────────────────────── extract-xiso ──
   [5]  🛠️  Criar ISO a partir de uma pasta
   [6]  🔄  Reescrever / otimizar ISO
  ── Geral ─────────────────────────────────────────────
   [7]  🧭  Assistente (detecta o ISO e sugere o que fazer)
   [8]  🚀  Lote: processar uma pasta inteira
   [9]  ⌨️  Comando manual (avançado)
   [c]  ⚙️  Configurações
   [l]  🧾  Ver log de execução
   [s]  📖  Sobre / ajuda

   [0]  🚪  Sair
```

Nada de comando pra decorar — é tudo número. O único texto que você digita é a
pasta de destino.

---

## O que cada opção faz

| Opção | Ferramenta | O que faz |
|---|---|---|
| **1 GOD** | iso2god | Converte ISO de Xbox 360 para Games on Demand. Threads e trim (padding) configuráveis. |
| **2 Analisar** | iso2god | Mostra plataforma, Title ID, Media ID e título, sem converter. |
| **3 Extrair** | extract-xiso | Extrai todo o conteúdo do ISO para uma pasta. |
| **4 Listar** | extract-xiso | Mostra os arquivos de dentro do ISO sem extrair nada. |
| **5 Criar** | extract-xiso | Monta um ISO novo a partir de uma pasta com os arquivos do jogo. Aceita várias pastas na mesma execução. |
| **6 Reescrever** | extract-xiso | Reorganiza o ISO deixando ele otimizado e menor. |
| **7 Assistente** | ambas | Lê a assinatura do ISO, diz de qual console é e mostra só as ações que fazem sentido. A recomendada vem marcada com ⭐ e ENTER aceita ela direto. |
| **8 Lote** | ambas | Varre uma pasta inteira, separa os ISOs por console e oferece a ação certa para cada grupo. |
| **9 Manual** | ambas | Passa argumentos direto pro binário, como no terminal. |

---

## Onde os arquivos são salvos

**Converter para GOD** — a pasta de destino é obrigatória. Vai exatamente onde você mandar.

**Extrair e Reescrever** — se você digitar uma pasta, vai pra ela. Se apertar ENTER,
o extract-xiso usa o comportamento padrão dele: cria uma pasta com o nome do ISO
**no diretório onde você estava quando abriu o programa**.

Digitar o destino sempre é o caminho mais previsível. O `~` funciona
(`~/Jogos/Extraidos`), e se a pasta não existir ele cria.

---

## Detecção do tipo de ISO

Lê a assinatura `MICROSOFT*XBOX*MEDIA` nos offsets conhecidos:

| Offset | Layout | Console |
|---|---|---|
| `0x10000` | XISO / XGD cru | Xbox (ou 360, se passar de 6 GB) |
| `0x18310000` | XGD1 | Xbox |
| `0xFDA0000` | XGD2 | Xbox 360 |
| `0x2090000` | XGD3 | Xbox 360 |

O resultado aparece na coluna CONSOLE da tabela de seleção, antes de você escolher,
e é cacheado para a lista não reler os arquivos a cada redesenho.

---

## Barra de progresso

```
  📦 Extraindo [█████████████░░░░░░░░░░░]  54.1%  30.0 MiB/55.6 MiB  12.8 MiB/s  ETA 00:02
```

A parte preenchida é pintada com gradiente azul → verde, e a onda desliza a cada quadro.

O percentual vem de duas fontes, nesta ordem:

1. **Percentual impresso pela ferramenta** — se ela imprimir `NN%`, esse valor manda.
2. **Monitor de destino** — uma thread soma o tamanho do que já foi gravado na pasta
   de saída e divide pelo tamanho do ISO.

Sem TTY (saída redirecionada para arquivo), imprime só uma linha limpa no final,
sem `\r`.

---

## Segurança

- Verifica espaço em disco **antes** de gravar, com 15% de margem
- Na reescrita, exige o dobro do espaço se você não for apagar o original
- Confirmação dupla antes de apagar ISOs originais
- `Ctrl+C` encerra o processo filho de forma limpa e avisa sobre arquivos incompletos
- Cria a pasta de destino se não existir e confere permissão de escrita
- Cada arquivo é processado individualmente, então um erro não derruba o lote inteiro

---

## Idioma

Português é o padrão. Em **Configurações → [1]** alterna para inglês, e a escolha fica salva.

---

## Estrutura da pasta

Tudo fica junto, na própria pasta da ferramenta:

```
~/Ferramentas/xiso-manager/
├── xiso_manager.py           o programa
├── extract-xiso              binário Xbox
├── config.json               idioma, caminhos, threads, pasta padrão
├── xiso-manager.log          log de todas as execuções
└── xiso-manager.log.old      log anterior (rotaciona a cada 1 MiB)
```

Os três últimos são criados sozinhos no primeiro uso.

Como os caminhos são calculados a partir da localização do próprio script, você pode
mover ou copiar a pasta inteira para outra máquina, outro disco ou um pendrive, e ela
continua funcionando sem reconfigurar nada.

Se a pasta não for gravável (script instalado em `/usr/local/bin`, por exemplo), config
e log caem em `~/.config/xiso-manager/` automaticamente.

O log pode ser consultado direto no menu, opção **[l]** — erros em vermelho, avisos em âmbar.

---

## Códigos de retorno

| Código | Significado |
|---|---|
| 0 | sucesso |
| 1 | erro geral |
| 2 | ferramenta ausente |
| 3 | espaço insuficiente |
| 4 | arquivo inválido |
| 130 | interrompido com Ctrl+C |

---

## Problemas comuns

**"Permissão negada" ao rodar**
`chmod +x ~/Ferramentas/xiso-manager/xiso_manager.py`

**"bad interpreter" ou nada acontece**
`python3 ~/Ferramentas/xiso-manager/xiso_manager.py`

**Binário aparece como ✗ não encontrado**
Mova o executável para a pasta da ferramenta ou informe o caminho em
**Configurações → [2]/[3]**.

**Caixas ou tabelas desalinhadas**
Fonte do terminal sem suporte a emoji de largura dupla. Desligue as cores em
**Configurações → [6]** ou troque para uma fonte com emoji (Noto Color Emoji).

**Sem cores**
O programa respeita `NO_COLOR` e desliga sozinho quando a saída não é um terminal.

---

## Requisitos

Python 3.8+, `extract-xiso` e `iso2god`. Nenhuma biblioteca externa.

---

## Créditos

- **[extract-xiso](https://github.com/XboxDev/extract-xiso)** — escrito
  originalmente por *in* (in@fishtank.com) e mantido hoje pela comunidade
  XboxDev. É ele quem extrai, lista, cria e reescreve os ISOs; o xiso-manager
  só dá a interface.
- **[iso2god-rs](https://github.com/iliazeus/iso2god-rs)** — de Ilia Pozdnyakov
  (iliazeus), também suportado pelo xiso-manager.
- **iso2god em Rust (português)** — de lux-insider, o mesmo autor do
  xiso-manager.

---

## Licença

MIT — veja [LICENSE](LICENSE).
