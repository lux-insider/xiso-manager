//! O log de execução (xiso-manager.log, ao lado do config.json), em UTF-8,
//! no formato "2026-10-18 21:00:00 [INFO] mensagem". Passando de 1 MiB, o
//! anterior vira xiso-manager.log.old na abertura.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::sync::Mutex;

use crate::config;

const LIMITE_LOG: u64 = 1024 * 1024;

static ARQUIVO: Mutex<Option<File>> = Mutex::new(None);

#[derive(Clone, Copy, Debug)]
pub enum Nivel {
    Info,
    Aviso,
    Erro,
}

/// Abre o log (e guarda o anterior, se passou do limite).
pub fn iniciar() {
    let pasta = config::dir_base();
    let _ = std::fs::create_dir_all(pasta);
    let arquivo = config::arq_log();
    if std::fs::metadata(&arquivo).is_ok_and(|m| m.is_file() && m.len() > LIMITE_LOG) {
        let antigo = pasta.join("xiso-manager.log.old");
        let _ = std::fs::remove_file(&antigo);
        let _ = std::fs::rename(&arquivo, &antigo);
    }
    let aberto = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&arquivo)
        .ok();
    *ARQUIVO.lock().unwrap_or_else(|e| e.into_inner()) = aberto;
}

pub fn evento(nivel: Nivel, mensagem: &str) {
    let mut guarda = ARQUIVO.lock().unwrap_or_else(|e| e.into_inner());
    let Some(arquivo) = guarda.as_mut() else {
        return;
    };
    let nome = match nivel {
        Nivel::Info => "INFO",
        Nivel::Aviso => "WARNING",
        Nivel::Erro => "ERROR",
    };
    let quando = chrono::Local::now().format("%Y-%m-%d %H:%M:%S");
    let _ = arquivo.write_all(format!("{quando} [{nome}] {mensagem}\n").as_bytes());
    let _ = arquivo.flush();
}

pub fn info(mensagem: &str) {
    evento(Nivel::Info, mensagem);
}
