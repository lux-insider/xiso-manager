//! A lógica do xiso-manager, usada pelo menu do terminal (`xiso-manager`)
//! e pela janela (`xiso-manager-gui`): onde estão as ferramentas, como
//! rodá-las, ler o progresso e cancelar, as configurações e o log.

pub mod saida;

pub mod acoes;
pub mod caminhos;
pub mod config;
pub mod disco;
pub mod entrada;
pub mod estilo;
pub mod eventos;
pub mod executar;
pub mod ferramentas;
pub mod iso;
pub mod log;
pub mod menu;
pub mod navegador;
pub mod selecao;
pub mod sinais;
pub mod terminal;
pub mod textos;
pub mod unicode_tabelas;
