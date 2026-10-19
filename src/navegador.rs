//! A escolha dos ISOs: navegar pelas pastas e marcar um ou vários.

use std::ffi::{OsStr, OsString};
use std::path::{Component, Path, PathBuf};

use crate::disco::fmt_u;
use crate::entrada::{pausar, perguntar};
use crate::estilo::{
    self, AZUL, Alinha, BRANC, CIANO, CINZA, ITAL, NEG, VERDE, VERM, campo14, cor, emo,
};
use crate::textos::t;
use crate::{caminhos, config, iso, say, selecao};

pub const CAB_ISO: [&str; 4] = ["#", "ARQUIVO", "TAMANHO", "CONSOLE"];
pub const AL_ISO: [Alinha; 4] = [
    Alinha::Esquerda,
    Alinha::Esquerda,
    Alinha::Direita,
    Alinha::Esquerda,
];

/// O caminho como o `pathlib` o escreve: sem "." no meio, sem barras
/// repetidas nem barra no fim.
pub fn como_pathlib(caminho: &Path) -> PathBuf {
    let mut saida = PathBuf::new();
    for parte in caminho.components() {
        if parte != Component::CurDir {
            saida.push(parte.as_os_str());
        }
    }
    if saida.as_os_str().is_empty() {
        saida.push(".");
    }
    saida
}

/// Chave de ordem como o `str.lower()` do Python: por ponto de código, e o
/// byte que não é UTF-8 conta como o "substituto" que o Python usaria.
pub fn chave_minuscula(nome: &OsStr) -> Vec<u32> {
    let mut saida = Vec::new();
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let mut bytes = nome.as_bytes();
        loop {
            match std::str::from_utf8(bytes) {
                Ok(s) => {
                    for c in s.chars().flat_map(char::to_lowercase) {
                        saida.push(c as u32);
                    }
                    break;
                }
                Err(e) => {
                    let bom = e.valid_up_to();
                    let s = std::str::from_utf8(&bytes[..bom]).unwrap();
                    for c in s.chars().flat_map(char::to_lowercase) {
                        saida.push(c as u32);
                    }
                    let ruins = e.error_len().unwrap_or(bytes.len() - bom);
                    for b in &bytes[bom..bom + ruins] {
                        saida.push(0xDC00 + *b as u32);
                    }
                    bytes = &bytes[bom + ruins..];
                }
            }
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        for c in char::decode_utf16(nome.encode_wide()) {
            match c {
                Ok(c) => saida.extend(c.to_lowercase().map(|c| c as u32)),
                Err(e) => saida.push(e.unpaired_surrogate() as u32),
            }
        }
    }
    saida
}

/// Chave de ordem por ponto de código (o `sorted` de textos do Python).
pub fn chave_exata(nome: &OsStr) -> Vec<u32> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let mut saida = Vec::new();
        let mut bytes = nome.as_bytes();
        loop {
            match std::str::from_utf8(bytes) {
                Ok(s) => {
                    saida.extend(s.chars().map(|c| c as u32));
                    return saida;
                }
                Err(e) => {
                    let bom = e.valid_up_to();
                    saida.extend(
                        std::str::from_utf8(&bytes[..bom])
                            .unwrap()
                            .chars()
                            .map(|c| c as u32),
                    );
                    let ruins = e.error_len().unwrap_or(bytes.len() - bom);
                    saida.extend(bytes[bom..bom + ruins].iter().map(|b| 0xDC00 + *b as u32));
                    bytes = &bytes[bom + ruins..];
                }
            }
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        char::decode_utf16(nome.encode_wide())
            .map(|c| match c {
                Ok(c) => c as u32,
                Err(e) => e.unpaired_surrogate() as u32,
            })
            .collect()
    }
}

/// Os .iso/.xiso da pasta, em ordem.
pub fn listar_isos(pasta: &Path) -> Vec<PathBuf> {
    let Ok(itens) = std::fs::read_dir(pasta) else {
        return Vec::new();
    };
    let mut nomes: Vec<OsString> = itens
        .flatten()
        .map(|i| i.file_name())
        .filter(|n| {
            let minusculo: String = caminhos::exibir(n).to_lowercase();
            (minusculo.ends_with(".iso") || minusculo.ends_with(".xiso")) && pasta.join(n).is_file()
        })
        .collect();
    nomes.sort_by_key(|n| chave_exata(n));
    nomes.into_iter().map(|n| pasta.join(n)).collect()
}

pub enum Item {
    Pasta(PathBuf),
    Iso(PathBuf),
}

/// As linhas da tabela de seleção.
pub fn linhas_iso(itens: &[(String, &Item)]) -> Vec<Vec<String>> {
    let mut linhas = Vec::new();
    for (i, (rotulo, item)) in itens.iter().enumerate() {
        let numero = cor(&(i + 1).to_string(), &[VERDE]);
        match item {
            Item::Pasta(_) => linhas.push(vec![
                numero,
                format!("{}  {}", emo::PASTA, rotulo),
                cor("\u{2014}", &[CINZA]),
                cor("\u{2014}", &[CINZA]),
            ]),
            Item::Iso(caminho) => {
                let tam = std::fs::metadata(caminho)
                    .map(|m| fmt_u(m.len()))
                    .unwrap_or_else(|_| "N/D".into());
                let (tipo, layout) = iso::detectar(caminho);
                let mut marca = iso::etiqueta(tipo);
                if !layout.is_empty() {
                    marca.push_str(&cor(&format!(" ({layout})"), &[CINZA]));
                }
                linhas.push(vec![
                    numero,
                    format!("{}  {}", emo::ARQUIVO, estilo::cortar(rotulo, 32)),
                    cor(&tam, &[BRANC]),
                    marca,
                ]);
            }
        }
    }
    linhas
}

fn tabela_iso(itens: &[(String, &Item)]) -> String {
    estilo::tabela(&CAB_ISO, &linhas_iso(itens), &AL_ISO)
}

fn guardar_pasta(pasta: &Path) {
    let valor = pasta.as_os_str().to_os_string();
    config::mudar(|c| c.pasta_padrao = valor);
}

/// Escolha de ISO navegando por pastas. Devolve os caminhos escolhidos.
pub fn navegador(multiplo: bool) -> Vec<PathBuf> {
    let mut atual = caminhos::resolver(&caminhos::expandir(&config::pasta_padrao()));
    if !atual.is_dir() {
        atual = caminhos::casa();
    }

    loop {
        estilo::limpar_tela();
        say!(
            "{}",
            estilo::titulo_caixa(t("nav_titulo"), emo::PASTA, CIANO)
        );
        say!(
            "{}",
            campo14(
                t("nav_pasta_atual"),
                &estilo::cortar(&caminhos::exibir_p(&atual), 44),
                emo::PASTA
            )
        );
        say!();

        let subpastas: Vec<PathBuf> = match std::fs::read_dir(&atual) {
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
                estilo::erro(&format!(
                    "{} {}",
                    t("sem_permissao_leitura"),
                    caminhos::exibir_p(&atual)
                ));
                atual = atual.parent().map(Path::to_path_buf).unwrap_or(atual);
                pausar();
                continue;
            }
            Err(_) => Vec::new(),
            Ok(itens) => {
                let mut lista: Vec<(Vec<u32>, PathBuf)> = itens
                    .flatten()
                    .filter(|i| !i.file_name().as_encoded_bytes().starts_with(b"."))
                    .map(|i| i.path())
                    .filter(|p| p.is_dir())
                    .map(|p| (chave_minuscula(p.file_name().unwrap_or_default()), p))
                    .collect();
                lista.sort_by(|a, b| a.0.cmp(&b.0));
                lista.into_iter().take(30).map(|(_, p)| p).collect()
            }
        };

        let isos = listar_isos(&atual);
        let mut itens: Vec<Item> = subpastas.into_iter().map(Item::Pasta).collect();
        let n_pastas = itens.len();
        itens.extend(isos.iter().cloned().map(Item::Iso));
        let rotulos: Vec<(String, &Item)> = itens
            .iter()
            .map(|item| match item {
                Item::Pasta(p) => (
                    format!("{}/", caminhos::exibir(p.file_name().unwrap_or_default())),
                    item,
                ),
                Item::Iso(p) => (caminhos::exibir(p.file_name().unwrap_or_default()), item),
            })
            .collect();

        if !itens.is_empty() {
            say!("{}", tabela_iso(&rotulos));
        } else {
            estilo::marca_nd(t("nav_nenhum_iso"), "");
        }

        say!();
        say!(
            "{}",
            estilo::opcao("..", t("nav_subir"), emo::SUBIR, AZUL, BRANC)
        );
        say!(
            "{}",
            estilo::opcao("c", t("nav_digitar"), emo::MANUAL, AZUL, BRANC)
        );
        if !isos.is_empty() && multiplo {
            say!(
                "{}",
                estilo::opcao("t", t("nav_todos"), emo::LOTE, AZUL, BRANC)
            );
        }
        say!(
            "{}",
            estilo::opcao("0", t("voltar"), emo::SAIR, VERM, BRANC)
        );
        say!();
        if multiplo && !isos.is_empty() {
            say!("{}", cor(&format!("  {}", t("nav_multi")), &[CINZA, ITAL]));
        }

        let escolha = perguntar(t("escolha"), false, None).trim().to_string();

        if escolha.is_empty() || escolha == "0" {
            return Vec::new();
        }
        if escolha == ".." {
            atual = atual.parent().map(Path::to_path_buf).unwrap_or(atual);
            continue;
        }
        if escolha.to_lowercase() == "c" {
            let caminho = perguntar(t("nav_caminho"), false, None);
            if !caminho.is_empty() {
                let alvo = como_pathlib(&caminhos::expandir_str(caminho.trim()));
                if alvo.is_dir() {
                    atual = caminhos::resolver(&alvo);
                } else if alvo.is_file() {
                    return vec![caminhos::resolver(&alvo)];
                } else {
                    estilo::erro(&format!(
                        "{} {}",
                        t("pasta_nao_existe"),
                        caminhos::exibir_p(&alvo)
                    ));
                    pausar();
                }
            }
            continue;
        }
        if escolha.to_lowercase() == "t" && !isos.is_empty() && multiplo {
            guardar_pasta(&atual);
            return isos;
        }

        let (indices, invalidos) = selecao::expandir(&escolha, itens.len());
        if !invalidos.is_empty() {
            estilo::aviso(&format!("{} {}", t("nav_invalido"), invalidos.join(", ")));
        }
        if indices.is_empty() {
            pausar();
            continue;
        }
        if let Item::Pasta(p) = &itens[indices[0]] {
            atual = caminhos::resolver(p);
            continue;
        }

        let mut selecionados: Vec<PathBuf> = indices
            .iter()
            .filter(|&&i| i >= n_pastas)
            .filter_map(|&i| match &itens[i] {
                Item::Iso(p) => Some(p.clone()),
                Item::Pasta(_) => None,
            })
            .collect();
        if selecionados.is_empty() {
            estilo::aviso(t("nav_nada"));
            pausar();
            continue;
        }
        if !multiplo {
            selecionados.truncate(1);
        }
        guardar_pasta(&atual);
        return selecionados;
    }
}

/// Tabela do que foi selecionado e o total. Devolve o total em bytes.
pub fn resumo_selecao(arquivos: &[PathBuf]) -> u64 {
    let itens: Vec<Item> = arquivos.iter().cloned().map(Item::Iso).collect();
    let rotulos: Vec<(String, &Item)> = arquivos
        .iter()
        .zip(itens.iter())
        .map(|(a, item)| (caminhos::exibir(&caminhos::nome_base(a.as_os_str())), item))
        .collect();
    say!("{}", tabela_iso(&rotulos));
    let total = caminhos::tamanho_arquivos(arquivos);
    say!();
    say!(
        "{}",
        campo14(
            t("total"),
            &format!(
                "{} {}  {}",
                arquivos.len(),
                t("arquivos_sel"),
                cor(&fmt_u(total), &[BRANC, NEG])
            ),
            emo::RESUMO
        )
    );
    total
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn caminho_como_o_pathlib() {
        assert_eq!(como_pathlib(Path::new("a//b/./c/")), PathBuf::from("a/b/c"));
        assert_eq!(como_pathlib(Path::new("./")), PathBuf::from("."));
        assert_eq!(como_pathlib(Path::new("/x/../y")), PathBuf::from("/x/../y"));
    }
}
