//! Caminhos: a pasta pessoal, o "~", caminho absoluto, comparação de pastas,
//! permissões, a busca no PATH e a forma de mostrar um nome na tela.
//!
//! Os caminhos ficam sempre como `OsString`/`PathBuf`, com os bytes de
//! verdade: um nome que não é UTF-8 (uma cópia vinda do Windows em Latin-1)
//! aparece na tela com "?" no lugar do que não dá para mostrar, mas é usado
//! inteiro nos arquivos e nas ferramentas.

use std::ffi::{OsStr, OsString};
use std::path::{Component, Path, PathBuf};

/// A pasta pessoal: HOME no Unix, USERPROFILE no Windows.
pub fn casa() -> PathBuf {
    #[cfg(windows)]
    {
        if let Some(p) = std::env::var_os("USERPROFILE").filter(|v| !v.is_empty()) {
            return PathBuf::from(p);
        }
        if let (Some(d), Some(p)) = (std::env::var_os("HOMEDRIVE"), std::env::var_os("HOMEPATH")) {
            let mut tudo = d;
            tudo.push(p);
            return PathBuf::from(tudo);
        }
        PathBuf::from("C:\\")
    }
    #[cfg(not(windows))]
    {
        std::env::var_os("HOME")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/"))
    }
}

fn eh_separador(b: u8) -> bool {
    b == b'/' || (cfg!(windows) && b == b'\\')
}

/// Troca o "~" do começo pela pasta pessoal ("~" e "~/…").
pub fn expandir(caminho: &OsStr) -> PathBuf {
    let bytes = caminho.as_encoded_bytes();
    if bytes.first() != Some(&b'~') || (bytes.len() > 1 && !eh_separador(bytes[1])) {
        return PathBuf::from(caminho);
    }
    let casa = casa();
    let mut base = casa.into_os_string().into_encoded_bytes();
    while base.len() > 1 && eh_separador(*base.last().unwrap()) {
        base.pop();
    }
    base.extend_from_slice(&bytes[1..]);
    // SAFETY: os dois pedaços vieram de OsStr e foram unidos numa divisa de
    // caractere ASCII (o "~" e o separador).
    PathBuf::from(unsafe { OsString::from_encoded_bytes_unchecked(base) })
}

pub fn expandir_str(texto: &str) -> PathBuf {
    expandir(OsStr::new(texto))
}

/// Junta com a pasta atual e resolve "." e ".." só no texto, sem seguir
/// links (como o `os.path.abspath`).
pub fn absoluto(caminho: &Path) -> PathBuf {
    let base = if caminho.is_absolute() {
        caminho.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("/"))
            .join(caminho)
    };
    normalizar(&base)
}

/// Resolve "." e ".." só no texto (como o `os.path.normpath`).
pub fn normalizar(caminho: &Path) -> PathBuf {
    let mut raiz: Vec<Component> = Vec::new();
    let mut partes: Vec<&OsStr> = Vec::new();
    for parte in caminho.components() {
        match parte {
            Component::Prefix(_) | Component::RootDir => raiz.push(parte),
            Component::CurDir => {}
            Component::ParentDir => {
                if partes.last().is_some_and(|p| *p != "..") {
                    partes.pop();
                } else if raiz.is_empty() {
                    // relativo: o ".." fica; "/.." continua sendo "/"
                    partes.push(OsStr::new(".."));
                }
            }
            Component::Normal(nome) => partes.push(nome),
        }
    }
    let mut saida = PathBuf::new();
    for r in raiz {
        saida.push(r.as_os_str());
    }
    for p in partes {
        saida.push(p);
    }
    if saida.as_os_str().is_empty() {
        saida.push(".");
    }
    saida
}

/// Tira o "\\?\" que o Windows põe nos caminhos resolvidos, quando ele não
/// é necessário (unidade com letra).
fn sem_prefixo_longo(caminho: PathBuf) -> PathBuf {
    #[cfg(windows)]
    {
        let texto = caminho.as_os_str().to_string_lossy();
        if let Some(resto) = texto.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{resto}"));
        }
        if let Some(resto) = texto.strip_prefix(r"\\?\") {
            let b = resto.as_bytes();
            if b.len() >= 2 && b[1] == b':' && b[0].is_ascii_alphabetic() {
                return PathBuf::from(resto);
            }
        }
    }
    caminho
}

/// O caminho de verdade, com os links resolvidos no que existir (como o
/// `Path.resolve()`); o que não existe é só juntado.
pub fn resolver(caminho: &Path) -> PathBuf {
    let abs = absoluto(caminho);
    if let Ok(r) = std::fs::canonicalize(&abs) {
        return sem_prefixo_longo(r);
    }
    let mut existente = abs.clone();
    let mut resto: Vec<OsString> = Vec::new();
    while let Some(nome) = existente.file_name().map(|n| n.to_os_string()) {
        existente.pop();
        resto.push(nome);
        if let Ok(r) = std::fs::canonicalize(&existente) {
            let mut r = sem_prefixo_longo(r);
            for parte in resto.iter().rev() {
                r.push(parte);
            }
            return r;
        }
    }
    abs
}

/// O nome como aparece na tela: o que não é UTF-8 vira "?", um por byte.
pub fn exibir(caminho: &OsStr) -> String {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        bytes_exibir(caminho.as_bytes())
    }
    #[cfg(not(unix))]
    {
        caminho.to_string_lossy().replace('\u{FFFD}', "?")
    }
}

pub fn exibir_p(caminho: &Path) -> String {
    exibir(caminho.as_os_str())
}

/// Bytes em UTF-8; cada byte inválido vira "?".
#[cfg_attr(not(unix), allow(dead_code))]
pub fn bytes_exibir(mut bytes: &[u8]) -> String {
    let mut saida = String::with_capacity(bytes.len());
    loop {
        match std::str::from_utf8(bytes) {
            Ok(s) => {
                saida.push_str(s);
                return saida;
            }
            Err(e) => {
                let bom = e.valid_up_to();
                saida.push_str(std::str::from_utf8(&bytes[..bom]).unwrap());
                let ruins = e.error_len().unwrap_or(bytes.len() - bom);
                for _ in 0..ruins {
                    saida.push('?');
                }
                bytes = &bytes[bom + ruins..];
            }
        }
    }
}

/// O nome como vai para o log: o que não é UTF-8 vira "\udcXX", como o
/// `backslashreplace` do Python fazia.
pub fn para_log(caminho: &OsStr) -> String {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let mut bytes = caminho.as_bytes();
        let mut saida = String::new();
        loop {
            match std::str::from_utf8(bytes) {
                Ok(s) => {
                    saida.push_str(s);
                    return saida;
                }
                Err(e) => {
                    let bom = e.valid_up_to();
                    saida.push_str(std::str::from_utf8(&bytes[..bom]).unwrap());
                    let ruins = e.error_len().unwrap_or(bytes.len() - bom);
                    for b in &bytes[bom..bom + ruins] {
                        saida.push_str(&format!("\\udc{b:02x}"));
                    }
                    bytes = &bytes[bom + ruins..];
                }
            }
        }
    }
    #[cfg(not(unix))]
    {
        caminho.to_string_lossy().into_owned()
    }
}

/// "~/Jogos" lê melhor que "/home/usuario/Jogos" e economiza colunas.
pub fn encurtar_home(texto: &str) -> String {
    let casa = exibir_p(&casa());
    match texto.strip_prefix(casa.as_str()) {
        Some(resto) => format!("~{resto}"),
        None => texto.to_string(),
    }
}

pub fn encurtar_p(caminho: &Path) -> String {
    encurtar_home(&exibir_p(caminho))
}

/// O nome final do caminho (como `os.path.basename`): depois da última
/// barra; "" se o caminho termina em barra.
pub fn nome_base(caminho: &OsStr) -> OsString {
    let bytes = caminho.as_encoded_bytes();
    let corte = bytes
        .iter()
        .rposition(|&b| eh_separador(b) || (cfg!(windows) && b == b':'))
        .map(|i| i + 1)
        .unwrap_or(0);
    // SAFETY: o corte é logo depois de um caractere ASCII.
    unsafe { OsStr::from_encoded_bytes_unchecked(&bytes[corte..]) }.to_os_string()
}

/// Texto depois da última barra (para os detalhes da barra de progresso).
pub fn nome_base_str(texto: &str) -> &str {
    let corte = texto
        .char_indices()
        .filter(|&(_, c)| c == '/' || (cfg!(windows) && (c == '\\' || c == ':')))
        .map(|(i, c)| i + c.len_utf8())
        .next_back()
        .unwrap_or(0);
    &texto[corte..]
}

/// A pasta do caminho (como `os.path.dirname`): "" quando não há barra.
pub fn pasta_de(caminho: &Path) -> PathBuf {
    let bytes = caminho.as_os_str().as_encoded_bytes();
    let Some(mut i) = bytes.iter().rposition(|&b| eh_separador(b)) else {
        return PathBuf::new();
    };
    let mut fim = i + 1;
    // "/a//b" → "/a"; "/b" → "/"
    while i > 0 && eh_separador(bytes[i - 1]) {
        i -= 1;
    }
    if i > 0 {
        fim = i;
    }
    // SAFETY: o corte é numa divisa de caractere ASCII.
    PathBuf::from(unsafe { OsStr::from_encoded_bytes_unchecked(&bytes[..fim]) })
}

/// "Halo.iso" → "Halo".
pub fn sem_extensao(caminho: &Path) -> OsString {
    caminho
        .file_stem()
        .map(|s| s.to_os_string())
        .unwrap_or_default()
}

/// Junta pedaços de texto num `OsString`.
pub fn juntar_os(partes: &[&OsStr]) -> OsString {
    let mut saida = OsString::new();
    for p in partes {
        saida.push(p);
    }
    saida
}

/// O nome do arquivo termina com `fim` (sem diferenciar maiúsculas)?
pub fn termina_com(nome: &OsStr, fim: &str) -> bool {
    let bytes = nome.as_encoded_bytes();
    bytes.len() >= fim.len()
        && bytes[bytes.len() - fim.len()..].eq_ignore_ascii_case(fim.as_bytes())
}

// ── Permissões ──────────────────────────────────────────────────────────

#[cfg(unix)]
fn acesso(caminho: &Path, modo: libc::c_int) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(c) = std::ffi::CString::new(caminho.as_os_str().as_bytes()) else {
        return false;
    };
    unsafe { libc::access(c.as_ptr(), modo) == 0 }
}

/// Arquivo que dá para executar.
pub fn executavel(caminho: &Path) -> bool {
    if caminho.as_os_str().is_empty() || !caminho.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        acesso(caminho, libc::X_OK)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

pub fn pode_escrever(caminho: &Path) -> bool {
    #[cfg(unix)]
    {
        acesso(caminho, libc::W_OK)
    }
    #[cfg(not(unix))]
    {
        std::fs::metadata(caminho).is_ok_and(|m| m.is_dir() || !m.permissions().readonly())
    }
}

pub fn pode_ler(caminho: &Path) -> bool {
    #[cfg(unix)]
    {
        acesso(caminho, libc::R_OK)
    }
    #[cfg(not(unix))]
    {
        caminho.exists()
    }
}

/// As duas pastas são a mesma, por qualquer caminho (link, montagem, nome
/// curto do Windows)? Se não der para conferir, compara o texto.
pub fn mesma_pasta(a: &Path, b: &Path) -> bool {
    match same_file::is_same_file(a, b) {
        Ok(sim) => sim,
        Err(_) => {
            let normal = |p: &Path| {
                let texto = exibir_p(&absoluto(p));
                if cfg!(windows) {
                    texto.to_lowercase().replace('/', "\\")
                } else {
                    texto
                }
            };
            normal(a) == normal(b)
        }
    }
}

/// O mesmo arquivo (sem a alternativa pelo texto).
pub fn mesmo_arquivo(a: &Path, b: &Path) -> Option<bool> {
    same_file::is_same_file(a, b).ok()
}

// ── PATH ────────────────────────────────────────────────────────────────

/// Procura o programa no PATH (como o `shutil.which`).
pub fn no_path(nome: &str) -> Option<PathBuf> {
    if nome.contains('/') || (cfg!(windows) && nome.contains('\\')) {
        let p = PathBuf::from(nome);
        return executavel(&p).then_some(p);
    }
    let path = std::env::var_os("PATH")?;
    let mut pastas: Vec<PathBuf> = std::env::split_paths(&path).collect();
    if cfg!(windows)
        && let Ok(aqui) = std::env::current_dir()
    {
        pastas.insert(0, aqui);
    }
    let extensoes: Vec<String> = if cfg!(windows) {
        let pathext = std::env::var("PATHEXT").unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into());
        let lista: Vec<String> = pathext
            .split(';')
            .filter(|e| !e.is_empty())
            .map(|e| e.to_string())
            .collect();
        if lista
            .iter()
            .any(|e| nome.to_lowercase().ends_with(&e.to_lowercase()))
        {
            vec![String::new()]
        } else {
            lista
        }
    } else {
        vec![String::new()]
    };
    for pasta in pastas {
        for ext in &extensoes {
            let alvo = pasta.join(format!("{nome}{ext}"));
            if executavel(&alvo) {
                return Some(alvo);
            }
        }
    }
    None
}

// ── Percorrer pastas ────────────────────────────────────────────────────

/// Percorre a árvore como o `os.walk`: entra nas subpastas, mas não nas que
/// são links; `cada` recebe cada arquivo (o que não é pasta). Devolve false
/// para parar no meio.
pub fn percorrer(pasta: &Path, cada: &mut dyn FnMut(&Path) -> bool) -> bool {
    let Ok(itens) = std::fs::read_dir(pasta) else {
        return true;
    };
    let mut subpastas = Vec::new();
    for item in itens.flatten() {
        let caminho = item.path();
        // como o os.walk: um link para pasta conta como pasta, mas não é seguido
        let eh_pasta = std::fs::metadata(&caminho).is_ok_and(|m| m.is_dir());
        if eh_pasta {
            let eh_link = item.file_type().is_ok_and(|t| t.is_symlink());
            if !eh_link {
                subpastas.push(caminho);
            }
        } else if !cada(&caminho) {
            return false;
        }
    }
    for sub in subpastas {
        if !percorrer(&sub, cada) {
            return false;
        }
    }
    true
}

/// Soma do tamanho dos arquivos da pasta (links seguidos para o tamanho).
pub fn tamanho_pasta(pasta: &Path) -> u64 {
    let mut total = 0u64;
    percorrer(pasta, &mut |arquivo| {
        if let Ok(m) = std::fs::metadata(arquivo) {
            total += m.len();
        }
        true
    });
    total
}

/// Quantos arquivos há na pasta, contando as subpastas.
pub fn contar_arquivos(pasta: &Path) -> usize {
    let mut n = 0;
    percorrer(pasta, &mut |_| {
        n += 1;
        true
    });
    n
}

pub fn tamanho_arquivos(lista: &[PathBuf]) -> u64 {
    lista
        .iter()
        .filter_map(|a| std::fs::metadata(a).ok())
        .map(|m| m.len())
        .sum()
}

/// O erro do sistema como o Python o mostrava: "[Errno 13] Permission
/// denied: '/caminho'".
pub fn erro_os(e: &std::io::Error, caminho: Option<&Path>) -> String {
    let base = match e.raw_os_error() {
        Some(n) => {
            let texto = e.to_string();
            let msg = texto
                .rsplit_once(" (os error")
                .map(|(m, _)| m.to_string())
                .unwrap_or(texto);
            if cfg!(windows) {
                format!("[WinError {n}] {msg}")
            } else {
                format!("[Errno {n}] {msg}")
            }
        }
        None => e.to_string(),
    };
    match caminho {
        Some(p) => format!("{}: '{}'", base, exibir_p(p)),
        None => base,
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn bytes_que_nao_sao_utf8_viram_interrogacao() {
        assert_eq!(bytes_exibir(b"A\xe7\xe3o.iso"), "A??o.iso");
        assert_eq!(bytes_exibir("ação".as_bytes()), "ação");
    }

    #[test]
    fn nome_base_e_pasta() {
        assert_eq!(nome_base(OsStr::new("/a/b/c.iso")), "c.iso");
        assert_eq!(nome_base(OsStr::new("c.iso")), "c.iso");
        assert_eq!(pasta_de(Path::new("/a/b/c.iso")), PathBuf::from("/a/b"));
        assert_eq!(pasta_de(Path::new("/c.iso")), PathBuf::from("/"));
        assert_eq!(pasta_de(Path::new("c.iso")), PathBuf::new());
        assert_eq!(nome_base_str("a/b/arquivo.bin"), "arquivo.bin");
    }

    #[cfg(unix)]
    #[test]
    fn normalizar_como_o_python() {
        assert_eq!(normalizar(Path::new("/a/./b/../c")), PathBuf::from("/a/c"));
        assert_eq!(normalizar(Path::new("/..")), PathBuf::from("/"));
        assert_eq!(normalizar(Path::new("a/../../b")), PathBuf::from("../b"));
        assert_eq!(normalizar(Path::new("../..")), PathBuf::from("../.."));
    }
}
