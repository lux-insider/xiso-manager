//! Uma ferramenta rodando para a janela: lê os eventos do
//! `--progresso-json` e avisa a janela do andamento e do fim.
//!
//! O cancelamento fecha a entrada da ferramenta (`--cancelar-pela-entrada`):
//! ela para no próximo ponto seguro e apaga o que criou, igual ao Ctrl+C.
//! Funciona igual no Windows, onde um programa de janela não tem console
//! para mandar Ctrl+C.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::process::{ChildStdin, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::Value;
use xiso_manager::{caminhos, ferramentas, log};

/// Uma atualização da barra.
pub struct Andamento {
    pub fase: String,
    pub feito: u64,
    pub total: u64,
    pub velocidade: f64,
    pub eta: Option<f64>,
}

pub enum Fim {
    /// Deu certo. `pasta`: onde ficou o resultado, quando a ferramenta diz.
    Ok {
        pasta: Option<String>,
        mensagem: String,
        verificado: Option<Value>,
    },
    /// `verificar`: a imagem foi lida inteira, mas não confere (código 2).
    NaoConfere(Value),
    Cancelado,
    Erro(String),
}

/// Fecha a entrada da ferramenta, que é o pedido para cancelar.
#[derive(Clone)]
pub struct Cancelador(Arc<Mutex<Option<ChildStdin>>>);

impl Cancelador {
    pub fn cancelar(&self) {
        self.0.lock().unwrap_or_else(|e| e.into_inner()).take();
    }
}

/// Quantas vezes por segundo a barra é redesenhada, no máximo. Mais que
/// isso o olho não vê, e só gastaria processador.
const INTERVALO: Duration = Duration::from_millis(66);

/// Roda `cmd` em segundo plano. `ao_andar` e `ao_fim` são chamados na
/// thread de leitura; quem chama leva o resultado para a janela.
pub fn iniciar(
    cmd: Vec<OsString>,
    ao_andar: impl Fn(Andamento) + Send + 'static,
    ao_fim: impl FnOnce(Fim) + Send + 'static,
) -> Result<Cancelador, String> {
    if !ferramentas::cancela_pela_entrada(&cmd) {
        return Err(format!(
            "{} não aceita --cancelar-pela-entrada: atualize para o extract-xiso-pt 0.2.4 \
             e o iso2god-pt 0.1.6 ou mais novos.",
            caminhos::exibir(&cmd[0])
        ));
    }
    log::info(&format!(
        "cmd (janela): {}",
        cmd.iter()
            .map(|a| caminhos::para_log(a))
            .collect::<Vec<_>>()
            .join(" ")
    ));
    let mut filho = ferramentas::novo_comando(&cmd[0])
        .args(&cmd[1..])
        .arg("--cancelar-pela-entrada")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| caminhos::erro_os(&e, Some(std::path::Path::new(&cmd[0]))))?;
    let cancelador = Cancelador(Arc::new(Mutex::new(filho.stdin.take())));
    let saida = filho.stdout.take().expect("stdout ligado");
    let erros = filho.stderr.take().expect("stderr ligado");

    // stderr: as últimas linhas, para explicar uma falha sem evento `erro`
    let ultimas = std::thread::spawn(move || {
        let mut linhas = VecDeque::new();
        let mut leitor = BufReader::new(erros);
        let mut buf = Vec::new();
        while leitor.read_until(b'\n', &mut buf).is_ok_and(|n| n > 0) {
            let linha = String::from_utf8_lossy(&buf).trim().to_string();
            if !linha.is_empty() {
                if linhas.len() == 8 {
                    linhas.pop_front();
                }
                linhas.push_back(linha);
            }
            buf.clear();
        }
        linhas
    });

    let guarda = cancelador.clone();
    std::thread::Builder::new()
        .name("tarefa".into())
        .spawn(move || {
            let mut andamento = Andamento {
                fase: String::new(),
                feito: 0,
                total: 0,
                velocidade: 0.0,
                eta: None,
            };
            let mut ultimo = Instant::now() - INTERVALO;
            let (mut pasta, mut mensagem, mut erro, mut verificado) = (None, None, None, None);
            let mut leitor = BufReader::new(saida);
            let mut buf = Vec::new();
            loop {
                buf.clear();
                match leitor.read_until(b'\n', &mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {}
                }
                let Ok(Value::Object(ev)) = serde_json::from_slice::<Value>(&buf) else {
                    continue;
                };
                let texto = |k: &str| ev.get(k).and_then(Value::as_str).map(String::from);
                match ev.get("evento").and_then(Value::as_str) {
                    Some("fase") => {
                        if let Some(m) = texto("mensagem") {
                            andamento.fase = m;
                        }
                        ultimo = Instant::now();
                        ao_andar(copia(&andamento));
                    }
                    Some("progresso") => {
                        andamento.feito = ev.get("bytes").and_then(Value::as_u64).unwrap_or(0);
                        andamento.total =
                            ev.get("total_bytes").and_then(Value::as_u64).unwrap_or(0);
                        andamento.velocidade = ev
                            .get("velocidade_bps")
                            .and_then(Value::as_f64)
                            .unwrap_or(0.0);
                        andamento.eta = ev.get("eta_segundos").and_then(Value::as_f64);
                        let fim = andamento.total > 0 && andamento.feito >= andamento.total;
                        if fim || ultimo.elapsed() >= INTERVALO {
                            ultimo = Instant::now();
                            ao_andar(copia(&andamento));
                        }
                    }
                    Some("concluido") => {
                        pasta = texto("pasta").filter(|p| !p.is_empty());
                        mensagem = texto("mensagem");
                    }
                    Some("erro") => erro = texto("mensagem"),
                    Some("verificado") => verificado = Some(Value::Object(ev)),
                    _ => {}
                }
            }
            let status = filho.wait();
            // a entrada some junto com o processo
            guarda.cancelar();
            let ultimas = ultimas.join().unwrap_or_default();
            let codigo = status.ok().and_then(|s| s.code());
            let fim = match codigo {
                Some(0) => Fim::Ok {
                    pasta,
                    mensagem: mensagem.unwrap_or_default(),
                    verificado,
                },
                Some(130) => Fim::Cancelado,
                Some(2) if verificado.is_some() => Fim::NaoConfere(verificado.unwrap_or_default()),
                _ => Fim::Erro(erro.or_else(|| ultimas.back().cloned()).unwrap_or_else(|| {
                    match codigo {
                        Some(c) => format!("a ferramenta saiu com o código {c}"),
                        None => "a ferramenta foi encerrada".into(),
                    }
                })),
            };
            ao_fim(fim);
        })
        .map_err(|e| e.to_string())?;
    Ok(cancelador)
}

fn copia(a: &Andamento) -> Andamento {
    Andamento {
        fase: a.fase.clone(),
        feito: a.feito,
        total: a.total,
        velocidade: a.velocidade,
        eta: a.eta,
    }
}
