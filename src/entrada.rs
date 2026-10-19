//! O teclado. Uma thread lê as linhas da entrada padrão; cada pergunta
//! espera a próxima linha conferindo, enquanto isso, o Ctrl+C e os pedidos
//! para sair. Assim a pergunta é interrompida na hora no Linux e no Windows
//! (onde o console não devolve a leitura no Ctrl+C).

use std::io::BufRead;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use crate::estilo::{self, BRANC, CINZA, SIM_SETA, cor};
use crate::textos::t;
use crate::{saida, say, sinais};

pub enum Lido {
    Linha(String),
    /// A entrada acabou (Ctrl+D, pipe fechado).
    Fim,
    /// Ctrl+C.
    Interrompido,
}

static LINHAS: OnceLock<Mutex<Receiver<Option<String>>>> = OnceLock::new();

fn canal() -> &'static Mutex<Receiver<Option<String>>> {
    LINHAS.get_or_init(|| {
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let entrada = std::io::stdin();
            let mut trava = entrada.lock();
            loop {
                let mut bytes = Vec::new();
                match trava.read_until(b'\n', &mut bytes) {
                    Ok(0) | Err(_) => {
                        let _ = tx.send(None);
                        return;
                    }
                    Ok(_) => {
                        if bytes.last() == Some(&b'\n') {
                            bytes.pop();
                            if bytes.last() == Some(&b'\r') {
                                bytes.pop();
                            }
                        }
                        let linha = String::from_utf8_lossy(&bytes).into_owned();
                        if tx.send(Some(linha)).is_err() {
                            return;
                        }
                    }
                }
            }
        });
        Mutex::new(rx)
    })
}

/// Começa a ler o teclado (antes de qualquer pergunta).
pub fn iniciar() {
    canal();
}

/// Uma linha que chegou junto com um Ctrl+C: ela fica para a próxima
/// pergunta (o Ctrl+C vem primeiro, como no terminal).
static GUARDADA: Mutex<Option<Option<String>>> = Mutex::new(None);

fn como_lido(item: Option<String>) -> Lido {
    match item {
        Some(linha) => Lido::Linha(linha),
        None => Lido::Fim,
    }
}

/// Mostra o texto e espera uma linha. Um pedido para sair (SIGTERM, terminal
/// fechado) sai na hora, sem esperar a resposta.
pub fn ler(texto: &str) -> Lido {
    sinais::conferir_encerramento();
    saida::escrever(texto);
    saida::descarregar();
    let rx = canal().lock().unwrap_or_else(|e| e.into_inner());
    loop {
        sinais::conferir_encerramento();
        if sinais::interrompido() {
            return Lido::Interrompido;
        }
        if let Some(item) = GUARDADA.lock().unwrap_or_else(|e| e.into_inner()).take() {
            return como_lido(item);
        }
        let item = match rx.recv_timeout(Duration::from_millis(40)) {
            Ok(item) => item,
            Err(RecvTimeoutError::Disconnected) => None,
            Err(RecvTimeoutError::Timeout) => continue,
        };
        // O sinal e a linha podem chegar juntos (quem mata o menu costuma
        // fechar o pipe logo depois; o ENTER pode vir logo depois do
        // Ctrl+C): o sinal vem primeiro.
        std::thread::sleep(Duration::from_millis(1));
        sinais::conferir_encerramento();
        if sinais::interrompido() {
            *GUARDADA.lock().unwrap_or_else(|e| e.into_inner()) = Some(item);
            return Lido::Interrompido;
        }
        return como_lido(item);
    }
}

pub fn perguntar(texto: &str, obrigatorio: bool, padrao: Option<&str>) -> String {
    loop {
        let sufixo = match padrao {
            Some(p) if !p.is_empty() => cor(&format!(" [{p}]"), &[CINZA]),
            _ => String::new(),
        };
        let resp = match ler(&estilo::prompt(&format!(
            "{}{}",
            cor(texto, &[BRANC]),
            sufixo
        ))) {
            Lido::Linha(l) => l.trim().to_string(),
            Lido::Fim => return padrao.unwrap_or("").to_string(),
            Lido::Interrompido => {
                say!();
                return padrao.unwrap_or("").to_string();
            }
        };
        if resp.is_empty()
            && let Some(p) = padrao
        {
            return p.to_string();
        }
        if !resp.is_empty() || !obrigatorio {
            return resp;
        }
        estilo::aviso(t("obrigatorio"));
    }
}

/// Pergunta de sim/não. `no_ctrl_c` é a resposta quando o usuário aperta
/// Ctrl+C (por padrão, a mesma do ENTER). Nas confirmações vale "não":
/// quem aperta Ctrl+C quer desistir.
pub fn perguntar_sim_nao(texto: &str, padrao_sim: bool, no_ctrl_c: Option<bool>) -> bool {
    let sufixo = if padrao_sim {
        t("sim_nao_s")
    } else {
        t("sim_nao_n")
    };
    let pergunta = format!(
        "{} {}",
        cor(texto, &[BRANC]),
        cor(&format!("[{sufixo}]"), &[CINZA])
    );
    match ler(&estilo::prompt(&pergunta)) {
        Lido::Linha(l) => {
            let resp = l.trim().to_lowercase();
            if resp.is_empty() {
                padrao_sim
            } else {
                matches!(resp.as_str(), "s" | "sim" | "y" | "yes" | "1")
            }
        }
        Lido::Fim => {
            say!();
            padrao_sim
        }
        Lido::Interrompido => {
            say!();
            no_ctrl_c.unwrap_or(padrao_sim)
        }
    }
}

pub fn perguntar_inteiro(texto: &str, padrao: i64, minimo: i64, maximo: i64) -> i64 {
    let bruto = perguntar(texto, false, Some(&padrao.to_string()));
    match bruto.trim().parse::<i64>() {
        Ok(n) => n.clamp(minimo, maximo),
        Err(_) => padrao,
    }
}

pub fn pausar() {
    let texto = format!(
        "\n  {} {}",
        cor(SIM_SETA, &[CINZA]),
        cor(t("enter_continuar"), &[CINZA])
    );
    match ler(&texto) {
        Lido::Linha(_) => {}
        Lido::Fim | Lido::Interrompido => say!(),
    }
}
