---
name: migration-scoping
description: Defines a bounded C-to-Rust migration unit before inventory and contract analysis.
---

# Migration Scoping

Use esta skill no início de uma nova migração, antes do inventário detalhado. O objetivo é selecionar uma **fração do código legado** que contenha lógica de negócio portável, sem tentar converter o projeto C inteiro nem reproduzir tecnologias de infraestrutura que não pertencem ao domínio Rust.

## Objetivo do port

Antes de escolher arquivos, descreva o comportamento de negócio que será extraído:

- regra, cálculo, validação, transformação ou decisão de domínio;
- entradas e saídas observáveis;
- consumidores atuais e consumidores esperados no Rust;
- resultado desejado: preferencialmente uma biblioteca Rust reutilizável;
- infraestrutura necessária apenas como dependência externa ou adaptador.

O alvo padrão é:

```yaml
output:
  kind: library
  library_kind: rlib
```

Use `binary` somente quando o comportamento a ser migrado for inseparável de um executável, entry point ou fluxo de aplicação.

A criação do manifesto e dos planos individuais é responsabilidade do `ctrust-lead`, mas é executada pelo controlador: após criar/atualizar o manifesto, o agente executa internamente `plan-slices` ou `reconcile`. Para cada `id` do manifesto deve existir `.ctrust/plans/units/<slice-id>.md`. O usuário não precisa criar ou executar scripts para gerar slices ou planos.
## Slices de migração

A unidade de port deve ser dividida em **slices independentes, ordenados e verificáveis**. Um slice é a menor fração de lógica de negócio que pode percorrer o ciclo completo:

```text
behavior → contract → model → port → verify
```

Registre os slices em `.ctrust/plans/units/<unit>-slices.yaml` e use IDs estáveis, como `parser-001-token-validation`. O ID do slice é usado como a unidade operacional nos comandos `contract`, `model` e `checkpoint`.

Cada slice deve conter:

- `id` e `order`;
- objetivo de negócio;
- `source.headers` com os headers C de entrada;
- `source.functions` com as funções C alvo e os nomes Rust planejados;
- funções, tipos e arquivos C incluídos;
- headers e dependências necessárias;
- infraestrutura adaptada, simulada ou excluída;
- destino Rust e produto (`library` ou `binary`);
- pré-requisitos e slices dependentes;
- testes de caracterização;
- critério de conclusão;
- status: `planned`, `in_progress`, `verified`, `blocked` ou `completed`.

Não crie slices por arquivo automaticamente. Divida por comportamento de negócio e contrato verificável. Um arquivo C pode participar de vários slices, mas cada slice deve declarar quais símbolos e regras realmente utiliza.

## Informações obrigatórias

Registre em `.ctrust/plans/units/<unit>.md`, no manifesto de slices e em `migration.yaml`:

- **origem C**: `source.project_root`, componente ou fração em `source.component`;
- **headers de entrada**: `source.headers` deve listar os arquivos `.h` que declaram as funções C alvo;
- **funções alvo**: `source.functions` deve listar cada função C que será portada e seu nome Rust planejado;
- **lógica de negócio**: regras, funções e tipos que serão preservados no port;
- **slices**: lista ordenada de frações independentes e seus critérios;
- **destino Rust**: `target.project_root` é a raiz do Cargo workspace; `target.path` é o caminho do crate, módulo ou diretório;
- **workspace Cargo**: crie ou atualize `target.project_root/Cargo.toml` antes do primeiro slice; ele deve ficar um nível acima dos crates e listar todos em `workspace.members`;
- **produto**: `output.kind: library` ou `output.kind: binary`;
- tipo de biblioteca (`rlib`, `cdylib` ou `staticlib`) ou nome do binário;
- `include`: arquivos, símbolos, headers e dependências necessárias por slice;
- `exclude`: arquivos, símbolos e subsistemas fora do port;
- dependências compartilhadas e adaptadores necessários;
- estratégia de coexistência C/Rust, se houver;
- riscos iniciais e critérios objetivos de conclusão.

## Classificação de infraestrutura

Classifique cada dependência encontrada em uma destas categorias:

- **portar** — contém lógica de negócio e deve ser implementada em Rust;
- **adaptar** — fornece uma capacidade externa necessária ao domínio, como filesystem, relógio, rede ou persistência;
- **simular** — será substituída por fake, fixture ou implementação em memória nos testes;
- **excluir** — infraestrutura específica que não deve entrar na biblioteca Rust.

Por padrão, avalie para excluir ou adaptar, em vez de portar diretamente:

- shared memory e IPC;
- semáforos, filas e mecanismos de sincronização específicos do sistema;
- drivers, hardware e registradores;
- event loops e frameworks de aplicação;
- detalhes de processos, sinais e daemonização;
- persistência, rede e filesystem quando não forem parte da regra de negócio;
- código de UI, CLI e integração operacional;
- wrappers de plataforma e APIs proprietárias.

A exclusão não significa ignorar o comportamento. Registre o contrato da dependência e defina uma interface ou adaptador para que a biblioteca Rust permaneça testável.

## Regras de escopo

1. Comece por uma unidade vertical de lógica de negócio que possa ser compilada e testada isoladamente.
2. Exija os headers C de entrada e a lista de funções alvo antes do inventário.
3. Ordene slices por dependência de chamadas e contrato, mantendo um único slice ativo por vez. Use `order` somente como desempate estável.
4. Siga callers e callees somente até o limite necessário para entender o contrato do slice selecionado.
5. Declare dependências entre slices com IDs estáveis, relação (`calls`, `requires`, `shares-contract` ou `replaces`) e `blocking: true|false`.
6. Não avance um slice enquanto suas dependências bloqueantes não estiverem `verified` ou `completed`.
7. Trate ciclos de chamadas como decisão de arquitetura, não como uma ordem arbitrária.
8. Use listas `include` e `exclude`; não considere todo o projeto C como parte da unidade por padrão.
9. Não copie shared memory, IPC ou APIs de plataforma para dentro da biblioteca Rust sem uma decisão explícita.
10. Não confunda o projeto Rust destino com a fonte de comportamento original.
11. Marque dependências externas ou compartilhadas sem incorporá-las automaticamente ao slice.
12. Prefira `library` quando o comportamento deve ser reutilizado; prefira `binary` quando a unidade representa um executável e seu entry point.
13. Se um slice não puder ter um critério de conclusão verificável, reduza ou divida o slice.
14. Não crie um `Cargo.toml` de workspace dentro de cada crate; mantenha um único workspace na raiz do projeto Rust.

## Gate de saída

O `ctrust-lead` deve criar o manifesto e os planos dos slices como parte desta fase. A unidade está pronta para `c-code-inventory` quando possui:

- comportamento de negócio a extrair descrito;
- headers C de entrada informados;
- funções C alvo listadas com nomes Rust planejados;
- manifesto de slices criado, ordenado e sem sobreposição não intencional;
- hierarquia de chamadas C registrada em `.ctrust/inventory/call-graph.yaml`, ou indisponibilidade explicitamente registrada;
- dependências de slice derivadas do grafo, com relação, motivo, status requerido e indicação de bloqueio;
- o grafo de dependências não possui ciclos não tratados;
- cada slice com `include`, `exclude`, dependências e critério de conclusão;
- infraestrutura classificada como portar, adaptar, simular ou excluir;
- adaptadores e interfaces necessários registrados;
- [ ] workspace Cargo criado ou localizado em `target.project_root/Cargo.toml`;
- [ ] todos os crates previstos registrados em `workspace.members`;
- [ ] destino Rust definido como raiz de workspace e caminho de crate;
- dependências críticas listadas;
- testes ou estratégia de caracterização identificados;
- uma próxima ação única registrada em `current-context.md`.

Não inicie a extração de contratos enquanto o escopo misturar lógica de negócio com infraestrutura não classificada ou enquanto os slices não estiverem ordenados.