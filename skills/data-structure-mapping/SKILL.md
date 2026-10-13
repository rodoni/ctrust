---
name: data-structure-mapping
description: Maps C data structures, pointer semantics and shared-memory layouts before Rust modeling.
---

# C Data Structure Mapping

Use esta skill durante `c-code-inventory`, antes de extrair comportamento e contratos. Funções e estruturas são inventariadas separadamente: uma função não pode ser portada enquanto os tipos que representam suas entradas, saídas e estado não estiverem compreendidos.

## Entrada

Leia o manifesto do slice, os headers transitivos, as definições C, `function-map.yaml`, `call-graph.yaml` e os usos das estruturas pelas funções. Inclua `struct`, `union`, `enum`, `typedef`, arrays, handles opacos e regiões de memória compartilhada.

## Saída

Grave:

```text
.ctrust/inventory/slices/<slice-id>/data-structures.yaml
```

Para cada estrutura, registre:

- nome C, typedef, kind e localização da declaração;
- todos os campos, tipos, ponteiros, arrays e nullabilidade;
- funções que leem ou escrevem cada estrutura;
- tamanho, alinhamento, packing, ordem de campos e requisitos `repr(C)`;
- aliasing, lifetime, ownership e transferência de ownership;
- se o ponteiro aponta para heap, stack, região global, mmap ou shared memory;
- sincronização, offsets, handles, processo compartilhado e ABI;
- classificação da infraestrutura: portar, adaptar, simular ou excluir;
- estratégia Rust: struct/enum/newtype/handle, ownership, lifetime, módulo e limite `unsafe`;
- evidências `arquivo:linha` e perguntas abertas.

Shared memory, ponteiros crus e layouts binários não devem ser convertidos diretamente para uma `struct` Rust sem registrar a representação, sincronização e estratégia de acesso. Quando a região não for lógica de negócio, prefira um adapter/handle ou simulação em testes.

## Gate de conclusão

O arquivo só pode ter `status: confirmed` quando estes campos forem verdadeiros:

```yaml
completion:
  structures_identified: true
  fields_mapped: true
  layout_mapped: true
  pointer_semantics_mapped: true
  shared_memory_classified: true
  rust_strategy_defined: true
  evidence_recorded: true
```

Se não houver estruturas relevantes, use `no_structures: true`, mantenha `structures: []` e confirme os mesmos campos com evidência da busca realizada. Não use ausência de uma lista como prova de que não há estruturas.
