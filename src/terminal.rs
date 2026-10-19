//! O terminal: largura, e no Windows o console em UTF-8 com cores.

use std::io::IsTerminal;

/// A saída é um terminal (a barra de progresso só se desenha nele).
pub fn interativo() -> bool {
    std::io::stdout().is_terminal()
}

/// Colunas do terminal: COLUMNS, senão o tamanho da janela, senão 80.
pub fn colunas() -> usize {
    if let Some(n) = std::env::var("COLUMNS")
        .ok()
        .and_then(|v| v.trim().parse::<usize>().ok())
        .filter(|&n| n > 0)
    {
        return n;
    }
    largura_janela().filter(|&n| n > 0).unwrap_or(80)
}

#[cfg(unix)]
fn largura_janela() -> Option<usize> {
    let mut tam: libc::winsize = unsafe { std::mem::zeroed() };
    (unsafe { libc::ioctl(1, libc::TIOCGWINSZ, &mut tam) } == 0).then_some(tam.ws_col as usize)
}

#[cfg(windows)]
fn largura_janela() -> Option<usize> {
    use windows_sys::Win32::System::Console::{
        CONSOLE_SCREEN_BUFFER_INFO, GetConsoleScreenBufferInfo, GetStdHandle, STD_OUTPUT_HANDLE,
    };
    unsafe {
        let mut info: CONSOLE_SCREEN_BUFFER_INFO = std::mem::zeroed();
        let h = GetStdHandle(STD_OUTPUT_HANDLE);
        (GetConsoleScreenBufferInfo(h, &mut info) != 0)
            .then(|| (info.srWindow.Right - info.srWindow.Left + 1) as usize)
    }
}

/// Windows: UTF-8 no console e as sequências de cor ligadas. Sem o modo de
/// terminal virtual, as cores saem como lixo ("←[38;2;..m"). Devolve se o
/// console aceitou as cores.
#[cfg(windows)]
pub fn preparar_console() -> bool {
    use windows_sys::Win32::System::Console::{
        ENABLE_PROCESSED_OUTPUT, ENABLE_VIRTUAL_TERMINAL_PROCESSING, GetConsoleMode, GetStdHandle,
        STD_OUTPUT_HANDLE, SetConsoleCP, SetConsoleMode, SetConsoleOutputCP,
    };
    unsafe {
        SetConsoleOutputCP(65001);
        SetConsoleCP(65001);
        let h = GetStdHandle(STD_OUTPUT_HANDLE);
        let mut modo = 0u32;
        if GetConsoleMode(h, &mut modo) == 0 {
            return false;
        }
        SetConsoleMode(
            h,
            modo | ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING,
        ) != 0
    }
}

#[cfg(not(windows))]
pub fn preparar_console() -> bool {
    false
}

/// Windows: só este processo está no console, ou seja, a janela foi aberta
/// para ele (dois cliques) e fecha junto com ele.
#[cfg(windows)]
pub fn janela_so_minha() -> bool {
    use windows_sys::Win32::System::Console::GetConsoleProcessList;
    let mut lista = [0u32; 4];
    unsafe { GetConsoleProcessList(lista.as_mut_ptr(), lista.len() as u32) == 1 }
}

#[cfg(not(windows))]
pub fn janela_so_minha() -> bool {
    false
}
