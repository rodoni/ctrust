---
name: rust-domain-modeling
description: Maps confirmed C contracts to explicit, idiomatic Rust types and APIs.
---

# Rust Domain Modeling

Modele o domínio Rust a partir de contratos confirmados, não a partir de uma tradução textual de `struct`, `typedef` e ponteiros C.

## Entrada

- contrato confirmado da unidade em `.ctrust/contracts/units/<unit>.yaml`;
- projeto C em `source.project_root`;
- componente/fração C em `source.component`;
- destino Rust em `target.project_root/target.path`;
- workspace Cargo na raiz `target.project_root`, com `Cargo.toml` um nível acima do crate;
- crate do slice registrado em `workspace.members`;
- `rust_file` e `module_path` definidos para cada símbolo Rust portado;
- `lib.rs`/`main.rs` limitado à composição e reexports;
- tipo de produto definido em `output.kind`: `library` ou `binary`.

## Decisões obrigatórias

Para cada elemento C, registre a decisão Rust:

- `struct`: struct pública, tipo privado, newtype ou composição;
- `union`: enum discriminada ou representação controlada;
- `typedef`: alias simples ou tipo de domínio;
- ponteiro: referência, slice, `Box`, `Option`, handle ou índice;
- `char *`: `&str`, `String`, bytes, `CStr` ou `CString`;
- `NULL`: `Option`, `Result` ou estado explícito;
- código de erro: `Result<T, E>` ou enum de domínio;
- `malloc/free`: owner e ciclo de vida Rust;
- callback: trait, closure ou função tipada;
- macro: função, `const`, generic ou macro Rust;
- flags: newtype ou enum com operações explícitas.

## Decisão do produto

Registre explicitamente uma das opções:

- `library`: defina a API pública, tipo de biblioteca (`rlib`, `cdylib` ou `staticlib`), consumidores e crate membro do workspace;
- `binary`: defina o nome do executável, `main`/entry point, argumentos, configuração e crate binário membro do workspace.

Não produza uma biblioteca apenas porque o C original era uma biblioteca, nem um binário apenas porque existia um `main`. A decisão deve seguir o contrato da unidade e o objetivo do port.

## Saída

Salve `.ctrust/plans/units/<unit>-model.yaml` ou o modelo indicado pelo plano. Registre tipos, API ou entry point, ownership, lifetimes, erros, invariantes, diferenças intencionais, tipo de produto e critérios de validação. O template começa com `status: draft`; altere para `status: confirmed` somente depois de revisar tipos, ownership, API, produto e critérios de validação. Atualize `current-context.md` e crie um checkpoint ao concluir.

A modelagem deve reduzir a superfície de `unsafe`; FFI só entra na decisão quando a unidade precisar coexistir temporariamente com C.
