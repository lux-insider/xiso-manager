//! Os eventos JSON do `--progresso-json` (iso2god e extract-xiso-pt) e a
//! limpeza do texto que vem de outro programa.
//!
//! Os eventos vêm de fora: um campo de tipo errado vale como ausente (ou é
//! mostrado como texto), nunca derruba a leitura.

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{Map, Value};

/// Sequências de escape inteiras (cor, limpar a tela, título da janela),
/// os controles C1 e os caracteres que invertem a direção do texto: o título
/// do jogo vem de dentro da ISO e não pode mexer no terminal.
fn re_controle() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(concat!(
            r"\x1b\[[0-?]*[ -/]*[@-~]|\x{9b}[0-?]*[ -/]*[@-~]",
            r"|[\x1b][\]PX^_][^\x07\x1b\x{9c}]*(?:\x07|\x1b\\|\x{9c})",
            r"|[\x{9d}\x{90}\x{98}\x{9e}\x{9f}][^\x07\x1b\x{9c}]*(?:\x07|\x1b\\|\x{9c})",
            r"|\x1b[ -/]*[0-~]",
            r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x{9f}\x{61c}\x{200e}\x{200f}\x{202a}-\x{202e}\x{2066}-\x{2069}]"
        ))
        .unwrap()
    })
}

fn re_espacos() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s+").unwrap())
}

/// Texto de outro programa sem nada que o terminal interprete; mantém os
/// espaços como estão (tab e quebras viram espaço).
pub fn sem_controle(texto: &str) -> String {
    let texto: String = texto
        .chars()
        .map(|c| {
            if matches!(c, '\t' | '\r' | '\n') {
                ' '
            } else {
                c
            }
        })
        .collect();
    re_controle().replace_all(&texto, "").into_owned()
}

/// Aplica os backspaces (o extract-xiso anima o progresso com \b), tira os
/// controles e junta os espaços.
pub fn limpar_linha_externa(texto: &str) -> String {
    let mut letras: Vec<char> = Vec::with_capacity(texto.len());
    for ch in texto.chars() {
        if ch == '\u{8}' {
            letras.pop();
        } else {
            letras.push(ch);
        }
    }
    let junto: String = letras.into_iter().collect();
    let limpo = re_controle().replace_all(&junto, "");
    re_espacos().replace_all(&limpo, " ").trim().to_string()
}

/// Um evento do `--progresso-json`, ou None.
pub fn evento_json(linha: &str) -> Option<Map<String, Value>> {
    if !(linha.starts_with('{') && linha.ends_with('}')) {
        return None;
    }
    match serde_json::from_str::<Value>(linha) {
        Ok(Value::Object(m)) if m.contains_key("evento") => Some(m),
        _ => None,
    }
}

/// O valor, se for um número de verdade (bool não conta).
pub fn numero(valor: Option<&Value>) -> Option<f64> {
    match valor {
        Some(Value::Number(n)) => n.as_f64(),
        _ => None,
    }
}

pub fn texto(valor: Option<&Value>) -> &str {
    match valor {
        Some(Value::String(s)) => s,
        _ => "",
    }
}

/// O valor "verdadeiro", como o Python o julgaria.
pub fn verdadeiro(valor: Option<&Value>) -> bool {
    match valor {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn float_py(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let cientifico = format!("{f:e}");
    let (mantissa, expoente) = cientifico.split_once('e').unwrap();
    let expoente: i32 = expoente.parse().unwrap();
    if (-4..16).contains(&expoente) {
        let fixo = format!("{f}");
        if fixo.contains('.') {
            fixo
        } else {
            format!("{fixo}.0")
        }
    } else {
        let sinal = if expoente < 0 { '-' } else { '+' };
        format!("{mantissa}e{sinal}{:02}", expoente.abs())
    }
}

pub fn repr_texto(s: &str) -> String {
    let aspas = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut saida = String::new();
    saida.push(aspas);
    for c in s.chars() {
        match c {
            '\\' => saida.push_str("\\\\"),
            '\n' => saida.push_str("\\n"),
            '\r' => saida.push_str("\\r"),
            '\t' => saida.push_str("\\t"),
            c if c == aspas => {
                saida.push('\\');
                saida.push(c);
            }
            c if (c as u32) < 0x20 || (0x7f..0xa0).contains(&(c as u32)) => {
                saida.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => saida.push(c),
        }
    }
    saida.push(aspas);
    saida
}

fn repr(valor: &Value) -> String {
    match valor {
        Value::String(s) => repr_texto(s),
        outro => py_str(outro),
    }
}

/// O valor como o "%s" do Python o mostraria (None, True, [1, 2]...).
pub fn py_str(valor: &Value) -> String {
    match valor {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.to_string()
            } else if let Some(u) = n.as_u64() {
                u.to_string()
            } else {
                float_py(n.as_f64().unwrap_or(0.0))
            }
        }
        Value::String(s) => s.clone(),
        Value::Array(a) => format!("[{}]", a.iter().map(repr).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| format!("{}: {}", repr_texto(k), repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn py_get(mapa: &Map<String, Value>, chave: &str, padrao: &str) -> String {
    mapa.get(chave)
        .map(py_str)
        .unwrap_or_else(|| padrao.to_string())
}

/// O relatório do `verificar --progresso-json` do extract-xiso-pt.
pub fn linhas_verificado(evento: &Map<String, Value>) -> Vec<String> {
    let vazio = Map::new();
    let h = match evento.get("hashes") {
        Some(Value::Object(m)) => m,
        _ => &vazio,
    };
    let completo = evento.get("disco_completo");
    let situacao_disco = if verdadeiro(completo) {
        "disco completo"
    } else if matches!(completo, None | Some(Value::Null)) {
        "enxuta"
    } else {
        "tamanho fora do padrão"
    };
    let mut linhas = vec![
        format!(
            "{} \u{00B7} {}",
            py_get(evento, "layout", "?"),
            situacao_disco
        ),
        format!(
            "estrutura íntegra \u{00B7} {} arquivos em {} pastas",
            py_get(evento, "arquivos", "?"),
            py_get(evento, "diretorios", "?")
        ),
    ];
    if let Some(Value::Array(avisos)) = evento.get("avisos") {
        for a in avisos {
            if let Value::String(s) = a {
                linhas.push(format!("aviso: {s}"));
            }
        }
    }
    for nome in ["crc32", "md5", "sha1"] {
        if verdadeiro(h.get(nome)) {
            linhas.push(format!("{:<6} {}", nome.to_uppercase(), py_str(&h[nome])));
        }
    }
    match evento.get("dat") {
        Some(Value::Object(dat)) if !dat.is_empty() => {
            let situacao = dat.get("situacao").and_then(Value::as_str);
            match situacao {
                Some("confere") => linhas.push(format!(
                    "ORIGINAL: idêntica ao Redump — {}",
                    py_get(dat, "jogo", "?")
                )),
                Some("enxuta") => linhas.push(format!(
                    "cópia enxuta de {}: íntegra, mas o Redump guarda o hash do disco \
                     completo, então não tem como conferir",
                    py_get(dat, "jogo", "?")
                )),
                Some("nao_confere") => linhas.push(format!(
                    "NÃO CONFERE: o Redump tem {} com outro SHA-1 \
                     (modificada, corrompida ou outra versão)",
                    py_get(dat, "rom", "?")
                )),
                _ => linhas.push(format!(
                    "não está no Redump{}",
                    if matches!(completo, None | Some(Value::Null)) {
                        " (ISO enxuta nunca confere)"
                    } else {
                        ""
                    }
                )),
            }
        }
        _ => {
            if !verdadeiro(evento.get("dats_usados")) {
                linhas.push("sem .dat do Redump instalado: só a integridade foi conferida".into());
            }
        }
    }
    linhas
}

/// Texto legível de um evento `fase`/`concluido`/`erro`.
pub fn texto_evento(evento: &Map<String, Value>) -> String {
    let tipo = evento.get("evento").and_then(Value::as_str).unwrap_or("");
    let mensagem = texto(evento.get("mensagem"));
    let pasta = texto(evento.get("pasta"));
    if tipo == "concluido" && !pasta.is_empty() && !mensagem.contains(pasta) {
        return if mensagem.is_empty() {
            pasta.to_string()
        } else {
            format!("{mensagem} {pasta}")
        };
    }
    if tipo == "erro" {
        return format!("erro: {mensagem}");
    }
    mensagem.to_string()
}

#[cfg(test)]
mod testes {
    use super::*;
    use serde_json::json;

    const PERIGOSOS: [&str; 5] = ["\x1b", "\x07", "\u{9b}", "\u{202e}", "]0;janela"];

    #[test]
    fn controles_saem_do_texto() {
        let linha = limpar_linha_externa(
            "título \x1b]0;janela\x07 com \x1b[2J limpar \u{9b}31m e \u{202e}etxet",
        );
        for p in PERIGOSOS {
            assert!(!linha.contains(p), "{p:?} em {linha:?}");
        }
        assert_eq!(linha, "título com limpar e etxet");
        assert_eq!(limpar_linha_externa("abc\u{8}\u{8}d  e\t"), "ad e");
    }

    #[test]
    fn valores_como_o_python_mostra() {
        assert_eq!(py_str(&json!(null)), "None");
        assert_eq!(py_str(&json!([1, null, "a"])), "[1, None, 'a']");
        assert_eq!(py_str(&json!(1.0)), "1.0");
        assert_eq!(py_str(&json!(1e16)), "1e+16");
        assert_eq!(py_str(&json!({"a": true})), "{'a': True}");
    }

    #[test]
    fn verificado_com_tipos_errados() {
        let ev = json!({"evento": "verificado", "hashes": "x", "avisos": 5, "dat": "y"});
        let linhas = linhas_verificado(ev.as_object().unwrap());
        assert_eq!(linhas[0], "? · enxuta");
        assert_eq!(
            linhas.last().unwrap(),
            "sem .dat do Redump instalado: só a integridade foi conferida"
        );
        let ev = json!({"evento": "verificado", "hashes": {"sha1": 3}, "avisos": [1, null],
                        "dat": {"situacao": 1}});
        let linhas = linhas_verificado(ev.as_object().unwrap());
        assert!(linhas.contains(&"SHA1   3".to_string()));
        assert_eq!(
            linhas.last().unwrap(),
            "não está no Redump (ISO enxuta nunca confere)"
        );
    }
}
