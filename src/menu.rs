//! O menu principal.

use crate::acoes::{self, APP_NOME, APP_VERSAO, R};
use crate::entrada::{Lido, ler, pausar};
use crate::estilo::{
    self, AZUL, BRANC, CINZA, SIM_PONTO, VERDE, VERM, cor, emo, gradiente, largura_visivel,
};
use crate::ferramentas::eh_pt;
use crate::textos::t;
use crate::{caminhos, config, log, say};

type Acao = fn() -> R;

/// (tecla, emoji, rótulo, ação). Três grupos, não dois: extrair e listar
/// funcionam nos dois consoles.
fn itens_menu() -> Vec<(&'static str, &'static str, &'static str, Acao)> {
    vec![
        ("1", emo::GOD, t("m_god"), || acoes::god(None)),
        ("2", emo::ANALISAR, t("m_info_god"), || {
            acoes::info_god(None)
        }),
        ("3", emo::EXTRAIR, t("m_extrair"), || acoes::extrair(None)),
        ("4", emo::LISTAR, t("m_listar"), || acoes::listar(None)),
        ("5", emo::CRIAR, t("m_criar"), acoes::criar),
        ("6", emo::OTIMIZAR, t("m_reescrever"), || {
            acoes::reescrever(None)
        }),
        ("v", emo::ANALISAR, t("m_verificar"), || {
            acoes::verificar(None)
        }),
        ("7", emo::ASSISTENTE, t("m_assistente"), acoes::assistente),
        ("8", emo::LOTE, t("m_lote"), acoes::lote),
        ("9", emo::MANUAL, t("m_manual"), acoes::manual),
        ("c", emo::CONFIG, t("m_config"), acoes::configuracoes),
        ("l", emo::LOG, t("m_log"), acoes::ver_log),
        ("s", emo::SOBRE, t("m_sobre"), acoes::sobre),
    ]
}

/// (título, ferramenta, observação, teclas). O menu inteiro é um gradiente
/// contínuo: cada grupo recebe a sua faixa da escala.
fn grupos_menu() -> Vec<(
    &'static str,
    &'static str,
    &'static str,
    &'static [&'static str],
)> {
    if eh_pt() {
        // o extract-xiso-pt também cria e reescreve ISOs de 360
        return vec![
            (t("g_360"), "iso2god", "", &["1", "2"]),
            (
                t("g_ambos"),
                "extract-xiso-pt",
                "",
                &["3", "4", "5", "6", "v"],
            ),
            (t("g_geral"), "", "", &["7", "8", "9"]),
            (t("g_sistema"), "", "", &["c", "l", "s"]),
        ];
    }
    vec![
        (t("g_360"), "iso2god", "", &["1", "2"]),
        (t("g_ambos"), "extract-xiso", t("so_le"), &["3", "4"]),
        (t("g_classico"), "extract-xiso", "", &["5", "6"]),
        (t("g_geral"), "", "", &["7", "8", "9"]),
        (t("g_sistema"), "", "", &["c", "l", "s"]),
    ]
}

/// ── Xbox 360 ──────────────────────────────  iso2god ──
fn cabeca_grupo(titulo: &str, ferramenta: &str, observacao: &str, ini: f64, fim: f64) -> String {
    let esquerda = format!(
        "  {} {} ",
        cor("\u{2500}\u{2500}", &[&gradiente(ini)]),
        estilo::texto_gradiente_faixa(titulo, ini, fim)
    );
    let direita = if !ferramenta.is_empty() {
        let obs = if observacao.is_empty() {
            String::new()
        } else {
            cor(&format!(" ({observacao})"), &[CINZA])
        };
        format!(
            " {}{} {}",
            cor(ferramenta, &[CINZA]),
            obs,
            cor("\u{2500}\u{2500}", &[&gradiente(fim)])
        )
    } else {
        cor("\u{2500}\u{2500}", &[&gradiente(fim)])
    };
    let meio = estilo::LARGURA as i64
        - largura_visivel(&esquerda) as i64
        - largura_visivel(&direita) as i64;
    format!(
        "{}{}{}",
        esquerda,
        estilo::regua_gradiente_parcial(meio.max(0) as usize, ini, fim),
        direita
    )
}

fn mostrar_menu() {
    estilo::limpar_tela();
    say!(
        "{}",
        estilo::titulo_caixa_completo(
            &format!("{APP_NOME}  \u{00B7}  v{APP_VERSAO}"),
            emo::APP,
            AZUL,
            true
        )
    );
    // Bolinha verde para pronto, vermelha para ausente.
    for (caminho, nome, cor_dono) in [
        (config::bin_god(), "iso2god", AZUL),
        (
            config::bin_extract(),
            if eh_pt() {
                "extract-xiso-pt"
            } else {
                "extract-xiso"
            },
            VERDE,
        ),
    ] {
        let pronto = caminhos::executavel(&caminho);
        let cor_estado = if pronto { VERDE } else { VERM };
        say!(
            "  {} {}{}",
            cor(SIM_PONTO, &[cor_estado]),
            cor(
                &estilo::pad(nome, 16, estilo::Alinha::Esquerda),
                &[cor_dono]
            ),
            cor(estilo::pronto_ou_nao(pronto), &[cor_estado])
        );
    }
    say!();
    say!("{}", estilo::regua_gradiente());
    say!();

    let itens = itens_menu();
    let grupos = grupos_menu();
    let total = grupos.len().max(1) as f64;
    for (indice, (titulo, ferramenta, observacao, teclas)) in grupos.iter().enumerate() {
        let ini = indice as f64 / total;
        let fim = (indice + 1) as f64 / total;
        say!("{}", cabeca_grupo(titulo, ferramenta, observacao, ini, fim));
        // a chave de cada item fica no meio da faixa do seu grupo
        let cor_chave = gradiente((ini + fim) / 2.0);
        for tecla in teclas.iter() {
            if let Some(item) = itens.iter().find(|i| i.0 == *tecla) {
                say!(
                    "{}",
                    estilo::opcao(item.0, item.2, item.1, &cor_chave, BRANC)
                );
            }
        }
        say!();
    }
    say!("{}", estilo::opcao("0", t("sair"), emo::SAIR, VERM, BRANC));
    say!();
    say!("{}", estilo::regua());
}

/// A mensagem de um pânico.
pub fn mensagem_panico(p: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = p.downcast_ref::<&str>() {
        s.to_string()
    } else if let Some(s) = p.downcast_ref::<String>() {
        s.clone()
    } else {
        "?".into()
    }
}

/// Uma ação; o erro inesperado vira mensagem e o menu segue.
fn rodar(acao: Acao) {
    let falha = match std::panic::catch_unwind(acao) {
        Ok(Ok(())) => return,
        Ok(Err(e)) => e,
        Err(p) => mensagem_panico(p.as_ref()),
    };
    estilo::erro(&format!("{}: {}", t("erro_inesperado"), falha));
    log::evento(log::Nivel::Erro, &format!("Exceção no menu: {falha}"));
    pausar();
}

fn avisar_config_ilegivel() {
    let Some(copia) = config::ilegivel() else {
        return;
    };
    estilo::aviso(t("config_ilegivel"));
    if !copia.is_empty() {
        say!(
            "{}",
            cor(
                &format!(
                    "  {} {}",
                    t("config_copia"),
                    caminhos::encurtar_home(&copia)
                ),
                &[CINZA]
            )
        );
    }
    pausar();
}

/// O laço do menu. Devolve o código de saída.
pub fn principal() -> i32 {
    #[cfg(feature = "teste-erro-fatal")]
    if std::env::var_os("XM_ERRO_FATAL").is_some() {
        panic!("falhou de propósito");
    }
    config::carregar();
    log::info(&"=".repeat(30));
    log::info(&format!(
        "Sessão iniciada \u{2014} {APP_NOME} v{APP_VERSAO}"
    ));
    avisar_config_ilegivel();

    crate::ferramentas::resolver_binarios(true);

    loop {
        mostrar_menu();
        let escolha = match ler(&estilo::prompt(t("escolha"))) {
            Lido::Linha(l) => l.trim().to_lowercase(),
            Lido::Fim => {
                say!();
                return 0;
            }
            Lido::Interrompido => {
                say!();
                "0".to_string()
            }
        };
        if matches!(escolha.as_str(), "0" | "q" | "sair" | "quit") {
            say!();
            say!(
                "  {}  {}",
                emo::SAIR,
                estilo::texto_gradiente(t("ate_logo"))
            );
            say!();
            log::info("Sessão encerrada");
            return 0;
        }
        match itens_menu().into_iter().find(|i| i.0 == escolha) {
            Some(item) => rodar(item.3),
            None => {
                estilo::aviso(t("opcao_invalida"));
                pausar();
            }
        }
    }
}
