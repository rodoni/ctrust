---
name: contract-extraction
description: Extracts and records observable C contracts before Rust implementation.
---

# Contract Extraction

Transforme o comportamento observável do C em contratos verificáveis antes de modelar a API Rust. Não invente invariantes: classifique cada afirmação como `confirmed`, `inferred` ou `open` e associe evidências.

## Pré-condição obrigatória

Antes de extrair o contrato, leia e valide `.ctrust/inventory/slices/<slice-id>/behavior.yaml`. O contrato não pode ser criado ou confirmado enquanto o comportamento C não estiver com `status: confirmed` e todos os campos de `completion` verdadeiros.


Leia o estado nesta ordem:

1. `.ctrust/migration.yaml`;
2. `.ctrust/current-context.md`;
3. inventário da unidade atual;
4. plano em `.ctrust/plans/units/<unit>.md`;
5. `source.project_root` e o componente declarado em `source.component`;
6. código C, headers e testes relacionados.

Nunca use o projeto Rust destino como fonte primária do comportamento que está sendo portado.
## Extraia por símbolo

Para cada função, tipo ou entry point relevante, registre:

- pré-condições;
- pós-condições;
- invariantes;
- ownership de entradas e saídas;
- nullabilidade e validade de buffers;
- códigos de erro e efeitos colaterais;
- comportamento de limites e entradas inválidas;
- concorrência, estado global e callbacks;
- evidências em formato `arquivo:linha` ou comando reproduzível;
- mapeamento de cada símbolo C que será portado para a futura função, método ou tipo Rust;
- ID de contrato que deverá aparecer nos comentários de origem das funções Rust.

## Saída

Salve um contrato compacto em `.ctrust/contracts/units/<unit>.yaml` e atualize `.ctrust/contracts/index.yaml`. O contrato não deve copiar grandes trechos de código.

Antes de avançar para modelagem, confirme que os riscos críticos e as perguntas abertas estão registrados. Um contrato não confirmado não deve ser tratado como requisito definitivo.
