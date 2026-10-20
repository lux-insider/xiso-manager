//! As ações da janela: o comando de cada uma (o mesmo que o menu do
//! terminal monta) e o texto do resultado no painel de tarefas.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

use serde_json::Value;
use xiso_manager::{caminhos, config, disco, iso};

use crate::tarefa::{Andamento, Fim};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Acao {
    Converter,
    Extrair,
    Criar,
    Reescrever,
    Verificar,
}

impl Acao {
    /// O número da seção na barra lateral.
    pub fn da_secao(n: i32) -> Option<Acao> {
        Some(match n {
            1 => Acao::Converter,
            2 => Acao::Extrair,
            3 => Acao::Criar,
            4 => Acao::Reescrever,
            5 => Acao::Verificar,
            _ => return None,
        })
    }

    fn gerundio(self) -> &'static str {
        match self {
            Acao::Converter => "Convertendo",
            Acao::Extrair => "Extraindo",
            Acao::Criar => "Criando a ISO de",
            Acao::Reescrever => "Reescrevendo",
            Acao::Verificar => "Verificando",
        }
    }
}

/// O que está marcado na janela.
pub struct Opcoes {
    pub iso: Option<PathBuf>,
    pub nome: String,
    pub destino_god: String,
    pub threads: i32,
    pub cortar_fim: bool,
    pub destino_extrair: String,
    pub pular_atualizacao: bool,
    pub sobrescrever_extrair: bool,
    pub sobrescrever_criar: bool,
    pub sobrescrever_reescrever: bool,
    pub liberar_midia: bool,
    pub pasta_criar: String,
    pub saida_criar: String,
    pub substituir_original: bool,
    pub saida_reescrever: String,
}

/// Uma ação pronta para rodar.
pub struct Pedido {
    pub acao: Acao,
    pub cmd: Vec<OsString>,
    pub nome: String,
    /// Onde fica o resultado (o botão "Abrir pasta").
    pub pasta: Option<PathBuf>,
    /// A ISO que a ação gera, para entrar na biblioteca.
    pub iso_nova: Option<PathBuf>,
    /// A ISO de origem (para o selo do Verificar).
    pub iso: Option<PathBuf>,
}

impl Pedido {
    pub fn titulo(&self) -> String {
        format!("{} {}", self.acao.gerundio(), self.nome)
    }
}

// ── caminhos sugeridos ──────────────────────────────────────────────────

/// "Halo.xiso.iso" → "Halo".
fn nome_sem_xiso(iso: &Path) -> String {
    let base = caminhos::sem_extensao(iso).to_string_lossy().into_owned();
    match base.len().checked_sub(5) {
        Some(i) if base.is_char_boundary(i) && base[i..].eq_ignore_ascii_case(".xiso") => {
            base[..i].to_string()
        }
        _ => base,
    }
}

/// `<pasta da ISO>/<nome do jogo>`, como o terminal sugere.
pub fn padrao_extrair(iso: &Path) -> PathBuf {
    caminhos::pasta_de(iso).join(nome_sem_xiso(iso))
}

/// `<nome>.xiso.iso` ao lado da original (sem repetir o `.xiso`). Nunca
/// sugere um arquivo que já existe (que pode ser a própria original ou
/// outra cópia): aí vira `<nome> (reescrita).xiso.iso`, `(reescrita 2)`…
pub fn padrao_reescrever(iso: &Path) -> PathBuf {
    let base = nome_sem_xiso(iso);
    let pasta = caminhos::pasta_de(iso);
    let livre = |c: &PathBuf| c != iso && std::fs::symlink_metadata(c).is_err();
    let saida = pasta.join(format!("{base}.xiso.iso"));
    if livre(&saida) {
        return saida;
    }
    (1..1000)
        .map(|n| match n {
            1 => pasta.join(format!("{base} (reescrita).xiso.iso")),
            n => pasta.join(format!("{base} (reescrita {n}).xiso.iso")),
        })
        .find(livre)
        .unwrap_or(saida)
}

/// A ISO ao lado da pasta do jogo, com o nome dela.
pub fn padrao_criar(pasta: &Path) -> PathBuf {
    let mut nome = pasta.as_os_str().to_os_string();
    nome.push(".iso");
    PathBuf::from(nome)
}

fn tem_xbe(pasta: &Path) -> bool {
    std::fs::read_dir(pasta).is_ok_and(|itens| {
        itens.flatten().any(|i| {
            i.file_name()
                .to_string_lossy()
                .eq_ignore_ascii_case("default.xbe")
                && i.path().is_file()
        })
    })
}

// ── comandos ────────────────────────────────────────────────────────────

fn ferramenta(bin: PathBuf, nome: &str) -> Result<OsString, String> {
    if bin.is_file() {
        Ok(bin.into_os_string())
    } else {
        Err(format!(
            "O {nome} não foi encontrado ({}). Coloque-o na pasta do xiso-manager.",
            caminhos::exibir_p(&bin)
        ))
    }
}

fn exigir(texto: &str, falta: &str) -> Result<PathBuf, String> {
    if texto.trim().is_empty() {
        Err(falta.into())
    } else {
        Ok(PathBuf::from(texto))
    }
}

pub fn montar(acao: Acao, o: &Opcoes) -> Result<Pedido, String> {
    let os = |s: &str| OsString::from(s);
    let iso = || {
        o.iso
            .clone()
            .ok_or("Escolha um jogo na Biblioteca.".to_string())
    };
    let mut pedido = Pedido {
        acao,
        cmd: Vec::new(),
        nome: o.nome.clone(),
        pasta: None,
        iso_nova: None,
        iso: o.iso.clone(),
    };
    match acao {
        Acao::Converter => {
            let iso = iso()?;
            let destino = exigir(&o.destino_god, "Escolha a pasta de destino.")?;
            pedido.cmd = vec![
                ferramenta(config::bin_god(), "iso2god-pt")?,
                os("converter"),
                os("--progresso-json"),
                os("-j"),
                os(&o.threads.clamp(1, 32).to_string()),
                os("--padding"),
                os(if o.cortar_fim { "parcial" } else { "nenhuma" }),
                iso.into(),
                destino.clone().into(),
            ];
            pedido.pasta = Some(destino);
        }
        Acao::Extrair => {
            let iso = iso()?;
            let alvo = exigir(&o.destino_extrair, "Escolha a pasta de destino.")?;
            pedido.cmd = vec![
                ferramenta(config::bin_extract(), "extract-xiso-pt")?,
                os("extrair"),
                iso.into(),
                os("-d"),
                alvo.clone().into(),
                os("--progresso-json"),
            ];
            if o.pular_atualizacao {
                pedido.cmd.push(os("-s"));
            }
            if o.sobrescrever_extrair {
                pedido.cmd.push(os("--sobrescrever"));
            }
            pedido.pasta = Some(alvo);
        }
        Acao::Criar => {
            let origem = exigir(&o.pasta_criar, "Escolha a pasta do jogo.")?;
            let saida = exigir(&o.saida_criar, "Escolha onde gravar a ISO.")?;
            pedido.nome = caminhos::nome_base(origem.as_os_str())
                .to_string_lossy()
                .into_owned();
            pedido.cmd = vec![
                ferramenta(config::bin_extract(), "extract-xiso-pt")?,
                os("criar"),
                origem.clone().into(),
                os("-s"),
                saida.clone().into(),
                os("--progresso-json"),
            ];
            if o.liberar_midia && tem_xbe(&origem) {
                pedido.cmd.push(os("--liberar-midia"));
            }
            if o.sobrescrever_criar {
                pedido.cmd.push(os("--sobrescrever"));
            }
            pedido.pasta = Some(caminhos::pasta_de(&saida));
            pedido.iso_nova = Some(saida);
        }
        Acao::Reescrever => {
            let origem = iso()?;
            pedido.cmd = vec![
                ferramenta(config::bin_extract(), "extract-xiso-pt")?,
                os("reescrever"),
                origem.clone().into(),
                os("--progresso-json"),
            ];
            let saida = if o.substituir_original {
                pedido.cmd.push(os("--substituir"));
                origem.clone()
            } else {
                let saida = exigir(&o.saida_reescrever, "Escolha onde gravar a ISO reescrita.")?;
                if saida == origem || caminhos::mesmo_arquivo(&saida, &origem) == Some(true) {
                    return Err("A ISO reescrita não pode ser a própria original: marque \
                                \"Trocar a ISO original\" para isso."
                        .into());
                }
                pedido.cmd.extend([os("-s"), saida.clone().into()]);
                if o.sobrescrever_reescrever {
                    pedido.cmd.push(os("--sobrescrever"));
                }
                saida
            };
            if o.liberar_midia && iso::detectar(&origem).0 == iso::Tipo::Xbox {
                pedido.cmd.push(os("--liberar-midia"));
            }
            pedido.pasta = Some(caminhos::pasta_de(&saida));
            pedido.iso_nova = Some(saida);
        }
        Acao::Verificar => {
            pedido.cmd = vec![
                ferramenta(config::bin_extract(), "extract-xiso-pt")?,
                os("verificar"),
                iso()?.into(),
                os("--progresso-json"),
            ];
        }
    }
    Ok(pedido)
}

// ── textos do painel ────────────────────────────────────────────────────

/// A fração da barra (negativa quando ainda não há total) e a linha de baixo.
pub fn andamento(a: &Andamento) -> (f32, String) {
    if a.total == 0 {
        let fase = if a.fase.is_empty() {
            "Preparando…".into()
        } else {
            a.fase.clone()
        };
        return (-1.0, fase);
    }
    let fracao = (a.feito as f64 / a.total as f64).clamp(0.0, 1.0);
    let mut partes = vec![
        format!("{:.0}%", (fracao * 100.0).floor()),
        format!("{} de {}", disco::fmt_u(a.feito), disco::fmt_u(a.total)),
    ];
    if a.velocidade > 0.0 {
        partes.push(disco::fmt_velocidade(a.velocidade));
    }
    if let Some(eta) = a.eta.filter(|e| e.is_finite() && *e >= 0.0) {
        partes.push(format!("faltam {}", disco::fmt_tempo(eta)));
    }
    if !a.fase.is_empty() {
        partes.insert(0, a.fase.clone());
    }
    (fracao as f32, partes.join("  ·  "))
}

/// Estados do painel, como a janela os numera.
pub const PRONTA: i32 = 2;
pub const FALHOU: i32 = 3;
pub const CANCELADA: i32 = 4;
pub const RESSALVA: i32 = 5;

pub struct Resultado {
    pub estado: i32,
    pub titulo: String,
    pub detalhe: String,
}

/// O selo do cartão depois de um Verificar.
pub fn selo(fim: &Fim) -> Option<i32> {
    match fim {
        Fim::Ok {
            verificado: Some(v),
            ..
        } => Some(if situacao_dat(v) == Some("confere") {
            1
        } else {
            2
        }),
        Fim::NaoConfere(_) => Some(3),
        _ => None,
    }
}

pub fn resultado(p: &Pedido, fim: &Fim) -> Resultado {
    let r = |estado, titulo: String, detalhe: String| Resultado {
        estado,
        titulo,
        detalhe,
    };
    match fim {
        Fim::Cancelado => r(
            CANCELADA,
            format!("Cancelado: {}", p.nome),
            if p.acao == Acao::Verificar {
                "A leitura foi interrompida; nada foi gravado.".into()
            } else {
                "O que já tinha sido gravado foi apagado.".into()
            },
        ),
        Fim::Erro(e) => r(
            FALHOU,
            format!("Não deu certo: {}", p.nome),
            na_lingua_da_janela(e),
        ),
        Fim::NaoConfere(v) => r(
            RESSALVA,
            format!("{}: não confere com o Redump", p.nome),
            linha_verificado(v),
        ),
        Fim::Ok {
            mensagem,
            verificado,
            ..
        } => {
            if let Some(v) = verificado {
                let enxuta = matches!(v.get("disco_completo"), None | Some(Value::Null));
                let titulo = match situacao_dat(v) {
                    Some("confere") => format!("{}: original, idêntica ao Redump", p.nome),
                    _ if enxuta => format!("{}: íntegra (cópia enxuta)", p.nome),
                    Some(_) => format!("{}: íntegra, mas fora do Redump", p.nome),
                    None => format!("{}: íntegra", p.nome),
                };
                return r(PRONTA, titulo, linha_verificado(v));
            }
            let titulo = match p.acao {
                Acao::Converter => format!("{} convertido para GOD", p.nome),
                Acao::Extrair => format!("{} extraído", p.nome),
                Acao::Criar => format!("ISO de {} criada", p.nome),
                Acao::Reescrever => format!("{} reescrito", p.nome),
                Acao::Verificar => format!("{} verificado", p.nome),
            };
            let detalhe = if mensagem.is_empty() {
                p.pasta
                    .as_deref()
                    .map(|c| format!("Em {}", caminhos::exibir_p(c)))
                    .unwrap_or_default()
            } else {
                mensagem.clone()
            };
            r(PRONTA, titulo, detalhe)
        }
    }
}

/// As mensagens das ferramentas falam das opções de terminal; na janela,
/// a opção é a caixa de marcar.
fn na_lingua_da_janela(erro: &str) -> String {
    erro.replace("use --sobrescrever", "marque \"Substituir\"")
        .replace("--sobrescrever", "\"Substituir\"")
}

fn situacao_dat(v: &Value) -> Option<&str> {
    v.get("dat")?.get("situacao")?.as_str()
}

/// "XGD3 · disco completo · 1.204 arquivos · ✓ Redump: Darkwatch (USA)"
fn linha_verificado(v: &Value) -> String {
    let mut partes = Vec::new();
    if let Some(l) = v.get("layout").and_then(Value::as_str) {
        partes.push(l.to_string());
    }
    partes.push(
        match v.get("disco_completo") {
            Some(Value::Bool(true)) => "disco completo",
            None | Some(Value::Null) => "enxuta",
            _ => "tamanho fora do padrão",
        }
        .to_string(),
    );
    if let Some(n) = v.get("arquivos").and_then(Value::as_u64) {
        partes.push(format!("{n} arquivos"));
    }
    let avisos = v
        .get("avisos")
        .and_then(Value::as_array)
        .map_or(0, Vec::len);
    if avisos > 0 {
        partes.push(format!("{avisos} aviso(s)"));
    }
    let jogo = || {
        v.get("dat")
            .and_then(|d| d.get("jogo").or_else(|| d.get("rom")))
            .and_then(Value::as_str)
            .unwrap_or("?")
            .to_string()
    };
    partes.push(match situacao_dat(v) {
        Some("confere") => format!("Redump: {}", jogo()),
        Some("nao_confere") => format!("o Redump tem {} com outro SHA-1", jogo()),
        Some(_) if matches!(v.get("disco_completo"), None | Some(Value::Null)) => {
            "o Redump só guarda o disco completo".into()
        }
        Some(_) => "não está nos .dat do Redump".into(),
        None => "sem .dat do Redump instalado".into(),
    });
    partes.join("  ·  ")
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn caminhos_sugeridos() {
        let iso = Path::new("/jogos/Darkwatch (USA).xiso.iso");
        assert_eq!(
            padrao_reescrever(iso),
            Path::new("/jogos/Darkwatch (USA) (reescrita).xiso.iso")
        );
        assert_eq!(
            padrao_reescrever(Path::new("/jogos/Halo.iso")),
            Path::new("/jogos/Halo.xiso.iso")
        );
        assert_eq!(
            padrao_extrair(Path::new("/jogos/Halo.iso")),
            Path::new("/jogos/Halo")
        );
        assert_eq!(
            padrao_extrair(Path::new("/jogos/Halo.XISO.iso")),
            Path::new("/jogos/Halo")
        );
        assert_eq!(
            padrao_criar(Path::new("/jogos/Halo")),
            Path::new("/jogos/Halo.iso")
        );
    }

    #[test]
    fn reescrita_nunca_por_cima_de_um_arquivo() {
        let pasta = std::env::temp_dir().join(format!("xm-reescrever-{}", std::process::id()));
        std::fs::create_dir_all(&pasta).unwrap();
        for n in ["Halo.iso", "Halo.xiso.iso", "Halo (reescrita).xiso.iso"] {
            std::fs::write(pasta.join(n), b"").unwrap();
        }
        let saida = padrao_reescrever(&pasta.join("Halo.iso"));
        std::fs::remove_dir_all(&pasta).unwrap();
        assert_eq!(saida, pasta.join("Halo (reescrita 2).xiso.iso"));
    }

    #[test]
    fn barra() {
        let a = Andamento {
            fase: String::new(),
            feito: 512 << 20,
            total: 1 << 30,
            velocidade: 100.0 * 1048576.0,
            eta: Some(5.2),
        };
        let (f, t) = andamento(&a);
        assert!((f - 0.5).abs() < 1e-6);
        assert!(t.starts_with("50%  ·  "), "{t}");
        assert!(t.ends_with("faltam 00:05"), "{t}");
    }

    #[test]
    fn redump() {
        let v = serde_json::json!({"layout": "XGD3", "disco_completo": true, "arquivos": 12,
            "avisos": [], "dat": {"situacao": "confere", "jogo": "Halo (USA)"}});
        assert_eq!(
            linha_verificado(&v),
            "XGD3  ·  disco completo  ·  12 arquivos  ·  Redump: Halo (USA)"
        );
    }
}
