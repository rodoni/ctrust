---
name: architecture-gates
description: Validates ctrust architecture policies, artifact order and safe domain APIs.
---

# Architecture Gates

Esta skill define gates do ctrust sem modificar crates do projeto destino.

## Required order

```text
data-structures confirmed
→ behavior confirmed
→ contract/model
→ organize/port
→ completeness/comparison
```

## Checks

- domínio não possui dependência obrigatória de Zenoh, serde ou serde_json proibidos pela policy;
- APIs domain-facing não expõem ponteiros crus, offsets, endereços ou DTOs;
- `data-structures.yaml` tem mapping C→DTO→Domain ou exclusão justificada;
- `unresolved: 0` antes da confirmação;
- mensagens possuem versão, subject, message ID e validação;
- rollback e baseline estão registrados.

Cada falha deve indicar arquivo, símbolo/campo, regra e correção sugerida.
