//! O lançador de verdade, copiado para uma pasta temporária (com espaço e
//! acento no caminho) ao lado de um Python portátil falso: o `python.exe` é
//! um script que anota os argumentos e sai com o código pedido. Os testes
//! rodam no Linux; o lançador procura os arquivos do mesmo jeito.
#![cfg(unix)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::Mutex;

// Os testes rodam em paralelo: um fork no meio da gravação de um executável
// de outro teste herda o arquivo aberto, e executá-lo dá "Text file busy".
// Gravar os executáveis e rodar o lançador é feito um teste de cada vez.
static UM_DE_CADA_VEZ: Mutex<()> = Mutex::new(());

const PYTHON: &[&str] = &[
    "python314.dll",
    "python314.zip",
    "python314._pth",
    "unicodedata.pyd",
];

struct Pacote {
    dir: PathBuf,
}

impl Pacote {
    fn novo(caso: &str) -> Pacote {
        let dir = std::env::temp_dir().join(format!("xm lançador {} {caso}", std::process::id()));
        let _vez = UM_DE_CADA_VEZ.lock().unwrap_or_else(|e| e.into_inner());
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("python")).unwrap();
        fs::copy(
            env!("CARGO_BIN_EXE_xiso-manager"),
            dir.join("xiso-manager.exe"),
        )
        .unwrap();
        fs::write(dir.join("xiso_manager.py"), "# programa\n").unwrap();
        let falso = dir.join("python").join("python.exe");
        fs::write(
            &falso,
            "#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done > \"$(dirname \"$0\")/../rodou.txt\"\nexit 7\n",
        )
        .unwrap();
        fs::set_permissions(&falso, fs::Permissions::from_mode(0o755)).unwrap();
        for nome in PYTHON {
            fs::write(dir.join("python").join(nome), "").unwrap();
        }
        Pacote { dir }
    }

    fn tirar(&self, nome: &str) -> PathBuf {
        let caminho = self.dir.join("python").join(nome);
        fs::remove_file(&caminho).unwrap();
        caminho
    }

    fn rodar(&self, argumentos: &[&str]) -> Output {
        let _vez = UM_DE_CADA_VEZ.lock().unwrap_or_else(|e| e.into_inner());
        Command::new(self.dir.join("xiso-manager.exe"))
            .args(argumentos)
            .current_dir(std::env::temp_dir())
            .stdin(Stdio::null())
            .output()
            .unwrap()
    }

    fn rodou(&self) -> Option<String> {
        fs::read_to_string(self.dir.join("rodou.txt")).ok()
    }
}

impl Drop for Pacote {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

fn conferir_falta(pacote: &Pacote, falta: &Path) {
    let saida = pacote.rodar(&[]);
    let erros = String::from_utf8_lossy(&saida.stderr);
    assert_eq!(saida.status.code(), Some(1), "{erros}");
    assert!(
        pacote.rodou().is_none(),
        "não pode chamar o Python sem {}",
        falta.display()
    );
    assert!(
        erros.contains(&format!(
            "não achei o Python portátil em {}.",
            falta.display()
        )),
        "{erros}"
    );
    assert!(
        erros.contains("Descompacte a pasta inteira do xiso-manager"),
        "{erros}"
    );
    assert!(erros.contains("Aperte ENTER para fechar..."), "{erros}");
}

#[test]
fn pacote_completo_roda_o_python_com_os_argumentos() {
    let pacote = Pacote::novo("completo");
    let saida = pacote.rodar(&["--lote", "C:\\Jogos com espaço\\Ação.iso"]);
    assert_eq!(
        saida.status.code(),
        Some(7),
        "o código de saída passa direto"
    );
    let script = pacote.dir.join("xiso_manager.py");
    assert_eq!(
        pacote.rodou().unwrap(),
        format!(
            "-X\nutf8\n{}\n--lote\nC:\\Jogos com espaço\\Ação.iso\n",
            script.display()
        )
    );
}

#[test]
fn falta_o_python_exe() {
    let pacote = Pacote::novo("sem exe");
    let falta = pacote.tirar("python.exe");
    conferir_falta(&pacote, &falta);
}

#[test]
fn falta_a_biblioteca_padrao() {
    let pacote = Pacote::novo("sem zip");
    let falta = pacote.tirar("python314.zip");
    conferir_falta(&pacote, &falta);
}

#[test]
fn falta_a_dll_do_python() {
    let pacote = Pacote::novo("sem dll");
    let falta = pacote.tirar("python314.dll");
    conferir_falta(&pacote, &falta);
}

#[test]
fn falta_o_unicodedata() {
    let pacote = Pacote::novo("sem unicodedata");
    let falta = pacote.tirar("unicodedata.pyd");
    conferir_falta(&pacote, &falta);
}

#[test]
fn pasta_python_sem_nada_da_versao() {
    let pacote = Pacote::novo("sem versao");
    for nome in ["python314.dll", "python314.zip", "python314._pth"] {
        pacote.tirar(nome);
    }
    conferir_falta(&pacote, &pacote.dir.join("python"));
}

#[test]
fn python_completo_em_vez_do_portatil() {
    // Uma instalação normal copiada para a pasta: a biblioteca padrão em
    // Lib\ e o unicodedata em DLLs\. Também serve.
    let pacote = Pacote::novo("instalacao");
    pacote.tirar("python314.zip");
    pacote.tirar("unicodedata.pyd");
    fs::create_dir_all(pacote.dir.join("python").join("Lib")).unwrap();
    fs::create_dir_all(pacote.dir.join("python").join("DLLs")).unwrap();
    fs::write(
        pacote
            .dir
            .join("python")
            .join("DLLs")
            .join("unicodedata.pyd"),
        "",
    )
    .unwrap();
    assert_eq!(pacote.rodar(&[]).status.code(), Some(7));
}

#[test]
fn sobra_de_outra_versao_nao_atrapalha() {
    let pacote = Pacote::novo("outra versao");
    fs::write(pacote.dir.join("python").join("python313._pth"), "").unwrap();
    fs::write(pacote.dir.join("python").join("python3.dll"), "").unwrap();
    assert_eq!(pacote.rodar(&[]).status.code(), Some(7));
}
