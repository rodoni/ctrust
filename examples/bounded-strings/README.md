# Teste real de conversão C → Rust

Este exemplo percorre o workflow do ctrust com duas funções reais da libc do
OpenBSD: [`strlcpy`](https://github.com/openbsd/src/blob/master/lib/libc/string/strlcpy.c)
e [`strlcat`](https://github.com/openbsd/src/blob/master/lib/libc/string/strlcat.c).
É um recorte de duas rotinas, não uma conversão da libc inteira. A licença e os
avisos de copyright foram preservados nos arquivos C.

As rotinas têm contratos pequenos, mas exercitam ponteiros, ownership, buffers,
NUL, retorno de comprimento, truncamento, bytes não UTF-8 e limites de capacidade.
A implementação Rust usa `&mut [u8]` e `&CStr`, sem `unsafe`, FFI ou dependências Cargo.

## Executar

Na raiz do ctrust, com `cc`, Cargo, Rust e rustfmt disponíveis:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/header_dependency_analyzer/requirements.txt
.venv/bin/python examples/bounded-strings/run.py
```

Cada execução cria uma pasta nova em `examples/bounded-strings/runs/`, imprimindo
o caminho de `REPORT.md`. Os resultados não entram no Git.

Para baixar os arquivos diretamente do upstream em vez de usar o snapshot:

```bash
.venv/bin/python examples/bounded-strings/run.py --download
```

O download requer acesso à internet. Uma falha não usa silenciosamente o snapshot.
O runner verifica os identificadores de revisão das fontes revisadas antes de
reutilizar os contratos. Um diretório de saída alternativo pode ser informado:

```bash
.venv/bin/python examples/bounded-strings/run.py --output /tmp/ctrust-strings-test
```

O diretório deve estar vazio; resultados anteriores nunca são apagados.

## Etapas executadas

1. Instalação do toolkit em um projeto Rust isolado e bootstrap do estado.
2. Escopo explícito, dois slices e um único Cargo workspace com crate `domain`.
3. Compilação da referência C executável, com `DEF_WEAK` desabilitado apenas para
   remover a infraestrutura de símbolos do OpenBSD.
4. Análise real de headers/calls, geração de grafo HTML e exportação de inventário.
5. Revisão dos tipos: ausência de aggregates confirmada, ponteiros e arrays
   mapeados para borrows seguros, ownership e aliasing registrados.
6. Comportamento e contratos revisados a partir do C, incluindo branches e limites.
7. Modelos Rust confirmados e organização dos módulos validada.
8. Implementação em arquivos por slice, testes Rust e retomada após implementação.
9. Comparação de 162 casos por função: 12 casos focados e 150 casos aleatórios
   com sementes fixas. Compara o retorno e **todos** os bytes do buffer, incluindo
   regiões que a operação deve preservar.
10. Evidências NDJSON, retomada no estágio de comparação, gates, checkpoints e
    avanço para o slice seguinte; ao final, auditoria com `--all`.

Há também testes negativos: contrato antes de estruturas/comportamento,
modelo antes de confirmar contrato, implementação com modelo draft e comparação
sem evidência devem retornar código 3 e preservar o progresso.

O runner materializa contratos, modelos e implementação **revisados para este
exemplo**. Ele não é um tradutor automático aplicável a bibliotecas arbitrárias.
A dependência externa `strlen` é classificada como `adapt`, com equivalente
`CStr::to_bytes().len()`, preservando o inventário original para auditoria.

## Inspecionar os resultados

Dentro da pasta de execução:

- `REPORT.md` e `summary.json`: resultado e etapas.
- `sources.json`: origem, modo de aquisição, revisão e SHA256 das fontes locais.
- `analysis/hierarchy.html`: árvore interativa de chamadas C.
- `rust/crates/domain/src/`: implementação Rust.
- `rust/.ctrust/inventory/slices/`: inventários, comportamento e mapeamentos.
- `rust/.ctrust/contracts/units/`: contratos.
- `rust/.ctrust/plans/units/`: modelos e organização.
- `rust/.ctrust/evidence/tests/`: entradas, retorno e buffer de C/Rust por caso.
- `rust/.ctrust/checkpoints/`: checkpoints.
- `commands.ndjson`: comandos, resultados e saídas; códigos 3 são esperados apenas
  nos testes negativos explicitamente identificados.

Os dois slices terminam `verified`. O estado global permanece na revisão final
do workspace, conforme o protocolo do toolkit; não se inventa encerramento global.

## Origem e limites

O terminal do ambiente utilizado não conseguia resolver os hosts externos.
Os arquivos em `upstream/` foram obtidos pela consulta web das fontes públicas,
com whitespace normalizado ao salvar o snapshot; não são um clone nem um arquivo
byte a byte do repositório upstream. A lógica, comentários de licença e revisão
foram preservados. `legacy.h` é um header de teste local com as duas declarações.
`--download` permite repetir o teste com os bytes recebidos diretamente do upstream.

A paridade demonstrada vale para os casos executados e o comportamento definido
no contrato C: fonte legível até NUL e buffers válidos sem sobreposição. Não se
executam ponteiros inválidos, dangling pointers, overlap ou fontes sem NUL,
pois esses casos não têm uma referência C de comportamento definido.
Truncamento e ausência de NUL dentro do destino são falhas de capacidade definidas,
classificadas como casos `error`; não são exceções nem códigos de erro negativos.

Os resultados não demonstram suporte a callbacks, concorrência, shared memory,
structs/packing, grandes grafos de dependências ou migração de projetos inteiros.
