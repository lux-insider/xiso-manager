//! Ctrl+C, pedidos para sair (SIGTERM, terminal fechado com SIGHUP, a
//! janela do Windows fechada) e o que precisa ser desfeito antes de sair.
//!
//! - Ctrl+C: o menu e a ferramenta recebem juntos. A ferramenta (as -pt)
//!   cancela e apaga o que criou; o menu espera ela sair e volta.
//! - SIGTERM/SIGHUP com uma ferramenta rodando: o menu pede a ela que pare
//!   (SIGTERM), espera ela limpar, desfaz o que for preciso e só então sai,
//!   morto pelo mesmo sinal. Sem ferramenta, sai na hora.
//! - Windows, janela fechada: o sistema dá alguns segundos; o menu espera a
//!   ferramenta e desfaz a reescrita pendente do extract-xiso oficial.

use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use crate::{caminhos, log, saida};

/// Ctrl+C recebido e ainda não tratado.
pub static INTERROMPIDO: AtomicBool = AtomicBool::new(false);
/// O sinal de saída recebido (0: nenhum).
pub static ENCERRAR: AtomicI32 = AtomicI32::new(0);
/// O PID da ferramenta externa rodando agora (0: nenhuma).
pub static FILHO: AtomicI32 = AtomicI32::new(0);

/// Quanto esperar a ferramenta limpar o que criou.
pub const PRAZO_LIMPEZA: Duration = Duration::from_secs(30);
/// Windows: o sistema dá 5 s depois de fechar a janela.
#[cfg_attr(not(windows), allow(dead_code))]
pub const PRAZO_JANELA: Duration = Duration::from_secs(4);

#[cfg_attr(not(windows), allow(dead_code))]
pub const SIGTERM: i32 = 15;

pub fn interrompido() -> bool {
    INTERROMPIDO.swap(false, Ordering::SeqCst)
}

pub fn encerrar() -> Option<i32> {
    match ENCERRAR.load(Ordering::SeqCst) {
        0 => None,
        n => Some(n),
    }
}

// ── Reescritas do extract-xiso oficial em andamento ─────────────────────

/// O que é preciso para reconhecer o original de uma reescrita do
/// extract-xiso oficial, se ele ficar como "<nome>.old". O oficial renomeia
/// o original antes de começar e não trata sinal nenhum: interrompido, deixa
/// o ISO novo pela metade com o nome do original. O .old é o original se
/// não existia antes e tem o tamanho e a data dele (o rename preserva os dois).
#[derive(Clone, Debug)]
pub struct Marca {
    pub id: u64,
    pub origem: PathBuf,
    pub antigo: PathBuf,
    pub saida: PathBuf,
    assinatura: (u64, i128),
    havia_antigo: bool,
    havia_saida: bool,
}

static PENDENTES: Mutex<Vec<Marca>> = Mutex::new(Vec::new());
static PROXIMA: AtomicU64 = AtomicU64::new(1);

fn assinatura(caminho: &std::path::Path) -> Option<(u64, i128)> {
    let m = std::fs::metadata(caminho).ok()?;
    let quando = m
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as i128)
        .unwrap_or(0);
    Some((m.len(), quando))
}

fn existe_sem_seguir(caminho: &std::path::Path) -> bool {
    std::fs::symlink_metadata(caminho).is_ok()
}

pub fn marcar_original(origem: PathBuf, antigo: PathBuf, saida: PathBuf) -> Option<Marca> {
    let assinatura = assinatura(&origem)?;
    Some(Marca {
        id: PROXIMA.fetch_add(1, Ordering::SeqCst),
        havia_antigo: existe_sem_seguir(&antigo),
        havia_saida: existe_sem_seguir(&saida),
        origem,
        antigo,
        saida,
        assinatura,
    })
}

/// Depois de uma reescrita que não terminou bem: devolve o nome ao
/// original. Devolve (erro, desfeito): `desfeito` quando o original voltou
/// ao nome; `erro` quando ele não pôde voltar.
pub fn desfazer_reescrita(marca: &Marca) -> (Option<std::io::Error>, bool) {
    if marca.havia_antigo {
        return (None, false);
    }
    let Some(agora) = assinatura(&marca.antigo) else {
        return (None, false); // o original nem chegou a ser renomeado
    };
    if agora != marca.assinatura {
        return (None, false);
    }
    // A sobra pela metade em outra pasta, criada por esta reescrita, sai. Na
    // mesma pasta ela tem o nome do original e é coberta pelo rename.
    if !marca.havia_saida
        && !caminhos::mesma_pasta(
            &caminhos::pasta_de(&marca.saida),
            &caminhos::pasta_de(&marca.origem),
        )
    {
        let _ = std::fs::remove_file(&marca.saida);
    }
    match std::fs::rename(&marca.antigo, &marca.origem) {
        Ok(()) => (None, true),
        Err(e) => (Some(e), false),
    }
}

pub fn adicionar_pendente(marca: &Marca) {
    PENDENTES
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .push(marca.clone());
}

/// Tira a marca da lista; devolve se ela ainda estava lá (ninguém a
/// desfez antes, numa saída por sinal).
pub fn tirar_pendente(marca: &Marca) -> bool {
    let mut lista = PENDENTES.lock().unwrap_or_else(|e| e.into_inner());
    let antes = lista.len();
    lista.retain(|m| m.id != marca.id);
    lista.len() != antes
}

/// Desfaz as reescritas que não terminaram (na saída por sinal).
pub fn desfazer_pendentes() {
    let mut lista = PENDENTES.lock().unwrap_or_else(|e| e.into_inner());
    while let Some(marca) = lista.pop() {
        let _ = desfazer_reescrita(&marca);
    }
}

#[cfg(test)]
pub fn pendentes() -> usize {
    PENDENTES.lock().unwrap().len()
}

// ── Encerrar a ferramenta ───────────────────────────────────────────────

/// A ferramenta ainda está rodando?
#[cfg(unix)]
pub fn vivo(pid: i32) -> bool {
    let mut status = 0;
    let r = unsafe { libc::waitpid(pid, &mut status, libc::WNOHANG) };
    // 0: rodando; o próprio PID: acabou de sair (e foi recolhido); -1: não é
    // filho deste processo (o dono o recolhe), confere se ainda existe
    r == 0 || (r == -1 && unsafe { libc::kill(pid, 0) } == 0)
}

#[cfg(windows)]
pub fn vivo(pid: i32) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, WAIT_TIMEOUT};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
    };
    unsafe {
        let h = OpenProcess(PROCESS_SYNCHRONIZE, 0, pid as u32);
        if h.is_null() {
            return false;
        }
        let r = WaitForSingleObject(h, 0);
        CloseHandle(h);
        r == WAIT_TIMEOUT
    }
}

/// Espera a ferramenta sair, até o prazo. Devolve se ela saiu.
pub fn esperar_pid(pid: i32, prazo: Duration) -> bool {
    let fim = Instant::now() + prazo;
    while vivo(pid) {
        if Instant::now() >= fim {
            return false;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    true
}

/// Pede à ferramenta que pare (SIGTERM; no Windows, encerra).
pub fn pedir_parada(pid: i32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid, libc::SIGTERM);
    }
    #[cfg(windows)]
    matar(pid);
}

/// Encerra à força.
pub fn matar(pid: i32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid, libc::SIGKILL);
    }
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_TERMINATE, TerminateProcess,
        };
        let h = OpenProcess(PROCESS_TERMINATE, 0, pid as u32);
        if !h.is_null() {
            TerminateProcess(h, 1);
            CloseHandle(h);
        }
    }
}

/// A ferramenta não saiu no prazo depois do pedido: encerra à força
/// (SIGTERM, mais 5 s, SIGKILL).
pub fn forcar_fim(pid: i32) {
    if !vivo(pid) {
        return;
    }
    pedir_parada(pid);
    if !esperar_pid(pid, Duration::from_secs(5)) {
        matar(pid);
    }
}

// ── Saída por sinal ─────────────────────────────────────────────────────

/// Sai como sairia sem o tratador: morto pelo mesmo sinal, depois de
/// desfazer o que estava pendente.
pub fn sair_pelo_sinal(sinal: i32) -> ! {
    let filho = FILHO.load(Ordering::SeqCst);
    if filho > 0 && vivo(filho) {
        pedir_parada(filho);
        if !esperar_pid(filho, PRAZO_LIMPEZA) {
            forcar_fim(filho);
        }
    }
    desfazer_pendentes();
    log::info("Sessão encerrada");
    saida::descarregar();
    #[cfg(unix)]
    unsafe {
        libc::signal(sinal, libc::SIG_DFL);
        libc::kill(libc::getpid(), sinal);
        // o sinal pode demorar um instante para chegar
        std::thread::sleep(Duration::from_millis(200));
        libc::_exit(128 + sinal);
    }
    #[cfg(not(unix))]
    std::process::exit(128 + sinal);
}

/// Se um pedido para sair chegou, sai agora.
pub fn conferir_encerramento() {
    if let Some(sinal) = encerrar() {
        sair_pelo_sinal(sinal);
    }
}

// ── Tratadores ──────────────────────────────────────────────────────────

#[cfg(unix)]
extern "C" fn tratador(sinal: libc::c_int) {
    if sinal == libc::SIGINT {
        INTERROMPIDO.store(true, Ordering::SeqCst);
        return;
    }
    let primeiro = ENCERRAR.swap(sinal, Ordering::SeqCst) == 0;
    if sinal == libc::SIGHUP {
        // o terminal foi fechado: escrever nele dá erro de E/S
        saida::SILENCIADO.store(true, Ordering::SeqCst);
    }
    let filho = FILHO.load(Ordering::SeqCst);
    if filho > 0 && primeiro {
        unsafe {
            libc::kill(filho, libc::SIGTERM);
        }
    }
}

#[cfg(unix)]
fn ignorado(sinal: libc::c_int) -> bool {
    unsafe {
        let mut atual: libc::sigaction = std::mem::zeroed();
        libc::sigaction(sinal, std::ptr::null(), &mut atual) == 0
            && atual.sa_sigaction == libc::SIG_IGN
    }
}

#[cfg(unix)]
fn instalar_um(sinal: libc::c_int) {
    unsafe {
        let mut acao: libc::sigaction = std::mem::zeroed();
        acao.sa_sigaction = tratador as *const () as usize;
        acao.sa_flags = libc::SA_RESTART;
        libc::sigemptyset(&mut acao.sa_mask);
        libc::sigaction(sinal, &acao, std::ptr::null_mut());
    }
}

/// Instala os tratadores. Um sinal que já chega ignorado (o SIGHUP no
/// nohup, o Ctrl+C num programa em segundo plano) continua ignorado.
#[cfg(unix)]
pub fn instalar() {
    if !ignorado(libc::SIGINT) {
        instalar_um(libc::SIGINT);
    }
    instalar_um(libc::SIGTERM);
    if !ignorado(libc::SIGHUP) {
        instalar_um(libc::SIGHUP);
    }
}

/// Windows: a janela foi fechada, a sessão vai sair ou o sistema vai
/// desligar. O Windows avisa cada processo do console e encerra o menu
/// assim que esta função volta; as ferramentas -pt cancelam e apagam o que
/// criaram por conta própria. Aqui o menu espera a ferramenta sair (até
/// `PRAZO_JANELA`) e desfaz a reescrita pendente do extract-xiso oficial.
/// Ctrl+C e Ctrl+Break não são tratados aqui.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn ao_fechar_console(evento: u32, prazo: Duration) -> bool {
    // CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
    if !matches!(evento, 2 | 5 | 6) {
        return false;
    }
    let _ = ENCERRAR.compare_exchange(0, SIGTERM, Ordering::SeqCst, Ordering::SeqCst);
    saida::SILENCIADO.store(true, Ordering::SeqCst);
    let filho = FILHO.load(Ordering::SeqCst);
    if filho > 0 {
        esperar_pid(filho, prazo);
    }
    desfazer_pendentes();
    log::info("Sessão encerrada");
    true
}

#[cfg(windows)]
unsafe extern "system" fn tratador_console(evento: u32) -> i32 {
    match evento {
        // Ctrl+C e Ctrl+Break: o menu trata (e não morre no meio)
        0 | 1 => {
            INTERROMPIDO.store(true, Ordering::SeqCst);
            1
        }
        _ => ao_fechar_console(evento, PRAZO_JANELA) as i32,
    }
}

#[cfg(windows)]
pub fn instalar() {
    unsafe {
        windows_sys::Win32::System::Console::SetConsoleCtrlHandler(Some(tratador_console), 1);
    }
}

#[cfg(test)]
mod testes {
    use super::*;
    use std::sync::Mutex as Trava;

    // os testes mexem nos estados globais: um de cada vez
    pub static SERIAL: Trava<()> = Trava::new(());

    fn pasta(nome: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("xm-sinais-{}-{}", nome, std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    /// Uma reescrita interrompida já no disco: o original como .old e o
    /// novo pela metade no lugar dele.
    fn reescrita_interrompida(nome: &str) -> (PathBuf, Marca) {
        let dir = pasta(nome);
        let origem = dir.join("Halo.iso");
        std::fs::write(&origem, b"ISO ORIGINAL").unwrap();
        let antigo = dir.join("Halo.iso.old");
        let marca = marcar_original(origem.clone(), antigo.clone(), origem.clone()).unwrap();
        std::fs::rename(&origem, &antigo).unwrap();
        std::fs::write(&origem, b"pela metade").unwrap();
        (dir, marca)
    }

    fn limpar_estado() {
        ENCERRAR.store(0, Ordering::SeqCst);
        FILHO.store(0, Ordering::SeqCst);
        saida::SILENCIADO.store(false, Ordering::SeqCst);
        PENDENTES.lock().unwrap().clear();
    }

    #[test]
    fn janela_fechada_espera_a_ferramenta_e_desfaz() {
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        limpar_estado();
        let (dir, marca) = reescrita_interrompida("fechar");
        adicionar_pendente(&marca);
        let mut filho = std::process::Command::new(if cfg!(windows) { "ping" } else { "sleep" })
            .args(if cfg!(windows) {
                vec!["-n", "2", "127.0.0.1"]
            } else {
                vec!["0.5"]
            })
            .stdout(std::process::Stdio::null())
            .spawn()
            .unwrap();
        FILHO.store(filho.id() as i32, Ordering::SeqCst);
        assert!(ao_fechar_console(2, PRAZO_JANELA)); // CTRL_CLOSE_EVENT
        assert!(!vivo(filho.id() as i32), "esperou a ferramenta sair");
        let _ = filho.wait();
        assert_eq!(
            std::fs::read(dir.join("Halo.iso")).unwrap(),
            b"ISO ORIGINAL"
        );
        assert!(!dir.join("Halo.iso.old").exists());
        assert!(encerrar().is_some());
        assert_eq!(pendentes(), 0);
        limpar_estado();
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn ferramenta_que_nao_sai_tem_prazo() {
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        limpar_estado();
        let (dir, marca) = reescrita_interrompida("prazo");
        adicionar_pendente(&marca);
        let mut filho = std::process::Command::new(if cfg!(windows) { "ping" } else { "sleep" })
            .args(if cfg!(windows) {
                vec!["-n", "30", "127.0.0.1"]
            } else {
                vec!["30"]
            })
            .stdout(std::process::Stdio::null())
            .spawn()
            .unwrap();
        FILHO.store(filho.id() as i32, Ordering::SeqCst);
        let inicio = Instant::now();
        assert!(ao_fechar_console(6, Duration::from_millis(300))); // CTRL_SHUTDOWN_EVENT
        assert!(inicio.elapsed() < Duration::from_secs(3));
        assert_eq!(
            std::fs::read(dir.join("Halo.iso")).unwrap(),
            b"ISO ORIGINAL"
        );
        let _ = filho.kill();
        let _ = filho.wait();
        limpar_estado();
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn ctrl_c_nao_passa_por_aqui() {
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        limpar_estado();
        let (dir, marca) = reescrita_interrompida("ctrlc");
        adicionar_pendente(&marca);
        for evento in [0, 1] {
            assert!(!ao_fechar_console(evento, PRAZO_JANELA));
        }
        assert!(encerrar().is_none());
        assert_eq!(pendentes(), 1);
        limpar_estado();
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn old_que_ja_existia_nao_e_mexido() {
        let _g = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
        let dir = pasta("old");
        let origem = dir.join("Halo.iso");
        let antigo = dir.join("Halo.iso.old");
        std::fs::write(&origem, b"ISO ORIGINAL").unwrap();
        std::fs::write(&antigo, b"OUTRO").unwrap();
        let marca = marcar_original(origem.clone(), antigo.clone(), origem.clone()).unwrap();
        assert!(!desfazer_reescrita(&marca).1);
        assert_eq!(std::fs::read(&antigo).unwrap(), b"OUTRO");
        std::fs::remove_dir_all(dir).unwrap();
    }
}
