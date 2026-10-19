//! O config.json, ao lado do programa.
//!
//! A ferramenta é autocontida: config e log ficam na pasta do executável,
//! então dá para mover a pasta inteira e tudo continua no lugar. Se essa
//! pasta não aceita escrita, vale ~/.config/xiso-manager.

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use serde_json::{Map, Value};

use crate::caminhos;
use crate::log;
use crate::textos::IDIOMAS;

#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub idioma: String,
    pub bin_extract_xiso: OsString,
    pub bin_iso2god: OsString,
    pub pasta_padrao: OsString,
    pub threads_god: i64,
    pub cor: bool,
}

pub const CHAVES: [&str; 6] = [
    "idioma",
    "bin_extract_xiso",
    "bin_iso2god",
    "pasta_padrao",
    "threads_god",
    "cor",
];

impl Config {
    pub fn padrao() -> Config {
        Config {
            idioma: "pt".into(),
            bin_extract_xiso: OsString::new(),
            bin_iso2god: OsString::new(),
            pasta_padrao: caminhos::casa().into_os_string(),
            threads_god: 4,
            cor: true,
        }
    }
}

static CONFIG: Mutex<Option<Config>> = Mutex::new(None);
/// None: o config.json foi lido (ou não existe). Some: não pôde ser lido, e
/// aqui fica onde ficou a cópia dele ("" se nem a cópia deu certo).
static ILEGIVEL: Mutex<Option<String>> = Mutex::new(None);
static DIR_BASE: OnceLock<PathBuf> = OnceLock::new();

pub fn dir_base() -> &'static Path {
    DIR_BASE.get_or_init(|| {
        let aqui = std::env::current_exe()
            .ok()
            .map(|p| caminhos::resolver(&p))
            .and_then(|p| p.parent().map(Path::to_path_buf));
        if let Some(aqui) = aqui {
            let teste = aqui.join(".escrita_ok");
            if std::fs::write(&teste, b"").is_ok() {
                let _ = std::fs::remove_file(&teste);
                return aqui;
            }
        }
        let alternativa = caminhos::casa().join(".config").join("xiso-manager");
        let _ = std::fs::create_dir_all(&alternativa);
        alternativa
    })
}

pub fn arq_config() -> PathBuf {
    dir_base().join("config.json")
}

pub fn arq_log() -> PathBuf {
    dir_base().join("xiso-manager.log")
}

fn com<R>(f: impl FnOnce(&mut Config) -> R) -> R {
    let mut guarda = CONFIG.lock().unwrap_or_else(|e| e.into_inner());
    let config = guarda.get_or_insert_with(Config::padrao);
    f(config)
}

pub fn atual() -> Config {
    com(|c| c.clone())
}

pub fn idioma() -> String {
    com(|c| c.idioma.clone())
}

pub fn cor() -> bool {
    com(|c| c.cor)
}

pub fn bin_extract() -> PathBuf {
    com(|c| PathBuf::from(&c.bin_extract_xiso))
}

pub fn bin_god() -> PathBuf {
    com(|c| PathBuf::from(&c.bin_iso2god))
}

pub fn pasta_padrao() -> OsString {
    com(|c| c.pasta_padrao.clone())
}

pub fn threads_god() -> i64 {
    com(|c| c.threads_god)
}

/// Muda um valor e grava o config.json.
pub fn mudar(f: impl FnOnce(&mut Config)) {
    com(f);
    salvar();
}

/// Muda um valor sem gravar (a gravação vem depois).
pub fn mudar_sem_salvar(f: impl FnOnce(&mut Config)) {
    com(f);
}

/// Um caminho do config: texto sem caractere nulo (o JSON aceita o nulo,
/// nenhum caminho aceita).
fn caminho_valido(valor: &Value) -> Option<OsString> {
    match valor {
        Value::String(s) if !s.contains('\0') => Some(OsString::from(s)),
        _ => None,
    }
}

/// Aceita o valor só se ele for utilizável; senão fica o padrão. Um
/// config.json editado à mão ou estragado pode trazer qualquer coisa.
pub fn validar(config: &mut Config, chave: &str, valor: &Value) {
    let padrao = Config::padrao();
    match chave {
        "idioma" => {
            config.idioma = match valor {
                Value::String(s) if IDIOMAS.contains(&s.as_str()) => s.clone(),
                _ => padrao.idioma,
            }
        }
        "threads_god" => {
            let numero = match valor {
                Value::Bool(b) => Some(*b as i64),
                Value::Number(n) => n.as_i64().or_else(|| {
                    n.as_f64()
                        .filter(|f| f.is_finite())
                        .map(|f| f.trunc() as i64)
                }),
                Value::String(s) => s.trim().parse::<i64>().ok(),
                _ => None,
            };
            config.threads_god = numero.map(|n| n.clamp(1, 32)).unwrap_or(padrao.threads_god);
        }
        "cor" => {
            config.cor = match valor {
                Value::Bool(b) => *b,
                _ => padrao.cor,
            }
        }
        "bin_extract_xiso" => {
            config.bin_extract_xiso = caminho_valido(valor).unwrap_or(padrao.bin_extract_xiso)
        }
        "bin_iso2god" => config.bin_iso2god = caminho_valido(valor).unwrap_or(padrao.bin_iso2god),
        "pasta_padrao" => {
            config.pasta_padrao = caminho_valido(valor).unwrap_or(padrao.pasta_padrao)
        }
        _ => {}
    }
}

/// Lê o config.json. Um arquivo que não dá para ler (JSON quebrado, não é
/// um objeto, não é UTF-8) volta ao padrão, e antes disso é copiado para
/// config.json.invalido, para os caminhos configurados não se perderem.
pub fn carregar() {
    let mut config = Config::padrao();
    *ILEGIVEL.lock().unwrap_or_else(|e| e.into_inner()) = None;
    let arquivo = arq_config();
    if arquivo.is_file() {
        match ler_json(&arquivo) {
            Ok(dados) => {
                for chave in CHAVES {
                    if let Some(valor) = dados.get(chave) {
                        validar(&mut config, chave, valor);
                    }
                }
                let sobra = dados.keys().any(|k| !CHAVES.contains(&k.as_str()));
                *CONFIG.lock().unwrap_or_else(|e| e.into_inner()) = Some(config);
                // chave que não existe mais (de versões antigas): regrava sem ela
                if sobra {
                    salvar();
                }
                return;
            }
            Err(Some(motivo)) => guardar_ilegivel(&arquivo, &motivo),
            Err(None) => {}
        }
    }
    *CONFIG.lock().unwrap_or_else(|e| e.into_inner()) = Some(config);
}

/// Err(Some(motivo)): o conteúdo não serve; Err(None): nem deu para ler.
fn ler_json(arquivo: &Path) -> Result<Map<String, Value>, Option<String>> {
    let bytes = std::fs::read(arquivo).map_err(|_| None)?;
    let texto = std::str::from_utf8(&bytes).map_err(|e| {
        Some(format!(
            "'utf-8' codec can't decode byte 0x{:02x} in position {}",
            bytes[e.valid_up_to()],
            e.valid_up_to()
        ))
    })?;
    let valor: Value = serde_json::from_str(texto).map_err(|e| Some(e.to_string()))?;
    match valor {
        Value::Object(mapa) => Ok(mapa),
        _ => Err(Some("não é um objeto JSON".into())),
    }
}

fn guardar_ilegivel(arquivo: &Path, motivo: &str) {
    let copia = caminhos::juntar_os(&[arquivo.as_os_str(), OsStr::new(".invalido")]);
    let copia_txt = match std::fs::copy(arquivo, &copia) {
        Ok(_) => caminhos::exibir(&copia),
        Err(_) => String::new(),
    };
    log::evento(
        log::Nivel::Aviso,
        &format!(
            "config.json ilegível ({motivo}); cópia: {}",
            if copia_txt.is_empty() {
                "não foi possível"
            } else {
                &copia_txt
            }
        ),
    );
    *ILEGIVEL.lock().unwrap_or_else(|e| e.into_inner()) = Some(copia_txt);
}

/// Onde ficou a cópia do config.json ilegível, se ele não pôde ser lido.
pub fn ilegivel() -> Option<String> {
    ILEGIVEL.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

/// O conteúdo do config.json (o mesmo formato do json.dump com indent=2).
pub fn como_json(config: &Config) -> Result<String, String> {
    let texto = |o: &OsString| {
        o.to_str()
            .map(|s| Value::String(s.to_string()))
            .ok_or_else(|| format!("caminho que não é UTF-8: {}", caminhos::para_log(o)))
    };
    let mut mapa = Map::new();
    mapa.insert("idioma".into(), Value::String(config.idioma.clone()));
    mapa.insert("bin_extract_xiso".into(), texto(&config.bin_extract_xiso)?);
    mapa.insert("bin_iso2god".into(), texto(&config.bin_iso2god)?);
    mapa.insert("pasta_padrao".into(), texto(&config.pasta_padrao)?);
    mapa.insert("threads_god".into(), Value::from(config.threads_god));
    mapa.insert("cor".into(), Value::Bool(config.cor));
    serde_json::to_string_pretty(&Value::Object(mapa)).map_err(|e| e.to_string())
}

/// Grava num temporário ao lado e troca de uma vez: regravar no lugar
/// trunca o arquivo antes, e uma queda no meio o deixava pela metade. Se o
/// config.json for um link, o arquivo trocado é o de verdade.
pub fn salvar() -> bool {
    let config = atual();
    match salvar_em(&arq_config(), &config) {
        Ok(()) => true,
        Err(e) => {
            log::evento(log::Nivel::Erro, &format!("Falha ao salvar config: {e}"));
            false
        }
    }
}

pub fn salvar_em(arquivo: &Path, config: &Config) -> Result<(), String> {
    use std::io::Write;
    let conteudo = como_json(config)?;
    let _ = std::fs::create_dir_all(dir_base());
    let destino = std::fs::canonicalize(arquivo).unwrap_or_else(|_| caminhos::resolver(arquivo));
    let temporario = PathBuf::from(caminhos::juntar_os(&[
        destino.as_os_str(),
        OsStr::new(".tmp"),
    ]));
    let gravar = || -> std::io::Result<()> {
        let mut f = std::fs::File::create(&temporario)?;
        f.write_all(conteudo.as_bytes())?;
        f.flush()?;
        f.sync_all()?;
        drop(f);
        if let Ok(m) = std::fs::metadata(&destino) {
            let _ = std::fs::set_permissions(&temporario, m.permissions());
        }
        std::fs::rename(&temporario, &destino)
    };
    gravar().map_err(|e| {
        let _ = std::fs::remove_file(&temporario);
        caminhos::erro_os(&e, Some(&temporario))
    })
}

#[cfg(test)]
mod testes {
    use super::*;
    use serde_json::json;

    #[test]
    fn caminhos_com_nulo_voltam_ao_padrao() {
        for chave in ["bin_extract_xiso", "bin_iso2god", "pasta_padrao"] {
            let mut c = Config::padrao();
            validar(&mut c, chave, &json!("a\u{0}b"));
            assert_eq!(c, Config::padrao(), "{chave}");
            validar(&mut c, chave, &json!("/a/b"));
            assert_ne!(c, Config::padrao(), "{chave}");
        }
    }

    #[test]
    fn threads_e_cor_de_tipo_errado() {
        let mut c = Config::padrao();
        validar(&mut c, "threads_god", &json!("muitas"));
        assert_eq!(c.threads_god, 4);
        validar(&mut c, "threads_god", &json!(99));
        assert_eq!(c.threads_god, 32);
        validar(&mut c, "threads_god", &json!(" 7 "));
        assert_eq!(c.threads_god, 7);
        validar(&mut c, "threads_god", &json!(2.9));
        assert_eq!(c.threads_god, 2);
        validar(&mut c, "cor", &json!("sim"));
        assert!(c.cor);
        validar(&mut c, "idioma", &json!("fr"));
        assert_eq!(c.idioma, "pt");
    }

    #[test]
    fn formato_igual_ao_do_python() {
        let mut c = Config::padrao();
        c.pasta_padrao = "/jogos/Ação".into();
        let texto = como_json(&c).unwrap();
        assert!(texto.starts_with("{\n  \"idioma\": \"pt\",\n  \"bin_extract_xiso\": \"\",\n"));
        assert!(texto.contains("\"pasta_padrao\": \"/jogos/Ação\""));
        assert!(texto.ends_with("\"threads_god\": 4,\n  \"cor\": true\n}"));
    }

    #[cfg(unix)]
    #[test]
    fn link_continua_link() {
        let pasta = std::env::temp_dir().join(format!("xm-config-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&pasta);
        std::fs::create_dir_all(&pasta).unwrap();
        let guardado = pasta.join("guardado.json");
        std::fs::write(&guardado, "{}").unwrap();
        let link = pasta.join("config.json");
        std::os::unix::fs::symlink(&guardado, &link).unwrap();
        let mut c = Config::padrao();
        c.threads_god = 9;
        salvar_em(&link, &c).unwrap();
        assert!(
            std::fs::symlink_metadata(&link)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        assert_eq!(
            std::fs::read_to_string(&guardado).unwrap(),
            como_json(&c).unwrap()
        );
        let nomes: Vec<_> = std::fs::read_dir(&pasta)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(nomes.len(), 2, "sem sobra do temporário: {nomes:?}");
        std::fs::remove_dir_all(&pasta).unwrap();
    }
}
