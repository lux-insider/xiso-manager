# Auditoria do xiso-manager 3.2.5

Escopo: o `xiso_manager.py` e o lançador do Windows (`windows/lancador`) da
versão 3.2.5 (commit `0a02eda`), e o pacote para Windows publicado
(`xiso-manager-3.2.5-windows-x64.zip`). Os números de linha citados são dessa
versão.

O menu não grava bytes de jogo: quem extrai, cria, reescreve e converte são o
extract-xiso-pt, o extract-xiso oficial e o iso2god. O que ele decide é **com
que argumentos** chamá-los, **o que fazer com o arquivo original** antes e
depois, **como esperar** por eles e **como ler** o que eles imprimem. A
auditoria olhou para isso.

## Como foi testado

Nada usa rede nem ISO de verdade:

- `tests/falsos/ferramenta_falsa.py` imita, pelo nome com que é chamada, as
  três ferramentas: os argumentos que aceitam, os eventos do
  `--progresso-json`, o texto que imprimem, os arquivos que criam no
  destino, a demora, as falhas e a reação aos sinais. As ferramentas -pt
  cancelam e limpam no primeiro Ctrl+C, no SIGTERM e no SIGHUP (como as
  versões atuais); o extract-xiso oficial não trata sinal nenhum e, na
  reescrita, faz o que o código em C faz (`extract-xiso.c`, linhas 807-816 e
  1012-1078): renomeia o original para `<nome>.old` na pasta dele, grava o
  novo com `O_TRUNC` e só apaga o novo pela metade se falhar por erro.
- `tests/test_telas.py` conduz o menu de verdade por 19 caminhos e guarda
  tudo o que ele imprime, gravado com a 3.2.5 **antes de qualquer
  correção**; os menus também em cores, num pseudoterminal. É o que garante
  que a aparência, as opções, as teclas e os textos não mudaram.
- Os sinais são testados como chegam de verdade: o Ctrl+C e o terminal
  fechado vão para o grupo de processos (menu e ferramenta); o SIGTERM, só
  para o menu.
- `tests/test_auditoria.py` tem os testes de cada item corrigido, e
  `windows/lancador/tests/lancador.rs` os do lançador: o `.exe` de verdade,
  copiado para uma pasta com espaço e acento, ao lado de um `python.exe`
  falso. O resultado está em [Resultado da fase 2](#resultado-da-fase-2).

## Gravidade

| Nível | Critério |
|---|---|
| **Crítica** | o usuário perde a ISO original |
| **Alta** | a ISO original some com o nome dela (fica com outro nome, sem aviso); processo rodando escondido; o menu faz o contrário do que o usuário pediu |
| **Média** | trava, derruba ou suja a tela com uma entrada estranha; erro sem explicação |
| **Baixa** | caso raro ou artificial, ganho pequeno |

## O que conta como "mudar o que o usuário vê"

Não apliquei nenhuma correção que crie texto novo ou que mude a aparência,
as opções, as teclas ou a ordem das perguntas. As telas gravadas em
`tests/telas` continuam idênticas depois de todas as correções, com uma
única exceção autorizada: a mensagem de erro do verificar, que aparecia
duas vezes (L-4).

Algumas correções fazem o menu mostrar, num caso que hoje dá errado, uma
mensagem que **já existe** e que ele já mostra no caso equivalente (por
exemplo, "A reescrita falhou; o ISO original voltou ao nome de antes." quando
a reescrita é interrompida, e não só quando falha). Essas correções foram
aplicadas, porque sem elas não dá para garantir que o original não se
perde. Cada uma está listada em
[O que pode aparecer diferente](#o-que-pode-aparecer-diferente), para você
vetar se discordar.

## Resumo

| # | Gravidade | Onde | Problema | Decisão |
|---|---|---|---|---|
| R-1 | Crítica | `xiso_manager.py:2673-2710` | reescrever com o extract-xiso-pt para "outra" pasta que é a mesma por outro caminho (link, atalho), apagando o antigo: a ISO reescrita substitui o original e o menu a apaga em seguida | corrigido |
| R-2 | Alta | `xiso_manager.py:2586-2638` | extract-xiso oficial com destino em outra pasta: o original vira `<nome>.old` na pasta dele; se falhar, não volta; se der certo, ninguém avisa | corrigido |
| R-3 | Alta | `xiso_manager.py:2618-2625` | extract-xiso oficial interrompido (Ctrl+C, SIGTERM, terminal ou janela fechados): fica um arquivo pela metade com o nome do original, e o original como `.old`, sem aviso | corrigido |
| P-1 | Alta | `xiso_manager.py:3467-3478` | SIGTERM no menu: ele morre e a ferramenta continua trabalhando escondida | corrigido |
| P-2 | Alta | `xiso_manager.py:3467-3478` | terminal fechado (SIGHUP): o menu morre na hora, sem esperar a ferramenta nem desfazer a reescrita | corrigido |
| L-1 | Alta | `xiso_manager.py:1806-1836, 1875-1877` | um evento JSON com um campo de tipo errado derruba a leitura: "Erro inesperado" e a ferramenta continua rodando escondida | corrigido |
| E-1 | Alta | `xiso_manager.py:1926-1935` | Ctrl+C em "Confirmar? [S/n]" confirma e a operação começa | corrigido |
| L-2 | Média | `xiso_manager.py:1789-1803` | linha enorme sem quebra: memória sem limite e tempo quadrático (32 MiB: 505 s e 407 MiB) | corrigido |
| L-3 | Média | `xiso_manager.py:1797` | a leitura decodifica cada pedaço de 4 KiB sozinho: um acento partido entre dois pedaços vira `��` | corrigido |
| L-4 | Média | `xiso_manager.py:1806-1828` | a mensagem de erro do verificar aparece duas vezes | corrigido (pedido) |
| P-3 | Média | `xiso_manager.py:1851-1874` | depois do Ctrl+C o menu para de ler a saída: uma ferramenta presa escrevendo no pipe cheio leva 35 s e morre com SIGKILL, sem limpar | corrigido |
| A-1 | Média | `xiso_manager.py:42-46, 205-210` | nome de arquivo com bytes que não são UTF-8 (Latin-1, comum em cópias do Windows): o navegador cai em "Erro inesperado" e o log suja a tela | corrigido |
| L-5 | Média | `xiso_manager.py:2883-2911` | `info --json` com um JSON inesperado derruba a análise | corrigido |
| L-6 | Média | `xiso_manager.py:1700, 2891-2904` | texto vindo das ferramentas (o título do jogo, lido da ISO) vai cru para o terminal: sequências de escape, controles C1, inversão de direção | corrigido |
| E-2 | Média | `xiso_manager.py:1985-1994` | um intervalo como `1-999999999` na seleção trava o menu e esgota a memória | corrigido |
| W-1 | Média | `xiso_manager.py:34-62` | Windows: fechar a janela no meio de uma reescrita com o extract-xiso oficial deixa o original como `.old` | corrigido |
| LZ-1 | Média | `windows/lancador/src/main.rs:51-61` | falta um arquivo do Python portátil: a janela fecha sem dar para ler o motivo | corrigido |
| C-1 | Baixa | `xiso_manager.py:161-169` | o `config.json` é truncado e regravado no lugar: uma queda no meio deixa o arquivo vazio | corrigido |
| C-2 | Baixa | `xiso_manager.py:116-138, 2045` | um caminho com caractere nulo no `config.json` derruba o navegador | corrigido |
| E-3 | Baixa | `xiso_manager.py:1987, 1995, 3027, 3098` | dígitos que não são 0-9 (`²`, `³`) ou um número de milhares de algarismos na escolha viram "Erro inesperado" | corrigido |
| P-4 | Baixa | `xiso_manager.py:1787` | a ferramenta herda a entrada do terminal e pode disputar o teclado com o menu | corrigido |
| W-2 | Média | `xiso_manager.py:1650, 3273` | a saída do `info --json` é lida na codificação padrão do sistema: rodando o `.py` direto no Windows, o título "Jogo de Ação" vira "Jogo de AÃ§Ã£o"; num Linux com `LANG=C`, a análise falha | corrigido (achado pelo CI no Windows) |
| W-3 | Baixa | `xiso_manager.py:241-258` | o log é gravado na codificação padrão do sistema e lido como UTF-8: rodando o `.py` direto no Windows, "Ver log" mostra "Sess�o iniciada" | corrigido (achado pelo CI no Windows) |
| V-1 | Média | `xiso_manager.py:2997-3006` | assistente com o extract-xiso oficial: ISO de Xbox clássico cai em "Não consegui identificar" | corrigido depois (pedido) |
| V-2 | Média | `xiso_manager.py:2326, 2490, 2596` | o extract-xiso oficial grava por cima sem perguntar (extrair, criar, reescrever para outra pasta) | corrigido depois (pedido) |
| V-3 | Baixa | `xiso_manager.py:1916-1918, 1930-1932` | Ctrl+C nas outras perguntas devolve a resposta padrão e o fluxo segue | **só descrito** (muda o fluxo) |
| V-4 | Baixa | `windows/lancador/src/main.rs:69-75` | erro fatal do programa (código 1) no Windows: a janela fecha antes de dar para ler | corrigido depois (pedido) |
| V-5 | Baixa | `xiso_manager.py:141-158` | `config.json` ilegível vira o padrão sem aviso e é regravado na próxima mudança | corrigido depois (pedido) |
| V-6 | Baixa | — | Windows: `taskkill /F` no menu deixa a ferramenta órfã | **só descrito** |
| D-1 | — | pacote Windows | o Python portátil poderia encolher de 24,6 MB para 8,7 MB | **só descrito** (pedido) |
| D-2 | Baixa | `xiso_manager.py:1272-1273` | antes de começar, o menu mede a pasta de destino inteira (no GOD, pode ser a raiz de um HD) | **só descrito** |
| D-3 | Baixa | `xiso_manager.py:2019-2040` | o navegador lê o cabeçalho de todas as ISOs da pasta na primeira vez | **só descrito** |

---

## 1. Arquivos: a ISO original nunca se perde

### R-1 (Crítica) — "outra" pasta que é a mesma: o original é substituído e depois apagado

`xiso_manager.py:2673-2710` (`_reescrever_pt`). Decide se o destino é a pasta
do original comparando os **textos** dos caminhos (`normcase`). Quando o
destino é a mesma pasta por outro caminho — um link simbólico, uma pasta
montada em dois lugares, um atalho de rede no Windows, um nome curto 8.3 —, o
menu acha que é outra pasta:

1. pergunta "`<destino>/Halo.iso` já existe. Substituir?" (é o próprio
   original);
2. com "sim", chama `reescrever Halo.iso -s <destino>/Halo.iso
   --sobrescrever`: o extract-xiso-pt grava a imagem nova e a renomeia por
   cima do original (pelo outro caminho);
3. como o usuário pediu para apagar o antigo, o menu faz
   `os.remove(origem)`, e apaga a imagem nova.

*Cenário (reproduzido com o extract-xiso-pt de verdade):* `~/Jogos` é um link
para `/mnt/hd/Jogos`. O navegador devolve o caminho resolvido
(`/mnt/hd/Jogos/Halo.iso`, `xiso_manager.py:2101, 2118`), e o usuário digita
`~/Jogos` como destino. Resultado: a pasta fica **vazia** e o resumo diz
"Sucesso 1".

*Correção:* decidir "mesma pasta" pela identidade da pasta
(`os.path.samefile`), não pelo texto. E, antes de apagar o original, conferir
que a imagem nova existe e não é o mesmo arquivo.

### R-2 (Alta) — extract-xiso oficial: o `.old` fica na pasta do original

`xiso_manager.py:2586-2638`. O `-r` do extract-xiso oficial **sempre**
renomeia o original para `<nome>.old` **na pasta do original**, mesmo com
`-d` apontando para outra pasta (`extract-xiso.c:807-816`). O menu procura o
`.old` no destino (`antigo = saida + ".old"`, linha 2589) e só tenta desfazer
o nome quando é a mesma pasta (linha 2620). Com destino em outra pasta
(reproduzido):

- **falha:** o original fica como `Halo.iso.old` e nada é dito;
- **sucesso sem apagar:** o original vira `Halo.iso.old` sem o aviso "O ISO
  original ficou em", que aparece na mesma pasta.

*Correção:* `antigo = origem + ".old"`, desfazer o nome em qualquer pasta e
mostrar o aviso que já existe quando o `.old` ficar.

### R-3 (Alta) — extract-xiso oficial interrompido: arquivo pela metade com o nome do original

`xiso_manager.py:2618-2625`. O extract-xiso oficial não trata sinal: o
Ctrl+C, o SIGTERM, o terminal ou a janela fechados o matam no meio, e o
arquivo novo pela metade **fica** (ele só se apaga em erro,
`extract-xiso.c:1078`). Como o arquivo pela metade tem o nome do original, a
condição `not os.path.exists(origem)` falha e o menu não desfaz nada.
Reproduzido com os três sinais: fica `Halo.iso` (pela metade) e
`Halo.iso.old` (o original), com no máximo "Pode haver arquivos incompletos
no destino". Quem apagar o `.old` achando que é lixo perde o jogo.

*Correção:* lembrar, antes de rodar, se já existia um `.old` e o tamanho e a
data do original. Se a reescrita não terminou bem e apareceu um `.old` que
não existia, com o tamanho e a data do original, ele é o original: volta ao
nome (`os.replace`, por cima do arquivo pela metade), com a mensagem que já
existe. Se o arquivo novo foi para outra pasta e não existia antes, a sobra
pela metade é apagada.

### Prova caso a caso, depois das correções

| Ação | Ferramenta | O que acontece com o original |
|---|---|---|
| extrair, listar, verificar, analisar, converter para GOD | todas | só é lido: os argumentos nunca o colocam como saída (`-d`/destino é pasta à parte) |
| criar | todas | não há original (a origem é uma pasta, só lida) |
| reescrever, mesma pasta, sem apagar | extract-xiso-pt | intocado; o novo vai para `<nome>.xiso.iso` (com pergunta se já existir) |
| reescrever, mesma pasta, apagando | extract-xiso-pt | `--substituir`: a ferramenta grava à parte, relê, e só então troca o original num `rename` atômico; falha ou sinal no meio deixam o original intacto |
| reescrever, outra pasta, apagando | extract-xiso-pt | apagado pelo menu **só** depois do sucesso, com a imagem nova existindo e não sendo o mesmo arquivo (R-1). "Outra pasta" que é a mesma vira o caso acima |
| reescrever, qualquer pasta | extract-xiso oficial | a ferramenta o renomeia para `<nome>.old`; se a reescrita não terminar bem (erro, Ctrl+C, SIGTERM, terminal fechado, janela fechada no Windows), o menu o devolve ao nome (R-2, R-3, P-1, P-2, W-1); com sucesso, fica como `.old` com aviso, ou é apagado pela ferramenta se o usuário pediu (`-D`) |

O único caso em que o menu não consegue desfazer o nome é quando **ele
mesmo** morre sem aviso no meio de uma reescrita com o extract-xiso oficial
(SIGKILL, queda de energia, travamento do sistema). Mesmo aí o original não
se perde: fica inteiro como `<nome>.iso.old` ao lado da ISO. Com o
extract-xiso-pt, nem isso: o original nunca muda de nome.

### Caminhos com espaço, acento e links

- **Espaço e acento:** os comandos vão como lista, sem shell, então nada é
  reinterpretado. No Windows o lançador passa `-X utf8`, e o Python fala UTF-8
  com as ferramentas.
- **Nome que não é UTF-8 no Linux** (Latin-1): ver A-1.
- **Links simbólicos:** o navegador resolve os links da pasta escolhida; o
  destino digitado não é resolvido. Era isso que abria o R-1, agora decidido
  pela identidade do arquivo.

## 2. Processos filhos

### P-1 (Alta) — SIGTERM: a ferramenta continua escondida

`xiso_manager.py:3467-3478`. O menu não trata SIGTERM: ele morre na hora
(código -15) e a ferramenta, que não recebeu sinal nenhum, continua. Como o
pipe ficou sem leitor e as ferramentas -pt ignoram falha de escrita, ela
segue até o fim **em segundo plano**: reproduzido com uma conversão para GOD,
que continuou criando o pacote depois de o menu sair. Com o extract-xiso
oficial na reescrita, o original fica como `.old` (R-3).

*Correção:* um tratador de SIGTERM. Se há uma ferramenta rodando, ele a
avisa (SIGTERM, que para as -pt é "cancele e limpe"), o menu espera ela
terminar, desfaz a reescrita se for o caso, e só então sai, com o mesmo
código de antes (morto por SIGTERM). Sem ferramenta rodando, sai na hora.

### P-2 (Alta) — terminal fechado (SIGHUP)

Fechar o terminal manda SIGHUP para o menu e para a ferramenta. As -pt
cancelam e limpam sozinhas; o menu morre na hora, então ninguém desfaz a
reescrita do extract-xiso oficial (R-3) e ninguém espera a ferramenta
terminar. *Correção:* o mesmo tratador do P-1, com a saída desligada (o
terminal não existe mais). Com `nohup`, o SIGHUP chega ignorado e continua
ignorado.

### P-3 (Média) — Ctrl+C com o pipe cheio: 35 s e SIGKILL

`xiso_manager.py:1851-1874`. Depois do Ctrl+C o menu espera a ferramenta
limpar o que criou (o certo), mas para de ler a saída. Uma ferramenta que
estava imprimindo muito (uma listagem grande) fica presa escrevendo no pipe
cheio e nunca chega ao ponto de cancelar: o menu espera 30 s, manda SIGTERM,
espera mais 5 s e mata com SIGKILL, **sem limpeza nenhuma**. Reproduzido:
35,0 s parado. *Correção:* continuar esvaziando o pipe (descartando, como
hoje) enquanto espera.

### P-4 (Baixa) — a ferramenta herda o teclado

`xiso_manager.py:1787`. A ferramenta herda a entrada padrão do menu. Nenhuma
das três lê o teclado nos comandos que o menu usa, mas no comando manual um
`iso2god` sem subcomando abre o assistente dele, que fica esperando o
teclado enquanto o menu mostra a barra parada; e o que o usuário digita para
o menu vai para a ferramenta. *Correção:* `stdin=subprocess.DEVNULL` nos
três lugares que rodam uma ferramenta: a execução normal, o `info --json`
da análise e a sondagem `--help` do iso2god.

### W-1 (Média) — Windows: janela fechada

Ao fechar a janela, o Windows avisa todos os processos do console. As
ferramentas -pt cancelam, limpam e saem (têm uns 5 s). O menu (Python) morre
na hora, então a reescrita do extract-xiso oficial não é desfeita.
*Correção:* um tratador de console (`SetConsoleCtrlHandler`, via `ctypes`)
para fechar, sair da sessão e desligar: espera a ferramenta sair (até 4 s) e
desfaz a reescrita pendente. Testado no Linux chamando a mesma função; o
tratador em si só roda no Windows.

### O lançador e o Ctrl+C

`windows/lancador/src/main.rs:13-33`. O tratador do lançador diz "tratei"
para todos os eventos: o Ctrl+C chega ao Python e à ferramenta, e o
lançador não morre no meio; fechar a janela encerra o lançador, mas cada
processo recebe o seu próprio aviso. Está certo e fica como está.

## 3. Leitura da saída das ferramentas

### L-1 (Alta) — JSON com tipo errado: "Erro inesperado" e processo escondido

`xiso_manager.py:1806-1836`. Os campos dos eventos são usados sem conferir o
tipo: `"bytes": "muito"` divide texto por número, `"mensagem": 123` soma
texto com número, `"hashes": "x"` chama `.get` em texto. A exceção sai do
laço de leitura para o `except Exception` (linha 1875), que mostra "Erro
inesperado" e **não espera nem encerra a ferramenta**: ela continua rodando
escondida, presa no pipe (reproduzido). Numa conversão em lote, a próxima ISO
começa com a anterior ainda viva.

*Correção:* cada campo é conferido (número é número, texto é texto, lista é
lista); um evento estranho é ignorado. E, se mesmo assim a leitura falhar, a
ferramenta é avisada e esperada, como no Ctrl+C.

### L-2 (Média) — linha enorme: memória sem limite e tempo quadrático

`xiso_manager.py:1789-1803`. A saída é acumulada com `buffer += ...` e, a
cada pedaço de 4 KiB, a quebra de linha é procurada desde o começo do
buffer. Uma linha sem quebra (um programa com defeito, um arquivo binário
listado) cresce sem limite e é varrida de novo a cada pedaço:

| Linha sem quebra | Tempo | Memória do menu |
|---|---|---|
| 8 MiB | 34 s | 109 MiB |
| 16 MiB | 128 s | 214 MiB |
| 32 MiB | 505 s | 407 MiB |

*Correção:* procurar a quebra só no pedaço novo e guardar no máximo 256 KiB
de uma linha (a tela mostra 58 colunas; os eventos JSON têm poucas centenas
de bytes, e a folga cobre um evento longo). Com 32 MiB: 0,4 s e o pico de
memória do menu abaixo de 120 MiB.

### L-3 (Média) — acento partido entre dois pedaços

`xiso_manager.py:1797`. Cada pedaço de 4 KiB é decodificado sozinho: um
caractere UTF-8 de dois bytes que cai na divisa vira `��` (reproduzido). Na
listagem de um jogo com nomes acentuados, uns nomes saem corrompidos.
*Correção:* decodificador incremental (`codecs`), que guarda o pedaço do
caractere para o próximo pedaço.

### L-4 (Média) — mensagem de erro do verificar duas vezes

O `verificar --progresso-json` do extract-xiso-pt manda o erro como evento
JSON (`{"evento":"erro"}`) e também como texto no stderr ("❌ ..."). O menu
junta os dois e mostra `erro: <mensagem>` e `❌ <mensagem>`. *Correção:* uma
linha de texto que repete a mensagem de um evento `erro` (com o `❌` ou o `X`
do console antigo do Windows na frente) não é mostrada de novo, e vice-versa.
Fica a primeira, que é a do evento JSON.

### L-5 (Média) — `info --json` inesperado derruba a análise

`xiso_manager.py:2883-2911`. Um JSON que não é objeto (`[1, 2]`) ou um campo
de tipo errado (`"tamanho_volume": "7 GB"`) viram "Erro inesperado" e a
análise das outras ISOs da seleção para. *Correção:* conferir os tipos e
tratar o que não confere como ausente.

### L-6 (Média) — texto da ISO cru no terminal

`xiso_manager.py:1700, 2891-2904`. O título do jogo vem de dentro da ISO
(XDBF, certificado do XBE) e o `info --json` o entrega como está: o menu o
imprimia cru, com sequências de escape (limpar a tela, trocar o título da
janela). A limpeza das linhas de saída (`RE_CONTROLE`) também deixa passar os
controles C1 (`\x9b` é o início de uma sequência em alguns terminais) e os
de inversão de direção do texto. O texto dos eventos do `--progresso-json`
(mensagens, avisos e o nome do jogo no Redump) também passava cru, porque o
JSON traz os controles escritos como `\u001b`. *Correção:* `RE_CONTROLE`
tira as sequências de escape inteiras, os controles C1 e os de direção do
texto; os valores do `info --json` e o texto dos eventos passam pela mesma
limpeza, sem mexer nos espaços (as telas gravadas não mudam).

### W-2 (Média) — saída do `info --json` na codificação do sistema

`xiso_manager.py:1650, 3273` (linhas da 3.2.6). O iso2god escreve em UTF-8,
mas o menu lia o `info --json` (e o `--help`) com `text=True`, que usa a
codificação padrão do sistema. O pacote do Windows escapa porque o lançador
roda o Python com `-X utf8`; quem roda o `xiso_manager.py` direto no Windows
(cp1252) via "Jogo de AÃ§Ã£o" no título, e num Linux com `LANG=C` (ASCII) a
análise falhava com "'ascii' codec can't decode". Achado pelo GitHub
Actions no Windows. *Correção:* ler essas saídas como UTF-8, trocando o que
não for UTF-8 por "�", como a leitura das outras ferramentas já fazia.

## 4. Configuração e entrada

### E-1 (Alta) — Ctrl+C em "Confirmar? [S/n]" confirma

`xiso_manager.py:1926-1935`. `perguntar_sim_nao` devolve a resposta padrão
quando recebe Ctrl+C. Na confirmação final de toda ação o padrão é "sim":
apertar Ctrl+C para desistir **começa a operação** (reproduzido). Numa
reescrita com "apagar o antigo", isso apaga os originais depois do sucesso.
*Correção:* Ctrl+C na confirmação vale "não", o que leva à mensagem que já
existe, "Operação cancelada pelo usuário.".

### E-2 (Média) — intervalo enorme na seleção

`xiso_manager.py:1985-1994`. `1-3000000` gera 3 milhões de textos de
"inválido" (1 s); `1-999999999` leva minutos, esgota a memória e imprime uma
linha gigante. *Correção:* percorrer só a parte do intervalo que existe; o
resto vira um único item "a-b" na lista de inválidos. Para intervalos de até
1000 números a mensagem continua exatamente igual.

### E-3 (Baixa) — dígitos que não são 0-9

`xiso_manager.py:1987, 1995, 3027, 3098`. `"²".isdigit()` é verdadeiro, mas
`int("²")` falha: vira "Erro inesperado". O mesmo com um número de mais de
4300 algarismos, que o `int` do Python 3.11 em diante recusa. *Correção:*
`isdecimal()`, que só aceita algarismos decimais (de qualquer escrita, como
`٣`, que o `int` lê como 3), e a conversão protegida; "²" passa a ser
"Entrada ignorada" ou "Opção inválida", como qualquer outra entrada
inválida.

### A-1 (Média) — nome de arquivo que não é UTF-8

`xiso_manager.py:42-46, 205-210`. No Linux, um nome gravado em Latin-1
(`A\xe7\xe3o.iso`) chega ao Python como texto com "substitutos". A saída
padrão num terminal UTF-8 normal (`LANG=pt_BR.UTF-8`) é estrita: imprimir o
nome derruba a tela inteira do navegador em "Erro inesperado: surrogates not
allowed", toda vez, e não dá para escolher nada naquela pasta (reproduzido).
O log tem o mesmo problema e, quando falha, despeja "--- Logging error ---"
com um traceback na tela. No Windows a saída já usa `errors="replace"`.
*Correção:* o mesmo `errors="replace"` no Linux (o nome sai com `?` no
lugar do byte inválido) e `errors="backslashreplace"` no arquivo de log. O
formato do log não muda.

### C-1 (Baixa) — config.json regravado no lugar

`xiso_manager.py:161-169`. `open(..., "w")` trunca o arquivo antes de
escrever: uma queda de energia ou um SIGKILL no meio deixa o `config.json`
vazio, e as configurações voltam ao padrão. *Correção:* gravar num
temporário na mesma pasta (`config.json.tmp`), levá-lo ao disco (`fsync`) e
trocar com `os.replace`. Se o `config.json` for um link, o arquivo trocado é
o de verdade, e o link continua. O formato do arquivo não muda.

### C-2 (Baixa) — caractere nulo num caminho do config

`xiso_manager.py:116-138, 2045`. Um `config.json` com `"pasta_padrao":
"a\u0000b"` (JSON válido) faz o navegador cair em "Erro inesperado:
embedded null byte" em toda ação. *Correção:* `_validar_config` recusa
caminhos com caractere nulo, como já recusa tipo errado.

### W-3 (Baixa) — log na codificação do sistema

`xiso_manager.py:241-258` (linhas da 3.2.6). O log era gravado na
codificação padrão do sistema, e a tela "Ver log" o lê como UTF-8: fora do
modo UTF-8 (o `.py` rodado direto no Windows), "Sessão iniciada" aparecia
como "Sess�o iniciada"; num Linux com `LANG=C`, como "Sess\xe3o iniciada".
Achado pelo GitHub Actions no Windows. *Correção:* o log é sempre gravado
em UTF-8, como no Linux comum e no pacote do Windows. O formato das linhas
não muda.

### Verificado e sem problema

- **config.json estragado:** JSON inválido, lista, valores de tipo errado,
  `threads_god` absurdo e idioma inexistente caem no padrão
  (`_validar_config`, `carregar_config`). Ver V-5 para o aviso que falta.
- **Programa que não existe mais:** na abertura o menu procura de novo
  (`resolver_binarios`); cada ação confere antes de rodar
  (`exigir_binario`); se o arquivo sumir entre a conferência e a execução,
  a mensagem "Binário não encontrado na hora de executar." já existe.
- **Fim da entrada (Ctrl+D):** todas as perguntas tratam `EOFError`.

## 5. Lançador do Windows

### LZ-1 (Média) — falta um arquivo do Python portátil

`windows/lancador/src/main.rs:51-61`. O lançador confere só o `python.exe`.
Se faltar a biblioteca padrão (`python314.zip`), a DLL (`python314.dll`) ou
o `unicodedata.pyd` (o menu importa), o Python falha com "Fatal Python error"
ou "ModuleNotFoundError" e, aberto com dois cliques, a janela fecha antes de
dar para ler. É o que acontece com uma descompactação pela metade ou um
antivírus que leva um arquivo para a quarentena. Simulado no Linux com um
`python.exe` falso.

*Correção:* conferir também esses arquivos e, faltando algum, a mensagem que
já existe ("não achei o Python portátil em `<arquivo>`. Descompacte a pasta
inteira...") com o caminho do arquivo que falta, esperando ENTER. A versão
(`python314`) vem dos nomes que estão na pasta, então o lançador não muda
quando o Python do pacote mudar; sobras de outra versão não atrapalham, e
uma instalação normal copiada para a pasta (`Lib\`, `DLLs\`) também serve.
Sem nenhum arquivo da versão, a mensagem aponta a pasta `python`.

### Verificado e sem problema

- **Localizar o Python:** sempre `python\python.exe` ao lado do `.exe`, pelo
  caminho real do executável (`current_exe`), não pela pasta atual.
- **Espaço e acento no caminho:** `Command` monta a linha de comando com as
  aspas certas, e o Python recebe a linha de comando em UTF-16.
- **Código de saída:** passa direto (`s.code()`, sempre presente no
  Windows).
- `cargo clippy --all-targets` sem avisos no Linux e em
  `x86_64-pc-windows-msvc`.

## 6. Desempenho e leveza

### D-1 (só descrito, pedido) — o Python portátil

Medido no pacote publicado (`xiso-manager-3.2.5-windows-x64.zip`, 13,4 MB,
Python 3.14 embeddable):

| | Pasta `python\` | Zip do pacote |
|---|---|---|
| Hoje: 37 arquivos | 24,6 MB | 13,3 MB |
| Só os 10 arquivos que o menu usa | 12,2 MB | 8,4 MB |
| E a biblioteca padrão só com os 70 módulos usados | 8,7 MB | 4,9 MB |

Os 10 arquivos: `python.exe`, `python314.dll`, `python314.zip`,
`python314._pth`, `vcruntime140.dll`, `vcruntime140_1.dll`,
`unicodedata.pyd`, `_ctypes.pyd` e `libffi-8.dll` (cores do console) e o
`LICENSE.txt`. Os maiores dispensáveis: `libcrypto-3.dll` (6,1 MB),
`sqlite3.dll` (1,5 MB), `libssl-3.dll` (1,3 MB), `python.cat` (0,6 MB),
`_zstd.pyd`, `_decimal.pyd`, `pyexpat.pyd`, `_ssl.pyd` e outros 18 `.pyd`
e `.dll`, além do `pythonw.exe`.

A lista de módulos foi tirada rodando o menu no Linux (Python 3.13) e
trocando os módulos do Linux pelos equivalentes do Windows. Antes de
publicar um pacote enxuto, é preciso rodar cada opção do menu no Windows: um
módulo que só é importado num caminho raro faltaria só ali.

### L-2 e L-3 — a leitura da saída

São os itens de desempenho com efeito real (tempo quadrático e memória sem
limite numa linha longa); estão na seção 3.

### D-2 (Baixa, só descrito) — medir a pasta de destino antes de começar

`xiso_manager.py:1272-1273`. Para a barra de progresso, o menu soma o tamanho
de tudo o que já existe no destino antes de começar (até 20 mil arquivos). No
GOD o destino costuma ser a raiz de um HD externo: com o disco frio, isso
atrasa o início em alguns segundos. O iso2god informa o progresso por JSON,
então a soma nem é usada depois do primeiro evento. Tirar a soma nesse caso
não muda a tela; fica descrito por ser pequeno.

### D-3 (Baixa, só descrito) — o navegador lê o cabeçalho de todas as ISOs

`xiso_manager.py:2019-2040`. A coluna CONSOLE lê até quatro pontos de cada
ISO da pasta na primeira vez (depois fica em cache). Numa pasta com milhares
de ISOs num HD, isso leva dezenas de segundos. Mudar exigiria mostrar a
tabela sem a coluna ou em etapas, o que muda a tela.

### Trabalho repetido no menu

Medido e sem efeito real: redesenhar o menu, montar a lista de opções e
consultar `eh_pt()` a cada volta custam microssegundos. O `cortar()` é
quadrático, mas para nos 58 caracteres da tela. O cache da detecção do tipo
de ISO já evita reler o disco a cada redesenho.

## 7. Só descritos: mudariam o que o usuário vê

### V-1 (Média) — assistente com o extract-xiso oficial

`xiso_manager.py:2997-3006`. O `else` que mostra "Não consegui identificar"
está ligado ao `if eh_pt() and tipo in (...)`, e não à detecção do tipo. Com
o extract-xiso oficial configurado, uma ISO de Xbox clássico (ou de 360)
mostra a dica de tipo certa e logo em seguida "Não consegui identificar", com
a lista genérica de ações no lugar da lista certa. Corrigir muda a tela do
assistente.

*Corrigido depois, a pedido* (`xiso_manager.py:3459`): a lista genérica e o
"Não consegui identificar" ficam só para o tipo que não foi identificado, e
o "Verificar" entra depois, só com o extract-xiso-pt (o oficial não tem).
Com o extract-xiso-pt, a tela do assistente é a mesma de antes.

### V-2 (Média) — o extract-xiso oficial grava por cima sem perguntar

`xiso_manager.py:2326, 2490, 2596`. Com o extract-xiso-pt, o menu pergunta
antes de gravar por cima ("já existe. Substituir?", "já tem arquivos. Extrair
por cima?"). Com o oficial, não: ele abre a saída com `O_TRUNC` e substitui
sem aviso um arquivo com o mesmo nome no destino (ao criar, ao reescrever
para outra pasta) e os arquivos da pasta de destino ao extrair. Não afeta a
ISO de origem, mas pode levar outra cópia que o usuário tinha no destino. A
correção é fazer as mesmas perguntas no caminho do oficial, o que é uma
pergunta nova nesse fluxo.

*Corrigido depois, a pedido* (`xiso_manager.py:2637, 2801, 2965`): as
mesmas perguntas, com os mesmos textos e o mesmo padrão "não", valem agora
para as duas ferramentas. Com "não", o item é pulado ("pulado") e a
ferramenta nem roda; com "sim", o oficial grava por cima como sempre fez.
Na reescrita, a pergunta só aparece com o destino em outra pasta: na mesma
pasta (por qualquer caminho, `_mesma_pasta`), o nome no destino é o do
próprio original, que o oficial renomeia para `.old` antes de gravar. Um
`.old` que já exista não precisa de pergunta: o oficial se recusa a
reescrever (`extract-xiso.c:810`).

### V-3 (Baixa) — Ctrl+C nas outras perguntas

`xiso_manager.py:1916-1918, 1930-1932`. Fora da confirmação final (E-1), o
Ctrl+C numa pergunta devolve a resposta padrão e o fluxo continua (na
pergunta seguinte, ou de volta ao menu, quando é o navegador). Não começa
nada sozinho, porque a confirmação final agora vale "não" no Ctrl+C, mas não
é o que se espera do Ctrl+C. Mudar para "cancela a ação inteira" mostraria
"Operação cancelada" onde hoje não aparece.

### V-4 (Baixa) — lançador: erro fatal com a janela fechando

`windows/lancador/src/main.rs:69-75`. Se o programa terminar com erro fatal
(código 1, "❌ <motivo>"), a janela aberta com dois cliques fecha na hora. A
correção seria esperar ENTER quando o código for 1 e o console for só do
lançador (`GetConsoleProcessList`); é uma pausa nova.

*Corrigido depois, a pedido* (`windows/lancador/src/main.rs:37, 58, 165`):
quando o programa termina com erro e o console é só do lançador (aberto com
dois cliques), ele mostra "Aperte ENTER para fechar...", a mesma linha de
quando falta um arquivo do Python, e espera. Vale também para uma queda do
Python com outro código, que antes fechava a janela sem mensagem nenhuma;
nesse caso vem antes "xiso-manager: o Python fechou com erro (código
0x…)". Saída normal (0) e Ctrl+C (130, ou o `STATUS_CONTROL_C_EXIT` do
Windows) fecham na hora. Na pausa, o Ctrl+C também fecha. Num terminal
aberto antes (`cmd`, PowerShell), o console tem outros processos e a janela
não fecha: não há pausa.

### V-5 (Baixa) — config.json ilegível

`xiso_manager.py:141-158`. Um `config.json` com JSON inválido vira o padrão
sem aviso, e na próxima mudança é regravado: os caminhos configurados se
perdem. A correção seria guardar uma cópia (`config.json.invalido`) e avisar;
é um aviso novo.

*Corrigido depois, a pedido* (`xiso_manager.py:164, 191, 205`): um
`config.json` com JSON inválido, que não é um objeto ou que não é UTF-8 é
copiado inteiro para `config.json.invalido`, ao lado, e na abertura o menu
avisa e espera o ENTER: "Não consegui ler o config.json: as configurações
voltaram ao padrão." e "O arquivo antigo ficou em <caminho>". O log também
registra. Um erro de leitura do disco continua como antes (o padrão, sem
aviso).

### V-6 (Baixa) — `taskkill /F` no Windows

Matar o menu à força no Windows deixa a ferramenta órfã, sem pai e sem
leitor. Um Job Object com "matar ao fechar" resolveria, mas mataria a
ferramenta sem limpeza, e piora o caso de fechar a janela, em que a
ferramenta precisa dos seus 5 s para apagar o que criou. Fica descrito.

## O que pode aparecer diferente

Nenhum texto novo, nenhuma opção, tecla ou pergunta nova. Das 20 telas
gravadas, só a do verificar com erro mudou (L-4, pedido). O que muda é
**quando** algumas mensagens que já existiam aparecem, sempre num caso que
antes dava errado. Se discordar de alguma, é só dizer qual.

Depois, a pedido, o V-2 e o V-4: uma pergunta e uma pausa que já existiam
passam a aparecer em mais casos, e o lançador ganhou uma linha nova, só para
uma queda do Python. Depois deles, o V-1 e o V-5: o assistente com o
extract-xiso oficial passa a mostrar a lista certa, e um `config.json`
ilegível ganha um aviso novo, de duas linhas (as quatro últimas linhas da
tabela).

| Item | Caso | Antes | Agora |
|---|---|---|---|
| L-4 | erro no verificar | a mensagem duas vezes ("erro: …" e "❌ …") | uma vez ("erro: …") |
| R-1 | reescrever com o extract-xiso-pt para a pasta do original digitada por outro caminho (link, montagem, atalho de rede, nome 8.3) | "… já existe. Substituir?" sobre o próprio original; apagando o antigo, a pasta ficava vazia com "Sucesso 1" | as mesmas perguntas e mensagens de quando o destino é a pasta do original |
| R-2 | reescrever com o extract-xiso oficial para outra pasta | sucesso e falha sem nada sobre o original, que ficava como `.old` | sucesso sem apagar: "O ISO original ficou em `<pasta do original>/<nome>.old`"; falha: "A reescrita falhou; o ISO original voltou ao nome de antes." |
| R-3 | reescrita com o extract-xiso oficial interrompida (Ctrl+C) | "Interrompido com Ctrl+C. Pode haver arquivos incompletos no destino.", com o original como `.old` | a mesma linha e mais "A reescrita falhou; o ISO original voltou ao nome de antes." |
| P-1 | SIGTERM no menu com uma ferramenta rodando | o menu sumia sem nada na tela, e a ferramenta continuava escondida | a tela do Ctrl+C: a mensagem de cancelamento da ferramenta, "Interrompido com Ctrl+C. Pode haver arquivos incompletos no destino." e o resumo; depois o menu sai. A linha fala em Ctrl+C porque não existe texto para o SIGTERM, e criar um mudaria os textos |
| P-2 | terminal fechado | — | nada na tela (o terminal não existe mais); no log, "Sessão encerrada", a linha de sempre da saída |
| E-1 | Ctrl+C em "Confirmar? [S/n]" | a operação começava | "Operação cancelada pelo usuário." |
| L-1 | evento JSON com campo de tipo errado | "Erro inesperado: …", com a ferramenta rodando escondida | o evento é ignorado e a operação segue até o fim |
| L-2 | linha de saída enorme, sem quebra | o menu parado por minutos | a linha cortada, como qualquer linha longa (a tela já mostrava só 58 colunas) |
| L-3 | acento partido entre duas leituras | `��` no nome | o nome certo |
| A-1 | nome de arquivo que não é UTF-8, no Linux | "Erro inesperado: surrogates not allowed" e a pasta inutilizável | o nome com `?` no lugar dos bytes inválidos ("A??o.iso"); no log, `\udce7` |
| L-5 | `info --json` inesperado | "Erro inesperado: <detalhe do Python>" e a análise parava | um campo de tipo errado fica de fora, como se não viesse; um JSON que não é objeto mostra o que o iso2god disse no stderr, ou "Erro inesperado" sem o detalhe; as outras ISOs seguem |
| L-6 | título ou linha com sequências de escape e controles | o terminal os interpretava (limpar a tela, trocar o título da janela, inverter o texto) | o texto sem eles |
| E-2 | intervalo com mais de 1000 números além do total | "Entrada ignorada: 4, 5, 6, …" com milhões de números, ou o menu travado | "Entrada ignorada: 4-3000000" |
| E-3 | "²", "³" ou um número enorme numa escolha | "Erro inesperado: …" | como qualquer entrada inválida: "Entrada ignorada: ²" no navegador, "Opção inválida. Tente novamente." no assistente, a volta ao menu no lote |
| C-2 | caminho com caractere nulo no `config.json` | "Erro inesperado: embedded null byte" em toda ação | o valor padrão (a pasta pessoal; as ferramentas procuradas de novo) |
| P-3 | Ctrl+C no meio de uma saída grande | 35 s parado, e a ferramenta morta sem limpar | o cancelamento na hora, com limpeza |
| P-4 | ferramenta que lê o teclado (comando manual) | esperava com a barra parada e pegava o que se digitava para o menu | recebe "fim da entrada" na hora |
| W-2 | título com acento no `info --json`, com o `.py` rodado direto no Windows | "Jogo de AÃ§Ã£o" | "Jogo de Ação" |
| W-3 | "Ver log", com o `.py` rodado direto no Windows | "Sess�o iniciada" | "Sessão iniciada" (as linhas gravadas antes continuam como estavam) |
| LZ-1 | falta a DLL, a biblioteca padrão ou o `unicodedata.pyd` | "Fatal Python error" e a janela fechando | a mensagem que já existia para o `python.exe` faltando, com o caminho do arquivo que falta, esperando ENTER |
| C-1 | o menu morre no meio de uma gravação do config | o `config.json` pela metade | o `config.json` de antes; pode sobrar um `config.json.tmp` ao lado, que a próxima gravação reaproveita |
| W-1 | janela fechada no Windows | — | nada (a janela já fechou) |
| V-2 | extrair, criar ou reescrever para outra pasta com o extract-xiso oficial, com o destino já ocupado | gravava por cima, sem perguntar | "… já tem arquivos. Extrair por cima?" ou "… já existe. Substituir?", como com o extract-xiso-pt; com "não", "pulado" |
| V-4 | erro fatal do programa com a janela aberta com dois cliques | a janela fechava na hora | "Aperte ENTER para fechar..."; numa queda do Python, antes disso, "xiso-manager: o Python fechou com erro (código 0x…)." |
| V-1 | assistente com o extract-xiso oficial e uma ISO de Xbox clássico | a dica certa e logo depois "Não consegui identificar", com extrair, listar e converter para GOD | só a dica certa, com extrair, listar e reescrever |
| V-5 | `config.json` ilegível | nada: as configurações voltavam ao padrão, e a próxima mudança apagava o arquivo antigo | na abertura, "Não consegui ler o config.json: as configurações voltaram ao padrão." e "O arquivo antigo ficou em <caminho>/config.json.invalido", esperando o ENTER |

## Resultado da fase 2

Ramo `auditoria-seguranca`: um commit por item, da gravidade mais alta para
a mais baixa, cada um com o código do item no título e com o teste que
falha no código antigo e passa no novo.

- **Python:** 60 testes (`python3 -m unittest discover -s tests`, 12 s). Os
  mesmos testes contra o `xiso_manager.py` da 3.2.5: 35 falham, pelo menos
  um de cada item corrigido; os 25 que passam nos dois são os que conferem
  que nada mais mudou (as telas e os casos que já funcionavam). Das 20
  telas, só a do verificar com erro difere da 3.2.5 (L-4).
- **Lançador:** 8 testes (`cargo test`); os 4 de arquivo faltando falham no
  lançador da 3.2.5. `cargo clippy --all-targets -- -D warnings` sem avisos
  no Linux e em `x86_64-pc-windows-msvc`; `cargo fmt --check` limpo.
- **Medidas:**

| | 3.2.5 | Agora |
|---|---|---|
| Linha de 32 MiB sem quebra (L-2) | 505 s, 407 MiB | 0,4 s, menos de 120 MiB |
| Ctrl+C numa listagem grande (P-3) | 35 s e SIGKILL, sem limpeza | menos de 0,5 s, com limpeza |
| Seleção `1-3000000` (E-2) | 1,1 s, 3 milhões de itens | 0,03 ms, um item |
| Seleção `1-999999999` (E-2) | minutos, memória esgotada | 0,01 ms, um item |

- **Sem Windows de verdade:** o tratador de console da W-1 só existe no
  Windows (a função que ele chama é testada no Linux), e o lançador foi
  compilado e analisado para `x86_64-pc-windows-msvc`, com os testes rodando
  o mesmo código no Linux. Antes de publicar, vale fechar a janela no meio
  de uma reescrita com o extract-xiso oficial num Windows de verdade e
  conferir que o original volta ao nome.

## Testes no Windows (GitHub Actions)

Depois da fase 2, o workflow `.github/workflows/testes.yml` passou a rodar
os testes a cada push na `main` e em todo pull request, no Linux e no
Windows:

- **Menu:** a suíte inteira no Linux (Python 3.10 e 3.14) e no Windows
  (3.8, o mais antigo que o README promete, e 3.14, o do pacote). No
  Windows as ferramentas falsas são arquivos `.cmd`; o que depende dos
  sinais do Unix, do pty, do módulo `resource` ou de nomes que não são
  UTF-8 é pulado lá, com o motivo. As 19 telas sem cor valem nos dois.
- **W-1 de verdade:** o menu num console próprio (um pseudoconsole, o mesmo
  do Windows Terminal), no meio de uma reescrita com o extract-xiso oficial;
  o console é fechado e o Windows manda `CTRL_CLOSE_EVENT` ao menu e à
  ferramenta. O original tem que voltar ao nome. Também o tratador
  registrado de verdade com `SetConsoleCtrlHandler`.
- **Lançador:** formatação, clippy, testes e compilação de release com o
  linker da Microsoft.
- **Pacote:** o lançador compilado e o Python portátil oficial (versão e
  SHA-256 fixados no workflow), numa pasta com espaço e acento: o menu abre
  e sai, e faltando a biblioteca padrão, a DLL, o `unicodedata.pyd` ou o
  `python.exe` sai a mensagem do lançador.

A primeira execução no Windows achou W-2 e W-3, que só aparecem fora do
modo UTF-8 e por isso não apareciam no pacote nem no Linux comum.

## V-2 e V-4, corrigidos depois

A pedido, os dois itens que mais pesavam entre os só descritos:

- **V-2:** 7 testes novos (`V2OficialPerguntaAntesDeGravarPorCima`): para
  extrair, criar e reescrever para outra pasta com o extract-xiso oficial e
  o destino ocupado, a pergunta aparece e o "não" deixa o arquivo do
  usuário intacto, sem rodar a ferramenta; o "sim" grava por cima; e a
  reescrita na mesma pasta digitada por outro caminho (um link) não
  pergunta. Os 3 de pergunta falham na 3.2.7. A ferramenta falsa passou a
  imitar também o `-c` do oficial (o caminho de saída e o `O_TRUNC` do
  `create_xiso`). As telas gravadas não mudaram.
- **V-4:** 3 testes da decisão no lançador (`cargo test`) e 3 no pacote de
  verdade, no Windows (`tests/test_pacote_windows.py`), com o lançador num
  console próprio, como aberto com dois cliques: com erro, ele espera o
  ENTER e sai com 1; com 0 e 130, fecha na hora; rodado de dentro de um
  `cmd.exe`, não espera.

## V-1 e V-5, corrigidos depois

Também a pedido, depois da 3.2.8:

- **V-1:** 1 teste novo (`V1AssistenteComOOficial`): com o extract-xiso
  oficial, uma ISO de Xbox clássico mostra só a dica certa e as ações
  extrair, listar e reescrever, sem "Não consegui identificar", sem
  converter para GOD e sem o verificar. Falha na 3.2.8. A tela gravada do
  assistente (com o extract-xiso-pt) não mudou.
- **V-5:** 2 testes novos (`V5ConfigIlegivel`): JSON inválido, um JSON que
  não é objeto e bytes que não são UTF-8 viram a cópia `config.json.invalido`
  com os mesmos bytes, o aviso na tela e a linha no log; um `config.json`
  válido não avisa nem copia. O primeiro falha na 3.2.8.

O que continua só descrito no xiso-manager: V-3 (Ctrl+C nas outras
perguntas, Baixa), V-6 (`taskkill /F` no Windows, Baixa, sem uma correção
que não piore outro caso) e D-1 a D-3 (tamanho do pacote e desempenho).
Nenhum deles põe arquivo em risco.
