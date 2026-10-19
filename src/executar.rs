//! Roda a ferramenta externa (extract-xiso-pt, extract-xiso, iso2god) com a
//! barra de progresso por cima e a saída dela num bloco recuado.
//!
//! - A entrada da ferramenta é vazia: o teclado é do menu.
//! - A saída é lida o tempo todo, inclusive enquanto a ferramenta cancela:
//!   uma ferramenta presa escrevendo num pipe cheio nunca chegaria a limpar.
//! - Ctrl+C: o menu espera a ferramenta apagar o que criou (até 30 s) e só
//!   força se ela não sair, ou se vier um segundo Ctrl+C.

use std::collections::{HashSet, VecDeque};
use std::ffi::OsString;
use std::io::Read;
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use regex::Regex;

use crate::disco::{fmt_bytes, fmt_tempo, fmt_velocidade};
use crate::estilo::{self, AMARE, BRANC, CIANO, CINZA, NEG, RESET, cor};
use crate::eventos::{self, evento_json, numero, sem_controle};
use crate::textos::t;
use crate::{caminhos, log, say, selecao, sinais, terminal};

/// Só as últimas linhas aparecem: guardar todas seria desperdício.
const LIMITE_BUFFER_SAIDA: usize = 200;
/// Uma linha maior que isto é cortada (a tela mostra 58 colunas).
const LIMITE_LINHA: usize = 256 * 1024;

pub struct Resultado {
    pub ok: bool,
    pub codigo: Option<i64>,
    pub duracao: f64,
    pub saida: VecDeque<String>,
    pub total_linhas: usize,
    pub erro: Option<String>,
    pub cancelado: bool,
}

impl Resultado {
    fn novo() -> Resultado {
        Resultado {
            ok: false,
            codigo: None,
            duracao: 0.0,
            saida: VecDeque::new(),
            total_linhas: 0,
            erro: None,
            cancelado: false,
        }
    }

    fn guardar(&mut self, linha: String) {
        if self.saida.len() == LIMITE_BUFFER_SAIDA {
            self.saida.pop_front();
        }
        self.saida.push_back(linha);
        self.total_linhas += 1;
    }
}

// ── Barra de progresso ──────────────────────────────────────────────────

/// label [████░░░░]  47.3%  1.20 GiB/2.54 GiB  8.40 MiB/s  ETA 02:41
///
/// A parte preenchida é pintada com o gradiente azul → verde, e a onda
/// desliza a cada quadro. Sem terminal, imprime uma linha só, no final.
pub struct Progresso {
    label: String,
    emoji: String,
    total: u64,
    inicio: Instant,
    quadro: u64,
    pub interativo: bool,
}

const LARGURA_BARRA: usize = 24;

impl Progresso {
    pub fn novo(label: &str, emoji: &str, total: u64) -> Progresso {
        Progresso {
            label: label.into(),
            emoji: emoji.into(),
            total,
            inicio: Instant::now(),
            quadro: 0,
            interativo: terminal::interativo(),
        }
    }

    fn pintar(&self, pct: f64, largura: usize) -> String {
        let cheio = (largura as f64 * pct) as usize;
        let usar_cor = estilo::usar_cor();
        let fase = (self.quadro as f64 * 0.03) % 1.0;
        let mut partes = String::new();
        for i in 0..largura {
            if i < cheio {
                let bruto = i as f64 / largura.saturating_sub(1).max(1) as f64 + fase;
                let ciclo = bruto % 1.0;
                let onda = if ciclo < 0.5 {
                    ciclo * 2.0
                } else {
                    (1.0 - ciclo) * 2.0
                };
                if usar_cor {
                    partes.push_str(&estilo::gradiente(onda));
                }
                partes.push('\u{2588}');
            } else {
                if usar_cor {
                    partes.push_str(CINZA);
                }
                partes.push('\u{2591}');
            }
        }
        if usar_cor {
            partes.push_str(RESET);
        }
        partes
    }

    pub fn atualizar(&mut self, feito: u64, final_: bool, detalhe: &str) {
        let decorrido = self.inicio.elapsed().as_secs_f64().max(0.001);
        let vel = feito as f64 / decorrido;
        let (pct, eta_txt) = if self.total > 0 {
            let pct = (feito as f64 / self.total as f64).min(1.0);
            let eta = if vel > 0.0 && self.total > feito {
                (self.total - feito) as f64 / vel
            } else {
                0.0
            };
            (pct, fmt_tempo(eta))
        } else {
            (0.0, "N/D".to_string())
        };

        if !self.interativo {
            if final_ {
                say!(
                    "  {} concluído: {} em {} ({})",
                    self.label,
                    fmt_bytes(feito as f64),
                    fmt_tempo(decorrido),
                    fmt_velocidade(vel)
                );
            }
            return;
        }

        let colunas = terminal::colunas() as i64;
        let rotulo = if self.emoji.is_empty() {
            self.label.clone()
        } else {
            format!("{} {}", self.emoji, self.label)
        };
        // Blocos em ordem de importância; o que não couber sai do fim para
        // trás. Uma linha maior que o terminal quebra, e aí o \r não apaga
        // mais o que sobrou: é assim que a barra vira lixo na tela.
        let essencial = format!("  {} [{}] 100.0%", rotulo, ".".repeat(LARGURA_BARRA));
        let mut sobra = colunas - estilo::largura_visivel(&essencial) as i64 - 1;
        let total_txt = if self.total > 0 {
            fmt_bytes(self.total as f64)
        } else {
            "N/D".into()
        };
        let bytes_txt = format!("  {}/{}", fmt_bytes(feito as f64), total_txt);
        let vel_txt = format!("  {}", fmt_velocidade(vel));
        let eta_completo = format!("  ETA {eta_txt}");
        let det_txt = if detalhe.is_empty() {
            String::new()
        } else {
            format!("  {}", estilo::cortar(detalhe, 18))
        };
        let mut extras: Vec<(usize, &str)> = Vec::new();
        for (i, (texto, _)) in [
            (&bytes_txt, ""),
            (&vel_txt, AMARE),
            (&eta_completo, CINZA),
            (&det_txt, CINZA),
        ]
        .iter()
        .enumerate()
        {
            if texto.is_empty() {
                continue;
            }
            let w = estilo::largura_visivel(texto) as i64;
            if w <= sobra {
                sobra -= w;
                extras.push((i, texto));
            } else {
                break;
            }
        }
        // se nem o essencial couber, encolhe a barra
        let mut largura_barra = LARGURA_BARRA;
        while largura_barra > 8
            && estilo::largura_visivel(&format!(
                "  {} [{}] 100.0%",
                rotulo,
                ".".repeat(largura_barra)
            )) as i64
                > colunas - 1
        {
            largura_barra -= 2;
        }
        let mut linha = format!(
            "  {} {}{}{} {}",
            rotulo,
            cor("[", &[CINZA]),
            self.pintar(pct, largura_barra),
            cor("]", &[CINZA]),
            cor(&format!("{:5.1}%", pct * 100.0), &[BRANC, NEG])
        );
        for (i, texto) in extras {
            match i {
                0 => linha.push_str(&format!(
                    "  {}{}{}",
                    cor(&fmt_bytes(feito as f64), &[CIANO]),
                    cor("/", &[CINZA]),
                    cor(&total_txt, &[CINZA])
                )),
                1 => linha.push_str(&cor(texto, &[AMARE])),
                _ => linha.push_str(&cor(texto, &[CINZA])),
            }
        }
        // \x1b[2K apaga a linha inteira, não só o que o cursor cobre
        estilo::escrever(&format!("\r\x1b[2K{linha}"));
        if final_ {
            estilo::escrever("\n");
        }
        self.quadro += 1;
    }

    pub fn limpar(&self) {
        if self.interativo {
            estilo::escrever("\r\x1b[2K");
        }
    }

    pub fn decorrido(&self) -> f64 {
        self.inicio.elapsed().as_secs_f64()
    }
}

/// Acompanha o progresso pelo que já foi gravado no destino (ou pelo
/// percentual que a ferramenta imprime) e desenha a barra numa thread.
struct Fiscal {
    barra: Arc<Mutex<Progresso>>,
    estado: Arc<Mutex<(Option<f64>, String)>>,
    parar: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

/// Guarda contra varrer uma árvore gigante a cada tique.
const LIMITE_ENTRADAS: usize = 20000;

fn medir(destino: &Option<PathBuf>, alvo_arquivo: bool, intervalo: &mut f64) -> u64 {
    let Some(destino) = destino else {
        return 0;
    };
    if alvo_arquivo {
        return std::fs::metadata(destino).map(|m| m.len()).unwrap_or(0);
    }
    if !destino.exists() {
        return 0;
    }
    let mut total = 0u64;
    let mut vistos = 0usize;
    let mut grande = false;
    caminhos::percorrer(destino, &mut |arquivo| {
        if let Ok(m) = std::fs::metadata(arquivo) {
            total += m.len();
        }
        vistos += 1;
        if vistos > LIMITE_ENTRADAS {
            grande = true;
            return false;
        }
        true
    });
    if grande {
        // árvore grande demais: afrouxa o intervalo em vez de travar a barra
        *intervalo = (*intervalo * 2.0).min(3.0);
    }
    total
}

impl Fiscal {
    fn iniciar(
        label: &str,
        emoji: &str,
        destino: Option<PathBuf>,
        total: u64,
        alvo_arquivo: bool,
        descontar_inicio: bool,
    ) -> Fiscal {
        let barra = Arc::new(Mutex::new(Progresso::novo(label, emoji, total)));
        let estado = Arc::new(Mutex::new((None, String::new())));
        let parar = Arc::new(AtomicBool::new(false));
        let interativo = barra.lock().unwrap().interativo;
        let thread = interativo.then(|| {
            let mut intervalo = 0.25f64;
            // Na reescrita na mesma pasta o arquivo de saída começa com o
            // tamanho do original: descontar faria a barra parar em 0.
            let base = if descontar_inicio {
                medir(&destino, alvo_arquivo, &mut intervalo)
            } else {
                0
            };
            let (barra, estado, parar) = (barra.clone(), estado.clone(), parar.clone());
            std::thread::spawn(move || {
                while !parar.load(Ordering::SeqCst) {
                    let (pct, detalhe) = estado.lock().unwrap().clone();
                    let feito = match pct {
                        Some(p) if total > 0 => (total as f64 * p) as u64,
                        _ => medir(&destino, alvo_arquivo, &mut intervalo).saturating_sub(base),
                    };
                    let mostrar = if total > 0 { feito.min(total) } else { feito };
                    barra.lock().unwrap().atualizar(mostrar, false, &detalhe);
                    let fim = Instant::now() + Duration::from_secs_f64(intervalo);
                    while Instant::now() < fim && !parar.load(Ordering::SeqCst) {
                        std::thread::sleep(Duration::from_millis(20));
                    }
                }
            })
        });
        Fiscal {
            barra,
            estado,
            parar,
            thread,
        }
    }

    fn percentual(&self, pct: f64) {
        self.estado.lock().unwrap().0 = Some(pct);
    }

    fn detalhe(&self, texto: &str) {
        self.estado.lock().unwrap().1 = texto.to_string();
    }

    fn encerrar(&mut self) -> f64 {
        self.parar.store(true, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
        let barra = self.barra.lock().unwrap();
        barra.limpar();
        barra.decorrido()
    }
}

// ── Leitura da saída ────────────────────────────────────────────────────

fn re_percentual() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(\d{1,3})\s?%").unwrap())
}

/// O que a leitura de uma linha pede à barra.
#[derive(Debug, PartialEq)]
pub enum Aviso {
    Percentual(f64),
    Detalhe(String),
}

/// Monta as linhas da saída da ferramenta e decide o que vai para a tela.
pub struct Leitor {
    pub resultado: Resultado,
    pendente: Vec<u8>,
    // O verificar --progresso-json do extract-xiso-pt manda o erro duas
    // vezes: como evento JSON e como texto ("❌ <mensagem>", ou "X" no
    // console antigo do Windows). Fica só a primeira que chegar.
    erros_evento: HashSet<String>,
    erros_texto: HashSet<String>,
}

impl Leitor {
    pub fn novo() -> Leitor {
        Leitor {
            resultado: Resultado::novo(),
            pendente: Vec::new(),
            erros_evento: HashSet::new(),
            erros_texto: HashSet::new(),
        }
    }

    /// Um pedaço lido da ferramenta. A quebra é procurada só no pedaço novo,
    /// e a linha em montagem tem limite: uma linha de 32 MiB sem quebra não
    /// pode custar minutos nem centenas de MiB.
    pub fn alimentar(&mut self, pedaco: &[u8], avisos: &mut dyn FnMut(Aviso)) {
        let mut inicio = 0;
        for (i, &b) in pedaco.iter().enumerate() {
            if b == b'\r' || b == b'\n' {
                self.juntar(&pedaco[inicio..i]);
                let bruto = std::mem::take(&mut self.pendente);
                let linha = eventos::limpar_linha_externa(&String::from_utf8_lossy(&bruto));
                if !linha.is_empty() {
                    self.tratar(linha, avisos);
                }
                inicio = i + 1;
            }
        }
        self.juntar(&pedaco[inicio..]);
    }

    fn juntar(&mut self, trecho: &[u8]) {
        let cabe = LIMITE_LINHA
            .saturating_sub(self.pendente.len())
            .min(trecho.len());
        self.pendente.extend_from_slice(&trecho[..cabe]);
    }

    /// O que sobrou sem quebra no fim vai direto para a saída.
    pub fn terminar(&mut self) {
        let bruto = std::mem::take(&mut self.pendente);
        let resto = eventos::limpar_linha_externa(&String::from_utf8_lossy(&bruto));
        if !resto.is_empty() {
            self.resultado.guardar(resto);
        }
    }

    fn tratar(&mut self, linha: String, avisos: &mut dyn FnMut(Aviso)) {
        let evento = evento_json(&linha);
        if let Some(ev) = &evento {
            if ev.get("evento").and_then(|v| v.as_str()) == Some("erro") {
                let mensagem = eventos::limpar_linha_externa(eventos::texto(ev.get("mensagem")));
                if self.erros_texto.contains(&mensagem) {
                    return;
                }
                self.erros_evento.insert(mensagem);
            }
        } else {
            let (marca, mensagem) = linha.split_once(' ').unwrap_or((&linha, ""));
            if (marca == "\u{274C}" || marca == "X") && !mensagem.is_empty() {
                if self.erros_evento.contains(mensagem) {
                    return;
                }
                self.erros_texto.insert(mensagem.to_string());
            }
        }
        let mut linha = linha;
        if let Some(ev) = &evento {
            match ev.get("evento").and_then(|v| v.as_str()) {
                Some("verificado") => {
                    for l in eventos::linhas_verificado(ev) {
                        self.resultado.guardar(sem_controle(&l));
                    }
                    return;
                }
                Some("progresso") => {
                    let total_bytes = numero(ev.get("total_bytes")).unwrap_or(0.0);
                    let feito = numero(ev.get("bytes")).unwrap_or(0.0);
                    if total_bytes > 0.0 {
                        avisos(Aviso::Percentual((feito / total_bytes).clamp(0.0, 1.0)));
                    }
                    return;
                }
                _ => {}
            }
            linha = sem_controle(&eventos::texto_evento(ev));
            if linha.is_empty() {
                return;
            }
        }
        // ferramentas que usam \r repetem a mesma linha várias vezes
        if self.resultado.saida.back() == Some(&linha) {
            return;
        }
        let achou = re_percentual().captures(&linha).map(|c| c[1].to_string());
        let detalhe: String = caminhos::nome_base_str(&linha).chars().take(20).collect();
        self.resultado.guardar(linha);
        match achou {
            Some(numero) => {
                let valor = numero
                    .chars()
                    .try_fold(0u32, |n, c| selecao::valor_decimal(c).map(|d| n * 10 + d));
                if let Some(v) = valor {
                    avisos(Aviso::Percentual((v as f64 / 100.0).min(1.0)));
                }
            }
            None => avisos(Aviso::Detalhe(detalhe)),
        }
    }
}

// ── Executar ────────────────────────────────────────────────────────────

pub struct Opcoes<'a> {
    pub emoji: &'a str,
    pub destino: Option<PathBuf>,
    pub total: u64,
    pub mostrar_saida: bool,
    pub max_linhas: usize,
    pub alvo_arquivo: bool,
    pub descontar_inicio: bool,
}

impl Default for Opcoes<'_> {
    fn default() -> Self {
        Opcoes {
            emoji: "",
            destino: None,
            total: 0,
            mostrar_saida: true,
            max_linhas: 10,
            alvo_arquivo: false,
            descontar_inicio: true,
        }
    }
}

/// O código de saída como o Python mostrava: negativo quando a ferramenta
/// morreu por um sinal; no Windows, o número sem sinal.
pub fn codigo_de(status: ExitStatus) -> i64 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(s) = status.signal() {
            return -(s as i64);
        }
        status.code().unwrap_or(1) as i64
    }
    #[cfg(windows)]
    {
        status.code().map(|c| c as u32 as i64).unwrap_or(1)
    }
}

/// Encerra à força a ferramenta que é nossa (SIGTERM, 5 s, SIGKILL).
fn forcar(filho: &mut Child) {
    if let Ok(Some(_)) = filho.try_wait() {
        return;
    }
    sinais::pedir_parada(filho.id() as i32);
    let fim = Instant::now() + Duration::from_secs(5);
    while Instant::now() < fim {
        if let Ok(Some(_)) = filho.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = filho.kill();
    let _ = filho.wait();
}

/// Espera a ferramenta sair (a saída continua sendo esvaziada). Passado o
/// prazo, ou num segundo Ctrl+C, força.
fn esperar_ferramenta(filho: &mut Child, prazo: Duration) {
    let fim = Instant::now() + prazo;
    loop {
        match filho.try_wait() {
            Ok(Some(_)) | Err(_) => return,
            Ok(None) => {}
        }
        if Instant::now() >= fim || sinais::interrompido() {
            forcar(filho);
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// O vigia do pedido para sair: a ferramenta recebeu SIGTERM; se não sair
/// no prazo, é encerrada à força. `terminou` avisa que ela já saiu.
fn vigiar(pid: i32, terminou: Arc<AtomicBool>) {
    std::thread::spawn(move || {
        let esperar = |prazo: Duration| {
            let fim = Instant::now() + prazo;
            while Instant::now() < fim {
                if terminou.load(Ordering::SeqCst) {
                    return true;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            terminou.load(Ordering::SeqCst)
        };
        if esperar(sinais::PRAZO_LIMPEZA) {
            return;
        }
        sinais::pedir_parada(pid);
        if !esperar(Duration::from_secs(5)) {
            sinais::matar(pid);
        }
    });
}

fn abrir(cmd: &[OsString]) -> std::io::Result<(Child, std::io::PipeReader)> {
    let (leitor, escritor) = std::io::pipe()?;
    let escritor_erro = escritor.try_clone()?;
    let mut comando = Command::new(&cmd[0]);
    comando
        .args(&cmd[1..])
        .stdin(Stdio::null())
        .stdout(escritor)
        .stderr(escritor_erro);
    let filho = comando.spawn()?;
    // o Command guarda as pontas de escrita: sem soltá-las, a leitura nunca
    // veria o fim da saída
    drop(comando);
    Ok((filho, leitor))
}

/// Roda a ferramenta com a barra por cima. Mostra a saída e o resultado.
pub fn executar(cmd: &[OsString], label: &str, op: Opcoes) -> Resultado {
    sinais::conferir_encerramento();
    // O comando completo vai só para o log: cortado na tela, não informa nada.
    log::info(&format!(
        "cmd: {}",
        cmd.iter()
            .map(|a| caminhos::para_log(a))
            .collect::<Vec<_>>()
            .join(" ")
    ));

    let mut fiscal = Fiscal::iniciar(
        label,
        op.emoji,
        op.destino.clone(),
        op.total,
        op.alvo_arquivo,
        op.descontar_inicio,
    );
    let mut leitor = Leitor::novo();

    match abrir(cmd) {
        Err(e) => {
            leitor.resultado.erro = Some(match e.kind() {
                std::io::ErrorKind::NotFound => t("erro_bin_sumiu").to_string(),
                std::io::ErrorKind::PermissionDenied => t("erro_permissao").to_string(),
                _ => {
                    let texto = caminhos::erro_os(&e, Some(std::path::Path::new(&cmd[0])));
                    log::evento(log::Nivel::Erro, &texto);
                    format!("{}: {}", t("erro_inesperado"), texto)
                }
            });
        }
        Ok((mut filho, mut saida)) => {
            let pid = filho.id() as i32;
            sinais::FILHO.store(pid, Ordering::SeqCst);
            let descartar = Arc::new(AtomicBool::new(false));
            let (tx, rx) = mpsc::channel::<Vec<u8>>();
            let leitura = {
                let descartar = descartar.clone();
                std::thread::spawn(move || {
                    let mut buf = vec![0u8; 4096];
                    loop {
                        match saida.read(&mut buf) {
                            Ok(0) => return,
                            Ok(n) => {
                                if !descartar.load(Ordering::SeqCst)
                                    && tx.send(buf[..n].to_vec()).is_err()
                                {
                                    descartar.store(true, Ordering::SeqCst);
                                }
                            }
                            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                            Err(_) => return,
                        }
                    }
                })
            };
            let terminou = Arc::new(AtomicBool::new(false));
            let mut vigia = false;
            let mut interrompido = false;
            let mut avisar = |a: Aviso| match a {
                Aviso::Percentual(p) => fiscal.percentual(p),
                Aviso::Detalhe(d) => fiscal.detalhe(&d),
            };
            loop {
                if sinais::interrompido() {
                    interrompido = true;
                    break;
                }
                if sinais::encerrar().is_some() && !vigia {
                    vigia = true;
                    vigiar(pid, terminou.clone());
                }
                match rx.recv_timeout(Duration::from_millis(40)) {
                    Ok(pedaco) => leitor.alimentar(&pedaco, &mut avisar),
                    Err(RecvTimeoutError::Timeout) => {}
                    Err(RecvTimeoutError::Disconnected) => break,
                }
            }
            if !interrompido {
                leitor.terminar();
                // a saída acabou; a ferramenta pode levar um instante para sair
                loop {
                    match filho.try_wait() {
                        Ok(Some(status)) => {
                            leitor.resultado.codigo = Some(codigo_de(status));
                            leitor.resultado.ok = status.success();
                            break;
                        }
                        Ok(None) => {}
                        Err(_) => break,
                    }
                    if sinais::interrompido() {
                        interrompido = true;
                        break;
                    }
                    if sinais::encerrar().is_some() && !vigia {
                        vigia = true;
                        vigiar(pid, terminou.clone());
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
            }
            if interrompido {
                // O Ctrl+C chegou à ferramenta também (mesmo grupo de
                // processos / mesmo console): ela para e APAGA a saída
                // incompleta. Encerrá-la agora interromperia essa limpeza;
                // então primeiro esperamos, esvaziando a saída.
                leitor.resultado.ok = false;
                leitor.resultado.codigo = None;
                leitor.resultado.cancelado = true;
                leitor.resultado.erro = Some(t("interrompido").to_string());
                descartar.store(true, Ordering::SeqCst);
                drop(rx);
                esperar_ferramenta(&mut filho, sinais::PRAZO_LIMPEZA);
            } else if sinais::encerrar().is_some() {
                // parou porque o menu recebeu SIGTERM/SIGHUP: é um cancelamento
                leitor.resultado.ok = false;
                leitor.resultado.cancelado = true;
                leitor.resultado.erro = Some(t("interrompido").to_string());
            }
            terminou.store(true, Ordering::SeqCst);
            sinais::FILHO.store(0, Ordering::SeqCst);
            let fim = Instant::now() + Duration::from_secs(1);
            while !leitura.is_finished() && Instant::now() < fim {
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }
    let mut r = leitor.resultado;
    r.duracao = fiscal.encerrar();

    // a linha final da barra, já com o número real
    if r.ok && op.total > 0 {
        fiscal.barra.lock().unwrap().atualizar(op.total, true, "");
    }
    if op.mostrar_saida && !r.saida.is_empty() {
        let linhas: Vec<String> = r.saida.iter().cloned().collect();
        let bloco = estilo::bloco_saida(&linhas, op.max_linhas, r.total_linhas);
        if !bloco.is_empty() {
            say!("{bloco}");
        }
    }
    if r.cancelado {
        estilo::aviso(r.erro.as_deref().unwrap_or(""));
    } else if let Some(erro) = &r.erro {
        estilo::erro(erro);
    } else if r.ok {
        estilo::marca_ok(
            label,
            &format!("{} {}", t("concluido"), fmt_tempo(r.duracao)),
        );
    } else {
        estilo::marca_falha(
            label,
            &format!(
                "{} {}",
                t("erro_codigo"),
                r.codigo
                    .map(|c| c.to_string())
                    .unwrap_or_else(|| "None".into())
            ),
        );
    }
    r
}

#[cfg(test)]
mod testes {
    use super::*;

    fn ler(pedacos: &[&[u8]]) -> (Vec<String>, Vec<Aviso>) {
        let mut l = Leitor::novo();
        let mut avisos = Vec::new();
        for p in pedacos {
            l.alimentar(p, &mut |a| avisos.push(a));
        }
        l.terminar();
        (l.resultado.saida.into_iter().collect(), avisos)
    }

    #[test]
    fn acento_partido_entre_dois_pedacos() {
        let texto = format!("{}çé fim\n", "a".repeat(4095));
        let bytes = texto.as_bytes();
        let (linhas, _) = ler(&[&bytes[..4096], &bytes[4096..]]);
        assert_eq!(linhas, vec![format!("{}çé fim", "a".repeat(4095))]);
    }

    const MSG: &str = "a imagem está truncada: termina no setor 1000, o volume declara 2000";

    #[test]
    fn erro_do_verificar_uma_vez_so() {
        let evento = format!("{{\"evento\": \"erro\", \"mensagem\": \"{MSG}\"}}\n");
        let texto = format!("\u{274c} {MSG}\n");
        let (linhas, _) = ler(&[evento.as_bytes(), texto.as_bytes()]);
        assert_eq!(linhas, vec![format!("erro: {MSG}")]);
        let antes = format!("X  {MSG}\n");
        let (linhas, _) = ler(&[antes.as_bytes(), evento.as_bytes()]);
        assert_eq!(linhas, vec![format!("X {MSG}")]);
    }

    #[test]
    fn progresso_e_percentual() {
        let (linhas, avisos) = ler(&[
            b"{\"evento\": \"progresso\", \"bytes\": 500, \"total_bytes\": 1000}\n",
            b"{\"evento\": \"progresso\", \"bytes\": \"muito\", \"total_bytes\": \"x\"}\n",
            b"copiando 42% feito\r\r\n",
            b"pasta/arquivo.bin\n",
            b"pasta/arquivo.bin\n",
        ]);
        assert_eq!(linhas, vec!["copiando 42% feito", "pasta/arquivo.bin"]);
        assert_eq!(
            avisos,
            vec![
                Aviso::Percentual(0.5),
                Aviso::Percentual(0.42),
                Aviso::Detalhe("arquivo.bin".into())
            ]
        );
    }

    #[test]
    fn linha_enorme_tem_limite() {
        let bloco = vec![b'x'; 1024 * 1024];
        let mut l = Leitor::novo();
        let inicio = Instant::now();
        for _ in 0..32 {
            l.alimentar(&bloco, &mut |_| {});
        }
        l.alimentar(b"\nfim da linha enorme\n", &mut |_| {});
        assert!(inicio.elapsed() < Duration::from_secs(10));
        let linhas: Vec<_> = l.resultado.saida.iter().collect();
        assert_eq!(linhas[0].len(), LIMITE_LINHA);
        assert_eq!(linhas[1], "fim da linha enorme");
    }

    #[test]
    fn controles_saem_da_linha() {
        let (linhas, _) = ler(&[
            "título \x1b]0;janela\x07 com \x1b[2J limpar \u{9b}31m e \u{202e}etxet\n".as_bytes(),
        ]);
        assert_eq!(linhas, vec!["título com limpar e etxet"]);
    }
}
