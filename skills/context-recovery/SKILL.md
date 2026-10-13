---
name: context-recovery
description: Maintains compact migration state and recovers work after context-window exhaustion.
---

# Context Recovery

Use esta skill para tratar `.ctrust/` como memória persistente da migração. Não copie conversas ou grandes trechos de código: registre fatos, decisões, evidências e uma próxima ação.

## Bootstrap versus recovery

`resume` só deve ser usado quando `.ctrust/migration.yaml` existe e possui `version: 2`.

Se o estado existir, mas estiver no schema legado (`source`/`target`/`output`/`status: scoped` sem `version: 2`), o agente deve executar primeiro:

```bash
.ctrust/sync.sh upgrade-state .
```

Se o upgrade bloquear por manifesto inválido, não invente IDs ou progresso; corrija a inconsistência com o `ctrust-lead` e preserve o backup em `.ctrust/recovery/`.

Para uma migração nova:

1. O agente deve confirmar que está na raiz do projeto Rust destino.
2. Se `.ctrust/sync.sh` não existir, instalar/distribuir o ctrust para o destino.
3. Se `.ctrust/` existir mas `.ctrust/migration.yaml` não existir, o agente deve executar internamente o bootstrap:

   ```bash
   .ctrust/sync.sh init .
   ```

   O usuário não executa esse comando por etapa; ele somente informa objetivo, origem e destino. Depois do bootstrap, o agente cria o manifesto, os planos e executa a reconciliação.

4. Configurar `source.project_root`, `source.component`, `target.path` e o produto.
5. Usar `migration-scoping` para criar o manifesto de slices.
6. Só então usar `resume` nas sessões seguintes.

Não trate a ausência de arquivos abertos no editor como ausência de código no workspace. O agente deve inspecionar a raiz do workspace; se a origem C não estiver configurada ou não puder ser identificada, peça somente o caminho necessário ao usuário.

## Ao iniciar uma sessão existente

```bash
.ctrust/sync.sh resume .
```

Se estiver dentro do diretório `.ctrust`, use:

```bash
./sync.sh resume ..
```

Antes de selecionar ou retomar um slice, execute:

```bash
.ctrust/sync.sh status-slices .
```

Leia `.ctrust/status/slices.md` e use a linha `Active: yes` como referência do slice atual. A coluna `Current stage` indica a fase do ciclo; `Remaining` é a lista operacional do que falta. O relatório é derivado do manifesto e do estado global, portanto não substitui o plano, o contrato, o modelo, o inventário ou o checkpoint.

O `resume` é leitura do estado persistente. Se os artefatos encontrados não corresponderem a `migration.yaml` — por exemplo, inventários/contratos já existentes com `status: initialized`, `phase: inventory` ou `current_unit: none` — execute primeiro:

```bash
.ctrust/sync.sh reconcile .
```

A reconciliação escolhe o primeiro slice operacional sem concluir etapas por inferência. Ela considera contrato confirmado somente com `status: confirmed`, não altera artefatos existentes e registra a transição no log. Depois dela, regenere `status-slices` e execute `resume` novamente.

```text
PHASE= UNIT= STATUS=
NEXT_ACTION= NEXT_COMMAND= NEXT_OWNER= NEXT_SKILL=
LAST_CHECKPOINT= ATTEMPTS=n/max LAST_ERROR=
LOOP_GUARD=ok|stop GUIDANCE=
```

Em janela pequena, o `--brief` deve ser suficiente. Use `--full` somente quando o cabeçalho não permitir decidir a próxima ação:

```bash
.ctrust/sync.sh resume . --full
```

Nesse caso leia somente nesta ordem:

1. `.ctrust/migration.yaml`;
2. `.ctrust/current-context.md`;
3. o plano da unidade atual em `.ctrust/plans/units/`;
4. o contrato, modelo e checkpoint apontados pelo estado;
5. evidências diretamente relacionadas.

## Um comando por turno

1. Execute `resume` (brief).
2. Execute **apenas** o `NEXT_COMMAND` informado. Se ele estiver vazio, a ação é manual: conduza-a com `NEXT_SKILL` sob `NEXT_OWNER`.
3. Ao concluir, registre a próxima ação única:

   ```bash
   .ctrust/sync.sh next . "<próxima ação>" "<próximo comando>"
   ```

4. Se a ação falhar, registre o motivo em vez de tentar variações do mesmo comando:

   ```bash
   .ctrust/sync.sh failed . "<motivo objetivo da falha>"
   ```

Regras de parada:

- não repita um comando já registrado como falho sem mudar a causa raiz;
- `failed` incrementa `attempts`; ao atingir `max_attempts` (padrão `2`), o estado passa a `status: blocked`, a saída traz `LOOP_GUARD=stop` e o código de saída é `3`;
- com `LOOP_GUARD=stop`, pare a execução, crie um checkpoint de bloqueio e peça uma decisão ao usuário:

  ```bash
  .ctrust/sync.sh checkpoint . "blocked: <motivo>"
  ```

- não desbloqueie elevando `max_attempts` por conta própria; o desbloqueio exige decisão do usuário ou correção explícita da causa;
- `next` zera `attempts` e `last_error`. Use-o apenas para uma ação **diferente**, após corrigir a causa da falha; não o use para repetir a ação bloqueada.

Não presuma que carregar a skill executa o comando automaticamente. A skill é uma instrução; o agente ou operador deve executar `resume` explicitamente.
Não repita inventário ou análise marcados como concluídos em `current-context.md`.

## Escrita segura dos artefatos

Ao persistir contratos, modelos, planos ou checkpoints usando uma ferramenta do cliente Kilo, forneça sempre o schema completo.

Para criar ou substituir um arquivo inteiro, use `write` com `filePath` e conteúdo completo. Para alterar um arquivo existente, leia-o primeiro e use `edit` com `filePath`, `oldString` e `newString`:

```json
{
  "filePath": ".ctrust/current-context.md",
  "oldString": "texto exato lido",
  "newString": "texto atualizado"
}
```

Antes de chamar `write` ou `edit`:

1. resolva o caminho final do arquivo;
2. confirme que ele está dentro do workspace autorizado;
3. leia o arquivo se a operação for `edit`;
4. confirme que `oldString` ocorre exatamente uma vez;
5. envie `filePath`, `oldString` e `newString` explicitamente;
6. só então execute a alteração.

Se a ferramenta rejeitar a chamada por ausência de `filePath`, `oldString` ou `newString`, não repita o mesmo payload. Corrija o campo ausente conforme o schema do Kilo. Se o arquivo não existir, troque `edit` por `write`.

## Ao encerrar uma etapa

Registre sempre:

- fase, unidade e status em `migration.yaml`;
- fatos e próxima ação em `current-context.md`;
- evidências por caminho, comando ou hash;
- riscos e diferenças conhecidas;
- checkpoint numerado em `.ctrust/checkpoints/`;
- evento compacto em `.ctrust/logs/progress.ndjson`.

Cada execução deve terminar com **uma única próxima ação concreta**, gravada por `next` com o comando correspondente. Se não houver comando, deixe `next_command` vazio e informe a skill responsável.

## Regras de compactação

- Prefira referências `arquivo:linha` a copiar código.
- Separe fatos confirmados de hipóteses e perguntas abertas.
- Registre decisões uma vez e referencie-as por ID.
- Divida o trabalho por unidade de migração, não por projeto inteiro.
- Mantenha `current-context.md` pequeno o suficiente para ser lido em uma única recuperação.

## Reconciliação e bloqueios

`reconcile` escolhe o slice elegível e deriva a fase operacional; `reconcile-artifacts` atualiza somente blocos gerenciados dos planos. Execute-os nessa ordem quando necessário. Reconciliar preserva erros, tentativas e bloqueios. Após corrigir a causa, use `next` com uma ação diferente para reabrir explicitamente o trabalho. `select-function` retoma o estágio salvo e preserva o ID da unidade; funções implementadas aguardam teste e funções testadas aguardam comparação.
