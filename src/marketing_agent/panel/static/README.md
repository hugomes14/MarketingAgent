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

## oficina/

Modulos ES proprios, sem build, importmap ou dependencias externas. Carregados apenas na
pagina da oficina, com a mesma CSP do painel:

- `estado.js`: GET sequencial a cada5s, If-None-Match, pausa quando oculto e espera10/20/40/60s
  em falhas; 401/403 termina a sondagem. Exporta detetarTransicoes e urlInterna; dados ficam
  na mesma origem (URLs com barras invertidas/controlos tambem sao recusadas).
- `coluna.js`: foco itinerante com setas/Home/End e ativacao por Enter; seleciona o agente,
  acompanha estados e pendencias e abre o parcial HTMX existente. Recargas serializadas
  para impedir que respostas antigas substituam a selecao mais recente.
- `main.js`: liga os modulos e deixa de afirmar estados apos tres falhas ou30s sem sucesso,
  incluindo pedidos bloqueados. Recuperacao 304 restaura tambem o painel escolhido.

Os dados entram no DOM por textContent. Nao ha POST, chamadas ao LLM ou Three.js nesta fase.
Verificar com `pytest tests/browser -m e2e_browser`; os testes usam relogio simulado,
falhas de rede e dados hostis. Continuam a existir os links normais para uso sem JavaScript.

## vendor/three/

Three.js0.186.1 local, MIT, com subconjunto de addons e sem Node/build.
[Proveniencia, hashes e atualizacao](vendor/three/README.md).
A ferramenta scripts/vendor_three.py verifica o SHA512 npm antes de ler o arquivo e altera
apenas imports nus para caminhos relativos. O painel2D ainda nao carrega estes ficheiros;
a integracao da cena pertence a tarefa9.
