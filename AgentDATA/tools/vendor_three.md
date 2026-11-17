# vendor_three

- Data: 2026-11-14; status: disponivel; dono: manutencao do painel.
- Origem: scripts/vendor_three.py, ferramenta propria; runtime Python3.12, so biblioteca padrao.
- Objetivo: importar o subconjunto Three.js0.186.1 necessario para a oficina, sem Node/npm.
- Usar para reproducao/atualizacao controlada; nao usar como instalador generico de pacotes.

```bash
uv run python scripts/vendor_three.py --help
uv run python scripts/vendor_three.py --version 0.186.1 --dest /tmp/three-revisao
```

Entradas: versao fixada e diretorio novo/vazio. Le metadados e tarball exclusivamente do
registo npm por HTTPS, com timeout60s e limite30MiB. Exige que URL e integridade publicada
correspondam as constantes revistas. SHA512 verificado antes de abrir o tar; apenas nomes
exatos, ficheiros regulares, sem caminhos inseguros ou links. Nao executa codigo descarregado.

Saidas:18 ficheiros (licenca/builds/addons) e README com SHA256 de origem/local e reescrita.
Unica alteracao: imports/exports nus three passam a caminhos relativos ao build local.
O destino nao pode estar preenchido; nunca sobrescreve ficheiros existentes. Uma falha de
escrita pode deixar o diretorio de geracao incompleto: rever e usar outra pasta vazia.

Codigos:0 sucesso;2 argumentos, rede, integridade, arquivo ou escrita invalidos. Nenhum
segredo ou variavel de ambiente propria. Os proxies HTTPS da biblioteca padrao podem aplicar-se.
Efeitos: pedidos GET ao registo e escrita na pasta indicada; nenhuma base de dados ou chamada LLM.

Validacao:

```bash
uv run pytest tests/test_vendor_three.py tests/panel/test_oficina_static.py -q
```

O ultimo teste precisa de TEST_DATABASE_URL exclusivo. Nunca pytest concorrente na mesma base.
Ao atualizar: conferir versao da referencia, origem, integridade, licenca e fecho de imports;
gerar em pasta temporaria, rever diff (apenas especificadores), hashes e testes antes de substituir.
Proveniencia publicada em src/marketing_agent/panel/static/vendor/three/README.md.
