fn main() {
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
