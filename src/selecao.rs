//! Os números digitados na seleção: "1,3,5-7", com algarismos de qualquer
//! escrita ("٣" vale 3).

use crate::unicode_tabelas::DECIMAIS;

/// O valor de um algarismo decimal (de qualquer escrita).
pub fn valor_decimal(ch: char) -> Option<u32> {
    let cp = ch as u32;
    DECIMAIS
        .iter()
        .find(|&&(a, b)| a <= cp && cp <= b)
        .map(|&(a, _)| (cp - a) % 10)
}

/// O número digitado, ou None. Só algarismos decimais ("²" não é um), e um
/// número grande demais também não serve.
pub fn inteiro(texto: &str) -> Option<u128> {
    let texto = texto.trim();
    if texto.is_empty() {
        return None;
    }
    let mut n: u128 = 0;
    for ch in texto.chars() {
        let d = valor_decimal(ch)?;
        n = n.checked_mul(10)?.checked_add(d as u128)?;
    }
    Some(n)
}

/// "1,3,5-7" → os índices (a partir de 0, sem repetir) e o que foi ignorado.
pub fn expandir(texto: &str, total: usize) -> (Vec<usize>, Vec<String>) {
    let total_n = total as u128;
    let mut indices: Vec<usize> = Vec::new();
    let mut invalidos: Vec<String> = Vec::new();
    for parte in texto.split(',') {
        let parte = parte.trim();
        if parte.is_empty() {
            continue;
        }
        if parte.contains('-') && !parte.starts_with('-') {
            let (a_txt, b_txt) = parte.split_once('-').unwrap();
            if let (Some(mut a), Some(mut b)) = (inteiro(a_txt), inteiro(b_txt)) {
                if a > b {
                    std::mem::swap(&mut a, &mut b);
                }
                // Só a parte que existe é percorrida: "1-999999999" não pode
                // virar um bilhão de itens. Acima do total, até 1000 números
                // saem um a um; mais que isso, num item só.
                if a == 0 {
                    invalidos.push("0".into());
                }
                let de = a.max(1) - 1;
                let ate = b.min(total_n);
                let mut i = de;
                while i < ate {
                    indices.push(i as usize);
                    i += 1;
                }
                let acima = a.max(total_n + 1);
                if b >= acima && b - acima >= 1000 {
                    invalidos.push(format!("{acima}-{b}"));
                } else {
                    let mut n = acima;
                    while n <= b {
                        invalidos.push(n.to_string());
                        n += 1;
                    }
                }
                continue;
            }
        }
        match inteiro(parte) {
            Some(n) if n >= 1 && n <= total_n => indices.push((n - 1) as usize),
            _ => invalidos.push(parte.to_string()),
        }
    }
    let mut vistos = std::collections::HashSet::new();
    indices.retain(|i| vistos.insert(*i));
    (indices, invalidos)
}

#[cfg(test)]
mod testes {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn intervalo_grande_e_rapido() {
        let inicio = std::time::Instant::now();
        assert_eq!(
            expandir("0-3000000, 1", 3),
            (vec![0, 1, 2], s(&["0", "4-3000000"]))
        );
        assert!(inicio.elapsed().as_secs_f64() < 0.5);
        assert_eq!(
            expandir("999999999-2", 3),
            (vec![1, 2], s(&["4-999999999"]))
        );
    }

    #[test]
    fn intervalo_pequeno() {
        assert_eq!(
            expandir("0-5,3-1,9,x", 3),
            (vec![0, 1, 2], s(&["0", "4", "5", "9", "x"]))
        );
        let (_, invalidos) = expandir("1-1000", 3);
        assert_eq!(
            invalidos,
            (4..=1000).map(|n| n.to_string()).collect::<Vec<_>>()
        );
    }

    #[test]
    fn digitos_que_nao_sao_de_zero_a_nove() {
        let enorme = "1".repeat(5000);
        let texto = format!("², 1-², {enorme}, 2-{enorme}, 1");
        assert_eq!(
            expandir(&texto, 3),
            (
                vec![0],
                vec![
                    "²".into(),
                    "1-²".into(),
                    enorme.clone(),
                    format!("2-{enorme}")
                ]
            )
        );
        assert_eq!(expandir("٣", 3), (vec![2], vec![]));
    }
}
