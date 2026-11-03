# Procedimento: qualidade de codigo

## Quando carregar

Carregar para implementacao, refatoracao, revisao de arquitetura, design de modulo, definicao de contrato ou mudanca de comportamento relevante.

## Prioridade de decisao

1. correcao;
2. clareza;
3. rastreabilidade;
4. simplicidade;
5. testabilidade;
6. consistencia com o repositorio;
7. performance;
8. elegancia.

## Arquitetura e organizacao

- Organizar por responsabilidade.
- Separar entrada, processamento e saida quando fizer sentido.
- Separar dominio, infraestrutura, interface e execucao quando isso reduzir acoplamento.
- Evitar `utils`, `misc` e arquivos genericos para esconder logica.
- Orquestradores devem ter nome descritivo, como `runIngestionPipeline.ts`.

## Robustez pratica

- Preferir fluxo linear e legivel.
- Evitar aninhamento profundo.
- Loops devem ter criterio de parada claro.
- Retries precisam de limite.
- Polling precisa de timeout.
- Declarar variaveis perto do uso.
- Tratar retornos e erros importantes.
- Evitar dependencia escondida em estado global obscuro.
- Garantir determinismo quando ordem, cache, reprodutibilidade ou hash importarem.

## Performance

Pensar em performance quando relevante:

- evitar explosao de memoria;
- evitar IO redundante;
- evitar parsing duplicado;
- evitar queries obviamente ruins;
- otimizar hot path real.

Nao fazer micro-otimizacao ornamental.
