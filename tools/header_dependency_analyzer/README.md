# Header Dependency Analyzer (MVP)

Analisa declarações de funções de um `.h` e segue suas chamadas transitivas pelas implementações `.c`, **sem compilar**.

## Instalação

```bash
python3 -m pip install -r requirements.txt
```

Para executar os testes:

```bash
python3 -m unittest discover -s tests -v
```

## Uso

Sem banco de compilação, todos os arquivos `.c` e `.h` do projeto são indexados:

```bash
python3 analyze.py \
  --project examples \
  --header examples/include/radar.h \
  --output analysis
```

Com um `compile_commands.json`, somente as unidades `.c` existentes e pertencentes ao projeto que aparecem no banco são selecionadas. Os headers do projeto continuam sendo indexados para preservar declarações e definições inline:

```bash
python3 analyze.py \
  --project ~/workspace/lab/flight-kinematics/c_legacy_code \
  --header ~/workspace/lab/flight-kinematics/c_legacy_code/cssnew/src/mi/inc/mi_Cinematica.h \
  --compile-commands ~/workspace/lab/flight-kinematics/c_legacy_code/build/compile_commands.json \
  --output analysis
```

O banco pode usar o formato padrão com `arguments` ou `command`. As flags são normalizadas e registradas no carregamento, mas ainda não são usadas para executar o pré-processador; portanto, `-D`, `-I`, `-include` e `-std` não alteram a árvore sintática nesta versão.

Saídas: `functions.json`, `dependencies.json`, `hierarchy.txt`, `graph.graphml` e `hierarchy.html` (árvore HTML interativa autocontida, sem dependências externas). Com `--compile-commands`, `dependencies.json` também informa o número de entradas e unidades de tradução selecionadas.

## O que é analisado

- Declarações de funções no header informado são as raízes da análise.
- Chamadas diretas como `foo(...)` são resolvidas transitivamente.
- Funções `static` são resolvidas apenas na mesma unidade `.c`.
- Chamadas sem implementação são marcadas como `unresolved`.
- Múltiplas definições globais são marcadas como `ambiguous` e não são escolhidas arbitrariamente.
- Chamadas por ponteiro, membro ou outras expressões indiretas são registradas, mas não seguidas.

## Limitações importantes

- A análise é aproximada e não compila o projeto nem executa o pré-processador C.
- Macros, `#ifdef`, aliases, tipos e chamadas por ponteiros de função podem ficar sem resolução.
- O banco de compilação atualmente seleciona unidades de tradução e registra suas flags; ele não interpreta essas flags para expandir includes ou escolher ramos condicionais.
- Sem `--compile-commands`, todos os `.c` e `.h` abaixo de `--project` são indexados.
- Com `--compile-commands`, todos os headers do projeto são mantidos, mas apenas fontes `.c` listadas no banco e existentes no projeto são indexadas.
- Funções declaradas mas sem implementação são marcadas como não resolvidas.
- O resultado não determina se o código pode ser removido com segurança.

## Exportação determinística para o ctrust

Use `export_inventory.py` para transformar a análise em artefatos YAML de um slice:

```bash
python3 export_inventory.py \
  --project /path/to/c-project \
  --header /path/to/c-project/include/parser.h \
  --function parse_packet \
  --function validate_header \
  --slice parser-001 \
  --output /path/to/rust-project/.ctrust/inventory/slices/parser-001 \
  --mode compact
```

Use `--cache-dir` para reutilizar ASTs de arquivos que não mudaram:

```bash
python3 export_inventory.py \
  --project /path/to/c-project \
  --header /path/to/c-project/include/parser.h \
  --function parse_packet \
  --function validate_header \
  --slice parser-001 \
  --output analysis/parser-001 \
  --cache-dir .ctrust/cache/header_dependency_analyzer
```

A exportação produz artefatos compactos por padrão. Use `--mode full` somente para investigação detalhada; o modo completo pode conter dados brutos e listas extensas e não deve ser usado na recuperação normal do agente.


## Melhorias estáticas

- No modo compacto, somente estruturas referenciadas pelas funções alcançáveis do slice e suas dependências de campos são exportadas.
- Estruturas não alcançáveis ficam disponíveis apenas com `--mode full` para diagnóstico.
- Funções registram fluxo de controle, acessos a campos, leituras aparentes de globais e chamadas indiretas.
- Resoluções possuem confiança `confirmed`, `inferred`, `ambiguous` ou `unresolved`.
- Ciclos de chamada são reportados como componentes fortemente conectados.
- Chamadas indiretas como `object.process()` recebem candidatos conservadores por nome, sem serem tratadas como resolução definitiva.
- O cache é invalidado pelo hash do arquivo-fonte.

Esses resultados são aproximações estáticas. Macros, `#ifdef`, aliases, tipos condicionais e callbacks exigem revisão do agente e podem continuar bloqueando o slice.
