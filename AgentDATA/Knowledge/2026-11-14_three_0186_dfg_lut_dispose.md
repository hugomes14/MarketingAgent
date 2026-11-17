# Three.js0.186.1: DFG_LUT retida apos desmontar a oficina

## Metadata

- Timestamp: `2026-11-14T10:57:02Z`
- Autor: agent; assunto: libertacao GPU da oficina; status: ativo.
- Retestar em: qualquer atualizacao do Three, materiais, passes ou montagem da cena.

## Achado

No Three0.186.1, libertar geometrias, texturas da cena, passes, ambiente, sombras e chamar
renderer.dispose deixa renderer.info.memory em geometrias0/texturas1. A textura restante e
a DFG_LUT usada por materiais fisicos, criada internamente e partilhada pelo modulo.

## Contexto

Port da cena Ederland para a oficina2D/3D, com Three fixado e CSP intocada. Teste no Chromium
headless/SwiftShader: qualidade alta, transicao para baixa, pausa e dispose. Nenhum modelo real.

## Metodo

O teste lifecycle falhou com uma textura restante. Instrumentacao temporaria de createTexture/
deleteTexture isolou uma textura criada em uploadTexture/SingleUniform.setValueT1. Leitura do
three.module.js identificou getDFGLUT e a atribuicao a uniforms.dfgLUT. Dispor apenas o
ambiente nao resolveu. Recolher a LUT dos uniforms dos materiais antes de material.dispose e
chamar texture.dispose no fim resolveu; instrumentacao removida do codigo final.

## Comandos

```bash
LD_LIBRARY_PATH="$PWD/var/browser-deps/usr/lib/x86_64-linux-gnu" \
  uv run pytest tests/browser/test_oficina_3d.py -m e2e_browser -k lifecycle -q
```

Usar TEST_DATABASE_URL exclusivo e nenhum pytest concorrente na mesma base.

## Evidencia

- RED: geometries0/textures1 apos a desmontagem.
- GREEN: teste lifecycle passou em5.20s, geometries0/textures0, canvas removido e RAF cancelado.
- Codigo Three inspecionado: getDFGLUT cria DataTexture nomeada DFG_LUT; uniform dfgLUT
  atribuido em setProgram. renderer.dispose nao chama dispose dessa textura.

## Codigo relevante

```text
renderer.properties.get(material).uniforms?.dfgLUT?.value
```

Recolher num Set antes de libertar materiais, confirmar name===DFG_LUT e descartar no fim.
Implementacao em src/marketing_agent/panel/static/oficina/scene.js; biblioteca vendida intacta.

## Interpretacao

Fato: os contadores reais passaram de1 para0 com a libertacao explicita. A coleta usa a
estrutura interna da versao fixada; a sua estabilidade fora de0.186.1 nao foi demonstrada.
O objeto partilhado pode ser carregado novamente se outro renderer o usar mais tarde.

## Consequencia operacional

Nao mascarar o contador nem subtrair1 nas estatisticas. Preservar o teste de desmontagem e
revalidar ao atualizar o fornecedor. A oficina monta uma cena de cada vez; nao foi validada
uma aplicacao com varios renderers simultaneos a partilhar materiais/LUT.

## Riscos e limites

Evidencia em WebGL por software; nao constitui medicao no PC/telemovel do responsavel.

## Pendencias

Medicoes reais e acessibilidade do marco M5, com a tarefa11.
