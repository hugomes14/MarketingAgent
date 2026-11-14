import {OfficeScene} from './scene.js';

/** Instancia visual; nenhuma atividade ou decisao de negocio e criada aqui. */
export class Oficina3D {
  constructor(contentor, {personagens = [], aoSelecionar, reduzirMovimento = false} = {}) {
    this.contentor = contentor;
    this.cena = new OfficeScene(contentor, {onSelect: aoSelecionar, reducedMotion: reduzirMovimento,
      onFrame: () => {contentor.dataset.pronto = '1';}});
    const canvas = this.cena.renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute('aria-label', 'Oficina 3D da equipa');
    this.cena.setCharacters(personagens);
    this.visibilidade = () => document.hidden ? this.cena.pause() : this.cena.resume();
    document.addEventListener('visibilitychange', this.visibilidade);
    this.visibilidade();
  }
  /** Liberta recursos e conserva apenas o diagnostico final, sem renderer ativo. */
  desmontar() {
    if (this.memoriaFinal) return this.memoriaFinal;
    document.removeEventListener('visibilitychange', this.visibilidade);
    this.memoriaFinal = this.cena.dispose();
    delete this.contentor.dataset.pronto;
    return this.memoriaFinal;
  }
  /** Contadores reais para inspecao e testes, nunca controla o dominio. */
  estatisticas() {
    return {ativo: this.cena.running, quadros: this.cena.frames, qualidade: this.cena.quality,
      pixelRatio: this.cena.renderer.getPixelRatio(), camara: this.cena.camera.position.toArray(),
      memoria: this.memoriaFinal || {...this.cena.renderer.info.memory}};
  }
}

/** Monta a cena com as personagens e atividades fornecidas pelo chamador. */
export async function montar(contentor, opcoes) {
  return new Oficina3D(contentor, opcoes);
}
