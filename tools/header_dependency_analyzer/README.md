# Header Dependency Analyzer

Análise estática aproximada de C por headers e raízes, usando Tree-sitter e NetworkX. Descobre dependências transitivas sem compilar o legado. O catálogo global antecede a criação dos slices.

```bash
python3 -m pip install -r requirements.txt
python3 analyze.py --project /legado --header /legado/include/api.h --output analysis --architecture
python3 export_inventory.py --project /legado --header include/api.h --output inventory.yaml
```

O workflow recomendado é `.ctrust/sync.sh inventory . --source /legado`. `--source` aponta para a raiz original do C; omitindo `--header`, o controlador descobre recursivamente todos os headers `.h` sob essa raiz. Use `--header` repetível para limitar os headers-raiz e `--function` opcional para limitar funções mantendo dependências. Includes locais entre aspas também podem ser resolvidos recursivamente pelo basename quando existe um único candidato; nomes duplicados permanecem explícitos como não resolvidos. O exportador independente publica YAML schema 3 sem progresso ou atribuições de slices.

O analisador produz `functions.json`, `dependencies.json`, `hierarchy.txt`, `graph.graphml` e `hierarchy.html`. Use `--architecture` no analisador para acrescentar `architecture.html` à saída. No workflow, o mesmo editor é gerado por `.ctrust/sync.sh report .` após o inventário; seleção e agrupamento são confirmados pelo controlador.

## Resolução e limites

- Chamadas diretas seguem definições transitivamente. Definições múltiplas ficam ambíguas; implementações ausentes ficam não resolvidas.
- Funções `static` são locais à unidade de tradução. Funções estáticas de headers são instanciadas por unidade consumidora.
- Includes locais, `-I`, `-iquote`, `-isystem` e `-include` são seguidos dentro dos arquivos indexados. Includes entre aspas sem caminho correspondente usam busca recursiva por basename apenas quando o resultado é único. Includes ausentes ou ambíguos e configurações de pré-processamento exigem revisão.
- `compile_commands.json` aceita `arguments` ou `command`, seleciona fontes `.c` e mantém headers indexados.
- Nenhum pré-processador é executado: `-D`, macros, `#ifdef` e configurações condicionais não são resolvidos. Confiança sintática de uma chamada direta não prova qual ramo é compilado.
- Parâmetros, variáveis locais e callbacks de headers são classificados com escopos; chamadas indiretas não são seguidas como referências definitivas. Candidatos por nome de membro são apenas inferências.
- Efeitos globais são aproximados: distingue armazenamento global de variáveis locais e de escrita na memória apontada. Não é uma análise completa de aliasing, concorrência ou efeitos interprocedurais.
- Estruturas/aliases e referências de tipos incluem corpos das funções. Identidades preservam estruturas homônimas; a seleção compacta é conservadora e não prova equivalência de layout.
- Cache guarda fatos por caminho absoluto, conteúdo e versão do analisador; cache ilegível ou inválido é reconstruído. A análise contextual de includes é recalculada.
- IDs de nós de diagnóstico do grafo incluem linha; o catálogo canônico schema 3 usa arquivo/símbolo/contexto sem linha e mantém hash de origem separado.

## Testes

```bash
python3 -m unittest discover -s tests -v
```

As regressões cobrem cache, escopos, efeitos globais, tipos, headers inline, seleção multi-header e exportação global com incertezas preservadas.
