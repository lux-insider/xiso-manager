//! Opções só da janela, em `janela.json`, ao lado do `config.json` do menu
//! do terminal (que fica intocado).

use std::path::PathBuf;

fn arquivo() -> PathBuf {
    xiso_manager::config::dir_base().join("janela.json")
}

/// Desenhar pela placa de vídeo? O padrão é o software: gasta ~30 MB de
/// memória contra ~100 MB do driver de vídeo, e nesta janela, que só
/// redesenha o pedaço que muda, é rápido do mesmo jeito.
pub fn usar_gpu() -> bool {
    if let Some(v) = std::env::var_os("XM_GPU") {
        return v == "1";
    }
    std::fs::read(arquivo())
        .ok()
        .and_then(|d| serde_json::from_slice::<serde_json::Value>(&d).ok())
        .and_then(|v| v.get("desenho_gpu").and_then(|g| g.as_bool()))
        .unwrap_or(false)
}
