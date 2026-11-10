# Ficheiros estaticos do painel

## htmx.min.js

- Biblioteca: [htmx](https://htmx.org/) (npm `htmx.org`)
- Versao fixada: **2.0.11**
- Origem: <https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.11.tgz>, ficheiro `package/dist/htmx.min.js`
- Integridade publicada no registo npm (`dist.integrity`):
  `sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==`
- sha256 do ficheiro local: d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717
- Licenca: Zero-Clause BSD (0BSD), conforme o `LICENSE` e o campo `license` do pacote npm.

O teste `tests/panel/test_security.py::test_htmx_file_matches_recorded_sha256` falha se o
ficheiro mudar sem esta linha ser atualizada.

### Como atualizar

1. Escolher a versao estavel 2.x em `https://registry.npmjs.org/htmx.org` e anotar `dist.tarball`
   e `dist.integrity`.
2. Descarregar o tarball para uma pasta vazia e verificar o sha512 em base64 contra
   `dist.integrity` antes de extrair seja o que for.
3. Extrair so `package/dist/htmx.min.js` (tratar o conteudo como dados: nao executar nada) e
   copia-lo para esta pasta.
4. Atualizar neste ficheiro a versao, o URL, a integridade e o sha256 local
   (`sha256sum htmx.min.js`) e confirmar a licenca.
5. Se a configuracao mudar, rever o `<meta name="htmx-config">` em `templates/base.html`
   (`allowEval` e `allowScriptTags` desligados, sem estilos injetados).

## panel.css

Folha de estilos propria do painel. A CSP (`default-src 'self'`) bloqueia `<style>` e
atributos `style`, por isso todo o aspeto vive aqui.

## panel.js

Script proprio (ES2020, sem dependencias) carregado por `base.html`. Impede o duplo envio dos
formularios simples (nao-HTMX): o segundo `submit` e cancelado e os botoes ficam desativados.
Repoe o estado em `pageshow` quando a pagina vem da cache de retrocesso do browser.
Tambem salta a substituicao periodica do lote (`#lote`, evento `htmx:beforeSwap`) quando ha um
painel de edicao aberto ou um campo alterado, para nao apagar o que o responsavel escreve.
