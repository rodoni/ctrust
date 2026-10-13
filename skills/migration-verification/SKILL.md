---
name: migration-verification
description: Verifies behavioral equivalence and safety gates during C-to-Rust migrations.
---

# Migration Verification

A migração só avança quando há evidência suficiente de que a fatia Rust preserva o contrato relevante e não amplia riscos. Uma API que compila com retornos default não é uma implementação: deve permanecer `in_progress` ou `blocked`.

## Gates

1. Formate e compile o código Rust.
2. Execute testes unitários e de integração.
4. Compare a saída Rust com a implementação C de referência quando ela ainda existir.
5. Confirme que `.ctrust/inventory/slices/<slice-id>/behavior.yaml` está confirmado e cobre o comportamento implementado.
6. Exercite entradas normais, limites, erros e recursos inválidos.
5. Audite `unsafe` e, somente quando aplicável, a fronteira de migração/FFI.
6. Registre diferenças intencionais e seus impactos.
7. Preserve rollback enquanto a paridade não estiver demonstrada.
8. A implementação Rust usa a lógica observada no C; não aceite uma interface compilável com retorno padrão como conclusão de port.
9. Rejeite `todo!`, `unimplemented!`, tokens de template, retornos default e corpos constantes sem justificativa.
10. Confirme comentários `ctrust:source`, testes de caracterização e comparação com a referência antes de marcar `verified`.
11. Registre qualquer diferença intencional no contrato, modelo e checkpoint; uma diferença não documentada bloqueia a verificação.

Use testes determinísticos, fixtures versionadas e logs comparáveis. Não trate ausência de crash como prova de equivalência.

## Fechamento do slice

Use `.ctrust/sync.sh verify-slice . <slice-id>` depois de registrar implementação, evidências e comparação de todas as funções. O comando valida os artefatos confirmados, os testes/evidências, o plano salvo, a origem C e o build Cargo antes de marcar o slice como `verified`, criar checkpoint e selecionar o próximo slice. Os gates usam o slice ativo por padrão; use um slice ID explícito para revisão específica e `--all` somente para revisão final. `compare passed` verifica uma função, sem promover automaticamente o slice.
