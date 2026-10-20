fn main() {
    // Ícone dos executáveis no Windows (os dois: o menu e a janela).
    println!("cargo:rerun-if-changed=gui/xiso-manager.rc");
    println!("cargo:rerun-if-changed=gui/imagens/xiso-manager.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("gui/xiso-manager.rc", embed_resource::NONE)
            .manifest_optional()
            .expect("ícone do Windows");
    }

    // A janela só é compilada com a feature `gui`; o menu do terminal não
    // precisa do Slint.
    #[cfg(feature = "gui")]
    // fluent-dark: os controles padrão (caixas de marcar, números) no tema
    // escuro, combinando com o resto da janela
    slint_build::compile_with_config(
        "gui/janela.slint",
        slint_build::CompilerConfiguration::new().with_style("fluent-dark".into()),
    )
    .expect("janela.slint");
}
