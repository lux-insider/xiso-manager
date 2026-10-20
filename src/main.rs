//! xiso-manager: gerenciador de imagens de Xbox e Xbox 360.
//!
//! A ferramenta certa para cada disco, sem o usuário precisar saber qual é:
//! detecta o tipo do ISO e só oferece o que faz sentido. O trabalho pesado é
//! do extract-xiso(-pt) e do iso2god; aqui ficam a interface e a orquestração.
//!
//! Códigos de saída: 0 sucesso | 1 erro fatal | 130 Ctrl+C

use xiso_manager::{entrada, estilo, log, menu, saida, say, sinais, terminal};

fn main() {
    let ansi = terminal::preparar_console();
    estilo::decidir_cor(ansi);
    // o pânico é mostrado como o erro fatal, não com o texto do Rust
    std::panic::set_hook(Box::new(|_| {}));
    sinais::instalar();
    log::iniciar();
    entrada::iniciar();

    let codigo = match std::panic::catch_unwind(menu::principal) {
        Ok(c) => c,
        Err(p) => {
            let motivo = menu::mensagem_panico(p.as_ref());
            say!();
            say!("  {}  {}", estilo::emo::ERRO, motivo);
            log::evento(log::Nivel::Erro, &format!("Erro fatal: {motivo}"));
            saida::descarregar();
            esperar_na_janela();
            1
        }
    };
    saida::descarregar();
    std::process::exit(codigo);
}

/// Windows, janela aberta com dois cliques: ela fecha junto com o programa,
/// antes de dar para ler o erro. Num terminal aberto antes, não espera.
fn esperar_na_janela() {
    if !terminal::janela_so_minha() {
        return;
    }
    use std::io::Write;
    eprint!("  Aperte ENTER para fechar...");
    let _ = std::io::stderr().flush();
    let _ = entrada::ler("");
}
