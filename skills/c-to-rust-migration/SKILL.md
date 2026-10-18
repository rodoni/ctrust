---
name: c-to-rust-migration
description: "Implemente funções próprias no destino planejado."
---

# c-to-rust-migration

Implemente funções próprias no destino planejado. Use record-implementation com ID canônico e module; preserve referência imediatamente antes da declaração: // ctrust:source file=<C relativo> line=<linha> symbol=<nome> slice=<slice> contract=<contrato>. Não use placeholders nem retornos default para mascarar lógica ausente. Alterar código exige novo registro, testes e comparação.

## Protocolo único

- Leia `.ctrust/docs/workflow-v2.md` e consulte `.ctrust/sync.sh resume . --json`.
- O único estado canônico é `.ctrust/migration.yaml`, schema **3**. Versões anteriores não são suportadas nem convertidas.
- Inventário, arquitetura e progresso são seções separadas do mesmo snapshot. Não crie `function-map.yaml` ou manifestos paralelos como fontes de progresso.
- Cada função portada possui um proprietário; consumidores usam referências. Função verificada válida não deve ser implementada novamente.
- Use `.ctrust/sync.sh prepare . <slice>` para criar rascunhos; não confirme templates sem análise. Estruturas → comportamento → contrato → modelo → organização precedem implementação.
- Use os argumentos exatos exibidos por `--help` e `status`. A próxima tarefa pode exigir análise/execução de testes antes do comando de registro.
- Falhas identificam requisito e alcance. Corrija a causa e repita o registro; não altere YAML operacional, não use `next`, `failed`, `reopen` ou `upgrade-state`.
- Registre implementação, evidência NDJSON e comparação da função pelo ID canônico. Execute `verify-slice` para concluir o slice e `finish` para revisar e encerrar a migração.
- Antes de encerrar uma sessão, consulte `resume`, registre decisões nos artefatos e gere `checkpoint`. Status e checkpoint não substituem evidências.
