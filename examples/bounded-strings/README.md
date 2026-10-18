# Migração executável C → Rust com ctrust v2

O exemplo executa o workflow **schema 3** com `strlcpy` e `strlcat` da libc do OpenBSD. Usa inventário global, arquitetura confirmada, cinco artefatos de preparação por slice, progresso canônico por função, evidências diferenciais e encerramento final do workspace.

```bash
.venv/bin/python examples/bounded-strings/run.py --output /tmp/ctrust-strings-v2
```

Requer `cc`, Cargo, rustfmt e dependências Python do analisador. O diretório de saída deve estar vazio; nada é apagado. Sem `--output`, cria um diretório novo em `runs/`.

O runner compila a referência C com `DEF_WEAK` desativado apenas para excluir infraestrutura de símbolos. Materializa código Rust, contratos e modelos previamente revisados para este exemplo; não é um tradutor genérico. Rust usa `&mut [u8]` e `&CStr`, sem dependências externas, unsafe ou FFI.

São **162 casos por função, 324 no total**, incluindo 12 casos focados e 150 aleatórios com sementes fixas por função. Compara retorno e todos os bytes do buffer. Exercita capacidade zero/um, truncamento, NUL embutido, bytes não UTF-8 e ausência de NUL no destino. Também verifica rejeição de contrato prematuro, comparação sem evidência, retomada nos testes/comparação e repetição idempotente de `verify-slice`.

O comando `finish` executa revisão final e publica `completed`. A suite `tests/test_migration.py` acrescenta o cenário de dois consumidores com uma função compartilhada em slice base, além de falhas de persistência e retomada.

## Artefatos

- `REPORT.md`, `summary.json`: resultado e etapas.
- `commands.ndjson`: comandos, códigos e saídas; código 3 só é esperado nos testes negativos identificados.
- `sources.json`: revisão, modo de aquisição e SHA256 das fontes.
- `architecture.yaml`: decisões revisadas de seleção e agrupamento.
- `rust/.ctrust/migration.yaml`: snapshot canônico.
- `rust/.ctrust/artifacts/`: estruturas, comportamento, contrato, modelo e organização revisados.
- `rust/.ctrust/evidence/`: resultados C/Rust por caso.
- `rust/.ctrust/logs/`: resultados dos gates Cargo e arquitetura.
- `rust/.ctrust/checkpoints/`: projeções regeneráveis.
- `rust/.ctrust/reports/architecture.html`: editor autônomo e progresso da revisão.

## Origem e limites

Os arquivos em `upstream/` são snapshots obtidos por consulta web, com whitespace normalizado; não são um clone byte a byte do upstream. A licença e os identificadores de revisão foram preservados. `legacy.h` é um header local de teste. URLs de origem estão em `sources.json`.

`--download` solicita aquisição direta do upstream e exige acesso à rede; falha não usa snapshot silenciosamente. O runner recusa reutilizar contratos se a revisão upstream mudou.

A paridade vale para os casos executados e entradas definidas pelo contrato C: fonte legível até NUL e buffers válidos sem sobreposição. Ponteiros inválidos, dangling pointers, overlap e fontes sem NUL não são executados porque não fornecem referência de comportamento definido. Truncamento e destino sem NUL são casos de capacidade com resultado definido, classificados como erro.

O exemplo não demonstra equivalência de projetos inteiros, concorrência, shared memory, packing ou resolução completa de callbacks/macros.
