//! Onde estão o extract-xiso e o iso2god, e se servem.

use std::collections::HashMap;
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::entrada::perguntar;
use crate::estilo::{self, AMARE, CINZA, cor, emo};
use crate::textos::t;
use crate::{caminhos, config, say};

fn pastas_comuns() -> Vec<PathBuf> {
    let casa = caminhos::casa();
    let mut lista = vec![
        // a própria pasta da ferramenta vem primeiro
        config::dir_base().to_path_buf(),
        casa.join("Ferramentas").join("xiso-manager"),
        casa.join("Downloads"),
        casa.join("Ferramentas"),
        casa.join("bin"),
        casa.join(".local").join("bin"),
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/usr/bin"),
        PathBuf::from("/opt"),
    ];
    if let Ok(aqui) = std::env::current_dir() {
        lista.push(aqui);
    }
    lista
}

fn nomes_extract() -> Vec<&'static str> {
    let mut nomes = vec![];
    if cfg!(windows) {
        // dentro das pastas o nome precisa do .exe
        nomes.extend(["extract-xiso-pt.exe", "extract-xiso.exe"]);
    }
    nomes.extend([
        "extract-xiso-pt",
        "extract-xiso",
        "extract_xiso",
        "extract-xiso-linux",
    ]);
    nomes
}

fn nomes_iso2god() -> Vec<&'static str> {
    let mut nomes = vec![];
    if cfg!(windows) {
        nomes.push("iso2god.exe");
    }
    nomes.extend(["iso2god", "iso2god-linux-x86_64"]);
    nomes
}

fn localizar(nomes: &[&str]) -> Option<PathBuf> {
    for nome in nomes {
        if let Some(achado) = caminhos::no_path(nome) {
            return Some(achado);
        }
    }
    for pasta in pastas_comuns() {
        for nome in nomes {
            let alvo = pasta.join(nome);
            if caminhos::executavel(&alvo) {
                return Some(alvo);
            }
        }
    }
    None
}

/// O arquivo existe; se não tiver permissão de execução, tenta dar.
pub fn garantir_permissao(caminho: &Path) -> bool {
    if !caminho.is_file() {
        return false;
    }
    if caminhos::executavel(caminho) {
        return true;
    }
    estilo::aviso(t("c_chmod"));
    #[cfg(unix)]
    let deu = {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(caminho, std::fs::Permissions::from_mode(0o755)).is_ok()
    };
    #[cfg(not(unix))]
    let deu = true;
    if deu {
        estilo::sucesso(t("c_chmod_ok"));
        true
    } else {
        estilo::erro(&format!(
            "{} {}",
            t("c_chmod_falha"),
            caminhos::exibir_p(caminho)
        ));
        false
    }
}

/// Acha as ferramentas que faltam no config; no modo interativo, pergunta
/// pelas que não foram achadas.
pub fn resolver_binarios(interativo: bool) {
    let mut mudou = false;
    if !caminhos::executavel(&config::bin_extract())
        && let Some(achado) = localizar(&nomes_extract())
    {
        config::mudar_sem_salvar(|c| c.bin_extract_xiso = achado.into_os_string());
        mudou = true;
    }
    if !caminhos::executavel(&config::bin_god())
        && let Some(achado) = localizar(&nomes_iso2god())
    {
        config::mudar_sem_salvar(|c| c.bin_iso2god = achado.into_os_string());
        mudou = true;
    }
    if mudou {
        config::salvar();
    }
    if !interativo {
        return;
    }

    let mut faltando: Vec<(&str, bool)> = Vec::new();
    if !caminhos::executavel(&config::bin_extract()) {
        faltando.push(("extract-xiso", true));
    }
    if !caminhos::executavel(&config::bin_god()) {
        faltando.push(("iso2god", false));
    }
    if faltando.is_empty() {
        return;
    }

    estilo::limpar_tela();
    say!(
        "{}",
        estilo::titulo_caixa(t("bin_faltando_titulo"), emo::CONFIG, AMARE)
    );
    for (nome, _) in &faltando {
        estilo::marca_falha(nome, t("indisponivel"));
    }
    say!();
    say!("{}", cor(&format!("  {}", t("bin_procurei")), &[CINZA]));
    say!();
    for (nome, eh_extract) in &faltando {
        let caminho = perguntar(&format!("{} ({})", t("bin_informe"), nome), false, None);
        if !caminho.is_empty() {
            let caminho = caminhos::expandir_str(caminho.trim());
            if caminho.is_file() && garantir_permissao(&caminho) {
                let valor = caminho.into_os_string();
                if *eh_extract {
                    config::mudar(|c| c.bin_extract_xiso = valor);
                } else {
                    config::mudar(|c| c.bin_iso2god = valor);
                }
                estilo::sucesso(t("c_bin_ok"));
            } else {
                estilo::erro(t("c_bin_ruim"));
            }
        } else {
            estilo::dica(t("bin_pulado"));
        }
    }
    say!();
}

/// O extrator configurado é o extract-xiso-pt (senão, o oficial).
pub fn eh_pt() -> bool {
    caminhos::exibir(&caminhos::nome_base(config::bin_extract().as_os_str()))
        .to_lowercase()
        .contains("extract-xiso-pt")
}

pub enum Falha {
    Abrir(std::io::Error),
    Prazo,
}

/// Roda um programa e guarda as duas saídas, com prazo.
pub fn capturar(
    cmd: &[OsString],
    prazo: Duration,
) -> Result<(ExitStatus, Vec<u8>, Vec<u8>), Falha> {
    let mut filho = Command::new(&cmd[0])
        .args(&cmd[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(Falha::Abrir)?;
    let ler = |fluxo: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut dados = Vec::new();
            if let Some(mut f) = fluxo {
                let _ = f.read_to_end(&mut dados);
            }
            dados
        })
    };
    let saida = ler(filho
        .stdout
        .take()
        .map(|f| Box::new(f) as Box<dyn Read + Send>));
    let erros = ler(filho
        .stderr
        .take()
        .map(|f| Box::new(f) as Box<dyn Read + Send>));
    let fim = Instant::now() + prazo;
    let status = loop {
        match filho.try_wait() {
            Ok(Some(s)) => break s,
            Ok(None) if Instant::now() < fim => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = filho.kill();
                let _ = filho.wait();
                return Err(Falha::Prazo);
            }
        }
    };
    Ok((
        status,
        saida.join().unwrap_or_default(),
        erros.join().unwrap_or_default(),
    ))
}

static PROPRIO: Mutex<Option<HashMap<PathBuf, bool>>> = Mutex::new(None);

/// O iso2god configurado é o compatível (com `converter`/`info` e o
/// `--progresso-json`)? Outros programas com o mesmo nome não servem. O
/// `--help` roda uma vez por binário.
pub fn iso2god_proprio() -> bool {
    let binario = config::bin_god();
    if let Some(r) = PROPRIO
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get_or_insert_with(HashMap::new)
        .get(&binario)
    {
        return *r;
    }
    let ajuda = match capturar(
        &[binario.clone().into_os_string(), "--help".into()],
        Duration::from_secs(10),
    ) {
        Ok((_, saida, _)) => String::from_utf8_lossy(&saida).into_owned(),
        Err(_) => String::new(),
    };
    let proprio = ajuda.contains("converter") && ajuda.contains("info");
    PROPRIO
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get_or_insert_with(HashMap::new)
        .insert(binario, proprio);
    proprio
}

pub fn exigir_binario(caminho: &Path, nome: &str) -> bool {
    if caminhos::executavel(caminho) {
        return true;
    }
    estilo::erro(&format!("{}: {}", t("bin_necessario"), nome));
    estilo::dica(t("bin_configure"));
    false
}

/// O iso2god configurado existe e é o compatível? Senão, explica o porquê.
pub fn exigir_iso2god() -> bool {
    if !exigir_binario(&config::bin_god(), "iso2god") {
        return false;
    }
    if iso2god_proprio() {
        return true;
    }
    estilo::erro(t("iso2god_incompativel"));
    estilo::dica(t("iso2god_onde_baixar"));
    false
}
