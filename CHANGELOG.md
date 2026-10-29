# Histórico de versões

Todas as mudanças relevantes do xiso-manager, da mais nova para a mais
antiga. As versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

| Versão | Data | Destaque |
|---|---|---|
| [4.1.0](#410--2026-10-20) | 20/10/2026 | A janela: biblioteca, ações, fila e selo do Redump |
| [4.0.1](#401--2026-10-20) | 20/10/2026 | Cancelamento limpo no Windows sem encerrar à força |
| [4.0.0](#400--2026-10-18) | 18/10/2026 | Reescrito em Rust: um executável só, sem Python |
| [3.2.9](#329--2026-10-02) | 02/10/2026 | Assistente com o extract-xiso oficial e config.json estragado |
| [3.2.8](#328--2026-10-02) | 02/10/2026 | Confirmação antes de sobrescrever e janela de erro no Windows |
| [3.2.7](#327--2026-10-02) | 02/10/2026 | Acentos no Windows e testes no GitHub Actions |
| [3.2.6](#326--2026-10-02) | 02/10/2026 | Auditoria: mais segurança com os arquivos |
| [3.2.5](#325--2026-10-01) | 01/10/2026 | iso2god-pt 0.1.5 |
| [3.2.4](#324--2026-10-01) | 01/10/2026 | extract-xiso-pt 0.2.3 |
| [3.2.3](#323--2026-09-30) | 30/09/2026 | Todos os tamanhos de disco completo no Verificar |
| [3.2.2](#322--2026-09-30) | 30/09/2026 | Cópia enxuta no Verificar e ícones XRGB |
| [3.2.1](#321--2026-09-29) | 29/09/2026 | `.dat` do Redump no Verificar |
| [3.2.0](#320--2026-09-29) | 29/09/2026 | extract-xiso-pt como padrão |
| [3.1.1](#311--2026-09-29) | 29/09/2026 | Nome e ícone dos jogos de Xbox 360 |
| [3.1](#31--2026-09-29) | 29/09/2026 | Versão para Windows e auditoria |
| [3.0](#30--2026-09-24) | 24/09/2026 | Primeira versão pública |

---

## 4.1.0 — 2026-10-20

### Correções depois do lançamento
As correções entram na própria versão (o download da release é atualizado).

- **29/10 — Emojis e caminhos:** os emojis que alguns terminais desenhavam
  com 1 coluna (🛠️, ⚙️, ⌨️...) desalinhavam o menu e as caixas; foram
  trocados por emojis que todo terminal desenha com 2 colunas. Caminhos
  digitados ou arrastados aceitam aspas e espaços escapados (`\ `), e uma
  pasta cujo nome termina com espaço é encontrada mesmo sem digitar o
  espaço. No Criar ISO, um destino que já é pasta recebe o ISO dentro dela,
  e o nome automático não leva o espaço do fim. Bibliotecas `tokio` e
  `pulldown-cmark` atualizadas (RUSTSEC-2026-0384 e RUSTSEC-2026-0414).

### Adicionado
- **A janela (`xiso-manager-gui`)**, ao lado do menu no terminal, com a
  mesma lógica por baixo:
  - **Biblioteca:** arrastar ISOs ou pastas para a janela, ou o botão
    Adicionar. Nome, ícone, Title ID e tipo de disco lidos da ISO, com cache
    (300 jogos abrem em menos de 1 segundo).
  - **Converter para GOD, Extrair, Criar ISO, Reescrever e Verificar**, com
    barra, velocidade e tempo que falta.
  - **Cancelar** apaga o que a ferramenta já tinha gravado, também no
    Windows. Fechar a janela no meio de uma tarefa cancela e espera a
    limpeza.
  - **Fila** de tarefas e **Fazer com todos os jogos da Biblioteca**.
  - **Selo** no cartão depois de Verificar: ✓ Redump, Íntegra ou Não
    confere.
  - **Configurações:** ferramentas (com a versão encontrada), pasta padrão,
    threads e desenho pelo processador (padrão, ~30 MB) ou pela placa de
    vídeo.
  - A ISO reescrita sugerida nunca é um arquivo que já existe, e nada é
    substituído sem a caixa "Substituir" marcada.
- Ícone do programa nos executáveis do Windows (menu e janela).

### Depois do lançamento
Mudanças que entram na própria 4.1.0 (o download da release é atualizado).

- **22/10 — Identidade nova:** o disco com os dados saindo em bits e virando
  arquivos, nas cores da janela. Logo da barra lateral, ícone da janela, ícone
  dos `.exe` no Windows e banner do README.

---

## 4.0.1 — 2026-10-20

### Corrigido
- **Windows:** quando o menu precisava parar a ferramenta sem Ctrl+C (ex.:
  ela não saía no prazo), encerrava à força, e a pasta ou o arquivo pela
  metade ficavam no disco. Com o extract-xiso-pt 0.2.4 e o iso2god-pt
  0.1.6, que aceitam `--cancelar-pela-entrada`, o menu pede o cancelamento
  fechando a entrada da ferramenta, e ela apaga o que criou. Ferramentas
  mais antigas continuam funcionando como antes.

### Alterado
- Usa o extract-xiso-pt 0.2.4 e o iso2god-pt 0.1.6 (ajuda e erros de uso em
  português, avisos do executável do jogo explicados).

---

## 4.0.0 — 2026-10-18

### Alterado
- **Reescrito em Rust.** O menu, as telas, as perguntas e as ações são os
  mesmos, conferidos byte a byte com a 3.2.9 pelos testes das telas. A
  diferença está na instalação: é um executável só, que abre na hora.
- **Linux:** o `xiso-manager` vem pronto na página de Releases; não precisa
  mais de Python.
- **Windows:** o pacote ficou só com o `xiso-manager.exe` e as duas
  ferramentas. Saíram o Python portátil e o lançador.

### Removido
- A versão em Python e o lançador do Windows.

---

## 3.2.9 — 2026-10-02

### Corrigido
- **Assistente com o extract-xiso oficial:** uma ISO de Xbox clássico
  mostrava a dica certa e, em seguida, "Não consegui identificar", com a lista
  errada de ações.
- **config.json estragado:** o arquivo é guardado em `config.json.invalido` e
  o menu avisa na abertura, em vez de voltar ao padrão em silêncio.

---

## 3.2.8 — 2026-10-02

### Alterado
- **Extrair, criar e reescrever com o extract-xiso oficial:** se já existir
  algo com o mesmo nome no destino, o menu pergunta antes de gravar por cima.

### Corrigido
- **Windows:** quando o programa termina com erro, a janela aberta com dois
  cliques espera o ENTER em vez de fechar antes da leitura da mensagem.

---

## 3.2.7 — 2026-10-02

### Corrigido
- **Windows (Python instalado):** títulos com acento no Analisar e acentos no
  log apareciam corrompidos. O log passa a ser sempre gravado em UTF-8.
- Linux sem UTF-8 (`LANG=C`): a análise falhava.

### Adicionado
- Testes automáticos no GitHub Actions, no Linux e no Windows: menu (Python
  3.8 e 3.14), `xiso-manager.exe` e pacote com o Python portátil.

---

## 3.2.6 — 2026-10-02

### Segurança
- **Reescrever:** com o destino digitado por outro caminho (link, montagem,
  atalho de rede), a ISO reescrita podia ser apagada no fim. O original só é
  apagado se a ISO nova existir e for outro arquivo.
- **Reescrever com o extract-xiso oficial:** se a reescrita falha ou é
  interrompida, o original volta ao nome de sempre.
- **Fechar o terminal ou a janela no meio:** o menu pede para a ferramenta
  parar, espera a limpeza e só então encerra.
- **Ctrl+C** responde na hora e vale como "não" em "Confirmar?".

### Corrigido
- Saídas inesperadas das ferramentas, linhas muito longas, acentos partidos
  entre leituras, nomes inválidos, `config.json` estragado e seleções como
  `1-999999999` não derrubam mais o programa.
- O `config.json` é gravado de forma atômica.
- **Windows:** o `xiso-manager.exe` confere os arquivos do Python portátil
  antes de abrir.

---

## 3.2.5 — 2026-10-01

### Alterado
- Usa o iso2god-pt 0.1.5: fechar a janela ou o terminal no meio da conversão
  cancela e apaga o pacote incompleto; menos uso de processador; ISOs e
  executáveis corrompidos viram erro explicado.

---

## 3.2.4 — 2026-10-01

### Alterado
- Usa o extract-xiso-pt 0.2.3: nomes acentuados copiados com os mesmos bytes
  na reescrita; fechar a janela cancela e limpa; hashes mais rápidos no
  Verificar.

---

## 3.2.3 — 2026-09-30

### Corrigido
- **Verificar:** discos completos de Xbox 360 com qualquer um dos tamanhos
  catalogados pelo Redump aparecem como "disco completo".

---

## 3.2.2 — 2026-09-30

### Corrigido
- **Verificar:** uma ISO enxuta com o nome de um jogo do Redump aparece como
  "cópia enxuta", e não como "não confere".
- **Converter para GOD / Analisar:** jogos de Xbox Original com ícone em XRGB
  passam a ter ícone (iso2god-pt 0.1.4).

---

## 3.2.1 — 2026-09-29

### Adicionado
- **Verificar com os `.dat` do Redump:** a primeira pergunta aceita o `.zip`
  ou `.dat` baixado do redump.org, que fica instalado. O resultado diz se a
  ISO é original, se não confere ou se não está no Redump.

---

## 3.2.0 — 2026-09-29

### Alterado
- **extract-xiso-pt como padrão** para extrair, listar, criar e reescrever. O
  extract-xiso oficial continua aceito pelo caminho em Configurações.
- Criar e reescrever também para Xbox 360.
- Liberar mídia do XBE só quando pedido (antes vinha ligado).

### Adicionado
- **[v] Verificar integridade:** estrutura, leitura completa, CRC32, MD5 e
  SHA-1.
- Barra de progresso real em todas as operações, pelo protocolo JSON.

### Segurança
- Pergunta antes de usar uma pasta ou ISO de destino que já existe; na
  reescrita, o original só é trocado depois que o novo foi gravado e
  conferido.

---

## 3.1.1 — 2026-09-29

### Alterado
- Usa o iso2god-pt 0.1.3: jogos de Xbox 360 convertidos aparecem com o nome
  oficial e o ícone do jogo.
- O pacote para Windows deixa de levar uma pasta `__pycache__` incluída por
  engano na 3.1.

---

## 3.1 — 2026-09-29

### Adicionado
- **Versão para Windows:** pasta pronta com o Python portátil oficial e o
  lançador `xiso-manager.exe`.

### Corrigido
- Console detectado pelo executável na raiz do disco (`default.xex` ou
  `default.xbe`), e não pelo tamanho.
- Extrair e Reescrever sem destino gravam ao lado da ISO.
- Várias ISOs extraídas para o mesmo destino ganham uma subpasta cada.
- Reescrever na mesma pasta: resumo, mensagem final e recuperação do
  original em caso de falha.
- O espaço exigido na reescrita é o da ISO nova, não o dobro.
- Ctrl+C espera o iso2god apagar a conversão incompleta.
- Extensões `.iso` e `.xiso` em qualquer combinação de maiúsculas.

---

## 3.0 — 2026-09-24

Primeira versão pública: interface de terminal em português (com opção de
inglês) que detecta o console de cada ISO e só oferece as ações que fazem
sentido.

---

### Observação sobre as versões 3.0 a 3.1.1

Os pacotes de Linux dessas versões incluíam o build oficial, sem modificação,
do [extract-xiso](https://github.com/XboxDev/extract-xiso), distribuído sob a
sua licença BSD, com o texto da licença junto. A partir da 3.2.0 o programa
usa o extract-xiso-pt e não distribui mais o extract-xiso.
