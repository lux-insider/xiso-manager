//! Lançador do xiso-manager no Windows.
//!
//! O programa é o `xiso_manager.py`; este `.exe` só existe para ele abrir com
//! dois cliques, sem instalar Python: roda o script com o Python portátil
//! oficial ("embeddable", de python.org) que vai na pasta `python\` ao lado.
//! Argumentos e código de saída passam direto.
#![cfg_attr(not(windows), allow(dead_code))]

use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::process::{exit, Command};

#[cfg(windows)]
mod console {
    // Um handler que diz "tratei" deixa o Ctrl+C chegar ao Python (que para o
    // que estiver fazendo e espera o iso2god apagar a conversão incompleta)
    // sem matar este processo no meio. Não dá para usar "ignorar Ctrl+C" do
    // Windows: esse estado é herdado pelo processo filho, e o Python pararia de
    // receber o Ctrl+C.
    type Handler = unsafe extern "system" fn(u32) -> i32;
    #[link(name = "kernel32")]
    extern "system" {
        fn SetConsoleCtrlHandler(handler: Option<Handler>, add: i32) -> i32;
    }
    unsafe extern "system" fn tratar(_tipo: u32) -> i32 {
        1
    }
    pub fn deixar_ctrl_c_com_o_filho() {
        unsafe {
            SetConsoleCtrlHandler(Some(tratar), 1);
        }
    }
}

fn falhar(mensagem: &str) -> ! {
    eprintln!("\n  xiso-manager: {mensagem}\n");
    eprint!("  Aperte ENTER para fechar...");
    io::stderr().flush().ok();
    let _ = io::stdin().lock().read_line(&mut String::new());
    exit(1);
}

fn main() {
    let pasta = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."));
    let python = pasta.join("python").join("python.exe");
    let script = pasta.join("xiso_manager.py");

    if !python.is_file() {
        falhar(&format!(
            "não achei o Python portátil em {}. Descompacte a pasta inteira do xiso-manager, \
             sem tirar a subpasta \"python\".",
            python.display()
        ));
    }
    if !script.is_file() {
        falhar(&format!("não achei o programa em {}.", script.display()));
    }

    #[cfg(windows)]
    console::deixar_ctrl_c_com_o_filho();

    let status = Command::new(&python)
        .arg("-X")
        .arg("utf8")
        .arg(&script)
        .args(std::env::args_os().skip(1))
        .status();

    match status {
        Ok(s) => exit(s.code().unwrap_or(1)),
        Err(e) => falhar(&format!("não consegui abrir o Python ({e})")),
    }
}
