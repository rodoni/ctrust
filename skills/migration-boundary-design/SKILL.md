---
name: migration-boundary-design
description: Designs temporary C/Rust migration boundaries and optional FFI surfaces when coexistence is required.
---

# Migration Boundary Design

Use esta skill somente quando a conversão não puder ser concluída em uma única etapa e componentes C e Rust precisarem coexistir temporariamente. A conversão direta para Rust idiomático é o caminho padrão; FFI é apenas uma possível implementação da fronteira de migração.

## Primeiro avalie

- se a unidade pode ser convertida diretamente sem manter uma API C;
- se a fronteira temporária reduz risco ou apenas replica complexidade;
- qual componente será removido e em que momento;
- como será demonstrada a paridade e como funcionará o rollback.

## Se FFI for necessária

- definir ABI e representação com `extern "C"` e `#[repr(C)]` quando aplicável;
- documentar quem aloca, quem libera e por quanto tempo cada valor é válido;
- converter strings e buffers com tamanho explícito;
- validar ponteiros, comprimentos, alinhamento e valores enum;
- impedir panics através da fronteira;
- manter blocos `unsafe` mínimos e com justificativa `SAFETY`;
- cobrir sucesso, erro, null, tamanho zero e entradas inválidas.

A fronteira deve ter plano de remoção. Não transforme uma ponte temporária em dependência permanente sem uma decisão arquitetural explícita.
