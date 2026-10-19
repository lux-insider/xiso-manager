//! De qual console é o ISO: lê a assinatura "MICROSOFT*XBOX*MEDIA" nos
//! offsets conhecidos e, no XISO (sem partição de vídeo), o executável da
//! raiz: default.xex é Xbox 360, default.xbe é Xbox.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use crate::estilo::{AZUL, CINZA, VERDE, cor};
use crate::textos::t;
use crate::{caminhos, log};

const MAGIC: &[u8] = b"MICROSOFT*XBOX*MEDIA";

#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash)]
pub enum Tipo {
    Xbox,
    Xbox360,
    Desconhecido,
}

/// offset da assinatura → (tipo, rótulo do layout de disco)
const OFFSETS: [(u64, Tipo, &str); 4] = [
    (0x10000, Tipo::Xbox, "XISO / XGD cru"),
    (0x1831_0000, Tipo::Xbox, "XGD1"),
    (0xFDA_0000, Tipo::Xbox360, "XGD2"),
    (0x209_0000, Tipo::Xbox360, "XGD3"),
];

type Chave = (PathBuf, u64, i64);
static CACHE: Mutex<Option<HashMap<Chave, (Tipo, String)>>> = Mutex::new(None);
const LIMITE_CACHE: usize = 4000;

/// Procura default.xex / default.xbe na tabela do diretório raiz. É a prova
/// de qual console é o disco; o tamanho é só um palpite.
fn executavel_na_raiz(f: &mut std::fs::File, base: u64) -> Option<Tipo> {
    f.seek(SeekFrom::Start(base + 32 * 2048 + MAGIC.len() as u64))
        .ok()?;
    let mut cab = [0u8; 8];
    f.read_exact(&mut cab).ok()?;
    let setor = u32::from_le_bytes(cab[0..4].try_into().unwrap()) as u64;
    let tamanho = u32::from_le_bytes(cab[4..8].try_into().unwrap()) as u64;
    if !(1..=4 * 1024 * 1024).contains(&tamanho) {
        return None;
    }
    f.seek(SeekFrom::Start(base + setor * 2048)).ok()?;
    let mut tabela = Vec::new();
    f.take(tamanho).read_to_end(&mut tabela).ok()?;

    // Árvore binária de entradas: esquerda/direita são deslocamentos em
    // palavras de 4 bytes dentro da tabela; 0 = sem filho.
    let mut achados = HashSet::new();
    let mut pendentes = vec![0usize];
    let mut vistos = HashSet::new();
    while let Some(pos) = pendentes.pop() {
        if vistos.len() >= 4096 {
            break;
        }
        if vistos.contains(&pos) || pos + 14 > tabela.len() {
            continue;
        }
        vistos.insert(pos);
        let esq = u16::from_le_bytes([tabela[pos], tabela[pos + 1]]) as usize;
        let dir = u16::from_le_bytes([tabela[pos + 2], tabela[pos + 3]]) as usize;
        let n = tabela[pos + 13] as usize;
        if esq == 0xFFFF {
            continue;
        }
        let fim = (pos + 14 + n).min(tabela.len());
        achados.insert(tabela[pos + 14..fim].to_ascii_lowercase());
        for x in [esq, dir] {
            if x != 0 {
                pendentes.push(x * 4);
            }
        }
    }
    if achados.contains(b"default.xex".as_slice()) {
        Some(Tipo::Xbox360)
    } else if achados.contains(b"default.xbe".as_slice()) {
        Some(Tipo::Xbox)
    } else {
        None
    }
}

fn analisar(caminho: &Path, tamanho: u64) -> std::io::Result<(Tipo, String)> {
    let mut f = std::fs::File::open(caminho)?;
    for (offset, tipo, rotulo) in OFFSETS {
        if offset + MAGIC.len() as u64 > tamanho {
            continue;
        }
        let mut lido = [0u8; 20];
        if f.seek(SeekFrom::Start(offset)).is_err() || f.read_exact(&mut lido).is_err() {
            continue;
        }
        if lido == MAGIC {
            if offset == 0x10000 {
                // XISO cru pode ser dos dois consoles: a raiz diz qual; o
                // tamanho é só o último recurso, se a raiz não puder ser lida
                let tipo =
                    executavel_na_raiz(&mut f, 0).unwrap_or(if tamanho > 6 * 1024u64.pow(3) {
                        Tipo::Xbox360
                    } else {
                        Tipo::Xbox
                    });
                return Ok((tipo, "XISO".into()));
            }
            return Ok((tipo, rotulo.into()));
        }
    }
    Ok((Tipo::Desconhecido, String::new()))
}

/// O tipo e o rótulo do layout. O resultado fica guardado (o navegador
/// redesenha a tabela a cada tecla); a chave inclui o tamanho e a data, para
/// um ISO trocado no mesmo caminho não herdar o tipo do anterior.
pub fn detectar(caminho: &Path) -> (Tipo, String) {
    let Ok(m) = std::fs::metadata(caminho) else {
        return (Tipo::Desconhecido, String::new());
    };
    let quando = m
        .modified()
        .ok()
        .and_then(|q| q.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let chave = (caminho.to_path_buf(), m.len(), quando);
    {
        let mut guarda = CACHE.lock().unwrap_or_else(|e| e.into_inner());
        let cache = guarda.get_or_insert_with(HashMap::new);
        if let Some(r) = cache.get(&chave) {
            return r.clone();
        }
        if cache.len() > LIMITE_CACHE {
            cache.clear();
        }
    }
    let resultado = match analisar(caminho, m.len()) {
        Ok(r) => r,
        Err(e) => {
            log::evento(
                log::Nivel::Aviso,
                &format!(
                    "Falha ao analisar {}: {}",
                    caminhos::para_log(caminho.as_os_str()),
                    caminhos::erro_os(&e, Some(caminho))
                ),
            );
            (Tipo::Desconhecido, String::new())
        }
    };
    CACHE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get_or_insert_with(HashMap::new)
        .insert(chave, resultado.clone());
    resultado
}

pub fn rotulo(tipo: Tipo) -> &'static str {
    match tipo {
        Tipo::Xbox => t("t_xbox"),
        Tipo::Xbox360 => t("t_xbox360"),
        Tipo::Desconhecido => t("t_desconhecido"),
    }
}

pub fn cor_tipo(tipo: Tipo) -> &'static str {
    match tipo {
        Tipo::Xbox => VERDE,
        Tipo::Xbox360 => AZUL,
        Tipo::Desconhecido => CINZA,
    }
}

/// Etiqueta curta e colorida para as tabelas.
pub fn etiqueta(tipo: Tipo) -> String {
    match tipo {
        Tipo::Xbox => cor("Xbox", &[VERDE]),
        Tipo::Xbox360 => cor("Xbox 360", &[AZUL]),
        Tipo::Desconhecido => cor("?", &[CINZA]),
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    fn iso(nome: &str, dados: &[u8]) -> PathBuf {
        let p = std::env::temp_dir().join(format!("xm-iso-{}-{}", std::process::id(), nome));
        std::fs::write(&p, dados).unwrap();
        p
    }

    /// Um XISO mínimo: a assinatura no setor 32 e a raiz com um arquivo.
    fn xiso(executavel: &[u8]) -> Vec<u8> {
        let mut d = vec![0u8; 0x10000];
        d.extend_from_slice(MAGIC);
        d.extend_from_slice(&33u32.to_le_bytes()); // setor da raiz
        d.extend_from_slice(&2048u32.to_le_bytes()); // tamanho da raiz
        d.resize(33 * 2048, 0);
        let mut entrada = vec![0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x20];
        entrada.push(executavel.len() as u8);
        entrada.extend_from_slice(executavel);
        d.extend_from_slice(&entrada);
        d.resize(34 * 2048, 0);
        d
    }

    #[test]
    fn xiso_pelo_executavel_da_raiz() {
        let a = iso("360", &xiso(b"DEFAULT.XEX"));
        assert_eq!(detectar(&a), (Tipo::Xbox360, "XISO".into()));
        let b = iso("xbox", &xiso(b"default.xbe"));
        assert_eq!(detectar(&b), (Tipo::Xbox, "XISO".into()));
        let c = iso("nada", b"ISO ORIGINAL");
        assert_eq!(detectar(&c), (Tipo::Desconhecido, String::new()));
        for p in [a, b, c] {
            std::fs::remove_file(p).unwrap();
        }
    }
}
