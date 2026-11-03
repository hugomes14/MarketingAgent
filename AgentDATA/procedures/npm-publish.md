# Procedimento: publicacao npm

## Quando carregar

Carregar somente quando a tarefa envolver pacote npm, release, `npm publish`, validacao de tarball ou promocao para pacote publico.

## Regras de promocao para `public/mahout-bench`

- O nome oficial de novas features de CLI e `mahout-bench`, mesmo quando a implementacao inicial ainda estiver no pacote privado legado `elephant-prompt-bench`.
- Nenhuma feature nova deve ser copiada, publicada ou promovida para `public/mahout-bench` sem ordem explicita do usuario.
- So passa para `public/mahout-bench` o que o usuario mandar e ja tiver sido testado e validado no fluxo privado.
- Implementacao privada, validacao local e promocao publica sao etapas separadas.

## Versionamento

- Usar SemVer valido: `MAJOR.MINOR.PATCH`.
- Nao usar versoes invalidas como `0.0.5.1`.
- Nunca tentar republicar a mesma versao.
- Alinhar `package.json`, `CITATION.cff` e README quando houver versao explicita.

## Validacao canonica

Rodar a partir da pasta do pacote:

```bash
npm whoami
npm view <nome-do-pacote> versions --json
pnpm typecheck
pnpm test
pnpm build
npm pack --dry-run --json
```

Para CLI, validar tarball em diretorio temporario:

```bash
tmpdir=$(mktemp -d /tmp/npm-pack-XXXXXX)
npm pack --pack-destination "$tmpdir"
tarball=$(find "$tmpdir" -maxdepth 1 -name '*.tgz' -print -quit)
installdir=$(mktemp -d /tmp/npm-install-XXXXXX)
npm install --prefix "$installdir" "$tarball"
node "$installdir/node_modules/<nome-do-pacote>/<caminho-do-bin-js>" --help
```

## Publicacao

Publicar somente depois de validacao, diff revisado e commit coerente:

```bash
npm publish --access public
```

Se a conta exigir 2FA:

```bash
npm publish --access public --otp <codigo_2fa>
```

Depois de publicar:

```bash
npm view <nome-do-pacote> version
npm view <nome-do-pacote> versions --json
npm view <nome-do-pacote> dist-tags --json
```
