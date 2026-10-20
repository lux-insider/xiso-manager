//! A leitura de cada jogo da biblioteca: tipo de disco, nome, Title ID e
//! ícone. Roda em segundo plano, e a janela recebe cada resultado assim
//! que fica pronto.
//!
//! O que já foi lido fica no `janela-cache.json`: na próxima abertura, um
//! jogo com o mesmo tamanho e a mesma data aparece na hora, sem chamar o
//! iso2god de novo.

use std::collections::HashMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{Map, Value, json};

use slint::{Rgba8Pixel, SharedPixelBuffer};
use xiso_manager::{config, disco, ferramentas, iso};

/// O que a janela mostra de um jogo. O ícone fica como o iso2god o
/// entrega (PNG em base64), que é também como vai para o cache.
#[derive(Clone)]
pub struct Info {
    pub nome: String,
    pub detalhe: String,
    pub aviso: String,
    pub icone_base64: Option<String>,
    /// O resultado do último Verificar (0 nenhum, 1 idêntica ao Redump,
    /// 2 íntegra, 3 não confere). Some quando o arquivo muda.
    pub selo: i32,
}

impl Info {
    pub fn icone(&self) -> Option<SharedPixelBuffer<Rgba8Pixel>> {
        self.icone_base64
            .as_deref()
            .and_then(base64)
            .and_then(|png| decodificar_png(&png))
    }
}

// ── cache ───────────────────────────────────────────────────────────────

/// Tamanho e data do arquivo: se um dos dois mudou, lê de novo.
type Marca = (u64, i64);

static CACHE: Mutex<Option<HashMap<PathBuf, (Marca, Info)>>> = Mutex::new(None);
/// Teto do cache: jogos que saíram da biblioteca vão sendo esquecidos.
const LIMITE_CACHE: usize = 5000;

fn arquivo_cache() -> PathBuf {
    xiso_manager::config::dir_base().join("janela-cache.json")
}

fn marca(m: &std::fs::Metadata) -> Marca {
    let quando = m
        .modified()
        .ok()
        .and_then(|q| q.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    (m.len(), quando)
}

pub fn carregar_cache() {
    let mut mapa = HashMap::new();
    if let Ok(dados) = std::fs::read(arquivo_cache())
        && let Ok(Value::Object(m)) = serde_json::from_slice::<Value>(&dados)
    {
        for (caminho, v) in m {
            let texto = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
            let (Some(tamanho), Some(data)) = (
                v.get("tamanho").and_then(Value::as_u64),
                v.get("data").and_then(Value::as_i64),
            ) else {
                continue;
            };
            let info = Info {
                nome: texto("nome"),
                detalhe: texto("detalhe"),
                aviso: texto("aviso"),
                icone_base64: v.get("icone").and_then(Value::as_str).map(String::from),
                selo: v
                    .get("selo")
                    .and_then(Value::as_i64)
                    .filter(|s| (0..=3).contains(s))
                    .unwrap_or(0) as i32,
            };
            mapa.insert(PathBuf::from(caminho), ((tamanho, data), info));
        }
    }
    *CACHE.lock().unwrap_or_else(|e| e.into_inner()) = Some(mapa);
}

/// Grava o cache só com os jogos que ainda estão na biblioteca.
pub fn salvar_cache(biblioteca: &[PathBuf]) {
    let guarda = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let Some(mapa) = guarda.as_ref() else {
        return;
    };
    let mut saida = Map::new();
    for c in biblioteca {
        let (Some(texto), Some(((tamanho, data), info))) = (c.to_str(), mapa.get(c)) else {
            continue;
        };
        saida.insert(
            texto.into(),
            json!({
                "tamanho": tamanho,
                "data": data,
                "nome": info.nome,
                "detalhe": info.detalhe,
                "aviso": info.aviso,
                "icone": info.icone_base64,
                "selo": info.selo,
            }),
        );
    }
    drop(guarda);
    let destino = arquivo_cache();
    let temporario = destino.with_extension("json.tmp");
    let Ok(texto) = serde_json::to_vec(&Value::Object(saida)) else {
        return;
    };
    if std::fs::write(&temporario, texto)
        .and_then(|()| std::fs::rename(&temporario, &destino))
        .is_err()
    {
        let _ = std::fs::remove_file(&temporario);
    }
}

fn do_cache(caminho: &Path, m: Marca) -> Option<Info> {
    let guarda = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    match guarda.as_ref()?.get(caminho) {
        Some((marca, info)) if *marca == m => Some(info.clone()),
        _ => None,
    }
}

/// Guarda o resultado do Verificar, se o arquivo ainda é o mesmo que foi lido.
pub fn marcar_selo(caminho: &Path, selo: i32) {
    let Ok(dados) = std::fs::metadata(caminho) else {
        return;
    };
    let m = marca(&dados);
    let mut guarda = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((marca, info)) = guarda.as_mut().and_then(|c| c.get_mut(caminho))
        && *marca == m
    {
        info.selo = selo;
    }
}

fn guardar(caminho: &Path, m: Marca, info: &Info) {
    let mut guarda = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let mapa = guarda.get_or_insert_with(HashMap::new);
    if mapa.len() >= LIMITE_CACHE {
        mapa.clear();
    }
    mapa.insert(caminho.to_path_buf(), (m, info.clone()));
}

// ── leitura ─────────────────────────────────────────────────────────────

/// É uma imagem de disco pelo nome? (.iso; o conteúdo é conferido depois)
pub fn parece_iso(caminho: &Path) -> bool {
    caminho
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("iso"))
}

/// Um caminho solto na janela: a própria ISO, ou as ISOs de dentro de uma
/// pasta (sem descer nas subpastas).
pub fn expandir(caminho: &Path) -> Vec<PathBuf> {
    if caminho.is_dir() {
        let mut achadas: Vec<PathBuf> = std::fs::read_dir(caminho)
            .map(|lista| {
                lista
                    .filter_map(Result::ok)
                    .map(|e| e.path())
                    .filter(|p| p.is_file() && parece_iso(p))
                    .collect()
            })
            .unwrap_or_default();
        achadas.sort();
        achadas
    } else if parece_iso(caminho) {
        vec![caminho.to_path_buf()]
    } else {
        Vec::new()
    }
}

/// O nome do arquivo sem a extensão: o nome do jogo até o iso2god dizer o
/// verdadeiro (ou quando ele não consegue).
pub fn nome_do_arquivo(caminho: &Path) -> String {
    caminho
        .file_stem()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

pub fn ler(caminho: &Path) -> Info {
    let mut info = Info {
        nome: nome_do_arquivo(caminho),
        detalhe: String::new(),
        aviso: String::new(),
        icone_base64: None,
        selo: 0,
    };
    let dados = match std::fs::metadata(caminho) {
        Ok(m) => m,
        Err(_) => {
            info.aviso = "Arquivo não encontrado".into();
            return info;
        }
    };
    let m = marca(&dados);
    if let Some(guardado) = do_cache(caminho, m) {
        return guardado;
    }
    let info = ler_do_disco(caminho, dados.len(), info);
    guardar(caminho, m, &info);
    info
}

fn ler_do_disco(caminho: &Path, tamanho: u64, mut info: Info) -> Info {
    let (tipo, layout) = iso::detectar(caminho);
    if tipo == iso::Tipo::Desconhecido {
        info.detalhe = disco::fmt_u(tamanho);
        info.aviso = "Não parece uma imagem de Xbox ou Xbox 360".into();
        return info;
    }
    let mut partes = vec![
        match tipo {
            iso::Tipo::Xbox360 => "Xbox 360",
            _ => "Xbox",
        }
        .to_string(),
        layout,
    ];
    if let Some(dados) = info_iso2god(caminho) {
        if let Some(t) = dados.get("titulo").and_then(|v| v.as_str())
            && !t.trim().is_empty()
        {
            info.nome = t.trim().to_string();
        }
        if let Some(id) = dados.get("title_id").and_then(|v| v.as_str()) {
            partes.push(id.to_string());
        }
        if let Some(m) = dados.get("motivo_sem_metadados").and_then(|v| v.as_str()) {
            info.aviso = format!("Title ID não lido: {m}");
        }
        info.icone_base64 = dados
            .get("thumbnail_png_base64")
            .and_then(|v| v.as_str())
            .map(String::from);
    }
    partes.push(disco::fmt_u(tamanho));
    info.detalhe = partes.join("  ·  ");
    info
}

/// `iso2god info --json`, se o iso2god configurado for o compatível.
fn info_iso2god(caminho: &Path) -> Option<serde_json::Map<String, serde_json::Value>> {
    if !ferramentas::iso2god_proprio() {
        return None;
    }
    let cmd: Vec<OsString> = vec![
        config::bin_god().into_os_string(),
        "info".into(),
        "--json".into(),
        caminho.into(),
    ];
    let (status, saida, _) = ferramentas::capturar(&cmd, Duration::from_secs(60)).ok()?;
    if !status.success() {
        return None;
    }
    match serde_json::from_slice(&saida).ok()? {
        serde_json::Value::Object(m) => Some(m),
        _ => None,
    }
}

fn base64(texto: &str) -> Option<Vec<u8>> {
    fn valor(c: u8) -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        } as u32)
    }
    let limpo: Vec<u8> = texto.bytes().filter(|&c| c != b'=').collect();
    let mut saida = Vec::with_capacity(limpo.len() * 3 / 4);
    for grupo in limpo.chunks(4) {
        let mut n = 0u32;
        for (i, &c) in grupo.iter().enumerate() {
            n |= valor(c)? << (18 - 6 * i);
        }
        let bytes = n.to_be_bytes();
        saida.extend_from_slice(&bytes[1..grupo.len()]);
    }
    Some(saida)
}

/// O ícone do jogo (64x64 nos discos de Xbox 360) em RGBA. Imagens
/// estranhas ou grandes demais ficam de fora: a janela mostra o disco.
fn decodificar_png(dados: &[u8]) -> Option<SharedPixelBuffer<Rgba8Pixel>> {
    let mut decodificador = png::Decoder::new(std::io::Cursor::new(dados));
    decodificador
        .set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut leitor = decodificador.read_info().ok()?;
    let (largura, altura) = (leitor.info().width, leitor.info().height);
    if largura == 0 || altura == 0 || largura > 512 || altura > 512 {
        return None;
    }
    let mut buf = vec![0u8; leitor.output_buffer_size()?];
    let quadro = leitor.next_frame(&mut buf).ok()?;
    let bytes = &buf[..quadro.buffer_size()];
    let rgba: Vec<u8> = match quadro.color_type {
        png::ColorType::Rgba => bytes.to_vec(),
        png::ColorType::Rgb => bytes
            .as_chunks::<3>()
            .0
            .iter()
            .flat_map(|&[r, g, b]| [r, g, b, 255])
            .collect(),
        png::ColorType::GrayscaleAlpha => bytes
            .as_chunks::<2>()
            .0
            .iter()
            .flat_map(|&[g, a]| [g, g, g, a])
            .collect(),
        png::ColorType::Grayscale => bytes.iter().flat_map(|&g| [g, g, g, 255]).collect(),
        png::ColorType::Indexed => return None,
    };
    if rgba.len() != (largura * altura * 4) as usize {
        return None;
    }
    Some(SharedPixelBuffer::clone_from_slice(&rgba, largura, altura))
}

#[cfg(test)]
mod testes {
    #[test]
    fn base64_como_o_iso2god_grava() {
        assert_eq!(super::base64("Zm9vYmFy").unwrap(), b"foobar");
        assert_eq!(super::base64("Zm9vYg==").unwrap(), b"foob");
        assert_eq!(
            super::base64("iVBORw0KGgr//g==").unwrap(),
            b"\x89PNG\r\n\x1a\n\xff\xfe"
        );
        assert!(super::base64("@@@@").is_none());
    }
}
