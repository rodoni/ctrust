---
name: c-code-inventory
description: Inventories legacy C code before a migration to Rust.
---

# C Code Inventory

Use esta skill antes de qualquer tradução. O resultado deve ser um inventário reproduzível, não uma estimativa baseada em nomes de arquivos.

## Entrada

Antes de inventariar, leia o manifesto criado pelo `ctrust-lead` em `.ctrust/plans/units/<unit>-slices.yaml`. O manifesto é a fonte de verdade do escopo; não invente slices adicionais durante o inventário.

Para cada slice, comece pelos headers declarados em `source.headers` e pelas funções declaradas em `source.functions`.

1. Confirme que cada header de entrada existe em `source.project_root`.
2. Leia os `#include` de cada header e resolva os includes recursivamente usando os diretórios de include configurados e a árvore do projeto.
3. Registre todos os headers encontrados, inclusive os compartilhados por outros slices.
4. Se um include não puder ser localizado, registre-o em `unresolved-includes.yaml` e informe o usuário/`ctrust-lead` com o caminho esperado e os diretórios pesquisados.
5. Não avance o slice para `contract-extraction` enquanto uma dependência obrigatória estiver ausente, salvo decisão explícita de simulação, adaptação ou exclusão.
6. Execute o analisador determinístico para cada slice, usando todos os headers de entrada declarados:

   Prefira `.ctrust/sync.sh inventory . "$SLICE_ID"`: o controlador deriva os argumentos do manifesto, valida a elegibilidade e publica transacionalmente o inventário. O comando registra falhas de execução; não contabilize a mesma falha novamente com `failed`. O comando direto abaixo serve para investigação explícita. A exportação usa staging, valida o conjunto completo e arquiva a versão anterior na troca; não substitua análises revisadas sem verificar a necessidade e preservar as decisões.

   ```bash
   python3 .ctrust/tools/header_dependency_analyzer/export_inventory.py \
     --project "$SOURCE_PROJECT_ROOT" \
     --header "$SOURCE_PROJECT_ROOT/include/input.h" \
     --header "$SOURCE_PROJECT_ROOT/include/related.h" \
     --function "$C_FUNCTION_1" \
     --function "$C_FUNCTION_2" \
     --slice "$SLICE_ID" \
     --output ".ctrust/inventory/slices/$SLICE_ID" \
     --cache-dir ".ctrust/cache/header_dependency_analyzer" \
     --mode compact
   ```

   Passe um `--function` para cada item de `source.functions`; essa seleção define as raízes do slice. Sem `--function`, o analyzer usa todas as declarações do header como raízes, o que pode incluir funções fora do escopo. Se houver `compile_commands.json`, passe `--compile-commands`; flags são registradas como metadata e a análise ainda não executa o pré-processador. Repita `--header` para todos os headers de entrada do slice. O cache é opcional e pode ser reutilizado entre sessões.
7. Execute `.ctrust/scripts/inventory-gate.sh . "$SLICE_ID"` depois da exportação. O gate exige os nove artefatos determinísticos, incluindo `behavior.yaml`, e informa arquivos ou campos faltantes/inválidos. Se falhar, complete o inventário antes de avançar; preserve análises enriquecidas ou confirmadas antes de reexportar. Depois localize cada função C alvo, confirme seu símbolo e linha e valide o nome Rust correspondente.
8. Use o `header_dependency_analyzer` como fonte normativa dos callers/callees diretos e das dependências transitivas.
9. Execute `call-graph-cscope.sh` apenas quando houver necessidade explícita de investigar chamadas indiretas, callbacks, ponteiros de função, macros ou callers reversos. O resultado é complementar e não substitui os YAMLs do analyzer.
10. Para cada slice, relacione chamadas com evidência `arquivo:linha`, classifique a aresta como `direct`, `callback`, `function_pointer`, `macro` ou `unresolved` e registre chamadas indiretas como risco.
11. Mapeie as estruturas de dados separadamente das funções e grave `data-structures.yaml`. Para cada `struct`, `union`, `enum`, `typedef`, array e handle, registre campos, layout, ponteiros, ownership, lifetime, aliasing e uso por funções.
12. Para ponteiros de shared memory ou regiões mmap, registre offsets, processo compartilhado, sincronização, ABI e decisão `adapt`, `simulate` ou `exclude`; não trate a estrutura como uma `struct` Rust comum sem uma estratégia explícita.

## Inventário por slice

Depois de receber o manifesto de slices do `migration-scoping`, inventarie cada slice separadamente. Não gere apenas um inventário global do projeto C.

Para cada slice, registre:

- `function-map.yaml` e `dependencies.yaml` gerados pelo `header_dependency_analyzer` são a fonte determinística para chamadas diretas e dependências transitivas;
- `behavior.yaml` é um rascunho factual e deve ser confirmado por `c-behavior-extraction`;
- `data-structures.yaml` é um rascunho estrutural compacto, contendo as estruturas alcançáveis pelo slice e suas dependências de campos; seus campos devem ser completados com o mapping C→DTO→Domain;
- `mapping-confirmation.yaml` registra contagens e bloqueia o avanço enquanto houver `unresolved`;
- `cscope` é opcional e só complementa investigações indiretas, ponteiros de função, callbacks, macros e callers reversos;
- informações manuais do agente só podem complementar ou justificar `unresolved`/`ambiguous`, nunca substituir silenciosamente uma definição resolvida pelo analyzer;
- evidência do grafo global em `.ctrust/inventory/call-graph.yaml`;
- chamadas diretas, callbacks, ponteiros de função, macros e chamadas não resolvidas;
- arquivos compartilhados usados por mais de um slice;
- infraestrutura classificada como portar, adaptar, simular ou excluir;
- símbolos fora do escopo;
- includes não resolvidos ou dependências bloqueadoras;
- testes e evidências relacionados.

Salve os resultados em `.ctrust/inventory/slices/<slice-id>/`:

```text
manifest.yaml
files.yaml
symbols.yaml
data-structures.yaml
mapping-confirmation.yaml
function-map.yaml
callers.yaml
dependencies.yaml
includes.yaml
risks.yaml
unresolved-includes.yaml
```

O inventário deve validar e enriquecer cada slice com fatos observados no C. Se encontrar uma dependência que exija dividir, unir ou reordenar slices, registre a proposta e devolva-a ao `ctrust-lead`; não altere silenciosamente o manifesto.

A ordem efetiva deve respeitar as dependências bloqueantes entre slices. `order` é apenas desempate; dependências explícitas têm precedência. Um ciclo de chamadas C não deve ser convertido em uma ordem falsa: registre o ciclo e proponha um slice único, uma interface/adaptador ou uma decisão explícita de fronteira.

O inventário deve declarar se o slice está pronto para iniciar `contract-extraction`.
