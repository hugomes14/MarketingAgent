import {OfficeScene} from './scene.js';
import {Vector3} from '../vendor/three/build/three.module.js';

const ESTADOS = new Set(['a_trabalhar', 'erro', 'desatualizado', 'em_espera', 'inativo']);
const JARDIM = {diretor: 0, copywriter: 4, designer: 7, revisor: 10};
const SECRETARIAS = {
  diretor: {type: 'office', index: 1}, copywriter: {type: 'desk', zone: 'team', index: 4},
  designer: {type: 'desk', zone: 'lab', index: 0}, revisor: {type: 'desk', zone: 'team', index: 7}
};
let instanciaAtual = null;

/** Instancia visual; nenhuma atividade ou decisao de negocio e criada aqui. */
export class Oficina3D {
  constructor(contentor, {personagens = [], aoSelecionar, reduzirMovimento = false} = {}) {
    this.contentor = contentor;
    this.personagens = personagens;
    this.reduzirMovimento = reduzirMovimento;
    this.fila = []; this.entrega = null; this.estado = null;
    this.cena = new OfficeScene(contentor, {
      onSelect: papel => {this.selecionar(papel); aoSelecionar?.(papel);},
      reducedMotion: reduzirMovimento,
      onFrame: () => {contentor.dataset.pronto = '1'; this.avancarEntrega();}
    });
    const canvas = this.cena.renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute('aria-label', 'Oficina 3D da equipa');
    this.sincronizar();
    this.visibilidade = () => document.hidden ? this.cena.pause() : this.cena.resume();
    document.addEventListener('visibilitychange', this.visibilidade);
    this.visibilidade();
    instanciaAtual = this;
  }
  /** Usa exclusivamente o snapshot recebido; primeira carga nao cria entregas. */
  aplicarEstado(estadoEquipa, transicoes = []) {
    if (this.memoriaFinal) return;
    this.estado = estadoEquipa;
    for (const transicao of transicoes) {
      if (transicao.tipo === 'entrega_pasta' && !this.fila.some(t => t.id === transicao.id) && this.entrega?.id !== transicao.id) this.fila.push(transicao);
    }
    this.iniciarEntrega(); this.sincronizar();
  }
  iniciarEntrega() {
    if (this.entrega || !this.fila.length) return;
    this.entrega = this.fila.shift();
    this.contentor.dataset.ultimaTransicao = 'entrega_pasta:' + this.entrega.id;
  }
  avancarEntrega() {
    if (!this.entrega || this.memoriaFinal) return;
    if (this.reduzirMovimento) {
      // Uma pose confirma a transicao sem deslocar a personagem ou usar um temporizador.
      this.entrega = null; return;
    }
    if (this.cena.arrived('copywriter')) {
      this.entrega = null; this.iniciarEntrega(); this.sincronizar();
    }
  }
  sincronizar() {
    const characters = this.personagens.map(base => {
      const agente = this.estado?.agentes.find(a => a.papel === base.id);
      const estado = ESTADOS.has(agente?.estado) ? agente.estado : 'desatualizado';
      let activity = estado === 'a_trabalhar' ? 'typing' : estado === 'inativo' ? 'relax' : 'paused';
      let place = estado === 'inativo' ? {type: 'garden', index: JARDIM[base.id]} : SECRETARIAS[base.id];
      // Sem snapshot confirmado, as quatro personagens continuam visiveis no jardim.
      if (!agente) place = {type: 'garden', index: JARDIM[base.id]};
      if (base.id === 'copywriter' && this.entrega) {
        activity = 'carry';
        if (!this.reduzirMovimento) {
          const desk = this.cena.seatFor(SECRETARIAS.revisor);
          place = {type: 'point', x: desk.x + 0.8, z: desk.z, rot: desk.rot};
        }
      }
      this.contentor.setAttribute('data-atividade-' + base.id, activity);
      return {...base, name: agente?.nome ?? base.name, place, activity, estado};
    });
    this.cena.setCharacters(characters);
    for (const character of characters) {
      const label = this.cena.chars.get(character.id)?.labelEl;
      if (label) {
        label.dataset.estado = character.estado;
        label.querySelector('.office-dot').className = 'office-dot estado-icone estado-' + character.estado;
      }
    }
  }
  /** Uma falha de ligacao tambem impede a cena de afirmar trabalho em curso. */
  marcarSemLigacao() {
    if (this.memoriaFinal) return;
    this.fila = []; this.entrega = null;
    if (this.estado) this.estado = {...this.estado, agentes: this.estado.agentes.map(a => ({...a, estado: 'desatualizado'}))};
    this.sincronizar();
    for (const ch of this.cena.chars.values()) {
      ch.route = []; ch.placeKey = 'sem-ligacao';
      ch.spot = {x: ch.root.position.x, z: ch.root.position.z, rot: ch.root.rotation.y, seat: false};
    }
  }
  /** Destaque visual sem notificar novamente a coluna e criar um ciclo. */
  selecionar(papel, {focar = true} = {}) {
    if (!this.cena.chars.has(papel) || this.memoriaFinal) return;
    this.cena.select(papel, focar);
    this.contentor.dataset.selecionado = papel;
  }
  /** Liberta recursos e conserva apenas o diagnostico final, sem renderer ativo. */
  desmontar() {
    if (this.memoriaFinal) return this.memoriaFinal;
    document.removeEventListener('visibilitychange', this.visibilidade);
    this.memoriaFinal = this.cena.dispose();
    if (instanciaAtual === this) instanciaAtual = null;
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

/** Coordenadas de leitura da cabeca no ecra, sem selecionar ou alterar o dominio. */
export function projetarPapel(papel) {
  const cena = instanciaAtual?.cena;
  const head = cena?.chars.get(papel)?.root.userData.parts.head;
  if (!head || instanciaAtual.memoriaFinal) return null;
  cena.scene.updateMatrixWorld(true); cena.camera.updateMatrixWorld(true);
  const point = head.getWorldPosition(new Vector3()).project(cena.camera);
  const rect = cena.renderer.domElement.getBoundingClientRect();
  return {x: rect.left + (point.x + 1) * rect.width / 2, y: rect.top + (1 - point.y) * rect.height / 2};
}
