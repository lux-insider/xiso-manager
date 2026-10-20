//! xiso-manager-gui: a janela do xiso-manager.
//!
//! A lógica é a mesma do menu do terminal (a biblioteca `xiso_manager`).
//! Aqui ficam só a janela e a ligação com ela. O trabalho pesado continua
//! nas ferramentas, em processos separados: a janela só lê o resultado e
//! nunca para esperando.

// No Windows, abre sem a janela preta do console atrás.
#![cfg_attr(windows, windows_subsystem = "windows")]

mod acoes;
mod biblioteca;
mod preferencias;
mod tarefa;

use std::cell::{Cell, RefCell};
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::mpsc;

use slint::{Model, ModelRc, VecModel};
use xiso_manager::{caminhos, config, disco, ferramentas, log};

slint::include_modules!();

/// Software por padrão; a placa de vídeo (OpenGL, FemtoVG) quando ligada
/// nas opções. Sem OpenGL (máquina virtual, driver ausente), fica o
/// software.
fn escolher_renderizador() -> &'static str {
    if preferencias::usar_gpu()
        && slint::BackendSelector::new()
            .backend_name("winit".into())
            .renderer_name("femtovg".into())
            .select()
            .is_ok()
    {
        return "placa de vídeo";
    }
    let _ = slint::BackendSelector::new()
        .backend_name("winit".into())
        .renderer_name("software".into())
        .select();
    "processador"
}

thread_local! {
    /// A lista entregue à janela. Só existe na thread da janela, que é a
    /// única que mexe nela.
    static MODELO: RefCell<Option<Rc<VecModel<Jogo>>>> = const { RefCell::new(None) };
    /// A tarefa rodando, para o botão Cancelar.
    static TAREFA: RefCell<Option<tarefa::Cancelador>> = const { RefCell::new(None) };
    /// Cancelar pedido antes de a ferramenta abrir: cancela assim que abrir.
    static CANCELAR_AO_ABRIR: Cell<bool> = const { Cell::new(false) };
    /// A janela foi fechada com uma tarefa rodando: sai quando ela acabar.
    static SAIR_AO_FIM: Cell<bool> = const { Cell::new(false) };
    /// Os pedidos esperando a vez, e o comando do que está rodando.
    static FILA: RefCell<VecDeque<acoes::Pedido>> = const { RefCell::new(VecDeque::new()) };
    static RODANDO: RefCell<Option<Vec<std::ffi::OsString>>> = const { RefCell::new(None) };
    static PLACAR: RefCell<Placar> = RefCell::new(Placar::default());
}

/// O balanço de uma fila: quantas rodaram, e as que deram errado.
#[derive(Default)]
struct Placar {
    total: usize,
    falhas: Vec<String>,
    ressalvas: usize,
}

/// Os jogos da janela, como o Slint os mostra.
fn modelo(_janela: &Janela) -> Rc<VecModel<Jogo>> {
    MODELO.with(|m| m.borrow().clone().expect("lista de jogos criada no início"))
}

fn caminhos_da_lista(m: &VecModel<Jogo>) -> Vec<PathBuf> {
    m.iter()
        .map(|j| PathBuf::from(j.caminho.as_str()))
        .collect()
}

/// "3 jogos · 22.42 GiB", com o tamanho somado dos arquivos.
fn atualizar_resumo(janela: &Janela) {
    let m = modelo(janela);
    let n = m.row_count();
    let total: u64 = caminhos_da_lista(&m)
        .iter()
        .filter_map(|c| std::fs::metadata(c).ok())
        .map(|d| d.len())
        .sum();
    janela.set_resumo(match n {
        0 => "".into(),
        1 => format!("1 jogo  ·  {}", disco::fmt_u(total)).into(),
        _ => format!("{n} jogos  ·  {}", disco::fmt_u(total)).into(),
    });
}

fn salvar(janela: &Janela) {
    let lista = caminhos_da_lista(&modelo(janela));
    preferencias::salvar_biblioteca(lista.iter().map(PathBuf::as_path));
}

/// Mesmo arquivo por outro caminho (link, `..`) não entra duas vezes.
fn ja_esta(m: &VecModel<Jogo>, caminho: &Path) -> bool {
    let novo = std::fs::canonicalize(caminho).unwrap_or_else(|_| caminho.to_path_buf());
    m.iter().any(|j| {
        let c = Path::new(j.caminho.as_str());
        c == caminho || std::fs::canonicalize(c).is_ok_and(|c| c == novo)
    })
}

/// Põe os jogos na lista (como "Lendo…") e manda ler.
fn adicionar(janela: &Janela, caminhos: Vec<PathBuf>, leitor: &mpsc::Sender<PathBuf>) {
    let m = modelo(janela);
    let mut mudou = false;
    for c in caminhos.iter().flat_map(|c| biblioteca::expandir(c)) {
        if ja_esta(&m, &c) {
            continue;
        }
        let Some(texto) = c.to_str() else {
            log::evento(
                log::Nivel::Aviso,
                &format!("Caminho não é UTF-8, fora da biblioteca: {}", c.display()),
            );
            continue;
        };
        m.push(Jogo {
            caminho: texto.into(),
            nome: biblioteca::nome_do_arquivo(&c).into(),
            detalhe: "".into(),
            aviso: "".into(),
            icone: Default::default(),
            tem_icone: false,
            lendo: true,
            selo: 0,
        });
        let _ = leitor.send(c);
        mudou = true;
    }
    if mudou {
        salvar(janela);
        atualizar_resumo(janela);
    }
}

/// Até 4 jogos lidos ao mesmo tempo (o iso2god gasta mais processador que
/// disco para achar o nome e o ícone). Cada resultado vai para a janela
/// pelo laço de eventos dela.
fn iniciar_leitor(janela: slint::Weak<Janela>) -> mpsc::Sender<PathBuf> {
    let (envia, recebe) = mpsc::channel::<PathBuf>();
    let recebe = std::sync::Arc::new(std::sync::Mutex::new(recebe));
    let quantos = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(1)
        .clamp(1, 4);
    for n in 0..quantos {
        let recebe = recebe.clone();
        let janela = janela.clone();
        let _ = std::thread::Builder::new()
            .name(format!("leitor-biblioteca-{n}"))
            .spawn(move || {
                loop {
                    let proximo = recebe.lock().unwrap_or_else(|e| e.into_inner()).recv();
                    let Ok(caminho) = proximo else {
                        return;
                    };
                    let info = biblioteca::ler(&caminho);
                    // o PNG é aberto aqui, fora da thread da janela
                    let icone = info.icone();
                    let janela = janela.clone();
                    let _ = slint::invoke_from_event_loop(move || {
                        let Some(janela) = janela.upgrade() else {
                            return;
                        };
                        let m = modelo(&janela);
                        let texto = caminho.to_string_lossy();
                        let Some(linha) = m.iter().position(|j| j.caminho.as_str() == texto) else {
                            return; // removido enquanto era lido
                        };
                        let icone = icone.map(slint::Image::from_rgba8);
                        m.set_row_data(
                            linha,
                            Jogo {
                                caminho: texto.as_ref().into(),
                                nome: info.nome.into(),
                                detalhe: info.detalhe.into(),
                                aviso: info.aviso.into(),
                                tem_icone: icone.is_some(),
                                icone: icone.unwrap_or_default(),
                                lendo: false,
                                selo: info.selo,
                            },
                        );
                    });
                }
            });
    }
    envia
}

/// Arrastar arquivos para a janela. O winit avisa um arquivo por vez; o
/// destaque some quando o último cai ou o arrasto sai da janela.
fn aceitar_arrastar(janela: &Janela, leitor: mpsc::Sender<PathBuf>) {
    use slint::winit_030::winit::event::WindowEvent;
    use slint::winit_030::{EventResult, WinitWindowAccessor};
    let fraca = janela.as_weak();
    janela.window().on_winit_window_event(move |_, evento| {
        let Some(janela) = fraca.upgrade() else {
            return EventResult::Propagate;
        };
        match evento {
            WindowEvent::HoveredFile(_) => janela.set_arrastando(true),
            WindowEvent::HoveredFileCancelled => janela.set_arrastando(false),
            WindowEvent::DroppedFile(caminho) => {
                janela.set_arrastando(false);
                adicionar(&janela, vec![caminho.clone()], &leitor);
            }
            _ => {}
        }
        EventResult::Propagate
    });
}

// ── ações ───────────────────────────────────────────────────────────────

/// Caminho para a janela. Os que não são UTF-8 não cabem num texto do
/// Slint; ficam vazios, e a janela pede para escolher outro.
fn texto(caminho: &Path) -> slint::SharedString {
    caminho.to_str().unwrap_or("").into()
}

fn jogo_selecionado(janela: &Janela) -> Option<Jogo> {
    let i = usize::try_from(janela.get_selecionado()).ok()?;
    modelo(janela).row_data(i)
}

/// Os caminhos sugeridos para o jogo escolhido.
fn sugerir(janela: &Janela) {
    janela.set_aviso_acao("".into());
    let Some(jogo) = jogo_selecionado(janela) else {
        return;
    };
    let iso = PathBuf::from(jogo.caminho.as_str());
    janela.set_destino_extrair(texto(&acoes::padrao_extrair(&iso)));
    janela.set_saida_reescrever(texto(&acoes::padrao_reescrever(&iso)));
}

fn opcoes(janela: &Janela) -> acoes::Opcoes {
    let jogo = jogo_selecionado(janela);
    acoes::Opcoes {
        iso: jogo.as_ref().map(|j| PathBuf::from(j.caminho.as_str())),
        nome: jogo.map(|j| j.nome.to_string()).unwrap_or_default(),
        destino_god: janela.get_destino_god().into(),
        threads: janela.get_threads(),
        cortar_fim: janela.get_cortar_fim(),
        destino_extrair: janela.get_destino_extrair().into(),
        pular_atualizacao: janela.get_pular_atualizacao(),
        sobrescrever_extrair: janela.get_sobrescrever_extrair(),
        sobrescrever_criar: janela.get_sobrescrever_criar(),
        sobrescrever_reescrever: janela.get_sobrescrever_reescrever(),
        liberar_midia: janela.get_liberar_midia(),
        pasta_criar: janela.get_pasta_criar().into(),
        saida_criar: janela.get_saida_criar().into(),
        substituir_original: janela.get_substituir_original(),
        saida_reescrever: janela.get_saida_reescrever().into(),
    }
}

/// O seletor de pasta ou de arquivo de cada campo, numa thread (aberto, ele
/// não trava a janela).
fn escolher(janela: &Janela, campo: i32) {
    let atual: String = match campo {
        1 => janela.get_destino_god(),
        2 => janela.get_destino_extrair(),
        3 => janela.get_pasta_criar(),
        4 => janela.get_saida_criar(),
        5 => janela.get_saida_reescrever(),
        _ => return,
    }
    .into();
    let atual = if atual.is_empty() {
        PathBuf::from(config::pasta_padrao())
    } else {
        PathBuf::from(atual)
    };
    let fraca = janela.as_weak();
    std::thread::spawn(move || {
        let dialogo = rfd::FileDialog::new();
        let escolhido = match campo {
            1..=3 => dialogo
                .set_title(match campo {
                    3 => "Pasta do jogo",
                    _ => "Pasta de destino",
                })
                .set_directory(disco::pasta_existente(&atual))
                .pick_folder(),
            _ => dialogo
                .set_title(if campo == 4 {
                    "ISO a criar"
                } else {
                    "ISO reescrita"
                })
                .add_filter("Imagens de disco", &["iso", "ISO"])
                .set_directory(disco::pasta_existente(&caminhos::pasta_de(&atual)))
                .set_file_name(caminhos::nome_base(atual.as_os_str()).to_string_lossy())
                .save_file(),
        };
        let Some(escolhido) = escolhido else {
            return;
        };
        let _ = slint::invoke_from_event_loop(move || {
            let Some(janela) = fraca.upgrade() else {
                return;
            };
            let Some(valor) = escolhido.to_str() else {
                janela.set_aviso_acao(
                    "Esse caminho tem caracteres que a janela não consegue mostrar; escolha outro."
                        .into(),
                );
                return;
            };
            janela.set_aviso_acao("".into());
            match campo {
                1 => {
                    janela.set_destino_god(valor.into());
                    preferencias::salvar_destino_god(valor);
                }
                2 => janela.set_destino_extrair(valor.into()),
                3 => {
                    janela.set_pasta_criar(valor.into());
                    janela.set_saida_criar(texto(&acoes::padrao_criar(&escolhido)));
                }
                4 => janela.set_saida_criar(valor.into()),
                _ => janela.set_saida_reescrever(valor.into()),
            }
        });
    });
}

/// Lê de novo um jogo da biblioteca (a ISO mudou), ou o adiciona.
fn reler(janela: &Janela, caminho: PathBuf, leitor: &mpsc::Sender<PathBuf>) {
    let m = modelo(janela);
    let texto = caminho.to_string_lossy();
    if let Some(linha) = m.iter().position(|j| j.caminho.as_str() == texto) {
        let mut jogo = m.row_data(linha).expect("linha existe");
        jogo.lendo = true;
        m.set_row_data(linha, jogo);
        let _ = leitor.send(caminho);
        atualizar_resumo(janela);
    } else {
        adicionar(janela, vec![caminho], leitor);
    }
}

/// Os pedidos de uma página: o jogo escolhido, ou todos da Biblioteca.
fn pedidos(janela: &Janela, acao: acoes::Acao) -> Result<Vec<acoes::Pedido>, String> {
    let base = opcoes(janela);
    if !janela.get_todos() || acao == acoes::Acao::Criar {
        return acoes::montar(acao, &base).map(|p| vec![p]);
    }
    let jogos: Vec<Jogo> = modelo(janela).iter().collect();
    if jogos.is_empty() {
        return Err("A Biblioteca está vazia.".into());
    }
    jogos
        .iter()
        .map(|j| {
            let iso = PathBuf::from(j.caminho.as_str());
            let o = acoes::Opcoes {
                nome: j.nome.to_string(),
                // cada um ao lado da sua ISO
                destino_extrair: acoes::padrao_extrair(&iso).to_string_lossy().into_owned(),
                saida_reescrever: acoes::padrao_reescrever(&iso)
                    .to_string_lossy()
                    .into_owned(),
                iso: Some(iso),
                ..opcoes(janela)
            };
            acoes::montar(acao, &o).map_err(|e| format!("{}: {e}", j.nome))
        })
        .collect()
}

/// Iniciar (ou pôr na fila, com uma tarefa rodando).
fn iniciar(janela: &Janela, secao: i32, leitor: &mpsc::Sender<PathBuf>) {
    let Some(acao) = acoes::Acao::da_secao(secao) else {
        return;
    };
    let novos = match pedidos(janela, acao) {
        Ok(p) => p,
        Err(e) => {
            janela.set_aviso_acao(e.into());
            return;
        }
    };
    // o mesmo pedido duas vezes não entra
    let rodando = RODANDO.with(|r| r.borrow().clone());
    let (mut entraram, mut repetidos) = (0, 0);
    FILA.with(|f| {
        let mut fila = f.borrow_mut();
        for p in novos {
            if rodando.as_ref() == Some(&p.cmd) || fila.iter().any(|q| q.cmd == p.cmd) {
                repetidos += 1;
            } else {
                fila.push_back(p);
                entraram += 1;
            }
        }
        janela.set_fila(fila.len() as i32);
    });
    let ocupado = janela.get_tarefa_estado() == 1;
    janela.set_aviso_acao(match (ocupado, entraram, repetidos) {
        (_, 0, _) => "Isso já está na fila.".into(),
        (true, 1, 0) => "Posto na fila.".into(),
        (true, n, 0) => format!("{n} postos na fila.").into(),
        (_, _, r) if r > 0 => format!("{r} já estava(m) na fila e ficou(aram) de fora.").into(),
        _ => "".into(),
    });
    if !ocupado && entraram > 0 {
        PLACAR.with(|p| *p.borrow_mut() = Placar::default());
        proxima(janela, leitor);
    }
}

/// Começa o próximo da fila. Fila vazia: mostra o balanço, se rodou mais
/// de um.
fn proxima(janela: &Janela, leitor: &mpsc::Sender<PathBuf>) {
    let pedido = FILA.with(|f| {
        let mut fila = f.borrow_mut();
        let p = fila.pop_front();
        janela.set_fila(fila.len() as i32);
        p
    });
    match pedido {
        Some(p) => rodar(janela, p, leitor),
        None => {
            RODANDO.with(|r| r.borrow_mut().take());
            let placar = PLACAR.with(|p| std::mem::take(&mut *p.borrow_mut()));
            if placar.total > 1 {
                let ultima = janela.get_tarefa_titulo();
                let falhas = placar.falhas.len();
                if falhas == 0 && placar.ressalvas == 0 {
                    janela.set_tarefa_estado(acoes::PRONTA);
                    janela.set_tarefa_titulo(
                        format!("Fila concluída: as {} tarefas deram certo", placar.total).into(),
                    );
                } else {
                    janela.set_tarefa_estado(if falhas > 0 {
                        acoes::FALHOU
                    } else {
                        acoes::RESSALVA
                    });
                    janela.set_tarefa_titulo(
                        format!(
                            "Fila concluída: {} de {} deram certo",
                            placar.total - falhas - placar.ressalvas,
                            placar.total
                        )
                        .into(),
                    );
                }
                let mut detalhe = Vec::new();
                if falhas > 0 {
                    detalhe.push(format!("Falharam: {}", placar.falhas.join(", ")));
                }
                if placar.ressalvas > 0 {
                    detalhe.push(format!("{} não conferem com o Redump", placar.ressalvas));
                }
                if detalhe.is_empty() {
                    detalhe.push(format!("Última: {ultima}"));
                }
                janela.set_tarefa_detalhe(detalhe.join("  ·  ").into());
            }
            if SAIR_AO_FIM.get() {
                let _ = janela.hide();
                let _ = slint::quit_event_loop();
            }
        }
    }
}

/// O fim de uma tarefa, na thread da janela.
fn terminou(janela: &Janela, r: acoes::Resultado, nome: String, leitor: &mpsc::Sender<PathBuf>) {
    TAREFA.with(|t| t.borrow_mut().take());
    janela.set_cancelando(false);
    let total = PLACAR.with(|p| {
        let mut p = p.borrow_mut();
        p.total += 1;
        match r.estado {
            acoes::FALHOU => p.falhas.push(nome),
            acoes::RESSALVA => p.ressalvas += 1,
            _ => {}
        }
        p.total
    });
    let cancelada = r.estado == acoes::CANCELADA;
    janela.set_tarefa_estado(r.estado);
    janela.set_tarefa_titulo(r.titulo.into());
    janela.set_tarefa_detalhe(r.detalhe.into());
    // o que a tarefa gravou pode ocupar o caminho sugerido
    let aviso = janela.get_aviso_acao();
    sugerir(janela);
    janela.set_aviso_acao(aviso);
    if cancelada {
        // cancelar para tudo: o balanço da fila não interessa
        FILA.with(|f| f.borrow_mut().clear());
        janela.set_fila(0);
        PLACAR.with(|p| p.borrow_mut().total = total.min(1));
    }
    proxima(janela, leitor);
}

fn rodar(janela: &Janela, pedido: acoes::Pedido, leitor: &mpsc::Sender<PathBuf>) {
    janela.set_tarefa_estado(1);
    janela.set_tarefa_titulo(pedido.titulo().into());
    janela.set_tarefa_detalhe("Preparando…".into());
    janela.set_tarefa_fracao(-1.0);
    janela.set_tarefa_pasta("".into());
    janela.set_cancelando(false);
    CANCELAR_AO_ABRIR.set(false);
    TAREFA.with(|t| t.borrow_mut().take());
    RODANDO.with(|r| *r.borrow_mut() = Some(pedido.cmd.clone()));

    let fraca = janela.as_weak();
    let leitor = leitor.clone();
    // Abrir a ferramenta consulta o `--help` dela na primeira vez: fora da
    // thread da janela.
    std::thread::spawn(move || {
        let cmd = pedido.cmd.clone();
        let nome = pedido.nome.clone();
        let f_andar = fraca.clone();
        let f_fim = fraca.clone();
        let l_fim = leitor.clone();
        let aberta = tarefa::iniciar(
            cmd,
            move |a| {
                let (fracao, detalhe) = acoes::andamento(&a);
                let fraca = f_andar.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(janela) = fraca.upgrade()
                        && janela.get_tarefa_estado() == 1
                        && !janela.get_cancelando()
                    {
                        janela.set_tarefa_fracao(fracao);
                        janela.set_tarefa_detalhe(detalhe.into());
                    }
                });
            },
            move |fim| {
                let r = acoes::resultado(&pedido, &fim);
                let pasta = match (&fim, r.estado) {
                    (tarefa::Fim::Ok { pasta: Some(p), .. }, _) if Path::new(p).is_dir() => {
                        Some(PathBuf::from(p))
                    }
                    (_, acoes::PRONTA) => pedido.pasta.clone().filter(|p| p.is_dir()),
                    _ => None,
                };
                let iso_nova = matches!(fim, tarefa::Fim::Ok { .. })
                    .then_some(pedido.iso_nova)
                    .flatten();
                let selo = acoes::selo(&fim).zip(pedido.iso);
                if let Some((s, iso)) = &selo {
                    biblioteca::marcar_selo(iso, *s);
                }
                let nome = pedido.nome;
                let _ = slint::invoke_from_event_loop(move || {
                    let Some(janela) = f_fim.upgrade() else {
                        return;
                    };
                    if let Some(iso) = iso_nova {
                        reler(&janela, iso, &l_fim);
                    }
                    if let Some((s, iso)) = selo {
                        mostrar_selo(&janela, &iso, s);
                    }
                    janela.set_tarefa_pasta(pasta.as_deref().map(texto).unwrap_or_default());
                    terminou(&janela, r, nome, &l_fim);
                });
            },
        );
        let _ = slint::invoke_from_event_loop(move || {
            let Some(janela) = fraca.upgrade() else {
                return;
            };
            match aberta {
                Ok(cancelador) => {
                    if CANCELAR_AO_ABRIR.get() {
                        cancelador.cancelar();
                    }
                    TAREFA.with(|t| *t.borrow_mut() = Some(cancelador));
                }
                Err(e) => {
                    let r = acoes::Resultado {
                        estado: acoes::FALHOU,
                        titulo: format!("Não deu certo: {nome}"),
                        detalhe: e,
                    };
                    terminou(&janela, r, nome, &leitor);
                }
            }
        });
    });
}

fn mostrar_selo(janela: &Janela, iso: &Path, selo: i32) {
    let m = modelo(janela);
    let texto = iso.to_string_lossy();
    if let Some(linha) = m.iter().position(|j| j.caminho.as_str() == texto) {
        let mut jogo = m.row_data(linha).expect("linha existe");
        jogo.selo = selo;
        m.set_row_data(linha, jogo);
    }
    // grava já: um selo é o resultado de minutos de leitura
    biblioteca::salvar_cache(&caminhos_da_lista(&m));
}

/// Cancelar para a tarefa e esvazia a fila.
fn cancelar(janela: &Janela) {
    esvaziar_fila(janela);
    if janela.get_tarefa_estado() != 1 {
        return;
    }
    janela.set_cancelando(true);
    janela.set_tarefa_detalhe("Parando e apagando o que já foi gravado…".into());
    TAREFA.with(|t| match t.borrow().as_ref() {
        Some(c) => c.cancelar(),
        None => CANCELAR_AO_ABRIR.set(true),
    });
}

fn esvaziar_fila(janela: &Janela) {
    FILA.with(|f| f.borrow_mut().clear());
    janela.set_fila(0);
}

/// Abre a pasta no gerenciador de arquivos do sistema.
fn abrir_pasta(pasta: &Path) {
    let programa = if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    match std::process::Command::new(programa).arg(pasta).spawn() {
        // espera numa thread, para o processo não ficar pendurado
        Ok(mut filho) => {
            std::thread::spawn(move || filho.wait());
        }
        Err(e) => log::evento(
            log::Nivel::Aviso,
            &format!("Não abriu a pasta {}: {e}", pasta.display()),
        ),
    }
}

fn ligar_acoes(janela: &Janela, leitor: mpsc::Sender<PathBuf>) {
    janela.set_threads(config::threads_god().clamp(1, 32) as i32);
    let destino = preferencias::destino_god();
    janela.set_destino_god(if destino.is_empty() {
        config::pasta_padrao().to_string_lossy().as_ref().into()
    } else {
        destino.into()
    });

    let fraca = janela.as_weak();
    janela.on_selecionar(move |i| {
        if let Some(janela) = fraca.upgrade() {
            janela.set_selecionado(i);
            sugerir(&janela);
        }
    });
    let fraca = janela.as_weak();
    janela.on_escolher(move |campo| {
        if let Some(janela) = fraca.upgrade() {
            escolher(&janela, campo);
        }
    });
    let fraca = janela.as_weak();
    janela.on_iniciar(move |secao| {
        if let Some(janela) = fraca.upgrade() {
            iniciar(&janela, secao, &leitor);
        }
    });
    let fraca = janela.as_weak();
    janela.on_cancelar(move || {
        if let Some(janela) = fraca.upgrade() {
            cancelar(&janela);
        }
    });
    let fraca = janela.as_weak();
    janela.on_abrir_pasta(move || {
        if let Some(janela) = fraca.upgrade() {
            let pasta = janela.get_tarefa_pasta();
            if !pasta.is_empty() {
                abrir_pasta(Path::new(pasta.as_str()));
            }
        }
    });
    let fraca = janela.as_weak();
    janela.on_esvaziar_fila(move || {
        if let Some(janela) = fraca.upgrade() {
            esvaziar_fila(&janela);
        }
    });
    let fraca = janela.as_weak();
    janela.on_limpar_tarefa(move || {
        if let Some(janela) = fraca.upgrade()
            && janela.get_tarefa_estado() != 1
        {
            janela.set_tarefa_estado(0);
            janela.set_tarefa_pasta("".into());
        }
    });

    // Fechar com uma tarefa rodando cancela a tarefa e espera ela apagar o
    // que criou; a janela some quando ela termina.
    let fraca = janela.as_weak();
    janela.window().on_close_requested(move || {
        let Some(janela) = fraca.upgrade() else {
            return slint::CloseRequestResponse::HideWindow;
        };
        if janela.get_tarefa_estado() == 1 {
            SAIR_AO_FIM.set(true);
            cancelar(&janela);
            slint::CloseRequestResponse::KeepWindowShown
        } else {
            slint::CloseRequestResponse::HideWindow
        }
    });
}

// ── configurações ───────────────────────────────────────────────────────

/// "versão 0.2.4", ou o que falta. Roda as ferramentas: fora da thread da
/// janela.
fn estado_ferramenta(bin: &Path, subcomando: &str) -> String {
    if !bin.is_file() {
        return "não encontrado".into();
    }
    let versao = ferramentas::capturar(
        &[bin.as_os_str().to_os_string(), "--version".into()],
        std::time::Duration::from_secs(10),
    )
    .ok()
    .filter(|(status, _, _)| status.success())
    .and_then(|(_, saida, _)| {
        String::from_utf8_lossy(&saida)
            .split_whitespace()
            .last()
            .map(String::from)
    });
    let Some(versao) = versao else {
        return "não respondeu ao --version".into();
    };
    let cmd = [bin.as_os_str().to_os_string(), subcomando.into()];
    if ferramentas::cancela_pela_entrada(&cmd) {
        format!("versão {versao}  ✓")
    } else {
        format!("versão {versao}: antiga ou não é a -pt, a janela precisa de uma mais nova")
    }
}

/// Mostra os caminhos do config.json e confere as ferramentas.
fn mostrar_configuracoes(janela: &Janela) {
    let (extract, god) = (config::bin_extract(), config::bin_god());
    janela.set_cfg_extract(texto(&extract));
    janela.set_cfg_iso2god(texto(&god));
    janela.set_cfg_pasta(texto(Path::new(&config::pasta_padrao())));
    janela.set_cfg_extract_estado("conferindo…".into());
    janela.set_cfg_iso2god_estado("conferindo…".into());
    let fraca = janela.as_weak();
    std::thread::spawn(move || {
        let e = estado_ferramenta(&extract, "extrair");
        let g = estado_ferramenta(&god, "converter");
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(janela) = fraca.upgrade() {
                janela.set_cfg_extract_estado(e.into());
                janela.set_cfg_iso2god_estado(g.into());
            }
        });
    });
}

fn cfg_escolher(janela: &Janela, campo: i32) {
    let inicial = match campo {
        1 => PathBuf::from(config::pasta_padrao()),
        2 => caminhos::pasta_de(&config::bin_extract()),
        _ => caminhos::pasta_de(&config::bin_god()),
    };
    let fraca = janela.as_weak();
    std::thread::spawn(move || {
        let dialogo = rfd::FileDialog::new().set_directory(disco::pasta_existente(&inicial));
        let escolhido = match campo {
            1 => dialogo.set_title("Pasta padrão").pick_folder(),
            2 => dialogo.set_title("extract-xiso-pt").pick_file(),
            _ => dialogo.set_title("iso2god-pt").pick_file(),
        };
        let Some(escolhido) = escolhido else {
            return;
        };
        let _ = slint::invoke_from_event_loop(move || {
            let Some(janela) = fraca.upgrade() else {
                return;
            };
            let valor = escolhido.into_os_string();
            match campo {
                1 => config::mudar(|c| c.pasta_padrao = valor),
                2 => {
                    ferramentas::garantir_permissao(Path::new(&valor));
                    config::mudar(|c| c.bin_extract_xiso = valor);
                }
                _ => {
                    ferramentas::garantir_permissao(Path::new(&valor));
                    config::mudar(|c| c.bin_iso2god = valor);
                }
            }
            mostrar_configuracoes(&janela);
        });
    });
}

fn ligar_configuracoes(janela: &Janela) {
    janela.set_cfg_threads(config::threads_god().clamp(1, 32) as i32);
    janela.set_cfg_gpu(preferencias::gpu_gravada());
    mostrar_configuracoes(janela);

    let fraca = janela.as_weak();
    janela.on_cfg_escolher(move |campo| {
        if let Some(janela) = fraca.upgrade() {
            cfg_escolher(&janela, campo);
        }
    });
    let fraca = janela.as_weak();
    janela.on_cfg_mudou(move || {
        let Some(janela) = fraca.upgrade() else {
            return;
        };
        let threads = i64::from(janela.get_cfg_threads().clamp(1, 32));
        if threads != config::threads_god() {
            config::mudar(|c| c.threads_god = threads);
        }
        let gpu = janela.get_cfg_gpu();
        if gpu != preferencias::gpu_gravada() {
            preferencias::salvar_gpu(gpu);
        }
    });
}

fn main() -> Result<(), slint::PlatformError> {
    config::carregar();
    ferramentas::SEM_CONSOLE.store(true, std::sync::atomic::Ordering::Relaxed);
    log::iniciar();
    ferramentas::resolver_binarios(false);

    let renderizador = escolher_renderizador();
    let janela = Janela::new()?;
    janela.set_versao(env!("CARGO_PKG_VERSION").into());
    janela.set_renderizador(renderizador.into());

    let lista = Rc::new(VecModel::<Jogo>::default());
    MODELO.with(|m| *m.borrow_mut() = Some(lista.clone()));
    janela.set_jogos(ModelRc::from(lista));

    biblioteca::carregar_cache();
    let leitor = iniciar_leitor(janela.as_weak());
    adicionar(&janela, preferencias::biblioteca(), &leitor);
    aceitar_arrastar(&janela, leitor.clone());

    // O seletor de arquivos roda numa thread: aberto, ele não trava a janela.
    let fraca = janela.as_weak();
    let leitor_botao = leitor.clone();
    janela.on_adicionar(move || {
        let fraca = fraca.clone();
        let leitor = leitor_botao.clone();
        let pasta = PathBuf::from(config::pasta_padrao());
        std::thread::spawn(move || {
            let escolhidos = rfd::FileDialog::new()
                .set_title("Adicionar ISOs")
                .add_filter("Imagens de disco", &["iso", "ISO"])
                .set_directory(pasta)
                .pick_files()
                .unwrap_or_default();
            if escolhidos.is_empty() {
                return;
            }
            let _ = slint::invoke_from_event_loop(move || {
                if let Some(janela) = fraca.upgrade() {
                    adicionar(&janela, escolhidos, &leitor);
                }
            });
        });
    });

    let fraca = janela.as_weak();
    janela.on_remover(move |i| {
        let Some(janela) = fraca.upgrade() else {
            return;
        };
        let m = modelo(&janela);
        let i = i as usize;
        if i < m.row_count() {
            m.remove(i);
            let sel = janela.get_selecionado();
            if sel == i as i32 {
                janela.set_selecionado(-1);
            } else if sel > i as i32 {
                janela.set_selecionado(sel - 1);
            }
            salvar(&janela);
            atualizar_resumo(&janela);
        }
    });

    ligar_acoes(&janela, leitor);
    ligar_configuracoes(&janela);

    let resultado = janela.run();
    biblioteca::salvar_cache(&caminhos_da_lista(&modelo(&janela)));
    resultado
}
