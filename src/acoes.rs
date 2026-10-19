//! As ações do menu. Cada uma devolve Err com a mensagem quando algo
//! inesperado acontece (uma pasta que não pode ser lida, por exemplo): o
//! menu mostra o erro e segue.

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::disco::{fmt_bytes, fmt_tempo, fmt_u, verificar_espaco};
use crate::entrada::{pausar, perguntar, perguntar_inteiro, perguntar_sim_nao};
use crate::estilo::{
    self, AMARE, AZUL, BRANC, CIANO, CINZA, ITAL, NEG, ROXO, VERDE, VERM, campo, campo14, cor, emo,
    etapa, resposta,
};
use crate::eventos::{self, numero, sem_controle};
use crate::executar::{Opcoes, executar};
use crate::ferramentas::{self, eh_pt, exigir_binario, exigir_iso2god};
use crate::iso::{self, Tipo};
use crate::navegador::{self, navegador, resumo_selecao};
use crate::textos::t;
use crate::{caminhos, config, log, say, selecao, sinais};

pub type R = Result<(), String>;

pub const APP_NOME: &str = "xiso-manager";
pub const APP_VERSAO: &str = env!("CARGO_PKG_VERSION");

fn os(texto: &str) -> OsString {
    OsString::from(texto)
}

fn nome(caminho: &Path) -> String {
    caminhos::exibir(&caminhos::nome_base(caminho.as_os_str()))
}

fn tamanho(caminho: &Path) -> u64 {
    std::fs::metadata(caminho).map(|m| m.len()).unwrap_or(0)
}

/// O caminho digitado, com o "~" expandido; vazio fica vazio.
fn digitado(texto: &str) -> PathBuf {
    if texto.is_empty() {
        PathBuf::new()
    } else {
        caminhos::expandir_str(texto)
    }
}

fn vazio(p: &Path) -> bool {
    p.as_os_str().is_empty()
}

// ── Destino ─────────────────────────────────────────────────────────────

fn destino_gravavel(pasta: &Path) -> bool {
    if vazio(pasta) {
        return true;
    }
    if pasta.is_dir() {
        return caminhos::pode_escrever(pasta);
    }
    let pai = match pasta.parent() {
        Some(p) if !p.as_os_str().is_empty() => p,
        _ => Path::new("."),
    };
    pai.is_dir() && caminhos::pode_escrever(pai)
}

/// Cria a pasta como o `Path.mkdir(parents=True, exist_ok=True)`, com o
/// erro apontando a pasta que falhou.
fn criar_pastas(p: &Path) -> Result<(), String> {
    match std::fs::create_dir(p) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            if let Some(pai) = p.parent().filter(|x| !x.as_os_str().is_empty() && *x != p) {
                criar_pastas(pai)?;
            }
            match std::fs::create_dir(p) {
                Ok(()) => Ok(()),
                Err(_) if p.is_dir() => Ok(()),
                Err(e) => Err(caminhos::erro_os(&e, Some(&navegador::como_pathlib(p)))),
            }
        }
        Err(_) if p.is_dir() => Ok(()),
        Err(e) => Err(caminhos::erro_os(&e, Some(&navegador::como_pathlib(p)))),
    }
}

fn preparar_destino(pasta: &Path, digitada: &str) -> bool {
    if vazio(pasta) {
        return true;
    }
    match criar_pastas(pasta) {
        Ok(()) => caminhos::pode_escrever(pasta),
        Err(e) => {
            estilo::erro(&format!(
                "{} {} ({})",
                t("sem_permissao_escrita"),
                digitada,
                e
            ));
            false
        }
    }
}

fn tem_xbe(pasta: &Path) -> bool {
    std::fs::read_dir(pasta).is_ok_and(|itens| {
        itens.flatten().any(|i| {
            i.file_name().to_string_lossy().to_lowercase() == "default.xbe"
                && pasta.join(i.file_name()).is_file()
        })
    })
}

/// Destino já ocupado: pergunta antes. Devolve (sobrescrever, seguir).
fn pode_sobrescrever(caminho: &Path, eh_pasta: bool) -> Result<(bool, bool), String> {
    let (ocupado, chave) = if eh_pasta {
        let ocupado = if caminho.is_dir() {
            let mut itens =
                std::fs::read_dir(caminho).map_err(|e| caminhos::erro_os(&e, Some(caminho)))?;
            itens.next().is_some()
        } else {
            false
        };
        (ocupado, "p_sobrescrever_pasta")
    } else {
        (caminho.exists(), "p_sobrescrever_iso")
    };
    if !ocupado {
        return Ok((false, true));
    }
    let sim = perguntar_sim_nao(
        &format!("{} {}", caminhos::encurtar_p(caminho), t(chave)),
        false,
        None,
    );
    Ok((sim, sim))
}

fn opcoes_comuns() -> Vec<OsString> {
    if eh_pt() {
        return Vec::new(); // o extract-xiso-pt fala por eventos JSON, não tem modo silencioso
    }
    if perguntar_sim_nao(t("p_silencioso"), false, None) {
        vec![os("-q")]
    } else {
        Vec::new()
    }
}

fn confirmar() -> bool {
    if perguntar_sim_nao(t("confirmar"), true, Some(false)) {
        return true;
    }
    estilo::aviso(t("cancelado"));
    pausar();
    false
}

fn contador(i: usize, total: usize, texto: &str, emoji: &str) {
    if total > 1 {
        say!("{}", campo(&format!("{i}/{total}"), texto, emoji, 8));
    }
}

/// Lê o destino da etapa 2: o texto digitado e o caminho expandido.
fn pedir_destino() -> Option<(String, PathBuf)> {
    let texto = perguntar(t("p_destino"), false, None);
    let destino = digitado(&texto);
    if !vazio(&destino) && !destino_gravavel(&destino) {
        estilo::erro(&format!(
            "{} {}",
            t("sem_permissao_escrita"),
            caminhos::exibir_p(&destino)
        ));
        pausar();
        return None;
    }
    say!(
        "{}",
        resposta(
            &if vazio(&destino) {
                t("r_padrao").to_string()
            } else {
                caminhos::encurtar_p(&destino)
            },
            CIANO
        )
    );
    Some((texto, destino))
}

// ── Resumo ──────────────────────────────────────────────────────────────

pub struct Extra {
    rotulo: String,
    valor: String,
    emoji: &'static str,
}

/// Resumo final, com o que foi gerado: sem isso o usuário sabe que deu
/// certo, mas não onde a coisa foi parar.
pub fn mostrar_resumo(
    titulo: &str,
    ok: usize,
    falhas: usize,
    duracao: f64,
    extras: &[Extra],
    produzidos: &[PathBuf],
) {
    let tudo_ok = falhas == 0 && ok > 0;
    let (cor_borda, emoji_topo) = if tudo_ok {
        (VERDE, emo::OK)
    } else if ok > 0 {
        (AMARE, emo::AVISO)
    } else {
        (VERM, emo::ERRO)
    };
    say!(
        "{}",
        estilo::titulo_caixa(
            &format!("{} \u{2014} {}", t("resumo"), titulo),
            emoji_topo,
            cor_borda
        )
    );
    if ok > 0 {
        estilo::marca_ok(t("r_sucesso"), &ok.to_string());
    } else {
        estilo::marca_nd(t("r_sucesso"), "0");
    }
    if falhas > 0 {
        estilo::marca_falha(t("r_falhas"), &falhas.to_string());
    } else {
        estilo::marca_ok(t("r_falhas"), "0");
    }
    say!(
        "{}",
        campo(t("r_tempo"), &fmt_tempo(duracao), emo::TEMPO, 17)
    );
    for e in extras {
        say!("{}", campo(&e.rotulo, &e.valor, e.emoji, 17));
    }
    if !produzidos.is_empty() {
        say!();
        say!("  {}", cor(t("r_gerados"), &[NEG, BRANC]));
        for caminho in produzidos.iter().take(8) {
            let arquivo = caminho.is_file();
            let tam = if arquivo {
                fmt_u(tamanho(caminho))
            } else {
                fmt_u(caminhos::tamanho_pasta(caminho))
            };
            say!(
                "    {} {}  {}",
                if arquivo { emo::ARQUIVO } else { emo::PASTA },
                cor(
                    &estilo::cortar(&caminhos::encurtar_p(caminho), 44),
                    &[BRANC]
                ),
                cor(&tam, &[CINZA])
            );
        }
        if produzidos.len() > 8 {
            say!(
                "    {}",
                cor(
                    &format!("\u{2026} +{}", produzidos.len() - 8),
                    &[CINZA, ITAL]
                )
            );
        }
    }
    say!();
    say!(
        "{}",
        cor(
            &format!(
                "  {}: {}",
                t("r_log"),
                caminhos::encurtar_p(&config::arq_log())
            ),
            &[CINZA, ITAL]
        )
    );
}

fn escolher(arquivos: Option<Vec<PathBuf>>) -> Vec<PathBuf> {
    arquivos.unwrap_or_else(|| navegador(true))
}

/// Conta o resultado; devolve true para parar o lote (cancelado).
fn contar(r: &crate::executar::Resultado, ok: &mut usize, falhas: &mut usize) -> bool {
    if r.cancelado {
        *falhas += 1;
        return true;
    }
    if r.ok {
        *ok += 1;
    } else {
        *falhas += 1;
    }
    false
}

// ── extract-xiso ────────────────────────────────────────────────────────

pub fn extrair(arquivos: Option<Vec<PathBuf>>) -> R {
    estilo::tela(t("m_extrair"), emo::EXTRAIR, t("d_extrair"), VERDE);
    if !exigir_binario(&config::bin_extract(), "extract-xiso") {
        pausar();
        return Ok(());
    }
    let arquivos = escolher(arquivos);
    if arquivos.is_empty() {
        return Ok(());
    }

    say!("{}", etapa(Some((1, 4)), t("e_selecao")));
    let total = resumo_selecao(&arquivos);

    say!("{}", etapa(Some((2, 4)), t("e_destino")));
    let Some((_, destino)) = pedir_destino() else {
        return Ok(());
    };

    say!("{}", etapa(Some((3, 4)), t("e_opcoes")));
    let pular = perguntar_sim_nao(t("p_pular_update"), false, None);
    let flags = opcoes_comuns();

    // Pasta de cada ISO, sempre explícita (-d): sem ela o extract-xiso
    // extrai na pasta em que o programa foi aberto. Vários ISOs para o
    // mesmo destino ganham uma subpasta cada.
    let n = arquivos.len();
    let pasta_de = |arquivo: &Path| -> PathBuf {
        let base = caminhos::sem_extensao(arquivo);
        if vazio(&destino) {
            caminhos::pasta_de(&caminhos::absoluto(arquivo)).join(base)
        } else if n == 1 {
            destino.clone()
        } else {
            destino.join(base)
        }
    };

    say!("{}", etapa(Some((4, 4)), t("e_verificacao")));
    let alvo_espaco = if vazio(&destino) {
        pasta_de(&arquivos[0])
    } else {
        destino.clone()
    };
    if !verificar_espaco(&alvo_espaco, total) {
        pausar();
        return Ok(());
    }
    if !vazio(&destino) && !preparar_destino(&destino, &caminhos::exibir_p(&destino)) {
        pausar();
        return Ok(());
    }
    if !confirmar() {
        return Ok(());
    }

    say!("{}", etapa(None, t("e_execucao")));
    let (mut ok, mut falhas) = (0, 0);
    let mut gerados = Vec::new();
    let inicio = Instant::now();
    let bin = config::bin_extract().into_os_string();

    for (i, arquivo) in arquivos.iter().enumerate() {
        let alvo = pasta_de(arquivo);
        contador(i + 1, n, &estilo::cortar(&nome(arquivo), 40), emo::ARQUIVO);
        let (sobrescrever, seguir) = pode_sobrescrever(&alvo, true)?;
        if !seguir {
            say!("{}", resposta(t("pulado"), CINZA));
            continue;
        }
        let mut args = vec![bin.clone()];
        if eh_pt() {
            args.extend([
                os("extrair"),
                arquivo.clone().into(),
                os("-d"),
                alvo.clone().into(),
                os("--progresso-json"),
            ]);
            if pular {
                args.push(os("-s"));
            }
            if sobrescrever {
                args.push(os("--sobrescrever"));
            }
        } else {
            args.extend([os("-x"), os("-d"), alvo.clone().into()]);
            if pular {
                args.push(os("-s"));
            }
            args.extend(flags.iter().cloned());
            args.push(arquivo.clone().into());
        }
        let r = executar(
            &args,
            t("extraindo"),
            Opcoes {
                emoji: emo::EXTRAIR,
                destino: Some(alvo.clone()),
                total: tamanho(arquivo),
                ..Default::default()
            },
        );
        if r.ok && alvo.is_dir() {
            gerados.push(alvo);
        }
        if contar(&r, &mut ok, &mut falhas) {
            break;
        }
    }

    mostrar_resumo(
        t("m_extrair"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &[],
        &gerados,
    );
    pausar();
    Ok(())
}

pub fn listar(arquivos: Option<Vec<PathBuf>>) -> R {
    estilo::tela(t("m_listar"), emo::LISTAR, t("d_listar"), CIANO);
    if !exigir_binario(&config::bin_extract(), "extract-xiso") {
        pausar();
        return Ok(());
    }
    let arquivos = escolher(arquivos);
    if arquivos.is_empty() {
        return Ok(());
    }

    say!("{}", etapa(Some((1, 2)), t("e_selecao")));
    resumo_selecao(&arquivos);

    say!("{}", etapa(Some((2, 2)), t("e_resultado")));
    let (mut ok, mut falhas) = (0, 0);
    let inicio = Instant::now();
    let bin = config::bin_extract().into_os_string();
    for arquivo in &arquivos {
        if arquivos.len() > 1 {
            say!(
                "{}",
                campo(
                    t("nav_titulo"),
                    &estilo::cortar(&nome(arquivo), 40),
                    emo::ARQUIVO,
                    10
                )
            );
        }
        let cmd = if eh_pt() {
            vec![bin.clone(), os("listar"), arquivo.clone().into()]
        } else {
            vec![bin.clone(), os("-l"), arquivo.clone().into()]
        };
        let r = executar(
            &cmd,
            t("listando"),
            Opcoes {
                emoji: emo::LISTAR,
                max_linhas: 40,
                ..Default::default()
            },
        );
        if contar(&r, &mut ok, &mut falhas) {
            break;
        }
    }
    mostrar_resumo(
        t("m_listar"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &[],
        &[],
    );
    pausar();
    Ok(())
}

struct Trabalho {
    origem: PathBuf,
    saida: PathBuf,
    alvo: PathBuf,
    tamanho: u64,
}

fn nome_da_pasta(pasta: &Path) -> OsString {
    caminhos::nome_base(caminhos::normalizar(pasta).as_os_str())
}

pub fn criar() -> R {
    estilo::tela(t("m_criar"), emo::CRIAR, t("d_criar"), ROXO);
    if !exigir_binario(&config::bin_extract(), "extract-xiso") {
        pausar();
        return Ok(());
    }

    say!("{}", etapa(Some((1, 4)), t("e_origem")));
    let mut trabalhos: Vec<Trabalho> = Vec::new();
    loop {
        let texto = perguntar(t("p_pasta_origem"), false, None);
        if texto.is_empty() {
            break;
        }
        let origem = caminhos::expandir_str(&texto);
        if !origem.is_dir() {
            estilo::erro(&format!(
                "{} {}",
                t("pasta_nao_existe"),
                caminhos::exibir_p(&origem)
            ));
            if !perguntar_sim_nao(t("p_tentar_novamente"), true, None) {
                break;
            }
            continue;
        }
        if !caminhos::pode_ler(&origem) {
            estilo::erro(&format!(
                "{} {}",
                t("sem_permissao_leitura"),
                caminhos::exibir_p(&origem)
            ));
            if !perguntar_sim_nao(t("p_tentar_novamente"), true, None) {
                break;
            }
            continue;
        }
        let n_arquivos = caminhos::contar_arquivos(&origem);
        let tam = caminhos::tamanho_pasta(&origem);
        say!(
            "{}",
            resposta(
                &format!(
                    "{} \u{00B7} {} {} \u{00B7} {}",
                    caminhos::exibir(&nome_da_pasta(&origem)),
                    n_arquivos,
                    t("arquivos"),
                    fmt_u(tam)
                ),
                VERDE
            )
        );
        trabalhos.push(Trabalho {
            origem,
            saida: PathBuf::new(),
            alvo: PathBuf::new(),
            tamanho: tam,
        });
        if !perguntar_sim_nao(t("p_outra_pasta"), false, None) {
            break;
        }
    }
    if trabalhos.is_empty() {
        estilo::aviso(t("nav_nada"));
        pausar();
        return Ok(());
    }

    say!("{}", etapa(Some((2, 4)), t("e_destino")));
    for tr in trabalhos.iter_mut() {
        let padrao = caminhos::juntar_os(&[&nome_da_pasta(&tr.origem), OsStr::new(".iso")]);
        let texto = perguntar(
            &format!("{} ({})", t("p_nome_saida"), caminhos::exibir(&padrao)),
            false,
            None,
        );
        let mut saida = digitado(texto.trim());
        if !vazio(&saida) && !caminhos::termina_com(saida.as_os_str(), ".iso") {
            saida = PathBuf::from(caminhos::juntar_os(&[
                saida.as_os_str(),
                OsStr::new(".iso"),
            ]));
        }
        if !vazio(&saida) {
            let pasta = caminhos::pasta_de(&saida);
            let pasta = if vazio(&pasta) {
                PathBuf::from(".")
            } else {
                pasta
            };
            if !destino_gravavel(&pasta) {
                estilo::erro(&format!(
                    "{} {}",
                    t("sem_permissao_escrita"),
                    caminhos::exibir_p(&saida)
                ));
                pausar();
                return Ok(());
            }
        }
        tr.alvo = if vazio(&saida) {
            std::env::current_dir().unwrap_or_default().join(&padrao)
        } else {
            saida.clone()
        };
        tr.saida = saida;
        say!("{}", resposta(&caminhos::encurtar_p(&tr.alvo), CIANO));
    }

    say!("{}", etapa(Some((3, 4)), t("e_opcoes")));
    let (mut sem_patch, mut liberar) = (false, false);
    if eh_pt() {
        // só jogos de Xbox têm default.xbe; e só mexe se o usuário pedir
        if trabalhos.iter().any(|tr| tem_xbe(&tr.origem)) {
            liberar = perguntar_sim_nao(t("p_liberar_midia"), false, None);
        }
    } else {
        sem_patch = perguntar_sim_nao(t("p_sem_patch"), false, None);
    }
    let flags = opcoes_comuns();

    say!("{}", etapa(Some((4, 4)), t("e_verificacao")));
    let total: u64 = trabalhos.iter().map(|tr| tr.tamanho).sum();
    let pasta_check = caminhos::pasta_de(&trabalhos[0].alvo);
    let pasta_check = if vazio(&pasta_check) {
        PathBuf::from(".")
    } else {
        pasta_check
    };
    if !verificar_espaco(&pasta_check, total) {
        pausar();
        return Ok(());
    }
    if !confirmar() {
        return Ok(());
    }

    say!("{}", etapa(None, t("e_execucao")));
    let (mut ok, mut falhas) = (0, 0);
    let mut gerados = Vec::new();
    let inicio = Instant::now();
    let bin = config::bin_extract().into_os_string();
    let n = trabalhos.len();
    for (i, tr) in trabalhos.iter().enumerate() {
        contador(
            i + 1,
            n,
            &caminhos::exibir(&nome_da_pasta(&tr.origem)),
            emo::PASTA,
        );
        let (sobrescrever, seguir) = pode_sobrescrever(&tr.alvo, false)?;
        if !seguir {
            say!("{}", resposta(t("pulado"), CINZA));
            continue;
        }
        let mut args = vec![bin.clone()];
        if eh_pt() {
            args.extend([
                os("criar"),
                tr.origem.clone().into(),
                os("-s"),
                tr.alvo.clone().into(),
                os("--progresso-json"),
            ]);
            if liberar && tem_xbe(&tr.origem) {
                args.push(os("--liberar-midia"));
            }
            if sobrescrever {
                args.push(os("--sobrescrever"));
            }
        } else {
            args.extend([os("-c"), tr.origem.clone().into()]);
            if !vazio(&tr.saida) {
                args.push(tr.saida.clone().into());
            }
            if sem_patch {
                args.push(os("-m"));
            }
            args.extend(flags.iter().cloned());
        }
        let r = executar(
            &args,
            t("criando"),
            Opcoes {
                emoji: emo::CRIAR,
                destino: Some(tr.alvo.clone()),
                total: tr.tamanho,
                alvo_arquivo: true,
                ..Default::default()
            },
        );
        if r.ok && tr.alvo.is_file() {
            gerados.push(tr.alvo.clone());
        }
        if contar(&r, &mut ok, &mut falhas) {
            break;
        }
    }
    mostrar_resumo(
        t("m_criar"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &[],
        &gerados,
    );
    pausar();
    Ok(())
}

fn sim_nao(valor: bool) -> String {
    if valor { "sim" } else { "não" }.to_string()
}

fn quando(caminho: &Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(caminho)
        .ok()
        .and_then(|m| m.modified().ok())
}

/// O erro de um rename como o Python o mostra: os dois nomes.
fn erro_rename(e: &std::io::Error, de: &Path, para: &Path) -> String {
    format!(
        "{} -> '{}'",
        caminhos::erro_os(e, Some(de)),
        caminhos::exibir_p(para)
    )
}

pub fn reescrever(arquivos: Option<Vec<PathBuf>>) -> R {
    estilo::tela(t("m_reescrever"), emo::OTIMIZAR, t("d_reescrever"), AMARE);
    if !exigir_binario(&config::bin_extract(), "extract-xiso") {
        pausar();
        return Ok(());
    }
    let arquivos = escolher(arquivos);
    if arquivos.is_empty() {
        return Ok(());
    }

    say!("{}", etapa(Some((1, 4)), t("e_selecao")));
    let total = resumo_selecao(&arquivos);

    say!("{}", etapa(Some((2, 4)), t("e_destino")));
    let Some((_, destino)) = pedir_destino() else {
        return Ok(());
    };

    say!("{}", etapa(Some((3, 4)), t("e_opcoes")));
    let mut apagar = perguntar_sim_nao(t("p_apagar_antigo"), false, None);
    if apagar {
        estilo::aviso(t("p_apagar_certeza"));
        if !perguntar_sim_nao(t("confirmar"), false, Some(false)) {
            apagar = false;
            say!("{}", resposta(t("cancelado"), CINZA));
        }
    }
    let (mut sem_patch, mut liberar) = (false, false);
    if eh_pt() {
        if arquivos.iter().any(|a| iso::detectar(a).0 == Tipo::Xbox) {
            liberar = perguntar_sim_nao(t("p_liberar_midia"), false, None);
        }
    } else {
        sem_patch = perguntar_sim_nao(t("p_sem_patch"), false, None);
    }
    let flags = opcoes_comuns();

    say!("{}", etapa(Some((4, 4)), t("e_verificacao")));
    // O que precisa estar livre é o espaço do ISO novo, que nunca é maior
    // que o original.
    let alvo_espaco = if vazio(&destino) {
        caminhos::pasta_de(&caminhos::absoluto(&arquivos[0]))
    } else {
        destino.clone()
    };
    if !verificar_espaco(&alvo_espaco, total) {
        pausar();
        return Ok(());
    }
    if !vazio(&destino) && !preparar_destino(&destino, &caminhos::exibir_p(&destino)) {
        pausar();
        return Ok(());
    }
    if !confirmar() {
        return Ok(());
    }

    say!("{}", etapa(None, t("e_execucao")));
    if eh_pt() {
        return reescrever_pt(&arquivos, &destino, apagar, liberar);
    }
    let (mut ok, mut falhas, mut inertes) = (0, 0, 0);
    let mut gerados = Vec::new();
    let inicio = Instant::now();
    let bin = config::bin_extract().into_os_string();
    let n = arquivos.len();

    for (i, arquivo) in arquivos.iter().enumerate() {
        contador(i + 1, n, &estilo::cortar(&nome(arquivo), 40), emo::ARQUIVO);
        // Saída sempre com -d. O extract-xiso renomeia o original para
        // "<nome>.old" na pasta do original e grava o novo com o nome de
        // sempre: o resultado se reconhece pelo nome.
        let origem = caminhos::absoluto(arquivo);
        let pasta_origem = caminhos::pasta_de(&origem);
        let pasta_saida = if vazio(&destino) {
            pasta_origem.clone()
        } else {
            caminhos::absoluto(&destino)
        };
        let saida = pasta_saida.join(caminhos::nome_base(origem.as_os_str()));
        let antigo = PathBuf::from(caminhos::juntar_os(&[
            origem.as_os_str(),
            OsStr::new(".old"),
        ]));
        // Em outra pasta, o ISO novo pega o lugar de um arquivo com o mesmo
        // nome que já esteja lá (o oficial grava por cima sem perguntar).
        if !caminhos::mesma_pasta(&pasta_saida, &pasta_origem) {
            let (_, seguir) = pode_sobrescrever(&saida, false)?;
            if !seguir {
                say!("{}", resposta(t("pulado"), CINZA));
                continue;
            }
        }
        let antes = quando(&saida);
        let marca = sinais::marcar_original(origem.clone(), antigo.clone(), saida.clone());
        if let Some(m) = &marca {
            sinais::adicionar_pendente(m);
        }

        let mut args = vec![bin.clone(), os("-r"), os("-d"), pasta_saida.clone().into()];
        if apagar {
            args.push(os("-D"));
        }
        if sem_patch {
            args.push(os("-m"));
        }
        args.extend(flags.iter().cloned());
        args.push(origem.clone().into());

        let r = executar(
            &args,
            t("reescrevendo"),
            Opcoes {
                emoji: emo::OTIMIZAR,
                destino: Some(saida.clone()),
                total: tamanho(&origem),
                alvo_arquivo: true,
                descontar_inicio: false,
                ..Default::default()
            },
        );
        let depois = quando(&saida);
        let gravou = depois.is_some() && depois != antes;

        // Falha ou interrupção no meio: o original ficou como "<nome>.old"
        // e no lugar dele pode haver o ISO novo pela metade.
        let (mut falha, mut desfeito) = (None, false);
        if let Some(m) = &marca
            && sinais::tirar_pendente(m)
            && !r.ok
        {
            (falha, desfeito) = sinais::desfazer_reescrita(m);
        }
        if desfeito {
            estilo::aviso(t("r_original_restaurado"));
        } else if let Some(e) = falha {
            estilo::erro(&format!(
                "{} {} ({})",
                t("r_original_em"),
                caminhos::exibir_p(&antigo),
                erro_rename(&e, &antigo, &origem)
            ));
        }

        // O extract-xiso pula ISOs já otimizados e sai com 0, sem gravar.
        if r.ok && !gravou {
            let ja_otimizado = r.saida.iter().any(|l| {
                let l = l.to_lowercase();
                l.contains("already optimized") || l.contains("skipping")
            });
            say!(
                "{}",
                resposta(
                    if ja_otimizado {
                        t("nada_a_fazer")
                    } else {
                        t("sem_saida")
                    },
                    CINZA
                )
            );
            inertes += 1;
        } else if r.ok {
            gerados.push(saida.clone());
            if antigo.exists() && !apagar {
                say!(
                    "{}",
                    resposta(
                        &format!("{} {}", t("r_original_em"), caminhos::encurtar_p(&antigo)),
                        CINZA
                    )
                );
            }
        }
        if contar(&r, &mut ok, &mut falhas) {
            break;
        }
    }

    let mut extras = vec![Extra {
        rotulo: t("p_apagar_antigo").into(),
        valor: sim_nao(apagar),
        emoji: emo::LIXO,
    }];
    if inertes > 0 {
        extras.push(Extra {
            rotulo: t("r_inertes").into(),
            valor: inertes.to_string(),
            emoji: emo::INFO,
        });
    }
    mostrar_resumo(
        t("m_reescrever"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &extras,
        &gerados,
    );
    pausar();
    Ok(())
}

/// O ISO novo existe e não é o próprio original (por outro caminho)?
fn pode_apagar_original(origem: &Path, saida: &Path) -> bool {
    saida.is_file() && caminhos::mesmo_arquivo(saida, origem) == Some(false)
}

/// Reescrita com o extract-xiso-pt: ele nunca mexe no original durante a
/// gravação. Sem destino e sem apagar, o novo fica ao lado como
/// "<nome>.xiso.iso"; apagando na mesma pasta, o --substituir troca o
/// original só no fim; com destino, o original só é apagado depois do sucesso.
fn reescrever_pt(arquivos: &[PathBuf], destino: &Path, apagar: bool, liberar: bool) -> R {
    let (mut ok, mut falhas) = (0, 0);
    let mut gerados = Vec::new();
    let inicio = Instant::now();
    let bin = config::bin_extract().into_os_string();
    let n = arquivos.len();

    for (i, arquivo) in arquivos.iter().enumerate() {
        contador(i + 1, n, &estilo::cortar(&nome(arquivo), 40), emo::ARQUIVO);
        let origem = caminhos::absoluto(arquivo);
        let pasta_origem = caminhos::pasta_de(&origem);
        let pasta_saida = if vazio(destino) {
            pasta_origem.clone()
        } else {
            caminhos::absoluto(destino)
        };
        let mut saida = pasta_saida.join(caminhos::nome_base(origem.as_os_str()));
        let mesma = caminhos::mesma_pasta(&pasta_saida, &pasta_origem);

        let mut args = vec![
            bin.clone(),
            os("reescrever"),
            origem.clone().into(),
            os("--progresso-json"),
        ];
        if mesma && apagar {
            args.push(os("--substituir"));
            saida = origem.clone();
        } else {
            if mesma {
                saida = pasta_saida.join(caminhos::juntar_os(&[
                    &caminhos::sem_extensao(&origem),
                    OsStr::new(".xiso.iso"),
                ]));
            }
            let (sobrescrever, seguir) = pode_sobrescrever(&saida, false)?;
            if !seguir {
                say!("{}", resposta(t("pulado"), CINZA));
                continue;
            }
            args.extend([os("-s"), saida.clone().into()]);
            if sobrescrever {
                args.push(os("--sobrescrever"));
            }
        }
        if liberar && iso::detectar(&origem).0 == Tipo::Xbox {
            args.push(os("--liberar-midia"));
        }
        let r = executar(
            &args,
            t("reescrevendo"),
            Opcoes {
                emoji: emo::OTIMIZAR,
                destino: Some(saida.clone()),
                total: tamanho(&origem),
                alvo_arquivo: true,
                descontar_inicio: false,
                ..Default::default()
            },
        );
        if r.ok {
            gerados.push(saida.clone());
            if saida == origem {
                say!("{}", resposta(t("r_substituido"), CINZA));
            } else if apagar
                && pode_apagar_original(&origem, &saida)
                && let Err(e) = std::fs::remove_file(&origem)
            {
                estilo::erro(&format!(
                    "{} ({})",
                    caminhos::exibir_p(&origem),
                    caminhos::erro_os(&e, Some(&origem))
                ));
            }
        }
        if contar(&r, &mut ok, &mut falhas) {
            break;
        }
    }
    let extras = [Extra {
        rotulo: t("p_apagar_antigo").into(),
        valor: sim_nao(apagar),
        emoji: emo::LIXO,
    }];
    mostrar_resumo(
        t("m_reescrever"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &extras,
        &gerados,
    );
    pausar();
    Ok(())
}

pub fn verificar(arquivos: Option<Vec<PathBuf>>) -> R {
    estilo::tela(t("m_verificar"), emo::ANALISAR, t("d_verificar"), CIANO);
    if !exigir_binario(&config::bin_extract(), "extract-xiso-pt") {
        pausar();
        return Ok(());
    }
    if !eh_pt() {
        estilo::aviso(t("precisa_pt"));
        pausar();
        return Ok(());
    }
    let arquivos = escolher(arquivos);
    if arquivos.is_empty() {
        return Ok(());
    }

    say!("{}", etapa(Some((1, 3)), t("e_selecao")));
    resumo_selecao(&arquivos);

    say!("{}", etapa(Some((2, 3)), t("e_opcoes")));
    // Os .dat ficam instalados no extract-xiso-pt; aqui só se instala um novo.
    let bin = config::bin_extract().into_os_string();
    let texto = perguntar(t("p_dat"), false, None);
    let dat = digitado(texto.trim().trim_matches('"').trim_matches('\''));
    if !vazio(&dat) {
        if !dat.is_file() {
            estilo::erro(&format!(
                "{} {}",
                t("dat_nao_existe"),
                caminhos::exibir_p(&dat)
            ));
            pausar();
            return Ok(());
        }
        let r = executar(
            &[bin.clone(), os("dats"), os("instalar"), dat.into()],
            t("instalando_dat"),
            Opcoes {
                emoji: emo::ANALISAR,
                max_linhas: 4,
                ..Default::default()
            },
        );
        if !r.ok {
            pausar();
            return Ok(());
        }
    }

    say!("{}", etapa(Some((3, 3)), t("e_resultado")));
    let (mut ok, mut falhas) = (0, 0);
    let inicio = Instant::now();
    let n = arquivos.len();
    for (i, arquivo) in arquivos.iter().enumerate() {
        contador(i + 1, n, &estilo::cortar(&nome(arquivo), 40), emo::ARQUIVO);
        let args = vec![
            bin.clone(),
            os("verificar"),
            arquivo.clone().into(),
            os("--progresso-json"),
        ];
        // código 2: o Redump tem este jogo com outro SHA-1 (conta como falha)
        let r = executar(
            &args,
            t("verificando"),
            Opcoes {
                emoji: emo::ANALISAR,
                total: tamanho(arquivo),
                max_linhas: 16,
                ..Default::default()
            },
        );
        if contar(&r, &mut ok, &mut falhas) {
            break;
        }
    }
    mostrar_resumo(
        t("m_verificar"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &[],
        &[],
    );
    pausar();
    Ok(())
}

// ── iso2god ─────────────────────────────────────────────────────────────

fn listar_nomes(pasta: &Path) -> Result<Vec<OsString>, String> {
    if !pasta.is_dir() {
        return Ok(Vec::new());
    }
    let itens = std::fs::read_dir(pasta).map_err(|e| caminhos::erro_os(&e, Some(pasta)))?;
    Ok(itens.flatten().map(|i| i.file_name()).collect())
}

pub fn god(arquivos: Option<Vec<PathBuf>>) -> R {
    estilo::tela(t("m_god"), emo::GOD, t("d_god"), AZUL);
    if !exigir_iso2god() {
        pausar();
        return Ok(());
    }
    let arquivos = escolher(arquivos);
    if arquivos.is_empty() {
        return Ok(());
    }

    say!("{}", etapa(Some((1, 4)), t("e_selecao")));
    let total = resumo_selecao(&arquivos);

    say!("{}", etapa(Some((2, 4)), t("e_destino")));
    let texto = perguntar(t("p_destino_obrig"), true, None);
    let destino = caminhos::expandir_str(&texto);
    if !destino_gravavel(&destino) {
        estilo::erro(&format!(
            "{} {}",
            t("sem_permissao_escrita"),
            caminhos::exibir_p(&destino)
        ));
        pausar();
        return Ok(());
    }
    say!("{}", resposta(&caminhos::encurtar_p(&destino), CIANO));

    say!("{}", etapa(Some((3, 4)), t("e_opcoes")));
    let titulo_jogo = if arquivos.len() == 1 {
        perguntar(t("p_titulo_jogo"), false, None)
    } else {
        String::new()
    };
    let threads = perguntar_inteiro(t("p_threads"), config::threads_god(), 1, 32);
    let cortar_fim = perguntar_sim_nao(t("p_trim"), true, None);

    say!("{}", etapa(Some((4, 4)), t("e_verificacao")));
    if !verificar_espaco(&destino, total) {
        pausar();
        return Ok(());
    }
    if !preparar_destino(&destino, &caminhos::exibir_p(&destino)) {
        pausar();
        return Ok(());
    }
    if !confirmar() {
        return Ok(());
    }

    config::mudar(|c| c.threads_god = threads);
    say!("{}", etapa(None, t("e_execucao")));
    let (mut ok, mut falhas) = (0, 0);
    let inicio = Instant::now();
    let antes: Vec<OsString> = listar_nomes(&destino)?;
    let bin = config::bin_god().into_os_string();
    let n = arquivos.len();

    for (i, arquivo) in arquivos.iter().enumerate() {
        contador(i + 1, n, &estilo::cortar(&nome(arquivo), 40), emo::JOGO);
        // "cortar o espaço não usado do fim" é o padding "parcial"
        let mut args = vec![
            bin.clone(),
            os("converter"),
            os("--progresso-json"),
            os("-j"),
            os(&threads.to_string()),
            os("--padding"),
            os(if cortar_fim { "parcial" } else { "nenhuma" }),
        ];
        if !titulo_jogo.is_empty() {
            args.extend([os("--titulo"), os(&titulo_jogo)]);
        }
        args.extend([arquivo.clone().into(), destino.clone().into()]);
        let r = executar(
            &args,
            t("convertendo"),
            Opcoes {
                emoji: emo::GOD,
                destino: Some(destino.clone()),
                total: tamanho(arquivo),
                ..Default::default()
            },
        );
        if contar(&r, &mut ok, &mut falhas) {
            break;
        }
    }

    let depois = listar_nomes(&destino)?;
    let mut novos: Vec<OsString> = depois.into_iter().filter(|n| !antes.contains(n)).collect();
    novos.sort_by_key(|n| navegador::chave_exata(n));
    let gerados: Vec<PathBuf> = novos.into_iter().map(|n| destino.join(n)).collect();
    mostrar_resumo(
        t("m_god"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &[],
        &gerados,
    );
    pausar();
    Ok(())
}

/// O `splitlines` do Python.
fn linhas_py(texto: &str) -> Vec<&str> {
    let mut linhas = Vec::new();
    let mut inicio = 0;
    let mut iter = texto.char_indices().peekable();
    while let Some((i, c)) = iter.next() {
        let quebra = matches!(
            c,
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if quebra {
            linhas.push(&texto[inicio..i]);
            let mut fim = i + c.len_utf8();
            if c == '\r'
                && let Some(&(_, '\n')) = iter.peek()
            {
                iter.next();
                fim += 1;
            }
            inicio = fim;
        }
    }
    if inicio < texto.len() {
        linhas.push(&texto[inicio..]);
    }
    linhas
}

/// Análise pelo iso2god próprio: `info --json`, mostrado com os mesmos
/// campos alinhados do resto da tela.
fn mostrar_info_iso2god(arquivo: &Path) -> bool {
    let cmd = vec![
        config::bin_god().into_os_string(),
        os("info"),
        os("--json"),
        arquivo.into(),
    ];
    log::info(&format!(
        "cmd: {}",
        cmd.iter()
            .map(|a| caminhos::para_log(a))
            .collect::<Vec<_>>()
            .join(" ")
    ));
    let (status, saida, erros) = match ferramentas::capturar(&cmd, Duration::from_secs(300)) {
        Ok(r) => r,
        Err(ferramentas::Falha::Abrir(e)) => {
            estilo::erro(&caminhos::erro_os(&e, Some(Path::new(&cmd[0]))));
            return false;
        }
        Err(ferramentas::Falha::Prazo) => {
            let lista: Vec<String> = cmd
                .iter()
                .map(|a| eventos::repr_texto(&caminhos::exibir(a)))
                .collect();
            estilo::erro(&format!(
                "Command '[{}]' timed out after 300 seconds",
                lista.join(", ")
            ));
            return false;
        }
    };
    let erros = String::from_utf8_lossy(&erros).into_owned();
    for linha in linhas_py(&erros) {
        let linha = eventos::limpar_linha_externa(linha);
        if !linha.is_empty() {
            say!("{}", cor(&format!("        {linha}"), &[CINZA]));
        }
    }
    let saida = String::from_utf8_lossy(&saida);
    let info = match serde_json::from_str::<serde_json::Value>(&saida) {
        Ok(serde_json::Value::Object(m)) => m,
        _ => {
            // o motivo já saiu acima, pelo stderr do iso2god
            if erros.trim().is_empty() {
                estilo::erro(t("erro_inesperado"));
            }
            return false;
        }
    };
    let valor = |chave: &str| -> Option<String> {
        match info.get(chave) {
            None | Some(serde_json::Value::Null) => None,
            Some(serde_json::Value::String(s)) if s.is_empty() => None,
            Some(v) => Some(sem_controle(&eventos::py_str(v))),
        }
        .filter(|s| !s.is_empty())
    };
    // Os campos vêm de outro programa: um tipo errado vale como ausente.
    let plataforma = match eventos::texto(info.get("plataforma_detectada")) {
        "xbox360" => Some("Xbox 360"),
        "xbox" => Some("Xbox"),
        _ => None,
    };
    match plataforma {
        Some(p) => say!("{}", campo(t("i_plataforma"), p, emo::JOGO, 16)),
        None => estilo::marca_nd(
            t("i_plataforma"),
            &format!("{} ({})", t("i_nao_detectada"), t("i_sem_executavel")),
        ),
    }
    for (rotulo, chave) in [
        ("Title ID", "title_id"),
        ("Media ID", "media_id"),
        (t("i_titulo"), "titulo"),
    ] {
        if let Some(v) = valor(chave) {
            say!("{}", campo(rotulo, &v, emo::INFO, 16));
        }
    }
    if eventos::verdadeiro(info.get("disco")) {
        let disco = match info.get("disco") {
            Some(v) => sem_controle(&eventos::py_str(v)),
            None => String::new(),
        };
        say!(
            "{}",
            campo(
                t("i_disco"),
                &format!(
                    "{}/{}",
                    disco,
                    valor("total_discos").unwrap_or_else(|| "?".into())
                ),
                emo::DISCO,
                16
            )
        );
    }
    if let Some(volume) = numero(info.get("tamanho_volume")).filter(|v| *v != 0.0) {
        say!(
            "{}",
            campo(
                t("i_volume"),
                &format!(
                    "{}  {}",
                    fmt_bytes(volume),
                    cor(
                        &format!("({})", valor("tipo_disco").unwrap_or_default()),
                        &[CINZA]
                    )
                ),
                emo::DISCO,
                16
            )
        );
    }
    say!();
    status.success()
}

pub fn info_god(arquivos: Option<Vec<PathBuf>>) -> R {
    estilo::tela(t("m_info_god"), emo::ANALISAR, t("d_info_god"), CIANO);
    if !exigir_iso2god() {
        pausar();
        return Ok(());
    }
    let arquivos = escolher(arquivos);
    if arquivos.is_empty() {
        return Ok(());
    }

    say!("{}", etapa(Some((1, 2)), t("e_selecao")));
    resumo_selecao(&arquivos);

    say!("{}", etapa(Some((2, 2)), t("e_resultado")));
    let (mut ok, mut falhas) = (0, 0);
    let inicio = Instant::now();
    for arquivo in &arquivos {
        let (tipo, layout) = iso::detectar(arquivo);
        say!(
            "{}",
            campo(
                t("nav_titulo"),
                &estilo::cortar(&nome(arquivo), 40),
                emo::ARQUIVO,
                16
            )
        );
        let mut texto = cor(iso::rotulo(tipo), &[iso::cor_tipo(tipo)]);
        if !layout.is_empty() {
            texto.push_str(&cor(&format!("  ({layout})"), &[CINZA]));
        }
        say!("{}", campo(t("tipo_detectado"), &texto, emo::ANALISAR, 16));
        if mostrar_info_iso2god(arquivo) {
            ok += 1;
        } else {
            falhas += 1;
        }
    }
    mostrar_resumo(
        t("m_info_god"),
        ok,
        falhas,
        inicio.elapsed().as_secs_f64(),
        &[],
        &[],
    );
    pausar();
    Ok(())
}

// ── Assistente e lote ───────────────────────────────────────────────────

#[derive(Clone, Copy)]
enum Qual {
    Extrair,
    Listar,
    Reescrever,
    God,
    InfoGod,
    Verificar,
}

fn rodar(qual: Qual, arquivos: Vec<PathBuf>) -> R {
    match qual {
        Qual::Extrair => extrair(Some(arquivos)),
        Qual::Listar => listar(Some(arquivos)),
        Qual::Reescrever => reescrever(Some(arquivos)),
        Qual::God => god(Some(arquivos)),
        Qual::InfoGod => info_god(Some(arquivos)),
        Qual::Verificar => verificar(Some(arquivos)),
    }
}

fn sugestao(qual: Qual) -> (&'static str, &'static str, &'static str) {
    match qual {
        Qual::Extrair => (emo::EXTRAIR, t("m_extrair"), t("d_extrair")),
        Qual::Listar => (emo::LISTAR, t("m_listar"), t("d_listar")),
        Qual::Reescrever => (emo::OTIMIZAR, t("m_reescrever"), t("d_reescrever")),
        Qual::God => (emo::GOD, t("m_god"), t("d_god")),
        Qual::InfoGod => (emo::ANALISAR, t("m_info_god"), t("d_info_god")),
        Qual::Verificar => (emo::ANALISAR, t("m_verificar"), t("d_verificar")),
    }
}

pub fn assistente() -> R {
    estilo::tela(t("m_assistente"), emo::ASSISTENTE, t("d_assistente"), ROXO);
    let arquivos = navegador(true);
    if arquivos.is_empty() {
        return Ok(());
    }
    let principal = &arquivos[0];
    let (tipo, layout) = iso::detectar(principal);

    estilo::limpar_tela();
    say!(
        "{}",
        estilo::titulo_caixa(t("tipo_detectado"), emo::ASSISTENTE, iso::cor_tipo(tipo))
    );
    say!(
        "{}",
        campo14(
            t("nav_titulo"),
            &estilo::cortar(&nome(principal), 44),
            emo::ARQUIVO
        )
    );
    match std::fs::metadata(principal) {
        Ok(m) => say!("{}", campo14(t("total"), &fmt_u(m.len()), emo::DISCO)),
        Err(_) => estilo::marca_nd(t("total"), "N/D"),
    }
    let mut texto = cor(iso::rotulo(tipo), &[iso::cor_tipo(tipo), NEG]);
    if !layout.is_empty() {
        texto.push_str(&cor(&format!("   {layout}"), &[CINZA]));
    }
    say!("{}", campo14(t("tipo_detectado"), &texto, emo::ANALISAR));
    if arquivos.len() > 1 {
        say!(
            "{}",
            campo14(t("arquivos_sel"), &arquivos.len().to_string(), emo::LOTE)
        );
    }
    say!();

    let mut escolhas = match tipo {
        Tipo::Xbox => {
            estilo::dica(t("sug_xbox"));
            vec![Qual::Extrair, Qual::Listar, Qual::Reescrever]
        }
        Tipo::Xbox360 => {
            estilo::dica(t("sug_360"));
            let mut v = vec![Qual::God, Qual::InfoGod];
            if eh_pt() {
                v.extend([Qual::Extrair, Qual::Reescrever]);
            }
            v
        }
        Tipo::Desconhecido => {
            estilo::dica(t("sug_nada"));
            vec![Qual::Extrair, Qual::Listar, Qual::God]
        }
    };
    // o verificar é do extract-xiso-pt; o oficial não tem
    if eh_pt() && tipo != Tipo::Desconhecido {
        escolhas.push(Qual::Verificar);
    }

    say!();
    say!(
        "{}",
        cor(&format!("  {}", t("acoes_sugeridas")), &[NEG, BRANC])
    );
    say!();
    for (i, qual) in escolhas.iter().enumerate() {
        let (emoji, rotulo, desc) = sugestao(*qual);
        let estrela = if i == 0 {
            cor(&format!("  {}", emo::ESTRELA), &[AMARE])
        } else {
            String::new()
        };
        say!(
            "{}{}",
            estilo::opcao_simples(&(i + 1).to_string(), rotulo, emoji),
            estrela
        );
        say!("{}", cor(&format!("        {desc}"), &[CINZA]));
    }
    say!();
    say!(
        "  {} {} {}",
        cor("[ENTER]", &[VERDE, NEG]),
        cor(t("sug_automatica"), &[CINZA]),
        cor(sugestao(escolhas[0]).1, &[BRANC])
    );
    say!(
        "{}",
        estilo::opcao("0", t("voltar"), emo::SAIR, VERM, BRANC)
    );

    let escolha = perguntar(t("escolha"), false, None).trim().to_string();
    if escolha == "0" {
        return Ok(());
    }
    if escolha.is_empty() {
        return rodar(escolhas[0], arquivos);
    }
    if let Some(n) = selecao::inteiro(&escolha)
        && n >= 1
        && n <= escolhas.len() as u128
    {
        return rodar(escolhas[n as usize - 1], arquivos);
    }
    estilo::aviso(t("opcao_invalida"));
    pausar();
    Ok(())
}

pub fn lote() -> R {
    estilo::tela(t("m_lote"), emo::LOTE, t("d_lote"), ROXO);

    say!("{}", etapa(Some((1, 3)), t("e_origem")));
    let padrao = config::pasta_padrao();
    let padrao_txt = caminhos::exibir(&padrao);
    let resp = perguntar(t("p_pasta_iso"), true, Some(&padrao_txt));
    let pasta = if resp == padrao_txt {
        caminhos::expandir(&padrao)
    } else {
        caminhos::expandir_str(&resp)
    };
    if !pasta.is_dir() {
        estilo::erro(&format!(
            "{} {}",
            t("pasta_nao_existe"),
            caminhos::exibir_p(&pasta)
        ));
        pausar();
        return Ok(());
    }
    let arquivos = navegador::listar_isos(&pasta);
    if arquivos.is_empty() {
        estilo::aviso(t("nav_nenhum_iso"));
        pausar();
        return Ok(());
    }
    let valor = pasta.clone().into_os_string();
    config::mudar(|c| c.pasta_padrao = valor);
    say!(
        "{}",
        resposta(
            &format!(
                "{} {} \u{00B7} {}",
                arquivos.len(),
                t("arquivos"),
                fmt_u(caminhos::tamanho_arquivos(&arquivos))
            ),
            VERDE
        )
    );

    say!("{}", etapa(Some((2, 3)), t("e_selecao")));
    resumo_selecao(&arquivos);

    let do_tipo = |tipo: Tipo| -> Vec<PathBuf> {
        arquivos
            .iter()
            .filter(|a| iso::detectar(a).0 == tipo)
            .cloned()
            .collect()
    };
    let classico = do_tipo(Tipo::Xbox);
    let trezentos = do_tipo(Tipo::Xbox360);
    let outros = arquivos.len() - classico.len() - trezentos.len();

    say!();
    if !classico.is_empty() {
        estilo::marca_ok(t("t_xbox"), &classico.len().to_string());
    } else {
        estilo::marca_nd(t("t_xbox"), "0");
    }
    if !trezentos.is_empty() {
        estilo::marca_ok(t("t_xbox360"), &trezentos.len().to_string());
    } else {
        estilo::marca_nd(t("t_xbox360"), "0");
    }
    if outros > 0 {
        estilo::marca_nd(t("t_desconhecido"), &outros.to_string());
    }

    say!("{}", etapa(Some((3, 3)), t("e_opcoes")));
    let mut escolhas: Vec<(&str, String, Qual, Vec<PathBuf>)> = Vec::new();
    if !classico.is_empty() {
        escolhas.push((
            emo::EXTRAIR,
            format!("{} ({})", t("m_extrair"), classico.len()),
            Qual::Extrair,
            classico.clone(),
        ));
        escolhas.push((
            emo::OTIMIZAR,
            format!("{} ({})", t("m_reescrever"), classico.len()),
            Qual::Reescrever,
            classico.clone(),
        ));
    }
    if !trezentos.is_empty() {
        escolhas.push((
            emo::GOD,
            format!("{} ({})", t("m_god"), trezentos.len()),
            Qual::God,
            trezentos.clone(),
        ));
    }
    escolhas.push((
        emo::EXTRAIR,
        format!("{} ({})", t("m_extrair"), arquivos.len()),
        Qual::Extrair,
        arquivos.clone(),
    ));

    say!();
    for (i, (emoji, rotulo, _, _)) in escolhas.iter().enumerate() {
        say!(
            "{}",
            estilo::opcao_simples(&(i + 1).to_string(), rotulo, emoji)
        );
    }
    say!(
        "{}",
        estilo::opcao("0", t("voltar"), emo::SAIR, VERM, BRANC)
    );

    let escolha = perguntar(t("escolha"), false, None).trim().to_string();
    if let Some(n) = selecao::inteiro(&escolha)
        && n >= 1
        && n <= escolhas.len() as u128
    {
        let (_, _, qual, lista) = escolhas.swap_remove(n as usize - 1);
        return rodar(qual, lista);
    }
    Ok(())
}

// ── Comando manual ──────────────────────────────────────────────────────

/// Divide como o `shlex.split` do Python (modo POSIX, sem comentários).
/// None se faltar fechar uma aspa ou sobrar uma barra no fim.
pub fn dividir_shell(texto: &str) -> Option<Vec<String>> {
    let mut saida = Vec::new();
    let mut atual = String::new();
    let mut tem = false;
    let mut letras = texto.chars().peekable();
    while let Some(c) = letras.next() {
        match c {
            ' ' | '\t' | '\r' | '\n' => {
                if tem {
                    saida.push(std::mem::take(&mut atual));
                    tem = false;
                }
            }
            '\\' => {
                atual.push(letras.next()?);
                tem = true;
            }
            '\'' => {
                tem = true;
                loop {
                    match letras.next()? {
                        '\'' => break,
                        x => atual.push(x),
                    }
                }
            }
            '"' => {
                tem = true;
                loop {
                    match letras.next()? {
                        '"' => break,
                        '\\' => {
                            let x = letras.next()?;
                            if x != '"' && x != '\\' {
                                atual.push('\\');
                            }
                            atual.push(x);
                        }
                        x => atual.push(x),
                    }
                }
            }
            x => {
                atual.push(x);
                tem = true;
            }
        }
    }
    if tem {
        saida.push(atual);
    }
    Some(saida)
}

/// O `str.split()` do Python: separa em qualquer espaço.
fn dividir_espacos(texto: &str) -> Vec<String> {
    texto
        .split(|c: char| c.is_whitespace() || ('\x1c'..='\x1f').contains(&c))
        .filter(|p| !p.is_empty())
        .map(String::from)
        .collect()
}

pub fn manual() -> R {
    estilo::tela(t("m_manual"), emo::MANUAL, t("d_manual"), AMARE);
    say!(
        "{}",
        estilo::opcao_simples(
            "1",
            if eh_pt() {
                "extract-xiso-pt"
            } else {
                "extract-xiso"
            },
            emo::EXTRAIR
        )
    );
    say!("{}", estilo::opcao_simples("2", "iso2god", emo::GOD));
    say!(
        "{}",
        estilo::opcao("0", t("voltar"), emo::SAIR, VERM, BRANC)
    );

    let qual = perguntar(t("escolha"), false, None).trim().to_string();
    let (binario, nome, exemplo) = match qual.as_str() {
        "1" if eh_pt() => (
            config::bin_extract(),
            "extract-xiso-pt",
            "extrair jogo.iso -d ~/Extraidos",
        ),
        "1" => (
            config::bin_extract(),
            "extract-xiso",
            "-x -s -d ~/Extraidos jogo.iso",
        ),
        "2" => (
            config::bin_god(),
            "iso2god",
            "converter -j 4 --padding parcial jogo.iso ~/GOD",
        ),
        _ => return Ok(()),
    };
    let ok_bin = if nome == "iso2god" {
        exigir_iso2god()
    } else {
        exigir_binario(&binario, nome)
    };
    if !ok_bin {
        pausar();
        return Ok(());
    }

    say!();
    say!(
        "{}",
        cor(&format!("  {}  {}", emo::DICA, exemplo), &[CINZA, ITAL])
    );
    let bruto = perguntar(t("p_args"), false, None);
    if bruto.is_empty() {
        return Ok(());
    }
    let args = dividir_shell(&bruto).unwrap_or_else(|| dividir_espacos(&bruto));

    say!();
    let mut cmd = vec![binario.into_os_string()];
    cmd.extend(args.into_iter().map(OsString::from));
    let r = executar(
        &cmd,
        t("executando"),
        Opcoes {
            emoji: emo::MANUAL,
            max_linhas: 30,
            ..Default::default()
        },
    );
    mostrar_resumo(
        t("m_manual"),
        usize::from(r.ok),
        usize::from(!r.ok),
        r.duracao,
        &[],
        &[],
    );
    pausar();
    Ok(())
}

// ── Configurações, log e sobre ──────────────────────────────────────────

fn mostrar_binario(chave: &str, rotulo: &str, emoji: &str, caminho: &Path) {
    let valor = if caminhos::executavel(caminho) {
        cor(&estilo::cortar(&caminhos::exibir_p(caminho), 34), &[VERDE])
    } else if !vazio(caminho) {
        cor(&estilo::cortar(&caminhos::exibir_p(caminho), 34), &[VERM])
    } else {
        cor(t("c_nao_definido"), &[CINZA])
    };
    say!(
        "{}   {}",
        estilo::opcao_simples(chave, rotulo, emoji),
        valor
    );
}

pub fn configuracoes() -> R {
    loop {
        estilo::limpar_tela();
        say!(
            "{}",
            estilo::titulo_caixa(t("cfg_titulo"), emo::CONFIG, CIANO)
        );
        let idioma = if config::idioma() == "pt" {
            "Português"
        } else {
            "English"
        };
        say!(
            "{}{}",
            estilo::opcao_simples("1", t("c_idioma"), emo::IDIOMA),
            cor(&format!("   {idioma}"), &[BRANC])
        );
        mostrar_binario("2", t("c_extract"), emo::EXTRAIR, &config::bin_extract());
        mostrar_binario("3", t("c_iso2god"), emo::GOD, &config::bin_god());
        say!(
            "{}{}",
            estilo::opcao_simples("4", t("c_pasta"), emo::PASTA),
            cor(
                &format!(
                    "   {}",
                    estilo::cortar(&caminhos::exibir(&config::pasta_padrao()), 34)
                ),
                &[BRANC]
            )
        );
        say!(
            "{}{}",
            estilo::opcao_simples("5", t("c_threads"), emo::LOTE),
            cor(&format!("   {}", config::threads_god()), &[BRANC])
        );
        say!(
            "{}{}",
            estilo::opcao_simples("6", t("c_cor"), emo::APP),
            cor(
                &format!(
                    "   {}",
                    if config::cor() {
                        t("c_ligado")
                    } else {
                        t("c_desligado")
                    }
                ),
                &[BRANC]
            )
        );
        say!();
        say!(
            "{}",
            estilo::opcao("0", t("voltar"), emo::SAIR, VERM, BRANC)
        );

        let escolha = perguntar(t("escolha"), false, None).trim().to_string();
        match escolha.as_str() {
            "" | "0" => return Ok(()),
            "1" => {
                let novo = if config::idioma() == "pt" { "en" } else { "pt" };
                config::mudar(|c| c.idioma = novo.into());
            }
            "2" | "3" => {
                let extract = escolha == "2";
                let atual = if extract {
                    config::bin_extract()
                } else {
                    config::bin_god()
                };
                let atual_txt = caminhos::exibir_p(&atual);
                let novo = perguntar(
                    t("c_novo_valor"),
                    false,
                    (!atual_txt.is_empty()).then_some(atual_txt.as_str()),
                );
                if !novo.is_empty() && novo != atual_txt {
                    let novo = caminhos::expandir_str(novo.trim());
                    if novo.is_file() && ferramentas::garantir_permissao(&novo) {
                        let valor = novo.into_os_string();
                        if extract {
                            config::mudar(|c| c.bin_extract_xiso = valor);
                        } else {
                            config::mudar(|c| c.bin_iso2god = valor);
                        }
                        estilo::sucesso(t("c_bin_ok"));
                    } else {
                        estilo::erro(t("c_bin_ruim"));
                    }
                    pausar();
                }
            }
            "4" => {
                let atual = config::pasta_padrao();
                let atual_txt = caminhos::exibir(&atual);
                let novo = perguntar(t("c_novo_valor"), false, Some(&atual_txt));
                if !novo.is_empty() {
                    let novo = if novo == atual_txt {
                        caminhos::expandir(&atual)
                    } else {
                        caminhos::expandir_str(novo.trim())
                    };
                    if novo.is_dir() {
                        let valor = novo.into_os_string();
                        config::mudar(|c| c.pasta_padrao = valor);
                        estilo::sucesso(t("c_salvo"));
                    } else {
                        estilo::erro(&format!(
                            "{} {}",
                            t("pasta_nao_existe"),
                            caminhos::exibir_p(&novo)
                        ));
                    }
                    pausar();
                }
            }
            "5" => {
                let n = perguntar_inteiro(t("c_novo_valor"), config::threads_god(), 1, 32);
                config::mudar(|c| c.threads_god = n);
            }
            "6" => {
                let novo = !config::cor();
                config::mudar(|c| c.cor = novo);
            }
            _ => {
                estilo::aviso(t("opcao_invalida"));
                pausar();
            }
        }
    }
}

/// As linhas como o `readlines` em modo texto: \r\n, \r e \n quebram.
fn linhas_texto(texto: &str) -> Vec<&str> {
    let mut linhas = Vec::new();
    let mut inicio = 0;
    let b = texto.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\n' || b[i] == b'\r' {
            linhas.push(&texto[inicio..i]);
            if b[i] == b'\r' && i + 1 < b.len() && b[i + 1] == b'\n' {
                i += 1;
            }
            inicio = i + 1;
        }
        i += 1;
    }
    if inicio < b.len() {
        linhas.push(&texto[inicio..]);
    }
    linhas
}

pub fn ver_log() -> R {
    estilo::limpar_tela();
    say!("{}", estilo::titulo_caixa(t("log_titulo"), emo::LOG, CIANO));
    let arquivo = config::arq_log();
    if !arquivo.is_file() {
        estilo::marca_nd(t("log_vazio"), "");
        pausar();
        return Ok(());
    }
    say!(
        "{}",
        campo14(
            t("sobre_log"),
            &estilo::cortar(&caminhos::exibir_p(&arquivo), 44),
            emo::ARQUIVO
        )
    );
    match std::fs::metadata(&arquivo).and_then(|m| {
        say!("{}", campo14(t("total"), &fmt_u(m.len()), emo::DISCO));
        say!();
        say!("{}", estilo::regua());
        say!();
        std::fs::read(&arquivo)
    }) {
        Ok(bytes) => {
            let texto = String::from_utf8_lossy(&bytes);
            let linhas = linhas_texto(&texto);
            for linha in &linhas[linhas.len().saturating_sub(25)..] {
                let cor_linha = if linha.contains("[ERROR]") {
                    VERM
                } else if linha.contains("[WARNING]") {
                    AMARE
                } else {
                    CINZA
                };
                say!(
                    "  {}",
                    cor(&estilo::cortar(linha, estilo::LARGURA), &[cor_linha])
                );
            }
        }
        Err(e) => estilo::erro(&format!(
            "{} {}",
            t("log_erro_ler"),
            caminhos::erro_os(&e, Some(&arquivo))
        )),
    }
    pausar();
    Ok(())
}

pub fn sobre() -> R {
    estilo::limpar_tela();
    say!(
        "{}",
        estilo::titulo_caixa(&format!("{APP_NOME} v{APP_VERSAO}"), emo::SOBRE, ROXO)
    );
    say!("{}", cor(&format!("  {}", t("sobre_1")), &[BRANC]));
    say!();
    say!("{}", campo("extract-xiso", t("sobre_2"), emo::EXTRAIR, 15));
    say!("{}", campo("iso2god", t("sobre_3"), emo::GOD, 15));
    say!();
    say!("{}", estilo::regua_gradiente());
    say!();
    say!(
        "{}",
        campo14(
            t("sobre_config"),
            &estilo::cortar(&caminhos::exibir_p(&config::arq_config()), 44),
            emo::CONFIG
        )
    );
    say!(
        "{}",
        campo14(
            t("sobre_log"),
            &estilo::cortar(&caminhos::exibir_p(&config::arq_log()), 44),
            emo::LOG
        )
    );
    say!();
    say!("{}", cor(&format!("  {}", t("sobre_4")), &[CINZA, ITAL]));
    pausar();
    Ok(())
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn divide_como_o_shlex() {
        let v = |x: &[&str]| Some(x.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert_eq!(
            dividir_shell("a 'b c' \"d\\\"e\" f\\ g"),
            v(&["a", "b c", "d\"e", "f g"])
        );
        assert_eq!(dividir_shell("x '' \"a\\b\""), v(&["x", "", "a\\b"]));
        assert_eq!(dividir_shell("#a b"), v(&["#a", "b"]));
        assert_eq!(dividir_shell("'aberta"), None);
        assert_eq!(dividir_shell("fim\\"), None);
    }

    #[test]
    fn linhas_como_o_python() {
        assert_eq!(linhas_py("a\r\nb\rc\n"), vec!["a", "b", "c"]);
        assert_eq!(linhas_texto("a\r\nb\rc\nd"), vec!["a", "b", "c", "d"]);
    }
}
