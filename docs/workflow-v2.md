# Contrato do workflow ctrust v2

O schema novo é `version: 3`. Versões anteriores são rejeitadas, sem conversão.
Todas as operações recebem a raiz do projeto que contém `.ctrust`.

## Persistência

`.ctrust/migration.yaml` é um snapshot canônico, com `version`, `revision`,
`source`, `inventory`, `architecture`, `progress`, `slice_results` e `closure`.
Inventário, decisões de arquitetura e progresso são seções independentes do mesmo
documento: uma alteração é validada sob lock e publicada com um único rename
atômico. Não há manifesto paralelo editável nem progresso duplicado por slice.
HTML, logs de execução, artefatos de análise e checkpoints são referências ou
projeções. Falhar ao gerar um relatório não desfaz progresso publicado.

O inventário contém funções únicas e relações entre elas. A identidade deriva
de arquivo relativo, símbolo e contexto de tradução para funções de linkage
interno em headers; linha é localização, nunca identidade. Definições conflitantes
ficam ambíguas. Renomear ou mover uma função exige decisão explícita, sem inferir
que um símbolo novo é a implementação anterior. O hash da definição é separado
da identidade. Um inventário novo invalida decisões e resultados dependentes.

O plano de arquitetura referencia o fingerprint do inventário e define slices,
destinos, atribuições e tratamentos. Cada função `port` tem um proprietário.
Dependências são derivadas do grafo, nunca de cópias do progresso. Tratamentos
`adapt`, `simulate` e `exclude` exigem justificativa. Funções desconhecidas,
dependências não tratadas e ciclos entre slices impedem confirmação. Ciclos
internos ao mesmo slice são permitidos. Revisões são explícitas; resultados
vinculados a uma revisão diferente são desatualizados, sem apagar seu histórico.

## Decisão e execução

Uma função pura calcula próximo requisito, ação, alcance e diagnóstico. CLI,
status e retomada usam a mesma decisão. Consultas não gravam. A execução revalida
as entradas antes de publicar. Apenas um slice é executado por vez, em ordem
topológica, com desempate pelo ID. Não salta silenciosamente um slice impedido.

Preparação por slice: estruturas, comportamento, contrato, modelo e organização.
Cada confirmação guarda o fingerprint dos artefatos e das entradas anteriores.
Marcos por função: implementação, evidência de teste e classificação da comparação.
Não se persiste uma segunda `phase` nem se usa `blocked` para substituir marcos.
Um impedimento identifica operação, causa, alcance e ação corretiva; não impede
a execução da própria correção. Repetir o mesmo registro válido é idempotente.

Dentro de um slice, concluir implementações próprias antes de registrar testes.
Isso permite compilar callers e callees do mesmo módulo juntos. Funções novas
precedem registros desatualizados do arquivo. Registrar uma função atualiza,
na mesma publicação, o fingerprint das funções irmãs já registradas nesse arquivo
e sob a mesma preparação, após validar seus vínculos de origem. Não adota funções
nunca registradas nem confirma comportamento: evidências antigas tornam-se
desatualizadas quando a revisão do arquivo muda. Isso evita pedir repetidamente
o registro manual das funções anteriores a cada nova função acrescentada.

Consumidores se vinculam à preparação/implementação da dependência, enquanto sua
aprovação exige a prova atual do proprietário. Corrigir somente a evidência da
base não reinicia contratos e implementações dos consumidores. Alterar contratos
ou código da base pode invalidá-los conforme a política conservadora.

Evidências identificam função, comando, categoria, valores esperado/observado e
fonte independente. Exigem `normal`, `boundary` e `error`; sem execução C de
referência exigem também `unresolved_reference`, razão e escopo de contrato.
Resultado reprovado não é aprovação. Alterar a implementação torna evidências
anteriores desatualizadas. Referências compartilhadas reutilizam a aprovação
canônica válida, sem outra unidade de trabalho.

| Falha/mudança | Preservado | Invalidação/ação |
|---|---|---|
| Formato de evidência inválido | Implementação e evidência anterior válida | Corrigir e registrar o arquivo; diagnóstico local |
| Teste/comparação reprovado | Código e resultados anteriores para auditoria | Investigar; alteração de código exige novos testes/comparação |
| Origem C alterada | Histórico | Inventariar novamente; reconfirmar arquitetura e resultados afetados |
| Contrato/modelo alterado | Histórico de aprovação | Reconfirmar dependentes e registrar implementação/testes atuais |
| Implementação Rust alterada | Registro anterior | Registrar implementação atual e executar testes/comparação |
| Gate Cargo falhou | Marcos das funções ainda válidos | Corrigir causa, mostrar alcance workspace e repetir gate |
| Interrupção durante publicação | Snapshot anterior ou novo completo | Reconsultar e repetir a operação; nunca corrigir YAML manualmente |
| Checkpoint/relatório ausente | Resultado canônico | Regenerar projeção; não promover nem contar outra vez |
| Entradas mudaram durante gate | Marcos anteriores | Recusar promoção, executar gates para entradas atuais |

Verificação de slice registra resultados por gate, comando, duração, log e hashes
do conjunto verificado. Sem aprovação de todos os gates não há promoção. Gates
incluem preparação, funções/evidências, origem/destino, segurança/arquitetura e
Cargo fmt/check/test. O resultado se aplica ao workspace capturado. Finalizar
publica uma única aprovação; checkpoint determinístico pode ser regenerado.
Uma repetição de `verify-slice` aceita um slice já concluído com entradas iguais.
`finish` executa revisão final do workspace e publica encerramento idempotente.

## Comandos públicos pretendidos

`init`, `inventory`, `architecture` (exportação/validação/confirmação), `confirm`
(preparação), `record-implementation`, `record-evidence`, `compare`, `verify-slice`,
`finish`, `status`, `resume`, `doctor`, `checkpoint` e `report`.
Argumentos exatos são apresentados por `--help`. `status --json` devolve a mesma
decisão usada pelo texto. Não existem `upgrade-state`, `reopen` ou edição de
`next_action` para desbloquear trabalho.

## Exemplos de decisão

- Base já verificada e consumida por dois slices: uma aprovação, duas referências;
  selecionar apenas funções próprias ainda pendentes dos consumidores.
- Função implementada sem evidência: próxima ação é testar e registrar evidência.
- Evidência inválida: conservar implementação, apresentar requisito e caminho;
  registrar evidência corrigida, sem recriar a função.
- Evidência aprovada sem comparação: próxima ação é classificar a comparação.
- Slice com funções verificadas: próxima ação é executar seus gates, não portar.
- Todos os slices verificados: próxima ação é `finish`; após revisão final válida,
  apresentar migração concluída. Nenhum estado de verificação genérico infinito.

## Política de invalidação inicial

Começar conservador: alterações de inventário ou arquitetura exigem revisão e
invalidam aprovações vinculadas à revisão anterior. Alterações de preparação
invalidam resultados posteriores do slice e seus consumidores. Alterações Rust
invalidam testes, comparação e gates que capturaram aquele conteúdo. Não otimizar
reuso parcial antes de demonstrar, com testes, que as dependências são completas.
Essa política é explícita e evita falsa aprovação; granularidade poderá melhorar
sem introduzir compatibilidade com estados antigos.
