//! Tudo o que o menu escreve na tela passa por aqui.
//!
//! Um erro de escrita (terminal fechado, pipe sem leitor) é ignorado: o menu
//! não pode cair por isso no meio de uma limpeza. Depois que o terminal é
//! fechado (SIGHUP, ou a janela do Windows), a saída é calada de vez.

use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};

/// O terminal foi fechado: daqui em diante nada mais é escrito.
pub static SILENCIADO: AtomicBool = AtomicBool::new(false);

pub fn escrever(texto: &str) {
    if SILENCIADO.load(Ordering::SeqCst) {
        return;
    }
    let mut saida = std::io::stdout().lock();
    let _ = saida.write_all(texto.as_bytes());
}

/// Como o `print()`: o texto e uma quebra de linha.
pub fn linha(texto: &str) {
    let mut tudo = String::with_capacity(texto.len() + 1);
    tudo.push_str(texto);
    tudo.push('\n');
    escrever(&tudo);
}

pub fn descarregar() {
    if SILENCIADO.load(Ordering::SeqCst) {
        return;
    }
    let _ = std::io::stdout().flush();
}

/// `say!()` imprime uma linha vazia; `say!("{x}")`, o texto e a quebra.
#[macro_export]
macro_rules! say {
    () => {
        $crate::saida::linha("")
    };
    ($($arg:tt)*) => {
        $crate::saida::linha(&format!($($arg)*))
    };
}
