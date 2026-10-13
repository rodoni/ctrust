---
name: c-behavior-extraction
description: Extracts the observable behavior of each C migration slice before contract confirmation.
---

# C Behavior Extraction

Esta skill é obrigatória depois de `c-code-inventory` e antes de `contract-extraction`. O inventário localiza funções e dependências; esta skill registra o que a implementação C realmente faz em cada caminho observável.

## Entrada

Leia, nesta ordem:

1. `.ctrust/migration.yaml`;
2. `.ctrust/current-context.md`;
3. `.ctrust/status/slices.md`;
4. `.ctrust/inventory/slices/<slice-id>/manifest.yaml`;
5. `function-map.yaml`, `callers.yaml`, `dependencies.yaml` e `call-graph.yaml`;
6. os headers, a definição C, callers/callees e testes de referência.

Nunca use a interface Rust como fonte primária para inferir comportamento.

## Extração obrigatória

Para cada função C do slice, registre em `.ctrust/inventory/slices/<slice-id>/behavior.yaml`:

- arquivo e intervalo de linhas da implementação;
- entradas, validade, nullabilidade e limites;
- saída de sucesso;
- códigos/condições de erro;
- fluxo de controle e branches relevantes;
- chamadas diretas, callbacks e ponteiros de função;
- estado global lido ou alterado;
- efeitos colaterais;
- alocação, liberação e ownership;
- casos normais, limites, erros e recursos inválidos;
- evidências `arquivo:linha`, teste ou comando reproduzível;
- diferenças permitidas, sempre justificadas.

Não copie grandes trechos do C. Registre fatos e referências precisas.

## Gate de conclusão

O arquivo só pode ter `status: confirmed` quando todos estes campos forem verdadeiros:

```yaml
completion:
  source_logic_read: true
  branches_mapped: true
  calls_mapped: true
  errors_mapped: true
  side_effects_mapped: true
  test_cases_defined: true
```

`open_questions` não pode conter dúvidas críticas sem decisão. Funções sem definição, branches não mapeados, chamadas indiretas não classificadas ou efeitos colaterais desconhecidos bloqueiam o slice.

## Saída para a próxima fase

Após confirmar `behavior.yaml`, atualize a próxima ação e encaminhe o mesmo slice para `contract-extraction`. O contrato deve derivar deste artefato, mas não o substituir. Não crie a implementação Rust nesta fase.
