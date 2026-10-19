//! Cores, larguras e as peças de layout do menu (caixas, réguas, tabelas,
//! campos alinhados). A aparência é a mesma da versão em Python: os testes
//! de tela comparam cada caractere.

use std::sync::OnceLock;

use regex::Regex;

use crate::saida;
use crate::textos::t;
use crate::unicode_tabelas::{COMBINANTES, LARGOS};
use crate::{config, log, say};

pub const LARGURA: usize = 66;

// ── Paleta ──────────────────────────────────────────────────────────────

pub const RESET: &str = "\x1b[0m";
pub const NEG: &str = "\x1b[1m";
pub const ITAL: &str = "\x1b[3m";

pub const AZUL: &str = "\x1b[38;2;56;150;255m";
pub const VERDE: &str = "\x1b[38;2;46;214;130m";
pub const CIANO: &str = "\x1b[38;2;92;200;220m";
pub const AMARE: &str = "\x1b[38;2;255;186;72m";
pub const VERM: &str = "\x1b[38;2;255;95;110m";
pub const ROXO: &str = "\x1b[38;2;170;130;255m";
pub const CINZA: &str = "\x1b[38;2;128;138;150m";
pub const BRANC: &str = "\x1b[38;2;235;240;245m";

// extremos do gradiente: azul → verde
const G_INI: (i64, i64, i64) = (56, 150, 255);
const G_FIM: (i64, i64, i64) = (46, 214, 130);

// ── Emoji e símbolos ────────────────────────────────────────────────────

pub mod emo {
    pub const APP: &str = "\u{1F4BF}";
    pub const OK: &str = "\u{2705}";
    pub const ERRO: &str = "\u{274C}";
    pub const AVISO: &str = "\u{26A0}\u{FE0F}";
    pub const INFO: &str = "\u{2139}\u{FE0F}";
    pub const DICA: &str = "\u{1F4A1}";
    pub const EXTRAIR: &str = "\u{1F4E6}";
    pub const LISTAR: &str = "\u{1F4CB}";
    pub const CRIAR: &str = "\u{1F6E0}\u{FE0F}";
    pub const OTIMIZAR: &str = "\u{1F504}";
    pub const GOD: &str = "\u{1F3AE}";
    pub const ANALISAR: &str = "\u{1F50D}";
    pub const LOTE: &str = "\u{1F680}";
    pub const MANUAL: &str = "\u{2328}\u{FE0F}";
    pub const CONFIG: &str = "\u{2699}\u{FE0F}";
    pub const LOG: &str = "\u{1F9FE}";
    pub const SOBRE: &str = "\u{1F4D6}";
    pub const SAIR: &str = "\u{1F6AA}";
    pub const PASTA: &str = "\u{1F4C1}";
    pub const ARQUIVO: &str = "\u{1F4C4}";
    pub const DISCO: &str = "\u{1F4BE}";
    pub const TEMPO: &str = "\u{23F1}\u{FE0F}";
    pub const RESUMO: &str = "\u{1F4CA}";
    pub const ASSISTENTE: &str = "\u{1F9ED}";
    pub const IDIOMA: &str = "\u{1F310}";
    pub const LIXO: &str = "\u{1F5D1}\u{FE0F}";
    pub const JOGO: &str = "\u{1F579}\u{FE0F}";
    pub const ESTRELA: &str = "\u{2B50}";
    pub const SUBIR: &str = "\u{2B06}\u{FE0F}";
}

pub const SIM_OK: &str = "\u{2713}";
pub const SIM_FALHA: &str = "\u{2717}";
pub const SIM_ND: &str = "\u{00B7}";
pub const SIM_SETA: &str = "\u{25B8}";
pub const SIM_PONTO: &str = "\u{25CF}";

// ── Cor ─────────────────────────────────────────────────────────────────

static COR_TERMINAL: OnceLock<bool> = OnceLock::new();

/// Decide, uma vez, se o terminal mostra cor: NO_COLOR desliga; sem
/// terminal (pipe, arquivo) não há cor; no Unix, TERM vazio ou "dumb"
/// também não. No Windows vale o que o console aceitou (`ansi_windows`).
pub fn decidir_cor(ansi_windows: bool) {
    let sim = (|| {
        if std::env::var_os("NO_COLOR").is_some_and(|v| !v.is_empty()) {
            return false;
        }
        use std::io::IsTerminal;
        if !std::io::stdout().is_terminal() {
            return false;
        }
        if cfg!(windows) {
            return ansi_windows;
        }
        let termo = std::env::var("TERM").unwrap_or_default();
        !(termo.is_empty() || termo == "dumb")
    })();
    let _ = COR_TERMINAL.set(sim);
}

pub fn usar_cor() -> bool {
    *COR_TERMINAL.get().unwrap_or(&false) && config::cor()
}

/// Aplica os estilos só quando o terminal mostra cor.
pub fn cor(texto: &str, estilos: &[&str]) -> String {
    if !usar_cor() || estilos.is_empty() {
        return texto.to_string();
    }
    format!("{}{}{}", estilos.concat(), texto, RESET)
}

fn rgb(r: f64, g: f64, b: f64) -> String {
    format!("\x1b[38;2;{};{};{}m", r as i64, g as i64, b as i64)
}

/// Cor interpolada azul → verde; `pos` de 0.0 a 1.0.
pub fn gradiente(pos: f64) -> String {
    let pos = pos.clamp(0.0, 1.0);
    let canal = |a: i64, b: i64| a as f64 + (b - a) as f64 * pos;
    rgb(
        canal(G_INI.0, G_FIM.0),
        canal(G_INI.1, G_FIM.1),
        canal(G_INI.2, G_FIM.2),
    )
}

/// Pinta cada letra com um tom do gradiente, numa onda que vai e volta.
pub fn texto_gradiente(texto: &str) -> String {
    if !usar_cor() {
        return texto.to_string();
    }
    let letras: Vec<char> = texto.chars().collect();
    let total = letras.len().saturating_sub(1).max(1) as f64;
    let mut saida = String::new();
    for (i, ch) in letras.iter().enumerate() {
        if *ch == ' ' {
            saida.push(' ');
            continue;
        }
        let p = (i as f64 / total) % 1.0;
        let onda = if p < 0.5 { p * 2.0 } else { (1.0 - p) * 2.0 };
        saida.push_str(&gradiente(onda));
        saida.push(*ch);
    }
    saida.push_str(RESET);
    saida
}

/// Como `texto_gradiente`, mas dentro de um trecho da escala.
pub fn texto_gradiente_faixa(texto: &str, inicio: f64, fim: f64) -> String {
    if !usar_cor() {
        return texto.to_string();
    }
    let letras: Vec<char> = texto.chars().collect();
    let total = letras.len().saturating_sub(1).max(1) as f64;
    let mut saida = String::new();
    for (i, ch) in letras.iter().enumerate() {
        if *ch == ' ' {
            saida.push(' ');
            continue;
        }
        saida.push_str(&gradiente(inicio + (fim - inicio) * (i as f64 / total)));
        saida.push(*ch);
    }
    saida.push_str(RESET);
    saida
}

// ── Largura visível ─────────────────────────────────────────────────────

fn re_ansi() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\x1b\[[0-9;]*m").unwrap())
}

fn na_tabela(tabela: &[(u32, u32)], cp: u32) -> bool {
    tabela
        .binary_search_by(|&(a, b)| {
            if cp < a {
                std::cmp::Ordering::Greater
            } else if cp > b {
                std::cmp::Ordering::Less
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// Colunas ocupadas pelo texto, sem contar as cores. Emoji conta 2; um
/// caractere seguido do seletor de emoji (U+FE0F) também.
pub fn largura_visivel(texto: &str) -> usize {
    let limpo = re_ansi().replace_all(texto, "");
    let letras: Vec<char> = limpo.chars().collect();
    let mut largura = 0;
    let mut i = 0;
    while i < letras.len() {
        let ch = letras[i];
        let cp = ch as u32;
        if letras.get(i + 1) == Some(&'\u{FE0F}') {
            largura += 2;
            i += 2;
            continue;
        }
        if ch == '\u{FE0E}' {
            i += 1;
            continue;
        }
        if cp == 0x200D {
            i += 2;
            continue;
        }
        if na_tabela(COMBINANTES, cp) {
            i += 1;
            continue;
        }
        if na_tabela(LARGOS, cp)
            || (0x1F300..=0x1FAFF).contains(&cp)
            || (0x1F004..=0x1F0CF).contains(&cp)
        {
            largura += 2;
        } else {
            largura += 1;
        }
        i += 1;
    }
    largura
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Alinha {
    Esquerda,
    Direita,
}

/// Preenche até a largura visível.
pub fn pad(texto: &str, largura: usize, alinhamento: Alinha) -> String {
    let atual = largura_visivel(texto);
    if atual >= largura {
        return texto.to_string();
    }
    let falta = largura - atual;
    match alinhamento {
        Alinha::Direita => format!("{}{}", " ".repeat(falta), texto),
        Alinha::Esquerda => format!("{}{}", texto, " ".repeat(falta)),
    }
}

pub fn padl(texto: &str, largura: usize) -> String {
    pad(texto, largura, Alinha::Esquerda)
}

/// Corta o texto para caber em `limite` colunas, terminando em "…".
pub fn cortar(texto: &str, limite: usize) -> String {
    if largura_visivel(texto) <= limite {
        return texto.to_string();
    }
    let mut saida = String::new();
    for ch in texto.chars() {
        let mut tentativa = saida.clone();
        tentativa.push(ch);
        if largura_visivel(&tentativa) + 1 > limite {
            break;
        }
        saida = tentativa;
    }
    saida.push('\u{2026}');
    saida
}

// ── Peças de layout ─────────────────────────────────────────────────────

/// Não limpa a tela: só separa um passo do outro, como as outras
/// ferramentas de terminal, que deixam o conteúdo fluir com o histórico.
pub fn limpar_tela() {
    say!();
}

fn borda_gradiente(largura: usize, esquerdo: &str, direito: &str) -> String {
    if !usar_cor() {
        return format!("{}{}{}", esquerdo, "\u{2500}".repeat(largura), direito);
    }
    let mut partes = String::new();
    for i in 0..largura {
        partes.push_str(&gradiente(
            i as f64 / largura.saturating_sub(1).max(1) as f64,
        ));
        partes.push('\u{2500}');
    }
    format!(
        "{}{}{}{}{}{}",
        gradiente(0.0),
        esquerdo,
        partes,
        gradiente(1.0),
        direito,
        RESET
    )
}

/// Cabeçalho de seção numa caixa arredondada, com emoji e borda colorida.
pub fn titulo_caixa(texto: &str, emoji: &str, cor_borda: &str) -> String {
    titulo_caixa_completo(texto, emoji, cor_borda, false)
}

pub fn titulo_caixa_completo(
    texto: &str,
    emoji: &str,
    cor_borda: &str,
    gradiente_titulo: bool,
) -> String {
    let largura = LARGURA;
    let miolo = if emoji.is_empty() {
        format!(" {texto}")
    } else {
        format!(" {emoji}  {texto}")
    };
    let espaco = (largura - 2).saturating_sub(largura_visivel(&miolo));
    let horizontal = "\u{2500}".repeat(largura - 2);
    let (topo, base) = if gradiente_titulo {
        (
            borda_gradiente(largura - 2, "\u{256D}", "\u{256E}"),
            borda_gradiente(largura - 2, "\u{2570}", "\u{256F}"),
        )
    } else {
        (
            cor(&format!("\u{256D}{horizontal}\u{256E}"), &[cor_borda]),
            cor(&format!("\u{2570}{horizontal}\u{256F}"), &[cor_borda]),
        )
    };
    let corpo = if gradiente_titulo {
        if emoji.is_empty() {
            format!(" {}", texto_gradiente(texto))
        } else {
            format!(" {}  {}", emoji, texto_gradiente(texto))
        }
    } else {
        cor(&miolo, &[NEG, BRANC])
    };
    let meio = format!(
        "{}{}{}{}",
        cor("\u{2502}", &[cor_borda]),
        corpo,
        " ".repeat(espaco),
        cor("\u{2502}", &[cor_borda])
    );
    format!("\n{topo}\n{meio}\n{base}\n")
}

pub fn regua() -> String {
    cor(&"\u{2500}".repeat(LARGURA), &[CINZA])
}

pub fn regua_gradiente() -> String {
    regua_gradiente_parcial(LARGURA, 0.0, 1.0)
}

/// Trecho de régua pintado com uma faixa do gradiente.
pub fn regua_gradiente_parcial(largura: usize, inicio: f64, fim: f64) -> String {
    if largura == 0 {
        return String::new();
    }
    if !usar_cor() {
        return "\u{2500}".repeat(largura);
    }
    let mut saida = String::new();
    for i in 0..largura {
        let pos = inicio + (fim - inicio) * (i as f64 / largura.saturating_sub(1).max(1) as f64);
        saida.push_str(&gradiente(pos));
        saida.push('\u{2500}');
    }
    saida.push_str(RESET);
    saida
}

/// A marca sempre ocupa 2 colunas: "✓" (1 coluna) ganha um espaço, "⏱️"
/// (2 colunas) fica como está. Assim os valores alinham na mesma tela.
pub fn marca_larga(simbolo: &str) -> String {
    if largura_visivel(simbolo) >= 2 {
        simbolo.to_string()
    } else {
        format!("{simbolo} ")
    }
}

/// Linha "rótulo: valor" alinhada. Um rótulo mais longo que a coluna não é
/// cortado, mas ganha um espaço para o valor não colar nele.
pub fn campo(rotulo: &str, valor: &str, emoji: &str, largura_rotulo: usize) -> String {
    let marca = if emoji.is_empty() {
        "  ".to_string()
    } else {
        marca_larga(emoji)
    };
    let mut rotulo_pad = padl(rotulo, largura_rotulo);
    if largura_visivel(&rotulo_pad) >= largura_rotulo && !rotulo_pad.ends_with(' ') {
        rotulo_pad.push(' ');
    }
    format!(
        "  {} {}{}",
        marca,
        cor(&rotulo_pad, &[CINZA]),
        cor(valor, &[BRANC])
    )
}

/// `campo` com a coluna do rótulo padrão (14).
pub fn campo14(rotulo: &str, valor: &str, emoji: &str) -> String {
    campo(rotulo, valor, emoji, 14)
}

/// Tabela alinhada. O separador acompanha a linha mais larga que aparece.
pub fn tabela(cabecalhos: &[&str], linhas: &[Vec<String>], alinhamentos: &[Alinha]) -> String {
    if linhas.is_empty() {
        return String::new();
    }
    let n = cabecalhos.len();
    let larguras: Vec<usize> = (0..n)
        .map(|i| {
            linhas
                .iter()
                .map(|l| largura_visivel(&l[i]))
                .chain(std::iter::once(largura_visivel(cabecalhos[i])))
                .max()
                .unwrap_or(0)
        })
        .collect();
    let cab = format!(
        "  {}",
        (0..n)
            .map(|i| pad(
                &cor(cabecalhos[i], &[NEG, CIANO]),
                larguras[i],
                alinhamentos[i]
            ))
            .collect::<Vec<_>>()
            .join("  ")
    );
    let corpo: Vec<String> = linhas
        .iter()
        .map(|lin| {
            format!(
                "  {}",
                (0..n)
                    .map(|i| pad(&lin[i], larguras[i], alinhamentos[i]))
                    .collect::<Vec<_>>()
                    .join("  ")
            )
        })
        .collect();
    let util = std::iter::once(&cab)
        .chain(corpo.iter())
        .map(|x| largura_visivel(x.trim_end()))
        .max()
        .unwrap_or(0);
    let sep = format!(
        "  {}",
        cor(&"\u{2500}".repeat(util.saturating_sub(2).max(4)), &[CINZA])
    );
    let mut tudo = vec![cab, sep];
    tudo.extend(corpo);
    tudo.join("\n")
}

/// Rótulo com pelo menos um espaço antes do valor.
fn rotulo_seguro(rotulo: &str, largura: usize) -> String {
    let texto = padl(rotulo, largura);
    if texto.ends_with(' ') {
        texto
    } else {
        format!("{texto} ")
    }
}

pub fn marca_ok(rotulo: &str, valor: &str) {
    say!(
        "  {} {}{}",
        cor(&marca_larga(SIM_OK), &[VERDE]),
        cor(&rotulo_seguro(rotulo, 17), &[CINZA]),
        cor(valor, &[BRANC])
    );
}

pub fn marca_falha(rotulo: &str, valor: &str) {
    say!(
        "  {} {}{}",
        cor(&marca_larga(SIM_FALHA), &[VERM]),
        cor(&rotulo_seguro(rotulo, 17), &[CINZA]),
        cor(valor, &[VERM, NEG])
    );
}

pub fn marca_nd(rotulo: &str, valor: &str) {
    say!(
        "  {} {}{}",
        cor(&marca_larga(SIM_ND), &[CINZA]),
        cor(&rotulo_seguro(rotulo, 17), &[CINZA]),
        cor(valor, &[CINZA])
    );
}

/// Linha de menu: "  [1]  📦  Extrair conteúdo de ISO".
pub fn opcao(chave: &str, texto: &str, emoji: &str, cor_chave: &str, cor_texto: &str) -> String {
    let marca = if emoji.is_empty() {
        String::new()
    } else {
        format!("{emoji}  ")
    };
    let rotulo = pad(&format!("[{chave}]"), 4, Alinha::Direita);
    format!(
        "  {}  {}{}",
        cor(&rotulo, &[cor_chave, NEG]),
        marca,
        cor(texto, &[cor_texto])
    )
}

/// `opcao` com a chave verde e o texto branco.
pub fn opcao_simples(chave: &str, texto: &str, emoji: &str) -> String {
    opcao(chave, texto, emoji, VERDE, BRANC)
}

/// Divisória de fase: "── 1/4 · Origem ──────────────".
pub fn etapa(numero: Option<(usize, usize)>, titulo: &str) -> String {
    let prefixo = match numero {
        Some((n, total)) => format!("{n}/{total} \u{00B7} {titulo}"),
        None => titulo.to_string(),
    };
    let cabeca = format!(
        "  {} {} ",
        cor("\u{2500}\u{2500}", &[CINZA]),
        cor(&prefixo, &[CIANO, NEG])
    );
    let resto = LARGURA.saturating_sub(largura_visivel(&cabeca));
    format!("\n{}{}", cabeca, cor(&"\u{2500}".repeat(resto), &[CINZA]))
}

/// Eco do que foi aceito, recuado sob a pergunta.
pub fn resposta(texto: &str, cor_texto: &str) -> String {
    format!(
        "    {} {}",
        cor("\u{2514}", &[CINZA]),
        cor(texto, &[cor_texto])
    )
}

/// A saída da ferramenta externa num bloco recuado e marcado: solta, ela
/// parece mensagem de erro do menu.
pub fn bloco_saida(linhas: &[String], limite: usize, total_real: usize) -> String {
    if linhas.is_empty() {
        return String::new();
    }
    let inicio = linhas.len().saturating_sub(limite);
    let mostradas = &linhas[inicio..];
    let mut partes = Vec::new();
    for (i, linha) in mostradas.iter().enumerate() {
        let gancho = if i == mostradas.len() - 1 {
            "\u{2514}"
        } else {
            "\u{251C}"
        };
        partes.push(format!(
            "    {} {}",
            cor(gancho, &[CINZA]),
            cor(&cortar(linha, LARGURA - 8), &[CINZA])
        ));
    }
    if total_real > limite {
        partes.insert(
            0,
            format!(
                "    {}",
                cor(
                    &format!("\u{2026} {} linha(s) anteriores", total_real - limite),
                    &[CINZA, ITAL]
                )
            ),
        );
    }
    partes.join("\n")
}

pub fn aviso(texto: &str) {
    say!("{}  {}", cor(emo::AVISO, &[AMARE]), cor(texto, &[AMARE]));
    log::evento(log::Nivel::Aviso, texto);
}

pub fn erro(texto: &str) {
    say!("{} {}", cor(emo::ERRO, &[VERM]), cor(texto, &[VERM, NEG]));
    log::evento(log::Nivel::Erro, texto);
}

pub fn sucesso(texto: &str) {
    say!("{} {}", cor(emo::OK, &[VERDE]), cor(texto, &[VERDE, NEG]));
    log::evento(log::Nivel::Info, texto);
}

pub fn dica(texto: &str) {
    say!("{}  {}", emo::DICA, cor(texto, &[CIANO, ITAL]));
}

/// Pergunta no estilo "  ▸ Digite o número: ".
pub fn prompt(texto: &str) -> String {
    format!("\n  {} {}: ", cor(SIM_SETA, &[CIANO]), texto)
}

/// Tela de uma ação: a caixa do título e a descrição.
pub fn tela(titulo: &str, emoji: &str, descricao: &str, cor_borda: &str) {
    limpar_tela();
    say!("{}", titulo_caixa(titulo, emoji, cor_borda));
    say!("{}", cor(&format!("  {descricao}"), &[CINZA]));
}

/// Escreve sem quebra de linha (a barra de progresso).
pub fn escrever(texto: &str) {
    saida::escrever(texto);
    saida::descarregar();
}

/// "N/D" quando o texto falta; usado nos rótulos de "pronto" do menu.
pub fn pronto_ou_nao(pronto: bool) -> &'static str {
    if pronto {
        t("pronto")
    } else {
        t("indisponivel")
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn largura_de_emoji_e_acentos() {
        assert_eq!(largura_visivel("abc"), 3);
        assert_eq!(largura_visivel(emo::PASTA), 2);
        assert_eq!(largura_visivel(emo::CONFIG), 2); // ⚙ + FE0F
        assert_eq!(largura_visivel("\u{2699}"), 1);
        assert_eq!(largura_visivel("e\u{301}"), 1); // e + acento combinante
        assert_eq!(largura_visivel("\x1b[1mação\x1b[0m"), 4);
        assert_eq!(largura_visivel("日本"), 4);
    }

    #[test]
    fn cortar_termina_em_reticencias() {
        assert_eq!(cortar("abcdef", 6), "abcdef");
        assert_eq!(cortar("abcdefg", 6), "abcde\u{2026}");
        assert_eq!(cortar("日本語です", 5), "日本\u{2026}");
    }

    #[test]
    fn pad_respeita_emoji() {
        assert_eq!(pad("[1]", 4, Alinha::Direita), " [1]");
        assert_eq!(padl(emo::PASTA, 4), format!("{}  ", emo::PASTA));
    }
}
