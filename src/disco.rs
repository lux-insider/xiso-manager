//! Espaço em disco e os formatos de tamanho e tempo.

use std::path::{Path, PathBuf};

use crate::entrada::perguntar_sim_nao;
use crate::estilo::{self, AMARE, CINZA, NEG, VERDE, VERM, campo14, cor, emo};
use crate::textos::t;
use crate::{caminhos, say};

/// 15% de folga na conferência do espaço.
const MARGEM_ESPACO: f64 = 1.15;

pub fn fmt_bytes(b: f64) -> String {
    if b == 0.0 {
        return "N/D".into();
    }
    let gb = b / 1024f64.powi(3);
    if gb >= 1.0 {
        format!("{gb:.2} GiB")
    } else {
        format!("{:.2} MiB", b / 1024f64.powi(2))
    }
}

pub fn fmt_u(b: u64) -> String {
    fmt_bytes(b as f64)
}

pub fn fmt_tempo(segundos: f64) -> String {
    let s = if segundos.is_finite() {
        segundos.trunc() as i64
    } else {
        0
    };
    let (m, s) = (s.div_euclid(60), s.rem_euclid(60));
    let (h, m) = (m.div_euclid(60), m.rem_euclid(60));
    if h != 0 {
        format!("{h:02}:{m:02}:{s:02}")
    } else {
        format!("{m:02}:{s:02}")
    }
}

pub fn fmt_velocidade(bps: f64) -> String {
    if bps == 0.0 {
        return "N/D".into();
    }
    format!("{}/s", fmt_bytes(bps))
}

pub struct Uso {
    pub total: u64,
    pub livre: u64,
}

#[cfg(unix)]
pub fn uso(pasta: &Path) -> std::io::Result<Uso> {
    use std::os::unix::ffi::OsStrExt;
    let c = std::ffi::CString::new(pasta.as_os_str().as_bytes())
        .map_err(|_| std::io::Error::from(std::io::ErrorKind::InvalidInput))?;
    let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(c.as_ptr(), &mut st) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(Uso {
        total: st.f_blocks as u64 * st.f_frsize as u64,
        livre: st.f_bavail as u64 * st.f_frsize as u64,
    })
}

#[cfg(windows)]
pub fn uso(pasta: &Path) -> std::io::Result<Uso> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
    let largo: Vec<u16> = pasta.as_os_str().encode_wide().chain(Some(0)).collect();
    let (mut livre_para_mim, mut total, mut livre) = (0u64, 0u64, 0u64);
    if unsafe { GetDiskFreeSpaceExW(largo.as_ptr(), &mut livre_para_mim, &mut total, &mut livre) }
        == 0
    {
        return Err(std::io::Error::last_os_error());
    }
    Ok(Uso { total, livre })
}

/// A pasta que existe mais perto do caminho (subindo até achar uma).
pub fn pasta_existente(caminho: &Path) -> PathBuf {
    let caminho = if caminho.as_os_str().is_empty() {
        Path::new(".")
    } else {
        caminho
    };
    let mut alvo = caminhos::absoluto(caminho);
    while !alvo.is_dir() {
        let pai = caminhos::pasta_de(&alvo);
        if pai == alvo || pai.as_os_str().is_empty() {
            return caminhos::absoluto(Path::new("."));
        }
        alvo = pai;
    }
    alvo
}

/// Painel de espaço em disco. Devolve true se pode seguir.
pub fn verificar_espaco(destino: &Path, necessario_bruto: u64) -> bool {
    let alvo = pasta_existente(destino);
    let uso = match uso(&alvo) {
        Ok(u) => u,
        Err(e) => {
            estilo::aviso(&format!(
                "{} {}",
                t("disco_erro"),
                caminhos::erro_os(&e, Some(&alvo))
            ));
            return perguntar_sim_nao(t("disco_seguir"), false, None);
        }
    };
    let necessario = (necessario_bruto as f64 * MARGEM_ESPACO) as u64;
    let pct = if uso.total > 0 {
        (uso.total - uso.livre.min(uso.total)) as f64 / uso.total as f64
    } else {
        0.0
    };
    let largura_barra = 24usize;
    let cheio = ((largura_barra as f64 * pct) as usize).min(largura_barra);
    let cor_uso = if pct < 0.75 {
        VERDE
    } else if pct < 0.90 {
        AMARE
    } else {
        VERM
    };
    let barra = format!(
        "{}{}",
        cor(&"\u{2588}".repeat(cheio), &[cor_uso]),
        cor(&"\u{2591}".repeat(largura_barra - cheio), &[CINZA])
    );
    say!(
        "{}",
        campo14(
            t("disco_destino"),
            &estilo::cortar(&caminhos::encurtar_p(&alvo), 44),
            emo::PASTA
        )
    );
    say!(
        "{}",
        campo14(
            t("disco_necessario"),
            &format!(
                "{}  {}",
                fmt_u(necessario_bruto),
                cor(
                    &format!("({}: {})", t("margem"), fmt_u(necessario)),
                    &[CINZA]
                )
            ),
            emo::EXTRAIR
        )
    );
    say!(
        "{}",
        campo14(
            t("disco_disponivel"),
            &format!(
                "{}{}",
                cor(&fmt_u(uso.livre), &[VERDE, NEG]),
                cor(
                    &format!("   {}: {}", t("disco_total"), fmt_u(uso.total)),
                    &[CINZA]
                )
            ),
            emo::DISCO
        )
    );
    say!(
        "{}",
        campo14(
            t("disco_uso"),
            &format!("{} {:.1}%", barra, pct * 100.0),
            emo::RESUMO
        )
    );
    if uso.livre < necessario {
        estilo::erro(&format!(
            "{} {}. ",
            t("disco_falta"),
            fmt_u(necessario - uso.livre)
        ));
        estilo::dica(t("disco_dica"));
        return false;
    }
    say!(
        "  {} {}",
        cor(&estilo::marca_larga(estilo::SIM_OK), &[VERDE]),
        cor(t("disco_ok"), &[VERDE])
    );
    true
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn formatos() {
        assert_eq!(fmt_u(0), "N/D");
        assert_eq!(fmt_u(52000), "0.05 MiB");
        assert_eq!(fmt_u(7 * 1024u64.pow(3)), "7.00 GiB");
        assert_eq!(fmt_tempo(5.9), "00:05");
        assert_eq!(fmt_tempo(3725.0), "01:02:05");
    }
}
