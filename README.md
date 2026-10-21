# ctrust v2

Toolkit de agentes, skills e ferramentas para migrar um recorte de código C para Rust idiomático, preservando contratos observáveis e evidências. O fluxo começa pelo inventário global do escopo e pela revisão da arquitetura; funções compartilhadas possuem um único proprietário.

## Modelo e fluxo

O novo workflow usa **schema 3**. O schema 2 anterior não é suportado, convertido nem importado. Para começar, use um projeto novo. Instalar ou atualizar o toolkit não apaga estados de projetos existentes; tentar executá-los produz um diagnóstico de versão incompatível.

```text
Escopo C → inventário global → arquitetura confirmada
  → slices em ordem de dependência
    → estruturas → comportamento → contrato → modelo → organização Rust
    → implementação por função → testes → comparação → gates do slice
  → revisão final do workspace → encerramento
```

`.ctrust/migration.yaml` é o único snapshot operacional: inventário, arquitetura e progresso são seções separadas, publicadas juntas sob lock e escrita atômica. Não há progresso duplicado nos slices. Status, próxima ação, HTML e checkpoints são derivados. Uma falha local registra seu requisito e alcance e preserva o último marco válido.

O [contrato do workflow](docs/workflow-v2.md) define identidade, persistência, idempotência e invalidação. O [plano de evolução](evol_ctrust_v2.md) registra decisões, etapas e validações.

## Requisitos

Bash, Python 3.10+, PyYAML, Tree-sitter C e NetworkX. Verificação do destino exige Cargo, rustc e rustfmt; exemplos diferenciais exigem `cc`. `cscope` é opcional; Node.js é opcional para o teste de comportamento do editor HTML.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/header_dependency_analyzer/requirements.txt
export PATH="$PWD/.venv/bin:$PATH"
```

## Instalação

```bash
./sync.sh generic /caminho/projeto-rust
./sync.sh kilocode /caminho/projeto-rust
./sync.sh update generic /caminho/projeto-rust
./sync.sh verify
```

Destinos: `generic`, `antigravity`, `opencode`, `kilocode`, `cursor`, `claude` e `all`. Instalação preserva arquivos diferentes e informa conflitos. `update` substitui componentes gerenciados, preservando o snapshot operacional. Arquivos antigos de instalações anteriores não são executados pelo novo dispatcher; comece a migração em um projeto novo. `clean` remove apenas componentes idênticos aos distribuídos.

Agentes/skills ficam nos diretórios do cliente; controlador, scripts, documentação e ferramentas ficam em `.ctrust`.

## Começar uma migração

Na raiz do projeto destino:

```bash
.ctrust/sync.sh init .
.ctrust/sync.sh inventory . --source /caminho/legado
# --source é a raiz original do C; sem --header, headers são descobertos recursivamente.
# --header pode se repetir para limitar a análise; --function limita raízes mantendo dependências.
.ctrust/sync.sh architecture . --export architecture.yaml
.ctrust/sync.sh report .
```

Abra `.ctrust/reports/architecture.html` para buscar funções, examinar chamadas/consumidores, filtrar funções compartilhadas, criar slices e atribuir grupos. Reabra ou exporte o plano JSON; JSON também é aceito como YAML pelo controlador. O HTML é um arquivo autônomo, representa uma revisão e não altera o estado operacional.

Para migrar apenas parte do legado, mantenha `--source` na raiz dessa parte e acrescente as dependências explicitamente:

```bash
.ctrust/sync.sh inventory . --source /caminho/aurora/modules/navigation/src \
  --include-dir /caminho/aurora/modules/sensors/include \
  --include-dir /caminho/aurora/modules/storage/include \
  --include-dir /caminho/aurora/shared/include
# Para analisar também implementações C de um módulo externo:
# acrescente --dependency-root /caminho/modulo
```

`--include-dir` indexa headers recursivamente e acrescenta o diretório à busca de includes; `--dependency-root` indexa fontes C e headers recursivamente para resolver chamadas transitivas. Ambas as opções podem se repetir e seus caminhos relativos partem do diretório atual. Headers adicionais não viram entradas do inventário: `--header` (relativo a `--source`) e `--function` continuam definindo as raízes. Funções externas alcançadas por chamadas entram no catálogo e precisam de tratamento no plano; estruturas externas permanecem disponíveis para análise. Mudanças nos arquivos dessas dependências invalidam o inventário.

Use `--compile-commands /caminho/compile_commands.json` para fornecer caminhos de include do build e distinguir headers com nomes iguais. Os diretórios externos ainda precisam ser fornecidos com `--include-dir` ou `--dependency-root` para serem indexados. Como alternativa, uma raiz comum em `--source` com `--header` explícito permite analisar os diretórios irmãos. Após mudar o inventário, exporte novamente o plano, revise as atribuições e execute `architecture --confirm`; a mudança invalida a arquitetura anterior.

O plano classifica **cada entrada do catálogo** com `port`, `adapt`, `simulate` ou `exclude`. Cada função portada pertence a exatamente um slice. Os demais tratamentos exigem justificativa; uma dependência chamada por função portada não pode ser simplesmente excluída. Callbacks/chamadas indiretas exigem `indirect_decision` revisada no plano. Incerteza de análise nunca é prova de resolução. Headers locais ausentes exigem fornecimento do arquivo e novo inventário ou decisão explícita `include_decisions` com `role` e `reason`; headers de sistema ausentes permanecem visíveis para revisão de tipos. Os gates Cargo usam modo offline; disponibilize as dependências do workspace antes da verificação.

Exemplo de agrupamento:

```yaml
version: 3
inventory: "<fingerprint exportado>"
slices:
  base:
    crate: crates/domain
    module: base
  position:
    crate: crates/domain
    module: position
assignments:
  "c:src/common.c:normalize_value":
    role: port
    slice: base
    rust_name: normalize_value
  "c:src/position.c:calculate_position":
    role: port
    slice: position
    rust_name: calculate_position
```

Dependências entre slices são derivadas das chamadas. Ciclos exigem reagrupamento ou uma decisão de adaptação. Vários consumidores não justificam automaticamente um único slice base: preserve coesão.

```bash
.ctrust/sync.sh architecture . --plan architecture.yaml
.ctrust/sync.sh architecture . --plan architecture.yaml --confirm
.ctrust/sync.sh status . --json
```

## Executar e retomar slices

```bash
.ctrust/sync.sh prepare . base
.ctrust/sync.sh resume .
```

`prepare` cria rascunhos em `.ctrust/artifacts/base/` sem substituir arquivos existentes ou criar código Rust. Faça análise do C e preencha os artefatos. Cada um exige `version: 3`, `slice`, `stage`, `status: confirmed`, `review`, `evidence` e lista das funções próprias. Estruturas e comportamento exigem os campos `completion` verdadeiros; estruturas exigem `unresolved: 0`. Ausência de structs não dispensa a análise de ponteiros e ownership. `organization.yaml` contém destinos canônicos e deve ser revisado junto ao modelo.

```bash
.ctrust/sync.sh confirm . base structures .ctrust/artifacts/base/structures.yaml
.ctrust/sync.sh confirm . base behavior .ctrust/artifacts/base/behavior.yaml
.ctrust/sync.sh confirm . base contract .ctrust/artifacts/base/contract.yaml
.ctrust/sync.sh confirm . base model .ctrust/artifacts/base/model.yaml
.ctrust/sync.sh confirm . base organization .ctrust/artifacts/base/organization.yaml
```

Dentro do slice, conclua as implementações próprias antes de registrar testes. Assim, callers e callees do mesmo módulo podem ser compilados juntos. O registro de um arquivo atualiza também os fingerprints de funções irmãs já registradas sob a mesma preparação, sem aprovar testes automaticamente.

O agente/usuário deve disponibilizar o workspace Cargo e as implementações. O toolkit não traduz C automaticamente. O scanner de domínio rejeita ponteiros crus e acoplamento de transporte; `unsafe` exige justificativa `SAFETY`. As verificações estáticas são conservadoras e não substituem auditoria.

Cada função Rust portada deve ter o vínculo imediatamente antes da declaração:

```rust
// ctrust:source file=src/common.c line=42 symbol=normalize_value slice=base contract=C-BASE
pub fn normalize_value(value: i32) -> i32 {
    value.max(0)
}
```

```bash
.ctrust/sync.sh record-implementation . c:src/common.c:normalize_value auto crate::base
.ctrust/sync.sh record-evidence . c:src/common.c:normalize_value .ctrust/evidence/normalize.ndjson
.ctrust/sync.sh compare . c:src/common.c:normalize_value
.ctrust/sync.sh verify-slice . base
.ctrust/sync.sh finish .
```

Evidência NDJSON exige `function_id`, `command`, `category`, `expected_source`, `expected`, `observed` e `result: passed`. Cubra `normal`, `boundary` e `error`. A comparação só registra `parity_verified` com referência executável declarada e origem `legacy_execution`/`approved_fixture`.

Sem referência executável, cubra também `unresolved_reference`, use `expected_reason` e uma fonte independente: `source_analysis`, `domain_rule`, `protocol_specification`, `manual_calculation`, `approved_fixture`, `independent_reference_model` ou `expert_review`. A classificação será `verified_without_legacy_reference`, com escopo `behavior_contract`.

Os gates validam preparação, funções/evidências, origem/destino, completude e políticas conservadoras de domínio; depois executam Cargo fmt/check/test. A saída identifica ação, gate, alcance, duração e log. `gate . all` valida sem promover; `verify-slice` promove uma vez e gera checkpoint regenerável. `finish` exige todos os slices válidos, executa revisão final e publica encerramento idempotente.

## Falhas, mudanças e feedback

`status` e `resume` mostram a mesma decisão, contagens cumulativas por marco, funções próprias/referências, impedimentos e resultados por gate. Consulta não muda progresso.

- Evidência malformada: corrija o arquivo e repita o registro; implementação permanece válida.
- Código alterado: registre implementação atual e refaça testes/comparação.
- Artefato de preparação alterado: reconfirme-o e os resultados posteriores afetados.
- Origem ou arquitetura alterada: revise e reconfirme; aprovações da revisão anterior não são adotadas automaticamente.
- Interrupção: consulte `resume` e repita a operação aplicável. Não edite YAML operacional para recuperar o fluxo.
- Checkpoint ausente: execute `checkpoint` ou repita a conclusão já aprovada; não há nova promoção.

A primeira política de invalidação é conservadora. O fingerprint Rust é por arquivo; alterar uma função pode exigir novos testes de outras funções do mesmo arquivo, mas não exige registrar manualmente cada irmã novamente. Corrigir somente a evidência da função base não reinicia a implementação de seus consumidores. Aprovações funcionais anteriores de slices não são apagadas pelo crescimento posterior do workspace; a revisão final sempre executa Cargo para o conjunto atual.

O controlador valida a estrutura das evidências e executa os gates Cargo. Ele não comprova sozinho que um comando declarado no NDJSON foi executado ou que uma revisão humana é correta. Use harness executável, logs auditáveis e revisão independente.

## Ferramentas e testes

```bash
.venv/bin/python -m unittest discover -s tests -v
(cd tools/header_dependency_analyzer && ../../.venv/bin/python -m unittest discover -s tests -v)
.venv/bin/python examples/bounded-strings/run.py --output /tmp/ctrust-strings-v2
.venv/bin/python examples/shared-functions/run.py --output /tmp/ctrust-shared-v2
./sync.sh verify
```

`tools/migration/` contém snapshot, inventário, arquitetura, decisão única, execução, CLI e HTML. `tools/header_dependency_analyzer/` contém análise C. Os testes do workflow anterior foram substituídos por testes do contrato schema 3, incluindo função compartilhada, recuperação, idempotência, publicação e revisão final; as regressões de análise C permanecem cobertas.

A análise C não executa o pré-processador. `compile_commands.json` seleciona unidades de tradução e fornece caminhos de includes/forced includes. Headers referenciados por caminho local são seguidos; includes entre aspas com apenas basename são procurados recursivamente quando há um único candidato. Colisões permanecem visíveis como não resolvidas e precisam de `compile_commands.json`/`-I` ou decisão manual. Macros, condicionais, aliases complexos e callbacks ainda exigem revisão. O inventário não determina sozinho se código pode ser removido.
