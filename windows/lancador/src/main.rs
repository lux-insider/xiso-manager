//! Lançador do xiso-manager no Windows.
//!
//! O programa é o `xiso_manager.py`; este `.exe` só existe para ele abrir com
//! dois cliques, sem instalar Python: roda o script com o Python portátil
//! oficial ("embeddable", de python.org) que vai na pasta `python\` ao lado.
//! Argumentos e código de saída passam direto.
#![cfg_attr(not(windows), allow(dead_code))]

use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
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
        fn GetConsoleProcessList(lista: *mut u32, tamanho: u32) -> u32;
    }
    unsafe extern "system" fn tratar(_tipo: u32) -> i32 {
        1
    }
    pub fn deixar_ctrl_c_com_o_filho() {
        unsafe {
            SetConsoleCtrlHandler(Some(tratar), 1);
        }
    }

    /// Com o Python já fechado, o Ctrl+C volta a fechar o lançador.
    pub fn devolver_ctrl_c() {
        unsafe {
            SetConsoleCtrlHandler(Some(tratar), 0);
        }
    }

    /// Só este processo está no console: ele foi criado para o lançador
    /// (aberto com dois cliques) e fecha junto com ele.
    pub fn janela_so_minha() -> bool {
        let mut lista = [0u32; 4];
        unsafe { GetConsoleProcessList(lista.as_mut_ptr(), lista.len() as u32) == 1 }
    }
}

#[cfg(not(windows))]
mod console {
    pub fn devolver_ctrl_c() {}
    pub fn janela_so_minha() -> bool {
        false
    }
}

/// O Python saiu pelo Ctrl+C sem tratá-lo (STATUS_CONTROL_C_EXIT).
const SAIDA_CTRL_C_DO_WINDOWS: i32 = 0xC000_013A_u32 as i32;

/// Esperar ENTER antes de fechar? Só quando o programa terminou com erro (o
/// "❌ <motivo>" do erro fatal sai com 1; uma queda do Python, com outro
/// código) e a janela é só do lançador: aberta com dois cliques, ela fecha
/// junto com ele, antes de dar para ler. Num terminal aberto antes, a
/// mensagem continua lá. Saída normal e Ctrl+C (130) fecham na hora.
fn esperar_antes_de_fechar(codigo: i32, janela_so_minha: bool) -> bool {
    janela_so_minha && !matches!(codigo, 0 | 130 | SAIDA_CTRL_C_DO_WINDOWS)
}

fn esperar_enter() {
    eprint!("  Aperte ENTER para fechar...");
    io::stderr().flush().ok();
    let _ = io::stdin().lock().read_line(&mut String::new());
}

fn falhar(mensagem: &str) -> ! {
    eprintln!("\n  xiso-manager: {mensagem}\n");
    esperar_enter();
    exit(1);
}

/// "python314" para "python314.dll", "python314.zip" ou "python314._pth".
fn versao_do_nome(nome: &str) -> Option<String> {
    let nome = nome.to_ascii_lowercase();
    let (base, extensao) = nome.rsplit_once('.')?;
    let digitos = base.strip_prefix("python3")?;
    let versao = !digitos.is_empty() && digitos.bytes().all(|b| b.is_ascii_digit());
    (versao && matches!(extensao, "dll" | "zip" | "_pth")).then(|| base.to_string())
}

/// O primeiro arquivo do Python portátil que falta, se faltar algum. Sem a
/// DLL, a biblioteca padrão ou o `unicodedata` (o menu importa), o Python
/// cai com "Fatal Python error" e a janela fecha antes de dar para ler: é o
/// que sobra de uma descompactação pela metade ou de um antivírus que leva
/// um arquivo para a quarentena. Os nomes levam a versão do Python
/// (`python314.dll`), que vem do que estiver na pasta; sobras de outra
/// versão não atrapalham. Uma instalação normal copiada para a pasta
/// (`Lib\`, `DLLs\`) também serve.
fn falta_no_python(pasta: &Path) -> Option<PathBuf> {
    let exe = pasta.join("python.exe");
    if !exe.is_file() {
        return Some(exe);
    }
    let mut versoes: Vec<String> = std::fs::read_dir(pasta)
        .map(|itens| {
            itens
                .filter_map(|item| item.ok()?.file_name().into_string().ok())
                .filter_map(|nome| versao_do_nome(&nome))
                .collect()
        })
        .unwrap_or_default();
    if versoes.is_empty() {
        return Some(pasta.to_path_buf());
    }
    // A versão mais nova primeiro: é dela o arquivo que a mensagem aponta.
    versoes.sort_by(|a, b| (b.len(), b).cmp(&(a.len(), a)));
    let faltas: Vec<Option<PathBuf>> = versoes
        .iter()
        .map(|versao| {
            let dll = pasta.join(format!("{versao}.dll"));
            let zip = pasta.join(format!("{versao}.zip"));
            if !dll.is_file() {
                Some(dll)
            } else if !zip.is_file() && !pasta.join("Lib").is_dir() {
                Some(zip)
            } else {
                None
            }
        })
        .collect();
    if faltas.iter().all(Option::is_some) {
        return faltas.into_iter().next().flatten();
    }
    let unicodedata = pasta.join("unicodedata.pyd");
    if !unicodedata.is_file() && !pasta.join("DLLs").join("unicodedata.pyd").is_file() {
        return Some(unicodedata);
    }
    None
}

fn main() {
    let pasta = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."));
    let python = pasta.join("python").join("python.exe");
    let script = pasta.join("xiso_manager.py");

    if let Some(falta) = falta_no_python(&pasta.join("python")) {
        falhar(&format!(
            "não achei o Python portátil em {}. Descompacte a pasta inteira do xiso-manager, \
             sem tirar a subpasta \"python\".",
            falta.display()
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
        Ok(s) => {
            let codigo = s.code().unwrap_or(1);
            if esperar_antes_de_fechar(codigo, console::janela_so_minha()) {
                if codigo != 1 {
                    eprintln!("\n  xiso-manager: o Python fechou com erro (código {codigo:#X}).");
                }
                eprintln!();
                console::devolver_ctrl_c();
                esperar_enter();
            }
            exit(codigo)
        }
        Err(e) => falhar(&format!("não consegui abrir o Python ({e})")),
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn erro_na_janela_so_do_lancador_espera_enter() {
        assert!(esperar_antes_de_fechar(1, true));
        // uma queda do Python (acesso inválido à memória)
        assert!(esperar_antes_de_fechar(0xC000_0005_u32 as i32, true));
    }

    #[test]
    fn saida_normal_e_ctrl_c_fecham_na_hora() {
        for codigo in [0, 130, SAIDA_CTRL_C_DO_WINDOWS] {
            assert!(!esperar_antes_de_fechar(codigo, true), "{codigo:#X}");
        }
    }

    #[test]
    fn terminal_aberto_antes_nao_espera() {
        for codigo in [0, 1, 130, 0xC000_0005_u32 as i32] {
            assert!(!esperar_antes_de_fechar(codigo, false), "{codigo:#X}");
        }
    }
}
