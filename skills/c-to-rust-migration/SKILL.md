---
name: c-to-rust-migration
description: Guides incremental, behavior-preserving migration from C to Rust with mandatory progress persistence.
---

## Rastreabilidade da origem C

Toda função, método ou implementação Rust que resulte de uma função C deve conter imediatamente antes da declaração um comentário no formato:

```rust
// ctrust:source file=src/parser.c line=142 symbol=parse_token slice=parser-001-token-validation contract=C-PARSER-001
pub fn parse_token(input: &[u8]) -> Result<Token, ParseError> {
    // ...
}
```

O comentário deve conter:

- `file`: caminho relativo ao `source.project_root`;
- `line`: linha da função ou símbolo original, quando conhecida;
- `symbol`: nome da função, método ou símbolo C;
- `slice`: ID do slice que originou o port;
- `contract`: ID do contrato confirmado.

Para uma função derivada de múltiplas funções C, use um comentário por origem:

```rust
// ctrust:source file=src/parser.c line=142 symbol=parse_token slice=parser-001 contract=C-PARSER-001
// ctrust:source file=src/tokenizer.c line=87 symbol=next_token slice=parser-001 contract=C-TOKEN-002
```

Se a linha exata não for conhecida, use `line=unknown` e registre a justificativa no modelo e no checkpoint. Não use comentários genéricos como “portado do C”; a referência precisa permitir localizar a origem.

A rastreabilidade é obrigatória para considerar o slice verificável. O `migration-verification` deve rejeitar ou marcar como incompleta qualquer função portada sem comentário de origem.

## Edição segura no destino Rust

Ao criar ou editar arquivos durante o port, use o schema real das ferramentas do cliente.

No Kilo:

- `write` exige `filePath` e conteúdo completo;
- `edit` exige `filePath`, `oldString` e `newString`;
- antes de `edit`, leia o arquivo e copie `oldString` literalmente;
- confirme que `oldString` ocorre uma única vez;
- para arquivo inexistente, use `write`, não `edit`;
- se `oldString` não corresponder, não tente aproximações: releia o arquivo e ajuste o trecho.

Exemplo:

```json
{
  "filePath": "/workspace/parser-rust/crates/parser-core/src/lib.rs",
  "oldString": "fn old_name() {}",
  "newString": "fn new_name() {}"
}
```

Nunca envie somente `path`, `file` ou `target.path`. Se o schema do cliente for diferente, consulte-o antes de invocar a ferramenta. Não tente corrigir uma chamada rejeitada repetindo o mesmo payload.
- leia `.ctrust/inventory/slices/<slice-id>/behavior.yaml` e trate-o como a descrição factual do comportamento C;
- não crie uma assinatura Rust para substituir a ausência de comportamento extraído;
- substitua ponteiros por ownership, borrowing ou handles explícitos;
- transforme códigos de erro em `Result` ou enums de domínio;
- encapsule detalhes de plataforma e I/O atrás de traits ou adaptadores;
- mantenha a implementação C como referência até a paridade ser demonstrada;
- use passos pequenos com `cargo fmt --all`, `cargo check --workspace`, testes e comparação de resultados, sempre a partir de `target.project_root`;
- use `rust-module-organization` antes da implementação para separar módulos por responsabilidade e impedir `lib.rs`/`main.rs` monolíticos;
- preserve a modular organization defined by `rust-module-organization`;
- keep `lib.rs`/`main.rs` limited to module declarations, composition and intentional reexports;
- implement each slice in its own cohesive `rust_file`/`module_path`, not by appending every function to `lib.rs`;

Depois de implementar as funções do slice e antes de verificar o port, o agente deve executar o gate de completude:

```bash
.ctrust/scripts/migration-completeness.sh .
```

Esse gate falha quando encontra `todo!`, `unimplemented!`, tokens de template, `Ok(Default::default())` e outros placeholders explícitos. Retornos `None`, `0` ou `false` são avisos e precisam ser justificados por comportamento e evidência. O comentário de origem é exigido nas funções mapeadas, sem impor origem C a testes e helpers Rust. Retornos constantes legítimos precisam ser derivados do contrato C e acompanhados de testes; não use uma assinatura compilável como substituto da lógica de origem.


## Protocolo obrigatório de progresso

A migração pode ser interrompida a qualquer momento, inclusive com código parcialmente implementado. Antes de encerrar uma etapa ou sessão, mesmo quando houver falha ou bloqueio:

1. Atualize a fase, unidade e status em `.ctrust/migration.yaml`.
2. Registre o comportamento Rust já implementado.
3. Registre o que ainda não foi implementado.
4. Liste os arquivos modificados e o destino Rust correspondente.
5. Registre comandos executados e seus resultados em `.ctrust/evidence/` ou no checkpoint.
6. Registre diferenças conhecidas em relação ao comportamento C.
7. Registre riscos, bloqueios e perguntas abertas.
8. Defina **uma única próxima ação concreta**, começando com um verbo.
9. Atualize `.ctrust/current-context.md`.
10. Crie um checkpoint em `.ctrust/checkpoints/`.
11. Registre o evento em `.ctrust/logs/progress.ndjson`.

Use o controlador do projeto destino:

```bash
.ctrust/sync.sh checkpoint . <unit>-<etapa>
```

Se estiver dentro de `.ctrust/`:

```bash
./sync.sh checkpoint .. <unit>-<etapa>
```

O checkpoint não deve conter apenas “continuar implementação”. A próxima ação precisa ser específica o suficiente para outra sessão executar sem recuperar a conversa original.

## Conteúdo mínimo do checkpoint

Toda migração parcial deve responder:

- qual unidade está sendo portada;
- qual fase foi concluída;
- o que funciona atualmente;
- o que está incompleto;
- quais arquivos foram alterados;
- quais testes passaram, falharam ou ainda não foram executados;
- quais diferenças existem em relação ao C;
- quais riscos ou bloqueios permanecem;
- qual é a única próxima ação.

Exemplo de próxima ação válida:

```text
Implementar ParseError::Incomplete em crates/parser/src/error.rs e adicionar o caso equivalente ao teste parser_characterization.
```

Exemplos inválidos:

```text
Continuar a migração.
Verificar depois.
Terminar o parser.
```

## Recuperação de uma migração parcial

Na sessão seguinte, execute primeiro:

```bash
.ctrust/sync.sh resume .
```

Depois leia o plano, o contrato, o modelo e o checkpoint apontados pelo estado. Não repita a modelagem ou os testes registrados como concluídos. Retome somente pela próxima ação persistida e atualize o checkpoint ao concluir uma nova etapa.

## Progresso por função

Use `select-function` para criar ou retomar a unidade indicada pelo estado. Preserve `active_unit` e `active_function_ids`; o nome C não substitui a identidade da função. Registre implementação com `record-implementation`, testes com `record-evidence` e classificação com `compare`. O controlador rejeita arquivos inexistentes, unidades diferentes da ativa e saltos de estágio. A comparação cobre `normal`, `boundary` e `error` com expected results independentes. Ao concluir as funções, execute `verify-slice`; ele executa gates/Cargo, cria checkpoint e só então avança o slice. Não promova slices manualmente para contornar gates.

### Registro robusto de evidência

`record-evidence` aceita o caminho relativo/absoluto do NDJSON, `auto` ou argumento vazio. Com `auto`, procura primeiro `.ctrust/evidence/<slice-id>/<c_name>.ndjson`, depois `.ctrust/evidence/<slice-id>/<rust_name>.ndjson` e por fim `.ctrust/evidence/tests/<unit-id>.ndjson`. Passar apenas o nome da função também usa essa resolução quando existe um único candidato. Se não houver arquivo ou houver mais de um candidato, informe o caminho completo; não trate o nome da função como caminho na raiz do projeto.

```bash
.ctrust/sync.sh record-evidence . <unit-id> auto
# ou
.ctrust/sync.sh record-evidence . <unit-id> .ctrust/evidence/<slice-id>/<function>.ndjson
```

### Registro da implementação Rust

`record-implementation` registra somente o arquivo Rust e o módulo canônico; não recebe descrição textual nem caminho de evidência. O destino pode ser resolvido pelo plano com `auto`:

```bash
.ctrust/sync.sh record-implementation . <unit-id> auto <module-path>
```

Ou pode ser informado explicitamente:

```bash
.ctrust/sync.sh record-implementation . <unit-id> crates/<crate>/src/<file>.rs crate::<module>
```

A evidência NDJSON é registrada separadamente com `record-evidence`. O ctrust rejeita destinos inexistentes ou descrições usadas como caminho Rust.
