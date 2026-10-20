fn main() {
    // A janela só é compilada com a feature `gui`; o menu do terminal não
    // precisa do Slint.
    #[cfg(feature = "gui")]
    slint_build::compile("gui/janela.slint").expect("janela.slint");
}
