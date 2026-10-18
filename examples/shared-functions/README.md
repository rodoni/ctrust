# Uma função base e dois consumidores

```bash
.venv/bin/python examples/shared-functions/run.py --output /tmp/ctrust-shared-v2
```

O diretório deve estar vazio. O exemplo cria C e Rust pequenos e previamente
revisados: `common` valida negativos e soma um; `first` reutiliza o resultado;
`second` preserva o erro ou soma mais um. Um único slice `base` possui `common`.
Entradas maiores que 2147483645 são excluídas para não executar overflow signed C.

Compila executáveis C e Rust, compara 12 casos normais/limites/erros, confirma a
preparação por slice e executa todos os gates e encerramento. O relatório demonstra
três funções únicas verificadas e duas referências reutilizadas, com uma única
implementação da função compartilhada. Não é um tradutor automático.

`REPORT.md`, `summary.json`, `commands.ndjson`, `architecture.yaml` e o snapshot,
evidências, checkpoints e HTML em `rust/.ctrust/` permitem inspecionar o resultado.
