# Plano de evolução do ctrust v2

Data de criação: 2026-10-18.

Este arquivo é a referência para executar e retomar a evolução do toolkit. A denominação **ctrust v2** identifica esta evolução do produto; o workflow atual já utiliza um schema chamado `version: 2`. A nova versão do schema deverá ser definida explicitamente, sem confundir os dois conceitos.

## Objetivo

Reduzir intervenções manuais na migração de C para Rust, principalmente quando uma função já foi migrada, é compartilhada entre slices ou encontra um problema de validação. Organizar o trabalho a partir de um inventário consolidado e de uma arquitetura confirmada antes da implementação.

Prioridades informadas pelo usuário:

1. Simplificar o controle de progresso e a recuperação após falhas de validação.
2. Evitar que funções comuns sejam atribuídas e implementadas em vários slices.
3. Apresentar claramente progresso, impedimentos e próxima ação.
4. Corrigir as falhas identificadas na análise do projeto.

## Decisões acordadas

- Não manter compatibilidade com migrações existentes. Não criar conversores, aliases de schema, execução dupla ou mecanismos de importação de estados antigos.
- Estados anteriores devem ser rejeitados com diagnóstico claro e orientação para iniciar uma nova migração.
- Preservar recuperação de sessões e falhas dentro do novo modelo. Ausência de compatibilidade não elimina persistência, checkpoints ou proteção contra perda de trabalho.
- Fazer inventário global **do escopo selecionado**, antes da definição definitiva dos slices. Não analisar obrigatoriamente todo o repositório C.
- Introduzir uma etapa de arquitetura após o inventário: selecionar funções, definir tratamento, criar slices, reagrupar funções e confirmar responsabilidades.
- Cada função selecionada para migração terá exatamente um slice responsável e um registro canônico de progresso. Consumidores manterão referências, sem cópias independentes de progresso.
- Sugerir agrupamentos compartilhados, inclusive slices base, sem agrupar automaticamente funções sem coesão apenas porque possuem vários consumidores.
- Separar análise factual, decisões arquiteturais e progresso de execução. Relatórios serão derivados dessas fontes, sem se tornar outra fonte de estado.
- Manter Rust idiomático e seguro como objetivo, FFI opcional e verificação baseada em evidências.

## Fluxo pretendido

```text
Escopo inicial: componente C, headers e funções de entrada
    ↓
Inventário consolidado: funções, estruturas e dependências alcançáveis
    ↓
Arquitetura: seleção, tratamento, slices e responsabilidades
    ↓
Validação e confirmação de uma revisão do plano
    ↓
Execução dos slices conforme suas dependências
    estruturas → comportamento → contrato → modelo → organização Rust
    → implementação por função → testes → comparação → verificação do slice
    ↓
Verificação final do conjunto e checkpoint
```

A arquitetura define o que será migrado e quem responde por cada função. A organização Rust posterior define arquivos, módulos e APIs a partir dos contratos e modelos confirmados. São decisões distintas, com responsabilidades explícitas.

## Revisão do ciclo de execução dos slices

Revisão solicitada pelo usuário em 2026-10-18 após problemas na primeira migração. As observações abaixo resultam da leitura do código atual; não constituem reprodução dos incidentes daquela migração, cujos artefatos não foram fornecidos.

### Fragilidades identificadas na implementação anterior (linha de base)

| Ponto observado | Consequência possível | Exigência para a evolução |
|---|---|---|
| `workflow.reconcile` retorna à fase genérica `function_recovery` quando os pré-requisitos estão válidos e ainda existem funções incompletas; não deriva diretamente a ação da função ativa | A função pode continuar marcada como ativa enquanto a fase e a orientação indicam selecionar outra unidade | Usar um único cálculo de próxima ação que considere os marcos válidos da função e do slice |
| `select-function` escolhe funções incompletas do mapa local; `FunctionMap.select_next` mantém outra lógica, restrita a funções planejadas | Duas regras de seleção podem divergir, especialmente na retomada | Manter uma única regra de seleção e retomada, usada por CLI, reconciliação e relatórios |
| `compare failed` marca função, slice e estado global como bloqueados; `require_action` e `reconcile` respeitam o bloqueio global | Um problema local impede ações que poderiam corrigir a própria causa | Definir alcance do impedimento e permitir ações corretivas aplicáveis sem desbloqueio textual genérico |
| `next` reabre o trabalho a partir de uma descrição diferente e redefine tentativas/erro por atualizações separadas | Trocar a orientação pode reabrir trabalho sem demonstrar que o requisito voltou a ser válido | Retomar pela revalidação do requisito ou pelo registro de uma correção explícita, preservando histórico |
| Progresso, manifesto, comparação e contexto são escritos em operações distintas; escrita atômica de cada arquivo não torna o comando inteiro atômico | Uma interrupção pode deixar revisões divergentes, apesar de haver lock durante parte das operações | Publicar cada alteração canônica como uma unidade coerente e reconstruir projeções a partir dela |
| O wrapper de `verify-slice` executa guard, gates, promoção, checkpoint e reconciliação em chamadas separadas | Interrupções podem deixar um slice promovido sem checkpoint ou avanço; repetir o comando pode ser recusado por já não ser o slice ativo | Tornar a conclusão retomável e idempotente, incluindo checkpoint e avanço sem repetir a promoção |
| `migration-gate.sh` encerra na primeira falha e executa verificações Cargo do workspace inteiro | O usuário pode não distinguir uma falha da função ativa de um problema em outro crate/slice | Persistir resultados por gate e explicitar alcance, entradas, comando e causa do impedimento |
| Algumas mensagens posteriores à implementação ou evidência deixam `next_command` vazio; `status-slices` usa orientações genéricas para etapas restantes | O usuário sabe que falta algo, mas precisa inferir qual ação executar | Emitir sempre uma ação concreta ou uma tarefa de análise bem delimitada com artefato e requisito |

Referências históricas, substituídas pela implementação nova: `tools/workflow.py` (`reconcile`, `operate`, `require_action`, `set_transition`), `tools/function_recovery.py` (`select_next`), `scripts/ctrust-state.sh` (`next`, `failed`, `verify-slice`) e `scripts/migration-gate.sh`.

### Contrato do ciclo proposto

Evitar acrescentar uma máquina de estados para cada problema. Persistir fatos e resultados com suas revisões; calcular a situação apresentada, os impedimentos e a próxima ação em uma única função de decisão. Os nomes abaixo descrevem marcos e critérios, não obrigam a criar novos enums ou arquivos.

| Momento do ciclo | Condição para avançar | Feedback necessário | Tratamento de falha ou retomada |
|---|---|---|---|
| Entrada no slice | Arquitetura confirmada, responsabilidade definida e dependências exigidas verificadas | Funções próprias, referências reutilizadas, dependências e primeiro requisito pendente | Informar a dependência e o responsável; não criar trabalho duplicado nem mudar de slice silenciosamente |
| Estruturas e comportamento | Artefatos analisados e confirmados para as entradas atuais | Requisitos confirmados, pendentes e inválidos, com caminho do artefato | Retomar o primeiro requisito pendente; não regenerar análise confirmada para resolver erro de estado |
| Contrato, modelo e organização Rust | Encadeamento válido entre revisões dos artefatos | Artefato a completar, motivo e destinos planejados | Se uma entrada mudou, mostrar quais resultados perderam validade e por quê |
| Seleção ou retomada de função | Função pertence ao slice; dependências e planejamento permitem a ação | Identidade, proprietário, último marco válido e ação exata | Priorizar função ativa incompleta; reutilizar função verificada válida; diagnosticar divergência sem reiniciar a função |
| Implementação | Código e vínculo de origem/destino registrados | Implementação registrada; testes ainda pendentes | Repetição com mesmas entradas é informativa; alteração de código invalida os resultados dependentes |
| Testes e evidências | Execução e proveniência válidas para a implementação atual | Casos/categorias aprovados, ausentes ou reprovados e comando executado | Evidência malformada permite correção da evidência; divergência comportamental exige investigação e pode requerer correção do código |
| Comparação | Evidências suficientes e classificação da referência coerente | Paridade demonstrada ou verificação limitada ao contrato, com limitações | Preservar execução anterior para auditoria; revalidar apenas resultados ainda aplicáveis |
| Verificação do slice | Funções próprias válidas, referências válidas e gates aplicáveis aprovados | Resultado por gate, alcance local/workspace e próximo impedimento | Falha de build/teste não apaga funções concluídas, mas pode tornar sua verificação desatualizada conforme entradas afetadas |
| Conclusão e próximo slice | Resultado de verificação corresponde às revisões atuais | Slice concluído, checkpoint, totais e próximo slice elegível | Interrupção retoma a finalização pendente; repetição não duplica checkpoint nem promoção |

Regras obrigatórias:

1. **Uma decisão de execução:** `status`, `resume`, seleção e execução devem concordar sobre a próxima ação para as mesmas entradas. Consultas não alteram estado.
2. **Erro não é progresso:** uma falha possui alcance, causa e requisito; não substitui o último marco válido. Distinguir ausência de evidência, resultado reprovado, entrada desatualizada, erro de ferramenta e inconsistência interna.
3. **Correção localizada:** corrigir formato de evidência não exige reimplementar. Corrigir código exige refazer testes e comparação afetados. Revisar contrato/modelo pode invalidar implementação dependente. Registrar o motivo de cada invalidação.
4. **Recuperação sem editar YAML operacional:** comandos corretivos validam a causa resolvida; mudar uma mensagem ou limpar um contador não comprova recuperação. Inconsistência interna deve produzir diagnóstico e recuperação determinística quando houver fonte canônica suficiente; caso contrário, exigir revisão explícita sem adivinhar fatos.
5. **Persistência coerente:** validar a operação antes de publicar a mudança canônica; falha de publicação preserva a revisão anterior. Relatórios e contexto podem ser regenerados e não governam a execução. Escolher em E01 o mecanismo mínimo suficiente; não exigir uma arquitetura de eventos distribuídos.
6. **Validade durante gates:** associar a verificação às revisões de arquitetura, artefatos e código efetivamente verificadas. Se mudarem durante a execução, não promover o slice com aquele resultado. Definir fronteira de lock/revalidação em E01.
7. **Falha registrada uma vez:** erros de ferramentas e validações devem ter resultado estruturado, sem depender de o agente chamar `failed` depois. Tentativas pertencem à operação e às entradas pertinentes; consultar o status não consome tentativas.
8. **Conclusão retomável:** a verificação e a finalização não dependem de repetir manualmente uma sequência frágil de comandos. Um checkpoint narrativo pode ser enriquecido depois; sua ausência não pode produzir conclusões contraditórias ou apagar aprovação já publicada.
9. **Encerramento definido:** distinguir todas as funções verificadas, slice verificado e migração concluída. Definir o critério da revisão final e uma operação de encerramento idempotente, evitando permanecer indefinidamente em uma fase genérica de verificação.
10. **Feedback contínuo:** apresentar início de cada ação/gate, resultado e próxima ação, inclusive em erro. Não deixar longos builds ou testes sem identificar o que está executando; registrar duração e localização do log ao terminar.

Exemplo de feedback desejado, com números ilustrativos:

```text
Slice: position — 3/5 funções próprias verificadas; 2 referências reutilizadas
Função: calculate_position — implementação registrada, comparação pendente
Última ação: validar evidência — recusada
Causa: falta categoria boundary em evidence/tests/calculate_position.ndjson
Impacto: comparação desta função impedida; implementação preservada
Próxima ação: executar o caso boundary e registrar a evidência completa
Comando de registro: <comando público definido em E01, com argumentos resolvidos>
Gate do workspace: ainda não executado
```

O feedback deve usar a mesma revisão de dados em todas as linhas. Contagens por marco e por impedimento devem deixar claro que uma função pode ter implementação válida e um impedimento de teste; essas contagens não devem ser somadas como categorias mutuamente exclusivas.

## Tabela de acompanhamento

Status permitidos: **planejada**, **em progresso**, **terminada**. Um impedimento deve ser registrado nas notas da etapa; não substitui seu status. Manter no máximo uma etapa em progresso.

| Etapa | Entrega | Dependências | Status |
|---|---|---|---|
| E00 | Registrar decisões, sequência e critérios neste plano | — | terminada |
| E00-R1 | Analisar execução dos slices e detalhar consistência, recuperação e feedback neste plano | E00 | terminada |
| E01 | Especificar o modelo canônico e as regras de execução | E00 | terminada |
| E02 | Corrigir e estabilizar o analisador C necessário ao inventário | E01 | terminada |
| E03 | Produzir inventário global com identidade única das funções | E01, E02 | terminada |
| E04 | Implementar plano de arquitetura e validação das decisões | E03 | terminada |
| E05 | Implementar progresso canônico e recuperação por função | E01, E04 | terminada |
| E06 | Integrar execução por slice, artefatos e gates ao novo modelo | E04, E05 | terminada |
| E07 | Unificar feedback de progresso e diagnósticos | E05, E06 | terminada |
| E08 | Adicionar seleção e reagrupamento na saída HTML do analisador | E04, E07 | terminada |
| E09 | Validar o fluxo completo e a recuperação em cenários reais | E02–E08 | terminada |
| E10 | Concluir remoção do fluxo anterior e atualizar documentação | E09 | terminada |

A sequência técnica começa pelo inventário confiável porque a arquitetura depende dele. A simplificação do progresso continua sendo a principal prioridade funcional e deve orientar as decisões desde E01.

## Detalhamento das etapas

### E01 — Modelo canônico e regras de execução

Tarefas:

- Definir schemas e caminhos dos três conjuntos canônicos: inventário, plano de arquitetura e progresso por função.
- Definir a versão do novo schema e a rejeição explícita de estados anteriores.
- Estabelecer identidade única de função, distinguindo símbolos globais, funções `static` e contexto de unidade de tradução quando necessário. Não usar apenas o nome ou a linha como identidade.
- Registrar localização e fingerprint da origem separadamente da identidade; mudanças de origem devem produzir diagnóstico e revisão, sem inventar correspondências ou preservar validade indevidamente.
- Definir quais marcos de progresso serão persistidos e quais informações serão derivadas. Evitar múltiplos campos editáveis representando a mesma condição.
- Separar progresso concluído, resultado de validação e impedimentos. Uma falha deve preservar o último marco válido e apontar a ação de recuperação.
- Definir repetição idempotente dos comandos: mesmas entradas já registradas retornam sucesso informativo; entradas divergentes exigem revisão explícita.
- Definir revisão do plano, fingerprints de evidências e política de invalidação quando origem, arquitetura ou implementação mudarem.
- Definir o contrato público do CLI, reduzindo comandos e evitando que o usuário precise editar estado operacional para recuperar o fluxo.
- Especificar o contrato do ciclo descrito nesta revisão: decisão única de próxima ação, alcance de impedimentos, publicação coerente, conclusão retomável e encerramento global. Documentar uma matriz de falha → fatos preservados → resultados invalidados → ação corretiva.

Conclusão: schemas, regras e exemplos documentados, incluindo função compartilhada, função já verificada, repetição de comando, falha de evidência e mudança de entradas. A implementação posterior deve seguir este contrato.

### E02 — Analisador C confiável

Tarefas:

- Reproduzir e classificar as falhas existentes, distinguindo defeitos do código, expectativas inadequadas dos testes e limitações intencionais da análise estática.
- Corrigir cache e identidade entre arquivos/projetos, invalidação de versão e recuperação de cache corrompido.
- Corrigir ou delimitar resolução de callbacks, escopos locais, funções em headers e funções `static`.
- Corrigir identificação de estruturas, aliases, referências de tipos e raízes não resolvidas ou ambíguas necessárias ao inventário.
- Preservar incerteza: macros, pré-processamento e chamadas indiretas não podem virar resolução confirmada sem suporte real.
- Atualizar documentação e testes conforme o comportamento suportado, sem apagar testes apenas para obter aprovação.

Conclusão: a suíte do analisador passa para o contrato suportado, limitações estão explícitas e não há perda silenciosa de dependências ou colisão de identidade nos cenários cobertos.

### E03 — Inventário global do escopo

Tarefas:

- Desacoplar descoberta de funções da necessidade de um manifesto definitivo de slices.
- Consolidar múltiplos headers e raízes em um catálogo único, preservando grafo, estruturas, origem e confiança da resolução.
- Garantir que uma função compartilhada apareça uma única vez no catálogo.
- Registrar dependências externas, ambíguas e não resolvidas como pendências classificáveis, sem tratá-las automaticamente como funções a portar.
- Manter exportação determinística, publicação segura e fingerprints do inventário.

Conclusão: duas raízes que chamam a mesma função geram uma única identidade e todas as relações de chamada pertinentes. O inventário pode ser produzido antes dos slices.

### E04 — Plano de arquitetura

Tarefas:

- Permitir selecionar funções, criar e renomear slices, reagrupar funções e definir tratamento: migrar, adaptar, simular, excluir ou referenciar uma implementação canônica existente no mesmo plano.
- Separar responsabilidade de implementação e relação de consumo. Uma referência não cria outro proprietário nem outra unidade de progresso.
- Sugerir funções compartilhadas e agrupamentos coesos com base no grafo, explicando os consumidores identificados.
- Exigir exatamente um responsável para cada função selecionada para port.
- Exigir decisão explícita para dependências necessárias que não serão migradas.
- Derivar dependências entre slices e detectar ciclos. Ciclos devem ser resolvidos por reagrupamento ou decisão arquitetural explícita; não alterar silenciosamente a ordem.
- Validar integridade das referências, cobertura das decisões e revisão do inventário de origem.
- Implementar confirmação do plano e política explícita para revisões posteriores, preservando evidências ainda válidas e invalidando apenas o que depende da alteração.
- Disponibilizar primeiro um formato estruturado e comandos de validação/aplicação, reutilizados depois pela interface HTML.

Conclusão: um plano confirmado representa completamente o recorte selecionado; uma função compartilhada pertence a um único slice, e seus consumidores dependem dele.

### E05 — Progresso e recuperação por função

Tarefas:

- Substituir cópias de progresso nos mapas por slice por um registro canônico por função.
- Derivar contexto ativo e próxima ação dos registros válidos, sem depender de combinações de campos duplicados.
- Retomar implementação, testes ou comparação conforme o último marco válido.
- Reutilizar funções verificadas quando origem, implementação e evidências continuam válidas.
- Tornar registros repetidos idempotentes e evitar duplicação de evidências.
- Registrar falhas com código, requisito, função afetada, detalhe e ação corretiva. Restringir bloqueios globais a impedimentos realmente globais.
- Preservar limites de tentativas onde úteis, sem contabilizar novamente a mesma falha nem confundir validação com execução mecânica.
- Garantir persistência segura e retomada após interrupção entre operações.
- Substituir as regras duplicadas de seleção/retomada por uma única decisão; testar que reconciliação não devolve uma função em testes ou comparação para uma seleção genérica incompatível.
- Implementar registro automático e único de falhas e recuperação orientada ao requisito, sem depender de alterar `next_action` para reabrir o fluxo.

Conclusão: uma falha de evidência não apaga implementação válida; uma função concluída não é selecionada novamente em outro slice; uma sessão nova identifica a mesma próxima ação sem depender da conversa.

### E06 — Execução por slice e gates

Tarefas:

- Fazer os slices consumirem o plano confirmado e referências ao catálogo global.
- Adaptar inventários locais, comportamento, contratos, modelos e organização para distinguir funções próprias e dependências reutilizadas.
- Manter estruturas e comportamento confirmados antes de contrato/modelo e organização Rust antes da implementação.
- Substituir a escolha implícita de proprietário por ordem de slice pela responsabilidade definida na arquitetura.
- Integrar evidências e comparação ao registro canônico, preservando a distinção entre paridade com referência executável e verificação contra contrato sem referência legada.
- Derivar conclusão dos slices sem exigir que consumidores repitam implementação e comparação de funções compartilhadas.
- Integrar CLI, wrappers Bash, checkpoints e reconciliação, sem criar uma segunda máquina de execução concorrente.
- Preservar gates de segurança, arquitetura, completude, rastreabilidade e build/testes Cargo.
- Implementar o contrato do ciclo de slices desta revisão, com resultados estruturados por gate e promoção vinculada às revisões verificadas.
- Unificar a coordenação da verificação, finalização, checkpoint e seleção seguinte no controlador; wrappers apenas despacham operações. Garantir repetição segura após interrupção em cada fronteira.
- Definir e implementar o encerramento global após revisão final, distinguindo-o da conclusão de funções e slices.

Conclusão: um slice base é implementado e verificado uma vez; slices consumidores reutilizam seus símbolos e verificam suas próprias responsabilidades e integrações.

### E07 — Feedback e diagnóstico

Tarefas:

- Usar uma única projeção para `status`, `resume`, relatórios e dados da futura interface.
- Mostrar funções únicas selecionadas, verificadas, implementadas aguardando testes, pendentes e com impedimentos, sem dupla contagem.
- Mostrar slice/função atual, último marco válido, impedimento, próxima ação e comando aplicável.
- Distinguir progresso de implementação de verificação e informar o tipo de referência utilizado.
- Exibir revisão da arquitetura, entradas desatualizadas e evidências invalidadas quando aplicável.
- Oferecer saída legível e estruturada para automação. Consulta de status não deve alterar progresso.
- Emitir feedback por ação/gate no início e no término, incluindo falha: marco preservado, alcance, requisito, comando/log e próxima ação resolvida. Se a próxima ação for analítica, identificar artefato e decisão necessários.
- Mostrar separadamente funções próprias, referências reutilizadas, validação do slice e validação do workspace, sem atribuir automaticamente todo erro Cargo à função ativa.

Conclusão: um usuário consegue identificar o que terminou, o que falta, por que parou e como continuar sem inspecionar diversos YAMLs.

### E08 — Seleção e reagrupamento no HTML

Tarefas:

- Estender a saída HTML do `header_dependency_analyzer`, reutilizando catálogo, grafo e schema de arquitetura.
- Adicionar busca, filtros, seleção de funções, edição de tratamento, criação de slices e atribuição em lote.
- Mostrar origem, callers/callees, consumidores, proprietário e incertezas relevantes.
- Apresentar sugestões de slices compartilhados e erros de atribuição ou ciclos antes da confirmação.
- Permitir exportar e reabrir um plano estruturado. O controlador valida e aplica o arquivo; o navegador não deve fingir que salvou estado operacional no projeto.
- Reutilizar o mesmo validador do CLI na aplicação do plano, sem regras divergentes entre interface e backend.
- Exibir progresso derivado em relatórios regenerados, deixando clara a revisão representada. Não exigir servidor apenas para consultar ou editar o plano exportável.

Conclusão: o usuário consegue selecionar funções, agrupá-las em slices e exportar um plano que passa pela validação e pode ser aplicado pelo controlador.

### E09 — Validação integrada

Cenários obrigatórios:

1. Migração nova, do escopo à revisão final.
2. Dois slices consumidores e uma função compartilhada em slice base, sem duplicação de implementação ou contagem.
3. Retomada de função já implementada, testada ou verificada.
4. Falha de evidência e correção sem perda do último marco válido.
5. Repetição de comandos concluídos com entradas iguais e divergentes.
6. Interrupção de processo durante persistência e recuperação em nova sessão.
7. Reagrupamento após confirmação, com revisão explícita e invalidação das dependências afetadas.
8. Dependência excluída sem tratamento suficiente, ciclo entre slices e função sem proprietário.
9. Origem ou implementação alterada após verificação, sem reutilização indevida da aprovação anterior.
10. Verificação com e sem referência C executável, mantendo classificações distintas.
11. Plano exportado pelo HTML, reaberto, validado e aplicado.
12. Rejeição clara do schema anterior, sem tentar converter estado.
13. Reconciliação e retomada imediatamente após implementação, testes e comparação: próxima ação consistente com o marco válido em todos os relatórios.
14. Função já verificada reaparece como dependência: reutilização sem nova unidade, reimplementação ou duplicação de contagem.
15. Evidência malformada versus divergência de resultado: recuperação distinta e invalidação apenas do que foi afetado.
16. Interrupção entre verificação, promoção, checkpoint e avanço: finalização retomável, sem duplicação e sem dois slices ativos.
17. Falha de um gate do workspace causada por outro componente: alcance e causa explícitos, sem apagar progresso da função corrente.
18. Mudança de entradas durante gates: resultado antigo não promove a revisão nova do slice.
19. Falha de gravação e relatórios desatualizados: preservação da revisão canônica e regeneração coerente do feedback.
20. Todos os slices verificados: revisão final e encerramento global executáveis, idempotentes e claramente apresentados.

Adaptar o exemplo `bounded-strings` ao novo fluxo e acrescentar um exemplo pequeno com dependência compartilhada. Usar casos executáveis C/Rust para verificar comportamento, além dos testes de controle e persistência.

Conclusão: suítes pertinentes aprovadas, exemplos executáveis com relatório e evidências, e retomada demonstrada sem intervenção manual em arquivos de estado.

### E10 — Remoção do fluxo anterior e documentação

Tarefas:

- Concluir a remoção de `upgrade-state`, aliases legados, conversores de mapas e caminhos substituídos. Remover código antigo durante as etapas anteriores sempre que sua substituição estiver pronta; não esperar E10 para manter duas implementações.
- Revisar scripts, templates, agentes e skills para que todos descrevam o mesmo fluxo.
- Atualizar README, comandos públicos, instalação e requisitos.
- Documentar início de uma nova migração, arquitetura, recuperação e significado dos relatórios.
- Garantir que distribuição e atualização do toolkit não deixem caminhos antigos ativos. Não apagar silenciosamente artefatos de projetos externos.
- Executar a verificação estrutural, as suítes pertinentes e os exemplos finais.

Conclusão: existe um único fluxo suportado e documentado, sem compatibilidade operacional com estados anteriores.

## Evidências e ponto de partida

Na leitura inicial desta sessão:

- `bash sync.sh verify`: aprovado.
- Suíte em `tests/`: 71 testes aprovados.
- Suíte em `tests/function_recovery/`: 5 testes aprovados.
- Suíte em `tests/organization/`: 8 testes aprovados.
- Suíte do analisador: 30 testes executados, com 19 falhas e 3 erros; 8 passaram.
- Entre os problemas observados estão testes referenciando `parse_file_bundle`, ausente no código atual, cache corrompido sem recuperação e falhas de resolução/estruturas.

Esses resultados são uma linha de base, não uma certificação da versão nova. Não executar correções indiscriminadas antes de classificar o contrato esperado em E01/E02.

Arquivos centrais da implementação nova:

- `docs/workflow-v2.md`: contrato do schema 3 e decisões de persistência/recuperação.
- `tools/migration/store.py` e `transaction.py`: validação do snapshot, lock e publicação atômica.
- `tools/migration/core.py`: arquitetura, dependências, decisão única e projeção de progresso.
- `tools/migration/inventory.py`: catálogo global e análise local das funções próprias.
- `tools/migration/execution.py`: preparação, registros, evidências e gates.
- `tools/migration/cli.py` e `html.py`: interface pública e editor autônomo.
- `tools/header_dependency_analyzer/`: análise C e exportação global; `--architecture` gera o editor junto da análise.
- `tests/test_migration.py`, `tests/test_sync.py` e testes do analisador: contrato novo, distribuição e regressões.
- `examples/bounded-strings/` e `examples/shared-functions/`: demonstrações executáveis e reproduzíveis.
- `sync.sh`, wrappers restantes, templates, agentes e skills: distribuição e protocolo único.

`workflow_stages.py`, `function_recovery.py`, `manifest_schema.py`, `inventory_transaction.py`,
`organization/`, `artifact_sync/`, gates antigos e respectivos testes de schema anterior foram retirados.
`tools/workflow.py` é somente uma entrada para o CLI novo. Não existe execução paralela do protocolo antigo.

## Protocolo de retomada e atualização

1. Ler este arquivo e verificar `git status` antes de trabalhar.
2. Consultar a tabela e as notas finais. Retomar a etapa em progresso; se não existir, iniciar a primeira planejada cujas dependências estejam terminadas.
3. Marcar a etapa como **em progresso** ao iniciar trabalho efetivo. Não marcar etapas de implementação apenas por discutir seu desenho.
4. Atualizar tarefas, decisões técnicas, arquivos alterados e validações na seção abaixo durante a execução.
5. Marcar **terminada** apenas quando a entrega e seus critérios estiverem atendidos. Não usar status de conclusão para esconder limitações.
6. Ao encerrar a sessão, registrar impedimentos, último resultado e próxima ação concreta. Este plano deve permitir continuar sem recuperar a conversa.
7. Não ampliar escopo para compatibilidade com migrações anteriores. Se surgir uma decisão nova que altere o plano, registrá-la explicitamente.

## Registro de execução e próxima ação

- **2026-10-18 — E00 terminada:** plano registrado conforme as decisões do usuário. Nenhuma implementação da evolução foi iniciada.
- **2026-10-18 — E00-R1 terminada:** ciclo atual analisado no código; fragilidades, contrato por momento do ciclo, regras de feedback/consistência e cenários de aceitação incorporados ao plano. E01, E05, E06, E07 e E09 ampliadas; implementação continua planejada. Esta revisão não reproduziu a primeira migração nem executou novas suítes, pois altera somente o plano.
- **2026-10-18 — E01 terminada:** contrato definido em `docs/workflow-v2.md`; schema 3, snapshot único, identidade sem linha, invalidação, idempotência, recuperação e encerramento explícitos.
- **2026-10-18 — E02 terminada:** corrigidos cache, callbacks/escopos, efeitos globais, estruturas/aliases, seleção multi-header, raízes sem resolução e funções estáticas em headers por unidade de tradução. Os 30 testes do analisador passaram. Análise continua estática e não executa o pré-processador.
- **2026-10-18 — E03–E08 terminadas:** implementados catálogo global, arquitetura validada, snapshot atômico, decisão única de retomada, registros idempotentes, gates Cargo/arquitetura, encerramento e editor HTML. Testes cobrem dependência compartilhada, mudança de entradas, evidências inválidas, processo interrompido e CLI instalado. `bounded-strings` executou 324 comparações e encerrou a migração. Detalhamento final de validação em E09/E10.
- **2026-10-18 — E09 terminada:** 64 testes aprovados e 336 comparações executáveis C/Rust aprovadas; relatórios, comandos e limites registrados abaixo.
- **2026-10-18 — E10 terminada:** protocolo anterior, conversores e testes específicos do schema antigo removidos; CLI, wrappers, agentes, skills, templates, README e exemplos usam somente schema 3. Distribuição nos sete destinos e preservação de dados externos verificadas.
- **Etapa ativa:** nenhuma.
- **Próxima etapa:** nenhuma pendente neste plano.
- **Próxima ação concreta:** para usar a versão implementada, seguir o README em um destino novo. Uma próxima sessão deve ler este registro e os relatórios, sem reiniciar a implementação já concluída.
- **Impedimentos conhecidos:** nenhum. As falhas iniciais do analisador foram corrigidas.
- **Situação da implementação:** E00–E10 e E00-R1 terminadas; o dispatcher executa exclusivamente o controlador schema 3. Sem commit ou publicação solicitados nesta sessão.

## Evidências finais da implementação

- Workflow/distribuição: **32 testes aprovados** em `tests/` (26 de migração e 6 de instalação/atualização).
- Analisador: **32 testes aprovados**, incluindo regressões iniciais, cache JSON corrompido e exportação do editor.
- `bash sync.sh verify`: aprovado. Verifica estrutura distribuída, sintaxe Bash e compilação Python.
- `git diff --check`: aprovado.
- Frontmatter YAML dos três agentes e das treze skills: validado.
- Exemplo bounded-strings: **324 comparações C/Rust aprovadas**, dois slices verificados e migração encerrada. Relatório desta execução: `/tmp/ctrust-v2-acceptance-strings/REPORT.md`.
- Exemplo shared-functions: **12 comparações C/Rust aprovadas**, uma implementação base, dois consumidores, três funções únicas verificadas e migração encerrada. Relatório: `/tmp/ctrust-v2-acceptance-shared/REPORT.md`.
- Os exemplos preservam comandos, saídas, referências e evidências em seus diretórios de execução. Caminhos em `/tmp` são evidências locais desta sessão, não artefatos permanentes do repositório; os runners permitem reproduzi-las.
- Editor validado com execução JavaScript em Node e DOM simulado: seleção, filtro, criação/remoção de slice, reagrupamento, exportação, reabertura e validação do plano exportado no backend. Não foi feita inspeção visual em navegador real.

### Refinamentos do ciclo implementados durante a validação

- Implementar as funções próprias do slice antes de registrar testes evita compilar callers com callees ainda ausentes. O controlador prioriza funções ainda não registradas.
- Registrar um arquivo atualiza também o fingerprint de funções irmãs já registradas sob a mesma preparação, após validar suas origens. Não exige registrar manualmente cada irmã a cada acréscimo de código; evidências antigas ficam desatualizadas conforme necessário.
- Consumidores dependem dos contratos/implementação da base e da validade de sua prova atual. Corrigir apenas evidência da base não reinicia a implementação dos consumidores.
- Organização confirma existência e associação do crate ao workspace antes do port; erros de layout deixam de aparecer somente no gate final.
- Tipos e comportamento gerados para revisão ficam limitados às funções próprias; dependências entram como referências, sem outro progresso.
- Resultados diferenciam paridade executável de verificação por contrato e separam aprovação funcional do slice da atualidade dos inputs Cargo.
- Falhas são registradas automaticamente e deduplicadas; não há contador global de tentativas. O fluxo não executa retries automáticos, portanto não precisa de um bloqueio global para limitar loops de recuperação.
- Logs de gates têm diretórios únicos por execução e resultados anteriores permanecem no histórico. Snapshots com fingerprints internos divergentes são rejeitados antes da publicação.

### Limitações deliberadas

- Análise C estática, sem pré-processador. Macros, condicionais, includes externos, callbacks e aliasing ainda exigem revisão; incerteza não vira aprovação automática.
- Fingerprint Rust por arquivo: alteração de código pode exigir novos testes de funções irmãs. O registro conjunto evita repetição manual de registros, mas não afirma isolamento semântico entre funções.
- Mudanças de inventário/arquitetura e de preparação usam invalidação conservadora; alterações de evidência têm alcance mais localizado.
- O controlador valida registros e executa gates Cargo, mas não autentica sozinho que um comando declarado em NDJSON foi executado nem substitui revisão humana dos contratos. Os dois runners executam efetivamente as referências C/Rust.
- O editor é um artefato autônomo de uma revisão; exige exportação e confirmação pelo CLI e não altera o workspace diretamente.
- Schemas anteriores são rejeitados sem conversão. Novas migrações devem usar destino novo; instalação/atualização não apaga estados externos.
