---
name: architecture-gates
description: "Revise o plano confirmado, responsabilidades únicas, dependências, segurança da API e origem dos símbolos."
---

# architecture-gates

Revise o plano confirmado, responsabilidades únicas, dependências, segurança da API e origem dos símbolos. O controlador executa gates de preparação e função, scanners conservadores e Cargo fmt/check/test. Use gate para validação sem promoção; verify-slice publica a conclusão. Resultado de workspace deve informar comando, alcance e log.

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
