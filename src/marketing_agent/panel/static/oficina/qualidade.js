const NIVEIS = ['insuficiente', 'baixa', 'alta'];

/**
 * Decide a qualidade pela mediana de janelas inteiras de frames, nunca por frames soltos:
 * um pico isolado (rede, coletor de lixo) nao altera a mediana. Cada janela fechada move o
 * nivel um degrau: acima do limite desce (alta -> baixa -> insuficiente) e so uma mediana
 * abaixo de `limiteSubidaMs` sobe, com histerese para nao oscilar entre 25 ms e 33 ms.
 */
export class ControloQualidade {
  constructor({limiteMs = 33, janela = 90, limiteSubidaMs = 25} = {}) {
    if (!(limiteMs > limiteSubidaMs) || !(janela >= 1)) throw new Error('Limites de qualidade invalidos');
    Object.assign(this, {limiteMs, janela, limiteSubidaMs});
    this.nivel = 2;
    this.amostras = [];
  }
  /** Regista a duracao de um frame em ms e devolve o nivel atual: alta, baixa ou insuficiente. */
  registar(ms) {
    if (Number.isFinite(ms) && ms >= 0) this.amostras.push(ms);
    if (this.amostras.length >= this.janela) this.fecharJanela();
    return NIVEIS[this.nivel];
  }
  fecharJanela() {
    const ordenadas = this.amostras.sort((a, b) => a - b);
    const meio = ordenadas.length >> 1;
    const mediana = ordenadas.length % 2 ? ordenadas[meio] : (ordenadas[meio - 1] + ordenadas[meio]) / 2;
    this.amostras = [];
    if (mediana > this.limiteMs) this.nivel = Math.max(0, this.nivel - 1);
    else if (mediana < this.limiteSubidaMs) this.nivel = Math.min(2, this.nivel + 1);
  }
  /** Descarta a janela em curso, por exemplo depois de pausa ou de mudar a qualidade. */
  reiniciarJanela() {
    this.amostras = [];
  }
}
