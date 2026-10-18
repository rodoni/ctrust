---
name: rust-module-organization
description: Defines modular Rust crate layouts and prevents monolithic lib.rs implementations during C-to-Rust ports.
---

# Rust Module Organization

Organize o port por responsabilidades de domínio, não por acúmulo de funções em `lib.rs`. Cada slice deve ter um módulo Rust claro, um arquivo de implementação adequado e uma API pública mínima.

## Estrutura padrão obrigatória

Para uma biblioteca:

```text
<crate>/
├── Cargo.toml
├── src/
│   ├── lib.rs              # composição e reexports
│   ├── type.rs             # tipos compartilhados
│   └── <slice_sem_id>.rs   # implementação + testes unitários #[cfg(test)]
└── tests/
    ├── <integration_name>.rs # testes de integração da API pública
    └── common/mod.rs         # helpers compartilhados, quando necessário
```

Para um binário, substitua `lib.rs` por `main.rs`. O arquivo da slice deve ser derivado da descrição semântica sem o prefixo/ID numérico, por exemplo:

```text
kin-001-velocity-kinematics
        ↓
src/velocity_kinematics.rs
```

Use `snake_case` para o nome físico do arquivo porque hífen não é um identificador válido de módulo Rust. O nome não deve conter `kin-001`, `slice_001` ou outro identificador ordinal.

Testes unitários ficam dentro de `src/<slice_sem_id>.rs`:

```rust
#[cfg(test)]
mod tests {
    // pode acessar itens privados da slice
}
```

Testes em `tests/` são somente de integração: cada arquivo é um crate de teste separado e acessa apenas a API pública.

## Regras obrigatórias

- `lib.rs` deve conter somente declarações de módulos, documentação do crate e reexports intencionais;
- `main.rs` deve conter somente bootstrap/composição, não regras de negócio extensas;
- cada slice deve ter um `rust_file` próprio e sem o prefixo/ID numérico da slice;
- cada `rust_file` deve conter a implementação da slice e seus testes unitários em `#[cfg(test)]`;
- testes de integração devem ficar em `tests/<nome>_integration.rs` e acessar somente a API pública;
- helpers compartilhados de integração devem ficar em `tests/common/mod.rs`;
- `type.rs` é o arquivo obrigatório de tipos compartilhados; não use `types.rs` como convenção alternativa nesta organização;
- extraia erros para `error.rs` ou para o módulo que owns o contrato;
- evite arquivos com centenas de linhas ou múltiplas responsabilidades sem justificativa;
- não crie um módulo por função trivial; agrupe por coesão;
- mantenha dependências entre módulos direcionadas e sem ciclos;
- preserve o comentário `// ctrust:source` em cada função portada, no arquivo do módulo correspondente.

## Gate de organização

Antes de verificar um slice, confirme:

- o arquivo Rust do slice está definido;
- o módulo está declarado pelo pai correto;
- `lib.rs` ou `main.rs` não contém a implementação principal do slice;
- tipos, erros e adaptadores estão em módulos apropriados;
- `module_path` e `rust_file` estão registrados no modelo e no function-map;
- o crate compila a partir do Cargo workspace raiz.

Quando a organização atual não for adequada, refatore primeiro em passos pequenos e registre a decisão no checkpoint.

## Símbolos compartilhados entre slices

A organização é física por slice, mas a identidade de uma função C é global dentro do crate. Consulte `.ctrust/plans/units/<unit>-symbol-map.yaml`: `function_id` determina o `canonical_rust_name` e o `rust_path`. Uma função alcançada por um slice dependente deve usar o caminho do `owner_slice`, com reexport intencional quando necessário; não derive um novo nome a partir da descrição do consumidor. Conflitos de nomes canônicos devem bloquear o plano antes da implementação.

## Atribuição de testes

`test_file` no modelo registra o caminho físico dos testes, mas não substitui a atribuição de casos. O organizador aceita `characterization_tests` no manifesto; quando ausente, agrega os `test_cases` definidos em cada função de `behavior.yaml`. Assim, comportamento confirmado por função também satisfaz a atribuição mínima da slice.
