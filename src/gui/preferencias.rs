//! Opções só da janela, em `janela.json`, ao lado do `config.json` do menu
//! do terminal (que fica intocado): o modo de desenho e a lista de jogos da
//! biblioteca.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

fn arquivo() -> PathBuf {
    xiso_manager::config::dir_base().join("janela.json")
}

fn ler() -> Map<String, Value> {
    std::fs::read(arquivo())
        .ok()
        .and_then(|d| serde_json::from_slice::<Value>(&d).ok())
        .and_then(|v| match v {
            Value::Object(m) => Some(m),
            _ => None,
        })
        .unwrap_or_default()
}

/// Grava uma chave, mantendo as outras. Grava num temporário e troca o
/// nome, para uma queda no meio não deixar o arquivo pela metade.
fn gravar(chave: &str, valor: Value) {
    let mut dados = ler();
    dados.insert(chave.into(), valor);
    let Ok(texto) = serde_json::to_string_pretty(&Value::Object(dados)) else {
        return;
    };
    let destino = arquivo();
    let temporario = destino.with_extension("json.tmp");
    let ok = std::fs::write(&temporario, texto.as_bytes())
        .and_then(|()| std::fs::rename(&temporario, &destino));
    if let Err(e) = ok {
        let _ = std::fs::remove_file(&temporario);
        xiso_manager::log::evento(
            xiso_manager::log::Nivel::Erro,
            &format!("Falha ao salvar janela.json: {e}"),
        );
    }
}

/// Desenhar pela placa de vídeo? O padrão é o software: gasta ~25 MB de
/// memória contra ~95 MB do driver de vídeo, e nesta janela, que só
/// redesenha o pedaço que muda, é rápido do mesmo jeito.
pub fn usar_gpu() -> bool {
    if let Some(v) = std::env::var_os("XM_GPU") {
        return v == "1";
    }
    ler()
        .get("desenho_gpu")
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

/// Os jogos da biblioteca, na ordem em que foram adicionados.
pub fn biblioteca() -> Vec<PathBuf> {
    match ler().get("biblioteca") {
        Some(Value::Array(lista)) => lista
            .iter()
            .filter_map(Value::as_str)
            .map(PathBuf::from)
            .collect(),
        _ => Vec::new(),
    }
}

/// Caminhos que não são texto UTF-8 ficam de fora (o JSON só guarda
/// texto); o jogo continua na janela até ela fechar.
pub fn salvar_biblioteca<'a>(caminhos: impl Iterator<Item = &'a Path>) {
    let lista = caminhos
        .filter_map(|c| c.to_str())
        .map(|c| Value::String(c.into()))
        .collect();
    gravar("biblioteca", Value::Array(lista));
}

/// A última pasta de destino dos pacotes GOD (vazio: a pasta padrão do
/// config.json).
pub fn destino_god() -> String {
    ler()
        .get("destino_god")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

pub fn salvar_destino_god(pasta: &str) {
    gravar("destino_god", Value::String(pasta.into()));
}
