---
name: transport-domain-decoupling
description: Defines DTO, domain and Zenoh boundaries without coupling migrated business logic to transport.
---

# Transport–Domain Decoupling

Use esta skill somente no ctrust para orientar a arquitetura do projeto Rust destino. Ela não cria nem edita crates do destino durante o planejamento.

## Boundary

- `transport`: Zenoh, serde/serde_json, DTOs versionados, codec, validação e adapters.
- `application`: composição, roteamento, switch legacy/replacement e observabilidade.
- `domain`: regras de negócio, tipos owned, snapshots, IDs e invariantes; não importa Zenoh.

A conversão deve ser explícita:

```text
C structure → Message_DTO → named mapper → Domain_Type/snapshot/ID/adapter
```

Não exponha ponteiros C, offsets de shared memory, endereços de processo ou DTOs nas APIs públicas do domínio.

## Required artifacts

Para cada slice, registre:

- `data-structures.yaml` confirmado antes de behavior/contract/model;
- `dto.yaml` com versão, subject, message ID e campos;
- `codec-validation.yaml` com decode, schema e invariantes;
- `zenoh-transport.yaml` com ports e rollback;
- mapeamento C→DTO→domínio, exclusões e diferenças;
- testes de boundary e comparação C/Rust.

`serde` no domínio é uma feature opcional; Zenoh permanece no adapter/transport. DTOs não devem atravessar a API pública do domínio.
