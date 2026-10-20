//! xiso-manager-gui: a janela do xiso-manager.
//!
//! A lógica é a mesma do menu do terminal (a biblioteca `xiso_manager`).
//! Aqui ficam só a janela e a ligação com ela. O trabalho pesado continua
//! nas ferramentas, em processos separados: a janela só lê o progresso e
//! nunca para esperando.

// No Windows, abre sem a janela preta do console atrás.
#![cfg_attr(windows, windows_subsystem = "windows")]

slint::include_modules!();

mod preferencias;

/// Software por padrão; a placa de vídeo (OpenGL, FemtoVG) quando ligada
/// nas opções. Sem OpenGL (máquina virtual, driver ausente), fica o
/// software.
fn escolher_renderizador() -> &'static str {
    if preferencias::usar_gpu()
        && slint::BackendSelector::new()
            .backend_name("winit".into())
            .renderer_name("femtovg".into())
            .select()
            .is_ok()
    {
        return "GPU (OpenGL)";
    }
    let _ = slint::BackendSelector::new()
        .backend_name("winit".into())
        .renderer_name("software".into())
        .select();
    "software"
}

fn main() -> Result<(), slint::PlatformError> {
    let renderizador = escolher_renderizador();
    let janela = Janela::new()?;
    janela.set_versao(env!("CARGO_PKG_VERSION").into());
    janela.set_renderizador(renderizador.into());
    janela.run()
}
