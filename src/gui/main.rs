//! xiso-manager-gui: a janela do xiso-manager.
//!
//! A lógica é a mesma do menu do terminal (a biblioteca `xiso_manager`).
//! Aqui ficam só a janela e a ligação com ela. O trabalho pesado continua
//! nas ferramentas, em processos separados: a janela só lê o resultado e
//! nunca para esperando.

// No Windows, abre sem a janela preta do console atrás.
#![cfg_attr(windows, windows_subsystem = "windows")]

mod biblioteca;
mod preferencias;

use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::mpsc;

use slint::{Model, ModelRc, VecModel};
use xiso_manager::{config, disco, ferramentas, log};

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
        return "GPU (OpenGL)";
    }
    let _ = slint::BackendSelector::new()
        .backend_name("winit".into())
        .renderer_name("software".into())
        .select();
    "software"
}

thread_local! {
    /// A lista entregue à janela. Só existe na thread da janela, que é a
    /// única que mexe nela.
    static MODELO: std::cell::RefCell<Option<Rc<VecModel<Jogo>>>> =
        const { std::cell::RefCell::new(None) };
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

    let resultado = janela.run();
    biblioteca::salvar_cache(&caminhos_da_lista(&modelo(&janela)));
    resultado
}
